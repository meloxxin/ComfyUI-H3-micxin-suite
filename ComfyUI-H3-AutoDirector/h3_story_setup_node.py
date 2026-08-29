# -*- coding: utf-8 -*-
"""
h3_story_setup_node.py (by micxin2025) — v2.0 一键短剧创作引擎
====================================================================

把 H3 Story Setup 从一个纯参数透传节点升级为「AI短剧一键创作引擎」：
输入一句话创意 → 自动生成剧本、分镜表、图片提示词、视频提示词（H3六段式）、
以及可直接喂给 MieLoopStart 的纯文本 prompt 列表。

与 H3PromptWriter 的分工：
  - H3StorySetup（本节点）：剧本创作层 — 创意 → 剧本 + 分镜 + 多镜头提示词列表
  - H3PromptWriter       ：单镜头提示词润色层 — 单镜头概念 → H3六段式精修

两种运行模式：
  1. 「一键短剧生成」：调用 LLM，输出全部创作产物（9个输出口）
  2. 「仅参数透传（兼容）」：不调用 LLM，仅输出 concept + setup_json（向后兼容旧工作流）

LLM 后端（与 H3PromptWriter 一致，可折叠切换）：
  - Local GGUF（默认）：ComfyUI 内直接加载 GGUF，带模型缓存 + 显存自动降级
  - HTTP endpoint：OpenAI 兼容 /v1/chat/completions，支持本地 llama.cpp server、
    Ollama、SiliconFlow、DeepSeek 等
推荐本地部署 Qwen3-VL-8B-Instruct GGUF。

输出端口（按顺序）：
  0. concept        STRING  — 原始创意（向后兼容）
  1. setup_json     STRING  — 设定参数 JSON（向后兼容）
  2. title          STRING  — 短剧标题
  3. screenplay     STRING  — 可读剧本（标题+梗概+剧情+分镜表）
  4. storyboard_json STRING — 完整分镜 JSON（含每镜头全部字段）
  5. image_prompts  STRING  — 图片提示词列表（JSON 数组字符串）
  6. video_prompts  STRING  — 简洁视频提示词列表（JSON 数组字符串）
  7. h3_prompts     STRING  — H3 六段式提示词列表（JSON 数组字符串）
  8. prompt_list    STRING  — 纯文本列表（每行一个 H3 提示词，直接喂 MieLoopStart）
"""

import os
import json
import hashlib
import re
import time
import math
import urllib.request
import urllib.error

from comfy_api.latest import io

# 复用 H3PromptWriter 的 Local GGUF 加载/调用/卸载逻辑（同包内，已测试稳定）。
# 这些函数带模型缓存、mmproj 多模态支持、显存不足自动降级。
try:
    from .h3_screenwriter import (
        _load_local_llm, _call_local_llm, _unload_local,
        _list_llm_files, _N_CTX, _MAX_GEN_TOKENS,
    )
    _HAS_LOCAL_GGUF = True
except Exception:
    _HAS_LOCAL_GGUF = False
    _N_CTX = 8192
    _MAX_GEN_TOKENS = 4096

    def _load_local_llm(*a, **kw):
        raise RuntimeError("H3StorySetup: Local GGUF 不可用（无法从 h3_screenwriter 导入）。请改用 HTTP endpoint。")

    def _call_local_llm(*a, **kw):
        raise RuntimeError("H3StorySetup: Local GGUF 不可用。")

    def _unload_local():
        pass

    def _list_llm_files(*a, **kw):
        return []

# ----------------------------------------------------------------------------
# 独立的 GGUF 文件扫描（不依赖 h3_screenwriter，彻底解决下拉框为空导致爆红）
# 无论 h3_screenwriter 是否导入成功，都用这个函数覆盖 _list_llm_files
# ----------------------------------------------------------------------------
def _scan_gguf_files(include_mmproj=True, mmproj_only=False):
    """扫描 ComfyUI/models/LLM 目录下的 .gguf 文件。"""
    try:
        import folder_paths
        llm_dirs = folder_paths.get_folder_paths("LLM")
    except Exception:
        llm_dirs = []
    # fallback：直接拼路径
    if not llm_dirs:
        try:
            import folder_paths
            base = os.path.join(folder_paths.models_dir, "LLM")
            if os.path.isdir(base):
                llm_dirs = [base]
        except Exception:
            pass
    files = []
    for d in llm_dirs:
        try:
            if os.path.isdir(d):
                for f in os.listdir(d):
                    if f.lower().endswith(".gguf") and f not in files:
                        files.append(f)
        except Exception:
            continue
    if mmproj_only:
        files = [f for f in files if "mmproj" in f.lower()]
    elif not include_mmproj:
        files = [f for f in files if "mmproj" not in f.lower()]
    return sorted(files)

# 无条件覆盖：保证 gguf / mmproj 下拉框始终有内容
_list_llm_files = _scan_gguf_files

# ============================================================================
# 常量 — 下拉枚举
# ============================================================================

STYLE_OPTIONS = [
    "电影感实拍", "2D 动画", "3D 电脑动画", "黏土动画", "水彩画",
    "复古胶片", "日式动漫", "极简产品广告", "纪录片", "赛博朋克霓虹",
]

DRAMA_GENRE_OPTIONS = [
    "都市情感", "悬疑推理", "甜宠恋爱", "逆袭爽文", "古风仙侠",
    "科幻未来", "恐怖惊悚", "喜剧搞笑", "职场商战", "校园青春",
    "家庭伦理", "历史正剧", "动作冒险", "奇幻魔法", "自由发挥",
]

ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1", "21:9", "4:3"]
RESOLUTION_MP_OPTIONS = [
    "0.2", "0.3", "0.4", "0.5", "0.6", "0.8", "1.0", "1.2", "1.5", "2.0",
]

MODE_OPTIONS = ["一键短剧生成", "仅参数透传（兼容）"]
BACKEND_OPTIONS = ["Local GGUF", "HTTP endpoint"]

# Local GGUF 下拉列表（模块加载时扫描 ComfyUI/models/LLM）
_GGUF_FILES = [""] + (_list_llm_files(False) or [])
_MMPROJ_FILES = ["None"] + (_list_llm_files(True, mmproj_only=True) or [])

# 风格 → 英文 style prefix（用于 H3 六段式 detailed_description 开头）
STYLE_PREFIX_MAP = {
    "电影感实拍": "Cinematic live-action, photorealistic",
    "2D 动画": "2D animation, hand-drawn style",
    "3D 电脑动画": "3D CG animation, Pixar-style rendering",
    "黏土动画": "Claymation, stop-motion clay animation",
    "水彩画": "Watercolor painting style, soft brush strokes",
    "复古胶片": "Vintage film, grainy 35mm analog aesthetic",
    "日式动漫": "Japanese anime style, cel-shaded",
    "极简产品广告": "Minimalist product cinematic, clean studio",
    "纪录片": "Documentary style, handheld naturalistic",
    "赛博朋克霓虹": "Cyberpunk neon, rain-soaked futuristic city",
}

# ============================================================================
# 系统提示词 — 短剧分镜生成专家
# ============================================================================

SHORT_DRAMA_SYSTEM_PROMPT = r"""You are a professional AI short-drama screenwriter and storyboard artist.
Your job: take the user's one-line creative idea and produce a COMPLETE, production-ready
short-drama package as STRICT JSON. No markdown, no commentary, no code fences — ONLY the JSON object.

## Output JSON Schema (must match exactly)

{
  "title": "string — Chinese title, ≤15 chars",
  "logline": "string — Chinese one-sentence premise, ≤50 chars",
  "synopsis": "string — Chinese plot summary 100-200 chars, with setup/confrontation/resolution",
  "characters": [
    {
      "name": "string — character name",
      "visual": "string — ENGLISH detailed appearance ONLY: age, hair style/color, face shape, body type, distinctive features. NO clothing. NO reference-sheet / four-panel / turnaround / white-background language — this is for the video model's subject definition, NOT for image generation.",
      "outfit": "string — ENGLISH detailed clothing/costume ONLY: top, bottom, shoes, accessories, colors, materials. NO reference-sheet / four-panel / turnaround language.",
      "language": "string — 'Mandarin' (default) or 'Cantonese' or 'English' or 'Japanese' or 'Korean'. The language this character speaks. If the user's concept mentions 粤语/广东话/Cantonese, set this to 'Cantonese' and write this character's dialogue in Cantonese.",
      "character_prompt": "string — ENGLISH character reference sheet prompt for text-to-image ONLY (separate output, never used in video prompts). MUST use four-panel layout: 'Character reference sheet, four panels arranged left to right: close-up of the face, then full-body front view, full-body side view, full-body back view, on a clean white background. [visual description] [outfit description]. High detail, consistent character design, professional turnaround sheet.'"
    }
  ],
  "scenes": [
    {
      "scene_name": "string — short English label for this unique location (e.g. 'rainy_convenience_store', 'office_meeting_room')",
      "scene_prompt": "string — ENGLISH clean background scene prompt for text-to-image. Describe the environment/location with clean, uncluttered composition, suitable for compositing characters later. Include lighting, time of day, atmosphere. Use 'clean background, no characters, no people' to ensure empty scene."
    }
  ],
  "shots": [
    {
      "shot_number": 1,
      "scene": "string — ENGLISH: time, place, environment, atmosphere, lighting",
      "scene_ref": "string — the scene_name from the scenes array that this shot uses",
      "action": "string — ENGLISH: concrete character actions, expressions, movements (filmable). This is a backup for video_prompt; write it in English.",
      "dialogue": "string — spoken line in the speaking character's language (from characters[].language). If character language is Cantonese, write dialogue in Cantonese using Cantonese characters (e.g., 係, 唔, 咁, 嘅, 喺, 嚟, 哋, 佢, 咗, 啲, 嘢, 睇, 飲, 食, 行, 走, 返, 邊, 幾, 靚, 平, 貴, 熱, 凍, 鹹). Empty string '' if NO dialogue in this shot. NEVER leave dialogue empty when a character is clearly speaking — the line will be lost in the video.",
      "camera": "string — ENGLISH camera language: shot type (close-up/wide/medium), movement (dolly in/pan/handheld/static), angle",
      "duration": 5,
      "video_prompt": "string — ENGLISH concise but complete video generation prompt: scene environment + character action + camera movement + lighting/mood + dialogue reference (if any). This is for a text-to-video model. 80-150 words. ALWAYS generate this field — it is the primary prompt used for video generation."
    }
  ]
}

## Hard Rules

1. Generate EXACTLY the number of shots specified by the user (num_shots). Not one more, not one less.
2. Each shot's duration should be close to the user's duration_per_shot (±2s allowed for dramatic pacing).
3. dialogue field: spoken words in the EXACT language the user used in their creative idea. DO NOT translate dialogue into English — if the user wrote Chinese dialogue, keep it in Chinese; if English, keep English; if Cantonese, keep Cantonese. If the user's creative idea contains a specific quoted line (e.g. 她说："你好"), use that EXACT line verbatim — do not rewrite, paraphrase, or translate it. Empty string '' only if the shot is truly silent. If a character is shown speaking or reacting verbally, ALWAYS include the actual spoken line — leaving it empty means the dialogue will be LOST from the video. Never put narration in dialogue.
4. ALL narrative fields (scene, action, video_prompt, character visual/outfit, scene_prompt, camera) MUST be in ENGLISH. The ONLY field that may contain non-English text is dialogue (which keeps the character's spoken language). H3 video model follows English narrative — Chinese in narrative fields causes confusion and garbled output.
5. characters[].visual = appearance ONLY (face, hair, body, features). characters[].outfit = clothing ONLY. Keep them separate.
6. characters[].character_prompt MUST follow the four-panel reference sheet format exactly: face close-up + front + side + back full-body views, white background. Include both visual and outfit details.
7. scenes[] should list ONLY unique locations (deduplicate). If multiple shots use the same location, list it once and reference via scene_ref.
8. scenes[].scene_prompt must produce a CLEAN, EMPTY background (no characters, no people). This is for compositing.
9. If the user's idea does not name characters, invent 1-3 characters with detailed visual + outfit descriptions.
10. The story must have conflict and a turn. Avoid flat, plotless descriptions.
11. camera field: use standard English cinematography terms (e.g. "extreme close-up, slow dolly in", "wide establishing shot, static", "medium shot, handheld tracking").
12. Output ONLY the JSON object. No text before or after. No ```json fences. No explanations.
13. The total video should feel like a coherent short drama, not disconnected clips. Shots should flow causally.
14. Every shot's scene_ref MUST match one of the scene_name values in the scenes array.
15. Language / dialect handling: If the user's concept mentions 粤语/广东话/Cantonese, set the relevant characters' language field to 'Cantonese' and write their dialogue in authentic Cantonese (using Cantonese-specific characters like 係, 唔, 咁, 嘅, 喺, 嚟, 哋, 佢, 咗, 啲, 嘢, 睇, 飲, 食, 行, 走, 返, 邊, 幾, 靚, 平, 貴, 熱, 凍, 鹹). Do NOT translate Cantonese dialogue into Mandarin or English. Characters not specified to speak Cantonese default to 'Mandarin' — write their dialogue in Mandarin Chinese, and DO NOT translate it into English. If the user's creative idea contains English dialogue, set language to 'English' and keep it in English. NEVER translate dialogue from its original language.
"""

# ============================================================================
# 辅助函数 — LLM 调用
# ============================================================================

def _call_llm_http(url, model, api_key, messages, temperature=0.7, seed=0,
                    timeout=120, max_retries=2, retry_delay=5, overall_timeout=180):
    """调用 OpenAI 兼容 HTTP 端点（/v1/chat/completions）。

    支持本地 llama.cpp server、Ollama、SiliconFlow、DeepSeek 等。
    带总超时护栏，避免 ComfyUI 单线程被长时间阻塞。
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def build_payload():
        p = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": 8192,
        }
        if seed and int(seed) > 0:
            p["seed"] = int(seed)
        return p

    last_err = None
    deadline = time.monotonic() + overall_timeout

    for attempt in range(max_retries):
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            raise RuntimeError(
                f"H3StorySetup: LLM overall timeout ({overall_timeout}s) reached "
                f"before attempt {attempt + 1}.")
        call_timeout = max(5, int(min(timeout, remaining)))
        payload = build_payload()
        try:
            req = urllib.request.Request(
                url, json.dumps(payload).encode("utf-8"), headers)
            with urllib.request.urlopen(req, timeout=call_timeout) as r:
                raw = r.read()
            data = json.loads(raw.decode("utf-8", "replace"))
            choices = data.get("choices") or [{}]
            content = (choices[0].get("message", {}).get("content") or "")
            if not content.strip():
                content = choices[0].get("message", {}).get(
                    "reasoning_content", "") or ""
            if not content.strip():
                raise ValueError("LLM returned empty content.")
            return content
        except urllib.error.HTTPError as e:
            last_err = e
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            print(f"[H3StorySetup] LLM attempt {attempt + 1}/{max_retries} "
                  f"failed: HTTP {e.code} — {body}", flush=True)
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                ValueError, KeyError, IndexError, json.JSONDecodeError) as e:
            last_err = e
            print(f"[H3StorySetup] LLM attempt {attempt + 1}/{max_retries} "
                  f"failed: {type(e).__name__}: {e}", flush=True)
        if attempt < max_retries - 1:
            sleep_time = min(retry_delay, max(0.0, deadline - time.monotonic()))
            time.sleep(sleep_time)

    raise RuntimeError(
        f"H3StorySetup: LLM call failed after {max_retries} attempts: {last_err}")


# ============================================================================
# 辅助函数 — JSON 提取（处理 LLM 输出中的 markdown 围栏等）
# ============================================================================

def _extract_json(text):
    """从 LLM 返回文本中提取 JSON 对象。

    处理情况：
      - 纯 JSON（最理想）
      - 被 ```json ... ``` 包裹
      - 被 ``` ... ``` 包裹
      - JSON 前后有说明文字
      - JSON 中有尾随逗号（容错修复）
    """
    if not text:
        raise ValueError("LLM 返回为空。")

    text = text.strip()

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 ```json ... ``` 或 ``` ... ```
    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            text = candidate  # 继续用其他方式修复

    # 找第一个 { 和最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        # 修复尾随逗号
        candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"无法解析 LLM 返回的 JSON: {e}\n"
                f"原始内容前500字: {text[:500]}")

    raise ValueError(
        f"LLM 返回中未找到 JSON 对象。原始内容前500字: {text[:500]}")


# ============================================================================
# 辅助函数 — H3 六段式包装
# ============================================================================

# 粤语核心特征字（普通话中极少使用，出现即大概率为粤语）
_CANTONESE_MARKERS = set(
    "咁係唔嗰嘅喺嚟哋佢咗啲㗎嘢睇靚凍鹹邊幾返行走飲食"
)

# 角色语言字段 → H3 六段式语言标签映射
_LANG_MAP = {
    "mandarin": "Chinese",
    "chinese": "Chinese",
    "putonghua": "Chinese",
    "cantonese": "Cantonese",
    "yue": "Cantonese",
    "guangdonghua": "Cantonese",
    "english": "English",
    "japanese": "Japanese",
    "korean": "Korean",
}


def _detect_dialogue_language(dialogue, character_language=None):
    """检测对话语言，生成 H3 六段式 <d>[Language] 标签。

    优先级：
      1. 角色设定的 language 字段（characters[].language）
      2. Unicode 范围检测（日文假名 / 韩文）
      3. 粤语特征字检测（咁係唔嗰嘅喺嚟哋佢咗啲㗎嘢睇靚凍鹹...）
      4. 英文检测（拉丁字母占比 > 60% 且无中文字符）
      5. 默认 Chinese（普通话）
    """
    # 1. 优先使用角色设定的语言
    if character_language:
        key = str(character_language).strip().lower()
        if key in _LANG_MAP:
            return _LANG_MAP[key]
        # 未知语言名原样返回（H3 可能支持）
        return str(character_language).strip()

    # 2. Unicode 检测：日文假名
    if any('\u3040' <= c <= '\u30ff' for c in dialogue):
        return "Japanese"
    # 韩文音节
    if any('\uac00' <= c <= '\ud7a3' for c in dialogue):
        return "Korean"

    # 3. 粤语特征字检测
    if any(c in _CANTONESE_MARKERS for c in dialogue):
        return "Cantonese"

    # 4. 英文检测：统计中文字符和拉丁字母
    cjk_count = sum(1 for c in dialogue if '\u4e00' <= c <= '\u9fff')
    latin_count = sum(1 for c in dialogue if c.isascii() and c.isalpha())
    total_letters = cjk_count + latin_count
    if total_letters > 0 and cjk_count == 0 and latin_count / total_letters > 0.6:
        return "English"

    # 5. 默认中文（普通话）
    return "Chinese"


def _filter_characters_by_concept(concept, characters, ext_characters, shots):
    """硬过滤：只保留用户 concept 里提到的角色，杜绝 LLM 自作主张加角色。

    逻辑：
      1. 从 concept 提取提到的角色名（匹配资产库 ext_characters 里的 name）
      2. 匹配到了 → 只保留这些角色；没匹配到 → 只保留第一个角色（兜底）
      3. 过滤 characters 数组
      4. 过滤 shots 里的 characters 引用（被过滤的角色替换成保留的第一个）
    """
    if not characters:
        return characters, shots

    # 1. 从 concept 提取提到的角色名
    mentioned_names = set()
    for ch in ext_characters:
        cname = (ch.get("name") or "").strip()
        if cname and cname in concept:
            mentioned_names.add(cname)

    # 如果没提到任何角色，只保留第一个（兜底）
    if not mentioned_names:
        keep_names = {(characters[0].get("name") or "").strip()}
        print(f"[H3StorySetup] concept 未提到具体角色，硬过滤保留第一个: {list(keep_names)}", flush=True)
    else:
        keep_names = mentioned_names
        print(f"[H3StorySetup] concept 提到角色: {list(keep_names)}，硬过滤其他角色", flush=True)

    # 2. 过滤 characters 数组
    filtered_chars = [
        ch for ch in characters
        if (ch.get("name") or "").strip() in keep_names
    ]
    if not filtered_chars:
        filtered_chars = [characters[0]]  # 兜底，至少保留一个
        print(f"[H3StorySetup] 过滤后为空，兜底保留第一个: {filtered_chars[0].get('name')}", flush=True)

    # 3. 过滤 shots 里的 characters 引用
    primary_name = (filtered_chars[0].get("name") or "").strip()
    for shot in shots:
        shot_chars = shot.get("characters")
        if isinstance(shot_chars, list) and shot_chars:
            kept = [
                c for c in shot_chars
                if (str(c).strip() in keep_names)
            ]
            if not kept:
                kept = [primary_name]
            shot["characters"] = kept
        # action 里如果提到了被过滤的角色名，替换成保留的第一个（避免画面里出现不存在的角色）
        action = shot.get("action", "")
        if action and isinstance(action, str):
            for ch in characters:
                cname = (ch.get("name") or "").strip()
                if cname and cname not in keep_names and cname in action:
                    action = action.replace(cname, primary_name)
            shot["action"] = action

    print(f"[H3StorySetup] 硬过滤完成: 角色 {len(characters)}→{len(filtered_chars)}", flush=True)
    return filtered_chars, shots


def _wrap_h3_six_section(shot, characters, scenes, style, aspect_ratio,
                          shot_index, total_shots, use_picture_tags=True,
                          dialogue_enabled=True):
    """把单个镜头数据包装成 H3 官方六段式提示词。

    两种模式：
      use_picture_tags=True （Ref2VA 模式）：
        生成 <Picture N> 标签，需在 H3ModelLoader 传入对应参考图。
        Picture 1..N  = 人物 1..N 的四视图参考图（characters 顺序）
        Picture N+1.. = 场景 1..M 的干净背景图（scenes 顺序）
        每个人物 = <Picture N>（参考图）+ <Subject N>（引用Picture外貌）

      use_picture_tags=False （纯文生视频模式）：
        不生成 <Picture N> 标签，<Subject N> 直接描述外貌和穿搭。
        无参考图时必须用此模式，否则 H3 看到 Picture 标签但无图会异常。

    六段：subject_definitions / summary / retention_analysis /
          detailed_description / overall_soundscape / non_diegetic_music

    Args:
        shot: 分镜JSON中的单个shot对象
        characters: 角色列表（每个人物含 name/visual/outfit）
        scenes: 场景列表（每个场景含 scene_name/scene_prompt）
        style: 视觉风格（中文）
        aspect_ratio: 画幅
        shot_index: 当前镜头序号（从1开始）
        total_shots: 总镜头数

    Returns:
        str: H3六段式提示词
    """
    style_prefix = STYLE_PREFIX_MAP.get(style, "Cinematic live-action, photorealistic")
    n_chars = len(characters)

    # 确定当前镜头使用哪个场景（通过 scene_ref 匹配 scenes 中的 scene_name）
    scene_ref = shot.get("scene_ref", "")
    scene_idx = 0
    for i, sc in enumerate(scenes):
        if sc.get("scene_name") == scene_ref:
            scene_idx = i
            break
    # 场景对应的 Picture/Subject 编号 = 人物数 + 场景索引 + 1
    scene_pic_idx = n_chars + scene_idx + 1
    current_scene = scenes[scene_idx] if scenes else {}

    # ---- 1. subject_definitions ----
    subject_lines = []
    # 四视图/参考图相关词汇 — 这些只应出现在 character_prompt（文生图）中，
    # 绝不能混进 video prompt 的 subject_definitions，否则 H3 会把四视图当视频内容生成
    _REF_SHEET_WORDS = [
        "four-panel", "four panel", "reference sheet", "turnaround",
        "white background", "clean white", "front view", "side view",
        "back view", "full-body front", "face close-up", "character sheet",
        "model sheet", "consistent character design", "professional turnaround",
    ]
    def _strip_ref_sheet_words(text):
        if not text:
            return text
        result = text
        for w in _REF_SHEET_WORDS:
            result = re.sub(re.escape(w), "", result, flags=re.IGNORECASE)
        # 清理多余空格和标点
        result = re.sub(r'\s+', ' ', result).strip(' ,.;:')
        return result

    for i, ch in enumerate(characters, 1):
        cname = ch.get("name", f"Character {i}")
        visual = _strip_ref_sheet_words(ch.get("visual", "a person with unspecified appearance"))
        outfit = _strip_ref_sheet_words(ch.get("outfit", ""))
        outfit_part = f" Wearing {outfit}." if outfit else ""
        if use_picture_tags:
            # Ref2VA 模式：Picture 参考图 + Subject 引用 Picture
            # ★ 关键：不写 LLM 生成的 visual 外貌描述（发型/五官/脸型），避免和参考图冲突。
            #   外貌完全由参考图决定，只强调"精确匹配参考图，不得改变发型"。
            #   穿搭保留（参考图可能是头部白底图，穿搭不在图里，需要文字补充）。
            subject_lines.append(
                f"<Picture {i}> is a character reference image for {cname}.")
            subject_lines.append(
                f"<Subject {i}> is {cname}, whose exact appearance, facial features, "
                f"hairstyle, hair length, hair color, face shape, and body type "
                f"must EXACTLY match <Picture {i}> — do NOT alter any visual features, "
                f"especially hairstyle and hair length.{outfit_part}")
        else:
            # 纯文生视频模式：Subject 直接描述，不引用 Picture
            subject_lines.append(
                f"<Subject {i}> is {cname}, {visual}.{outfit_part}")

    # 场景
    for i, sc in enumerate(scenes):
        pic_idx = n_chars + i + 1
        sname = sc.get("scene_name", f"Scene {i+1}")
        sprompt = sc.get("scene_prompt", "")
        if use_picture_tags:
            subject_lines.append(
                f"<Picture {pic_idx}> is a clean background reference image of {sname}: "
                f"{sprompt[:150]}")
            subject_lines.append(
                f"<Subject {pic_idx}> is the environment/location {sname}, "
                f"whose setting, lighting, and atmosphere are referenced from <Picture {pic_idx}>.")
        else:
            subject_lines.append(
                f"<Subject {pic_idx}> is the environment/location {sname}: {sprompt[:150]}")

    subject_definitions = "\n".join(subject_lines)

    # ---- 2. summary ----
    # 优先用 video_prompt（英文），其次 action（现在系统提示词也要求英文），
    # 都为空时用英文默认描述，绝不让中文混进英文叙述
    action_en = (
        shot.get("video_prompt")
        or shot.get("action")
        or "Characters interact in the scene environment with natural movement."
    )
    action_en = str(action_en).strip()
    # 检测当前镜头出现的人物（action/scene 中提到名字的）
    appearing = []
    for i, ch in enumerate(characters, 1):
        cname = ch.get("name", "")
        if cname and (cname in shot.get("action", "") or cname in shot.get("scene", "")):
            appearing.append(f"<Subject {i}>")
    if not appearing:
        appearing = [f"<Subject {i}>" for i in range(1, min(n_chars + 1, 3))]
    scene_subject = f"<Subject {scene_pic_idx}>"

    summary = (
        f"[reference generation] Shot {shot_index}/{total_shots}: "
        f"{style_prefix}. {', '.join(appearing)} in {scene_subject}. "
        f"{action_en[:200]}")

    # ---- 3. retention_analysis ----
    retention_lines = []
    for i, ch in enumerate(characters, 1):
        cname = ch.get("name", f"Character {i}")
        if use_picture_tags:
            retention_lines.append(
                f"<Picture {i}> (appears in [Shot 1]): fully_preserved - "
                f"{cname}'s character reference image used for identity, "
                f"hairstyle, facial features, and outfit consistency — "
                f"hairstyle and hair length must EXACTLY match the reference.")
            retention_lines.append(
                f"<Subject {i}> (appears in [Shot 1]): fully_preserved - "
                f"{cname}'s appearance, hairstyle, clothing, and identity "
                f"consistent throughout, exactly referenced from <Picture {i}>.")
        else:
            retention_lines.append(
                f"<Subject {i}> (appears in [Shot 1]): fully_preserved - "
                f"{cname}'s appearance, clothing, and identity consistent throughout.")
    # 场景
    for i, sc in enumerate(scenes):
        pic_idx = n_chars + i + 1
        sname = sc.get("scene_name", f"Scene {i+1}")
        if use_picture_tags:
            retention_lines.append(
                f"<Picture {pic_idx}> (appears in [Shot 1]): fully_preserved - "
                f"{sname} clean background reference, environment and lighting preserved.")
            retention_lines.append(
                f"<Subject {pic_idx}> (appears in [Shot 1]): fully_preserved - "
                f"{sname} setting and atmosphere consistent, referenced from <Picture {pic_idx}>.")
        else:
            retention_lines.append(
                f"<Subject {pic_idx}> (appears in [Shot 1]): fully_preserved - "
                f"{sname} setting and atmosphere consistent.")
    retention_analysis = "\n".join(retention_lines)

    # ---- 4. detailed_description ----
    camera = shot.get("camera", "medium shot, static")
    action = shot.get("action", "")
    dialogue = shot.get("dialogue", "")
    video_prompt = shot.get("video_prompt", "")

    detail_parts = [f"The target video uses a {style_prefix} style."]
    scene_subject = f"<Subject {scene_pic_idx}>"
    detail_parts.append(
        f"[Shot 1] {camera}. The scene is {scene_subject}. {video_prompt}")

    # 台词/说话动作：智能匹配说话者为对应 <Subject N>
    # v2.3：
    #   dialogue_enabled=True  → 生成 <d> 台词行（H3 对口型+配音）
    #   dialogue_enabled=False → 不写台词文字，只写"说话动作"，让 H3 生成自然口型，
    #                           后期配音贴合（避免张嘴无声/闭口说话）
    if dialogue and dialogue.strip():
        dialogue_clean = dialogue.strip()
        speaker_name = ""
        speaker_index = -1

        # 1. 从 dialogue 提取说话者前缀（格式："名字：台词" 或 "名字:台词"）
        for sep in ["：", ":"]:
            if sep in dialogue_clean:
                parts = dialogue_clean.split(sep, 1)
                candidate = parts[0].strip()
                if 1 <= len(candidate) <= 10 and not any(
                    c in candidate for c in "，。！？、,.!?\"'"
                ):
                    speaker_name = candidate
                    dialogue_clean = parts[1].strip()
                    break

        # 2. 如果 dialogue 无前缀，且当前镜头只有一个角色，直接用该角色
        if not speaker_name:
            shot_chars = shot.get("characters", []) or []
            if isinstance(shot_chars, list) and len(shot_chars) == 1:
                speaker_name = str(shot_chars[0]).strip()

        # 3. 匹配 speaker_name 到 Subject 编号和角色索引
        speaker_subject = ""
        if speaker_name:
            for i, ch in enumerate(characters, 1):
                cname = ch.get("name", "")
                if cname and (
                    cname == speaker_name
                    or speaker_name in cname
                    or cname in speaker_name
                ):
                    speaker_subject = f"<Subject {i}> (S{i}) says: "
                    speaker_index = i - 1
                    break

        # 4. 仍未匹配，在 action 中搜索角色名
        if not speaker_subject:
            for i, ch in enumerate(characters, 1):
                cname = ch.get("name", "")
                if cname and cname in action:
                    speaker_subject = f"<Subject {i}> (S{i}) says: "
                    speaker_index = i - 1
                    break

        # 5. 全部失败，用第一个角色（避免泛化 "Character says:" 标签导致对话被吞）
        if not speaker_subject and characters:
            speaker_subject = "<Subject 1> (S1) says: "
            speaker_index = 0

        if dialogue_enabled:
            # 6. 检测对话语言（优先角色 language 字段，其次粤语特征字检测）
            character_language = None
            if 0 <= speaker_index < len(characters):
                character_language = characters[speaker_index].get("language", "")
            lang = _detect_dialogue_language(dialogue_clean, character_language)
            detail_parts.append(
                f"{speaker_subject}<d>[{lang}] {dialogue_clean}</d>"
            )
        else:
            # 后期配音模式：画面里生成自然的说话口型，但不绑定台词内容
            speaker_tag = speaker_subject.rstrip(": ") or "<Subject 1>"
            detail_parts.append(
                f"{speaker_tag} is speaking with natural expressive mouth movements, "
                f"lips moving as if talking, emotional facial expressions, no visible words."
            )

    detailed_description = "\n".join(detail_parts)

    # ---- 5. overall_soundscape ----
    scene_lower = shot.get("scene", "").lower()
    if any(w in scene_lower for w in ["雨", "rain", "街", "street", "城市", "city"]):
        soundscape = "Urban ambient atmosphere with distant traffic, subtle room tone, and environmental acoustics matching the scene."
    elif any(w in scene_lower for w in ["室内", "室", "room", "家", "house", "公寓", "apartment"]):
        soundscape = "Indoor room tone with subtle ambient acoustics, faint background hum, and natural spatial reverb."
    elif any(w in scene_lower for w in ["自然", "森林", "forest", "山", "mountain", "户外", "outdoor"]):
        soundscape = "Natural outdoor ambience with wind, distant wildlife, and open-air acoustics."
    else:
        soundscape = "Subtle ambient atmosphere matching the scene environment, with natural room tone and spatial acoustics."

    # ---- 6. non_diegetic_music ----
    music = "Understated background score that supports the emotional tone without overpowering the scene."

    six_section = (
        f"subject_definitions:\n{subject_definitions}\n\n"
        f"summary:\n{summary}\n\n"
        f"retention_analysis:\n{retention_analysis}\n\n"
        f"detailed_description:\n{detailed_description}\n\n"
        f"overall_soundscape:\n{soundscape}\n\n"
        f"non_diegetic_music:\n{music}"
    )

    return six_section.strip()


# ============================================================================
# 辅助函数 — 可读剧本生成
# ============================================================================

def _build_screenplay_text(data):
    """从分镜JSON生成可读的中文剧本文本。"""
    lines = []
    lines.append(f"【短剧标题】{data.get('title', '未命名')}")
    lines.append("")
    lines.append(f"【一句话梗概】{data.get('logline', '')}")
    lines.append("")
    lines.append(f"【剧情简介】{data.get('synopsis', '')}")
    lines.append("")
    lines.append("【角色设定】")
    for ch in data.get("characters", []):
        lines.append(f"  - {ch.get('name', '?')}:")
        lines.append(f"    外貌: {ch.get('visual', '')}")
        if ch.get("outfit"):
            lines.append(f"    穿搭: {ch['outfit']}")
    lines.append("")
    # 场景列表（如果有）
    scene_list = data.get("scenes", [])
    if scene_list:
        lines.append("【场景列表】")
        for sc in scene_list:
            lines.append(f"  - {sc.get('scene_name', '?')}: {sc.get('scene_prompt', '')[:100]}...")
        lines.append("")
    lines.append("=" * 50)
    lines.append("【分镜表】")
    lines.append("")

    for shot in data.get("shots", []):
        sn = shot.get("shot_number", "?")
        dur = shot.get("duration", "?")
        lines.append(f"── 镜头 {sn}（{dur}秒）──")
        lines.append(f"  场景: {shot.get('scene', '')}")
        lines.append(f"  动作: {shot.get('action', '')}")
        if shot.get("dialogue"):
            lines.append(f"  台词: {shot['dialogue']}")
        lines.append(f"  镜头: {shot.get('camera', '')}")
        lines.append("")

    return "\n".join(lines)


# ============================================================================
# 辅助函数 — 分辨率/帧数计算（与 H3PromptWriter 保持一致）
# ============================================================================

ASPECT_FACTORS = {
    "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0,
    "21:9": 21 / 9, "4:3": 4 / 3,
}


def _resolve_resolution(aspect_ratio, resolution_mp):
    a = ASPECT_FACTORS.get(aspect_ratio, 16 / 9)
    mp_px = float(resolution_mp) * 1_000_000.0
    w = math.sqrt(mp_px * a)
    h = math.sqrt(mp_px / a)
    w = max(32, int(round(w / 32.0)) * 32)
    h = max(32, int(round(h / 32.0)) * 32)
    return w, h


def _resolve_length(duration_seconds):
    f = max(5, round(float(duration_seconds) * 24))
    f = f + (5 - (f % 17)) % 17
    return int(f)


# ============================================================================
# 节点类 — H3StorySetup v2.0
# ============================================================================

class H3StorySetup:
    """H3 一键短剧创作引擎（普通节点版，彻底解决 io.ComfyNode 的 widgets_values 错位问题）。

    输入一句话创意 → 自动生成剧本、分镜表、图片提示词、H3六段式视频提示词。
    兼容 Local GGUF / HTTP endpoint 两种 LLM 后端。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "concept": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "placeholder": "一句话创意（可中文）。例如：外卖小哥在暴雨夜送餐，发现顾客是十年未见的初恋。",
                }),
                "style": (STYLE_OPTIONS, {"default": "电影感实拍"}),
                "drama_genre": (DRAMA_GENRE_OPTIONS, {"default": "都市情感"}),
                "num_shots": ("INT", {"default": 6, "min": 1, "max": 20, "step": 1}),
                "ref2va_mode": ("BOOLEAN", {"default": True, "label_on": "Ref2VA（需参考图）", "label_off": "纯文生视频"}),
                "backend": (BACKEND_OPTIONS, {"default": "Local GGUF"}),
                "gguf_name": (_GGUF_FILES, {"default": _GGUF_FILES[0] if _GGUF_FILES else ""}),
                "mmproj_name": (_MMPROJ_FILES, {"default": "None"}),
                "n_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 200, "step": 1}),
                "llm_base_url": ("STRING", {"default": "http://127.0.0.1:8080/v1/chat/completions"}),
                "model": ("STRING", {"default": ""}),
                "api_key": ("STRING", {"default": ""}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 999999, "step": 1}),
                "keep_loaded": ("BOOLEAN", {"default": False, "label_on": "保留在显存", "label_off": "跑完即卸载"}),
                "dialogue_mode": ("BOOLEAN", {"default": False, "label_on": "生成台词<d>", "label_off": "纯画面（后期配音）"}),
            },
            "optional": {
                "asset_library": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = (
        "STRING", "STRING", "STRING", "STRING", "STRING", "STRING",
        "STRING", "STRING", "STRING", "STRING", "STRING",
    )
    RETURN_NAMES = (
        "title", "screenplay", "character_prompts", "scene_prompts",
        "video_prompts", "h3_prompts", "asset_library", "script_json",
        "shot_durations", "used_image_paths", "shot_ref_images",
    )
    FUNCTION = "execute"
    CATEGORY = "H3 helper/micxin/AutoDirector"

    def execute(self, **kwargs):
        concept = (kwargs.get("concept", "") or "").strip()
        style = kwargs.get("style", "电影感实拍")
        drama_genre = kwargs.get("drama_genre", "都市情感")
        num_shots = int(kwargs.get("num_shots", 6))
        # v2.2: duration_per_shot/aspect_ratio/resolution_mp 去掉输入口，用默认值
        # 实际渲染参数由 PromptWriter 控制，这里只用于生成提示词文本
        duration_per_shot = 5
        aspect_ratio = "9:16"
        resolution_mp = "0.4"
        ref2va_mode = bool(kwargs.get("ref2va_mode", True))
        # 资产库联动（v2.1 新增）
        asset_library_input = (kwargs.get("asset_library", "") or "").strip()
        backend = kwargs.get("backend", "Local GGUF")
        # Local GGUF 专属
        gguf_name = (kwargs.get("gguf_name", "") or "").strip()
        mmproj_name = (kwargs.get("mmproj_name", "None") or "None").strip()
        n_gpu_layers = int(kwargs.get("n_gpu_layers", -1))
        keep_loaded = bool(kwargs.get("keep_loaded", False))
        # HTTP endpoint 专属
        llm_base_url = (kwargs.get("llm_base_url", "") or "").strip()
        model = (kwargs.get("model", "") or "").strip()
        api_key = (kwargs.get("api_key", "") or "").strip()

        # 共用 LLM 生成参数
        temperature = float(kwargs.get("temperature", 0.7))
        temperature = max(0.0, min(2.0, temperature))
        seed = int(kwargs.get("seed", 0))

        # v2.3: 台词模式（默认 False=后期配音，纯画面无 <d> 台词）
        dialogue_mode = bool(kwargs.get("dialogue_mode", False))

        # ---- 通用：setup_json（向后兼容）----
        width, height = _resolve_resolution(aspect_ratio, resolution_mp)
        total_duration = num_shots * duration_per_shot
        setup = {
            "style": style,
            "drama_genre": drama_genre,
            "num_shots": num_shots,
            "duration_per_shot": duration_per_shot,
            "total_duration_seconds": total_duration,
            "aspect_ratio": aspect_ratio,
            "resolution_mp": str(resolution_mp),
            "width": width,
            "height": height,
        }
        setup_json = json.dumps(setup, ensure_ascii=False)

        # ---- 解析外部资产库（v2.1 新增）----
        ext_characters = []
        ext_scenes = []
        ext_props = []
        if asset_library_input:
            try:
                ext_asset = json.loads(asset_library_input)
                if isinstance(ext_asset, dict):
                    ext_characters = ext_asset.get("characters", []) or []
                    ext_scenes = ext_asset.get("scenes", []) or []
                    ext_props = ext_asset.get("props", []) or []
                    # 兼容扁平格式
                    if not ext_characters and not ext_scenes:
                        for k, v in ext_asset.items():
                            if isinstance(v, dict):
                                if str(k).startswith("S") or "character" in str(k).lower():
                                    ext_characters.append(v)
                                elif "scene" in str(k).lower():
                                    ext_scenes.append(v)
                print(f"[H3StorySetup] 外部资产库: {len(ext_characters)}角色, "
                      f"{len(ext_scenes)}场景, {len(ext_props)}道具.", flush=True)
            except (json.JSONDecodeError, TypeError) as e:
                print(f"[H3StorySetup] 外部资产库解析失败: {e}", flush=True)

        # ---- 一键短剧生成模式（v2.2 起唯一模式，去掉兼容模式）----
        if not concept:
            raise ValueError(
                "H3StorySetup: 创意为空 — 在 concept 输入框中填写一句话创意。")

        # 按 backend 校验必要参数
        if backend == "HTTP endpoint":
            if not llm_base_url:
                raise ValueError("H3StorySetup: llm_base_url 为空（HTTP endpoint 模式）。")
            if not model:
                raise ValueError("H3StorySetup: model 为空（HTTP endpoint 模式）。")
        else:  # Local GGUF
            if not _HAS_LOCAL_GGUF:
                raise ValueError(
                    "H3StorySetup: Local GGUF 不可用（无法从 h3_screenwriter 导入 llama_cpp 函数）。"
                    "请切换到 HTTP endpoint 模式，或确认 ComfyUI-H3-AutoDirector 包完整。")
            if not gguf_name:
                raise ValueError(
                    "H3StorySetup: gguf_name 为空（Local GGUF 模式）。"
                    "请在下拉框中选择一个 .gguf 模型，或切换到 HTTP endpoint。")

        # 构建 user message
        user_brief = (
            f"CREATIVE IDEA (Chinese): {concept}\n"
            f"DRAMA GENRE: {drama_genre}\n"
            f"VISUAL STYLE: {style}\n"
            f"NUMBER OF SHOTS: {num_shots} (generate EXACTLY this many shots)\n"
            f"DURATION PER SHOT: ~{duration_per_shot} seconds (±2s allowed)\n"
            f"ASPECT RATIO: {aspect_ratio} ({width}x{height})\n"
        )

        # v2.1: 注入外部资产库信息（如果有）— v2.5 改为"选择需要的"，不再强制使用全部
        if ext_characters or ext_scenes:
            user_brief += "\n=== AVAILABLE PRESET ASSETS (select only what the story needs, do NOT invent new ones) ===\n"
            if ext_characters:
                user_brief += "AVAILABLE CHARACTER PRESETS — select ONLY the characters that actually appear in the story. You do NOT need to use all of them:\n"
                for i, ch in enumerate(ext_characters, 1):
                    cname = ch.get("name", f"Character {i}")
                    cid = ch.get("id", f"S{i}")
                    cvisual = ch.get("description", ch.get("visual", ""))
                    coutfit = ch.get("outfit", "")
                    user_brief += f"  - {cid} ({cname}): {cvisual}"
                    if coutfit:
                        user_brief += f". Outfit: {coutfit}"
                    user_brief += "\n"
            if ext_scenes:
                user_brief += "AVAILABLE SCENE PRESETS — select ONLY the scenes that actually appear in the story. You do NOT need to use all of them:\n"
                for i, sc in enumerate(ext_scenes, 1):
                    sname = sc.get("name", sc.get("scene_name", f"Scene {i}"))
                    sid = sc.get("id", sc.get("scene_name", f"scene{i}"))
                    sdesc = sc.get("description", sc.get("scene_prompt", ""))
                    user_brief += f"  - {sid} ({sname}): {sdesc}\n"
            user_brief += (
                "IMPORTANT RULES:\n"
                "1. Select characters/scenes ONLY from the presets above. Do NOT invent new characters or scenes.\n"
                "2. You do NOT need to use all presets — only include characters/scenes that actually appear in the story.\n"
                "3. The characters[] array in your JSON output should contain ONLY the characters that appear in the shots.\n"
                "4. The scenes[] array should contain ONLY the scenes that appear in the shots.\n"
                "5. If the creative idea only mentions one character, only include that one character — do NOT add other preset characters.\n"
            )
            user_brief += "=== END PRESET ASSETS ===\n"

        user_brief += (
            f"Now output the complete short-drama package as STRICT JSON following the schema."
        )

        messages = [
            {"role": "system", "content": SHORT_DRAMA_SYSTEM_PROMPT},
            {"role": "user", "content": user_brief},
        ]

        print(f"[H3StorySetup] 调用 LLM 生成短剧... "
              f"(backend={backend}, 创意: {concept[:50]}..., 镜头数: {num_shots}, 温度: {temperature})",
              flush=True)

        t0 = time.monotonic()
        if backend == "Local GGUF":
            llm = _load_local_llm(gguf_name, mmproj_name, n_gpu_layers, _N_CTX)
            raw_response = _call_local_llm(llm, messages, temperature, seed)
            if not keep_loaded:
                _unload_local()
        else:
            raw_response = _call_llm_http(
                llm_base_url, model, api_key, messages,
                temperature=temperature, seed=seed)
        elapsed = time.monotonic() - t0
        print(f"[H3StorySetup] LLM 返回 ({len(raw_response)} chars, 耗时 {elapsed:.1f}s)。",
              flush=True)

        # 解析 JSON
        data = _extract_json(raw_response)

        # 基本校验
        if "shots" not in data or not isinstance(data["shots"], list):
            raise ValueError(
                f"LLM 返回的 JSON 中缺少 shots 数组。可用字段: {list(data.keys())}")

        shots = data["shots"]
        characters = data.get("characters", [])
        title = data.get("title", "未命名短剧")

        # ★ 硬过滤：只保留用户 concept 里提到的角色，杜绝 LLM 自作主张加角色导致参考图爆炸
        characters, shots = _filter_characters_by_concept(
            concept, characters, ext_characters, shots)

        print(f"[H3StorySetup] 解析成功: 标题='{title}', "
              f"角色={len(characters)}个, 镜头={len(shots)}个。", flush=True)

        # ---- 生成各输出 ----

        # 1. 可读剧本
        screenplay = _build_screenplay_text(data)

        # 2. storyboard_json（完整，加上元数据 + scenes + reference_map）
        scene_list = data.get("scenes", [])
        # 构建参考图映射表（H3ModelLoader ref_image 端口 → Picture N → 人物/场景）
        ref_map_lines = [
            "H3ModelLoader 参考图端口映射（按顺序接入 ref_image_0, ref_image_1, ...）:",
        ]
        ref_idx = 0
        for i, ch in enumerate(characters):
            cname = ch.get("name", f"Character {i+1}")
            ref_map_lines.append(
                f"  ref_image_{ref_idx} → Picture {ref_idx+1} → 人物: {cname}（四视图参考图）")
            ref_idx += 1
        for i, sc in enumerate(scene_list):
            sname = sc.get("scene_name", f"Scene {i+1}")
            ref_map_lines.append(
                f"  ref_image_{ref_idx} → Picture {ref_idx+1} → 场景: {sname}（干净背景图）")
            ref_idx += 1
        ref_map_lines.append("")
        ref_map_lines.append("生成顺序：先用 character_prompts 生成人物四视图图，再用 scene_prompts 生成场景干净背景图。")
        ref_map_lines.append("然后按上表顺序接入 H3ModelLoader 的 ref_image_0/1/2/... 端口。")
        reference_map = "\n".join(ref_map_lines)

        storyboard_full = {
            "title": title,
            "logline": data.get("logline", ""),
            "synopsis": data.get("synopsis", ""),
            "characters": characters,
            "scenes": scene_list,
            "reference_map": reference_map,
            "style": style,
            "genre": drama_genre,
            "aspect_ratio": aspect_ratio,
            "resolution": f"{width}x{height}",
            "total_shots": len(shots),
            "total_duration_seconds": sum(s.get("duration", duration_per_shot) for s in shots),
            "shots": shots,
        }
        storyboard_json = json.dumps(storyboard_full, ensure_ascii=False, indent=2)

        # 3. character_prompts（人物四视图参考图，JSON数组）
        character_prompt_list = [
            ch.get("character_prompt", "")
            for ch in characters if ch.get("character_prompt")
        ]
        character_prompts = json.dumps(character_prompt_list, ensure_ascii=False, indent=2)

        # 4. scene_prompts（场景干净背景图，JSON数组）
        scene_prompt_list = [
            sc.get("scene_prompt", "")
            for sc in scene_list if sc.get("scene_prompt")
        ]
        scene_prompts = json.dumps(scene_prompt_list, ensure_ascii=False, indent=2)

        # 5. video_prompts（简洁版，JSON数组）
        video_prompt_list = [s.get("video_prompt", "") for s in shots if s.get("video_prompt")]
        video_prompts = json.dumps(video_prompt_list, ensure_ascii=False, indent=2)
        # 纯文本格式：每行一个 prompt，不带 JSON 符号，直接喂 easy promptLine / H3PromptWriter
        video_prompts_text = "\n".join(video_prompt_list)

        # 6. h3_prompts（H3六段式，JSON数组）
        #    ref2va_mode=True 时含 <Picture N> 标签（需参考图）；False 时纯文本描述
        h3_prompt_list = []
        for i, shot in enumerate(shots):
            h3 = _wrap_h3_six_section(
                shot, characters, scene_list, style, aspect_ratio,
                shot_index=i + 1, total_shots=len(shots),
                use_picture_tags=ref2va_mode,
                dialogue_enabled=dialogue_mode)
            h3_prompt_list.append(h3)
        h3_prompts = json.dumps(h3_prompt_list, ensure_ascii=False, indent=2)

        # 7. prompt_list（纯文本，===SHOT_BREAK=== 分隔的六段式）
        prompt_list = "\n===SHOT_BREAK===\n".join(h3_prompt_list)

        # === v2.1: 资产库联动输出 ===
        # 8. asset_library（把 LLM 生成的 characters/scenes 转成资产库 JSON 格式）
        #    可直接接 H3 PromptWriter (micxin) 的 asset_library 输入口，或 H3 Asset Library 节点
        asset_lib_characters = []
        for i, ch in enumerate(characters, 1):
            cid = ch.get("id", f"S{i}")
            cname = ch.get("name", f"Character {i}")
            cvisual = ch.get("visual", "")
            coutfit = ch.get("outfit", "")
            cdesc = cvisual
            if coutfit:
                cdesc = f"{cvisual}. Wearing {coutfit}" if cvisual else coutfit
            asset_lib_characters.append({
                "id": cid,
                "name": cname,
                "description": cdesc,
                "image": "",  # 留空，用户生成参考图后填入
                "character_prompt": ch.get("character_prompt", ""),
            })
        asset_lib_scenes = []
        for i, sc in enumerate(scene_list, 1):
            sid = sc.get("id", sc.get("scene_name", f"scene{i}"))
            sname = sc.get("name", sc.get("scene_name", f"Scene {i}"))
            sdesc = sc.get("description", sc.get("scene_prompt", ""))
            asset_lib_scenes.append({
                "id": sid,
                "name": sname,
                "description": sdesc,
                "image": "",
                "scene_prompt": sc.get("scene_prompt", ""),
            })
        asset_library_output = json.dumps({
            "characters": asset_lib_characters,
            "scenes": asset_lib_scenes,
            "props": [],
        }, ensure_ascii=False, indent=2)

        # 9. script_json（与 H3 PromptWriter 剧本模式兼容的格式）
        #    可直接接 PromptWriter 的 bypass 模式粘贴，或下游解析使用
        script_shots = []
        for i, shot in enumerate(shots, 1):
            shot_dur = int(shot.get("duration", duration_per_shot))
            shot_chars = []
            # 尝试从 action/scene 中匹配角色名
            for j, ch in enumerate(characters, 1):
                cname = ch.get("name", "")
                if cname and (cname in shot.get("action", "") or cname in shot.get("scene", "")):
                    shot_chars.append(ch.get("id", f"S{j}"))
            if not shot_chars and characters:
                shot_chars = [characters[0].get("id", "S1")]
            scene_ref = shot.get("scene_ref", "")
            if not scene_ref and scene_list:
                scene_ref = scene_list[0].get("scene_name", "scene1")
            script_shots.append({
                "shot": i,
                "scene": scene_ref,
                "characters": shot_chars,
                "duration": shot_dur,
                "prompt": h3_prompt_list[i-1] if i-1 < len(h3_prompt_list) else "",
            })
        script_json_output = json.dumps({
            "characters": asset_lib_characters,
            "scenes": asset_lib_scenes,
            "shots": script_shots,
        }, ensure_ascii=False, indent=2)

        # 10. shot_durations（每镜帧数列表，JSON数组）
        shot_frames_list = []
        for shot in shots:
            dur_sec = int(shot.get("duration", duration_per_shot))
            f = max(5, round(float(dur_sec) * 24))
            f = f + (5 - (f % 17)) % 17
            shot_frames_list.append(int(f))
        shot_durations_output = json.dumps(shot_frames_list, ensure_ascii=False)

        # 11. used_image_paths（v2.4）：本次剧本用到的角色+场景图路径，供 AIO Ref2VA 参考。
        #     从外部资产库（ext_characters/ext_scenes）按 name/id 匹配 LLM 实际用到的角色/场景，
        #     只输出匹配到的图，不含道具、不含无关角色——避免整库 30+ 张图全灌进 AIO。
        def _match_asset_img(name, id_, items, name_keys, id_keys=("id",)):
            for it in items:
                for nk in name_keys:
                    nv = it.get(nk)
                    if nv and name and (str(nv) == str(name) or str(name) in str(nv) or str(nv) in str(name)):
                        return it.get("image", "")
                for ik in id_keys:
                    iv = it.get(ik)
                    if iv and id_ and str(iv) == str(id_):
                        return it.get("image", "")
            return ""
        used_imgs = []
        for ch in characters:
            img = _match_asset_img(ch.get("name", ""), ch.get("id", ""),
                                   ext_characters, name_keys=("name",))
            if img and img not in used_imgs:
                used_imgs.append(img)
        for sc in scene_list:
            img = _match_asset_img(
                sc.get("scene_name", "") or sc.get("name", ""), sc.get("id", ""),
                ext_scenes, name_keys=("name", "scene_name"))
            if img and img not in used_imgs:
                used_imgs.append(img)
        used_image_paths = "\n".join(used_imgs)
        if used_imgs:
            print(f"[H3StorySetup] 本次用到的参考图 {len(used_imgs)} 张: "
                  f"{[os.path.basename(u) for u in used_imgs]}", flush=True)
        else:
            print("[H3StorySetup] 未匹配到资产库参考图（LLM 可能发明了新角色/场景，"
                  "或资产库未配置 image）。AIO 将退回内部 tab 的图。", flush=True)

        # 12. shot_ref_images（v2.4）：每镜参考图清单（JSON 数组，每镜=[该镜角色图..., 该镜场景图]）。
        #     按分镜 JSON 的 characters / scene_ref 从资产库自动匹配，实现"每镜只用该镜的图"，
        #     避免整批全库图灌给 AIO。
        shot_ref_images = []
        for shot in shots:
            refs = []
            shot_char_names = shot.get("characters") or []
            if not shot_char_names:
                for ch in characters:
                    cname = ch.get("name", "")
                    if cname and (cname in shot.get("action", "") or cname in shot.get("scene", "")):
                        shot_char_names.append(cname)
            for nm in shot_char_names:
                img = _match_asset_img(nm, "", ext_characters, name_keys=("name",))
                if img and img not in refs:
                    refs.append(img)
            sc_ref = shot.get("scene_ref", "")
            if not sc_ref and scene_list:
                sc_ref = scene_list[0].get("scene_name", "")
            img = _match_asset_img(sc_ref, "", ext_scenes, name_keys=("name", "scene_name"))
            if img and img not in refs:
                refs.append(img)
            if not refs:
                refs = list(used_imgs)  # 兜底：该镜匹配不到，退回全剧用到的图
            shot_ref_images.append(refs)
        shot_ref_images_output = json.dumps(shot_ref_images, ensure_ascii=False)

        print(f"[H3StorySetup] 生成完成: "
              f"characters={len(character_prompt_list)}, "
              f"scenes={len(scene_prompt_list)}, "
              f"ref_images={len(character_prompt_list)+len(scene_prompt_list)}, "
              f"video_prompts={len(video_prompt_list)}, "
              f"h3_prompts={len(h3_prompt_list)}, "
              f"asset_library={'yes' if asset_library_output else 'no'}, "
              f"script_json={'yes' if script_json_output else 'no'}。", flush=True)

        return (
            title,                 # 0 title
            screenplay,            # 1 screenplay
            character_prompts,     # 2 character_prompts
            scene_prompts,         # 3 scene_prompts
            video_prompts,         # 4 video_prompts
            h3_prompts,            # 5 h3_prompts
            asset_library_output,  # 6 asset_library
            script_json_output,    # 7 script_json
            shot_durations_output, # 8 shot_durations
            used_image_paths,      # 9 used_image_paths
            shot_ref_images_output,# 10 shot_ref_images
        )


# ============================================================================
# H3PromptSplit：把 JSON 数组 prompt 拆成独立端口，替代 MieLoop 循环
# ============================================================================

class H3PromptSplit(io.ComfyNode):
    """把 JSON 数组格式的 prompt 列表拆分成最多 8 个独立 STRING 输出。

    用于替代 MieLoop 循环节点：手动展开 N 个渲染链，每个链接一个
    prompt 输出端口。不依赖任何第三方循环节点，每个镜头独立渲染，
    完全可控，避免循环节点按换行拆分六段式 prompt 导致的错乱。
    """

    MAX_PROMPTS = 8

    @classmethod
    def define_schema(cls):
        outputs = [
            io.String.Output(id=f"prompt_{i}", display_name=f"prompt_{i}")
            for i in range(cls.MAX_PROMPTS)
        ]
        outputs.append(io.Int.Output(id="count", display_name="count"))

        return io.Schema(
            node_id="H3PromptSplit",
            display_name="H3 Prompt Split (micxin)",
            category="H3 helper/micxin/AutoDirector",
            description=(
                "把 JSON 数组格式的 prompt 列表拆分成最多 8 个独立 STRING "
                "输出，用于手动展开多镜头渲染链，替代 MieLoop 循环。"
            ),
            inputs=[
                io.String.Input(
                    "prompts_json",
                    multiline=True,
                    default="[]",
                    placeholder='["prompt 1", "prompt 2", ...]',
                    tooltip=(
                        "JSON 数组格式的 prompt 列表，来自 H3StorySetup 的 "
                        "h3_prompts 输出。"
                    ),
                ),
            ],
            outputs=outputs,
        )

    @classmethod
    def execute(cls, **kwargs):
        prompts_json = kwargs.get("prompts_json", "[]") or "[]"
        try:
            prompts = json.loads(prompts_json)
            if not isinstance(prompts, list):
                prompts = []
        except (json.JSONDecodeError, TypeError):
            prompts = []

        count = len(prompts)
        outputs = []
        for i in range(cls.MAX_PROMPTS):
            if i < len(prompts):
                outputs.append(str(prompts[i]))
            else:
                outputs.append("")
        outputs.append(count)

        print(
            f"[H3PromptSplit] 拆分完成: {count} 个 prompt "
            f"(最多支持 {cls.MAX_PROMPTS} 个)。",
            flush=True,
        )
        return io.NodeOutput(*outputs)


# ============================================================================
# H3ShotQueue：逐镜自动队列（每次 Queue 自动下一镜，尾帧自动接续任意帧）
# ============================================================================

class H3ShotQueue(io.ComfyNode):
    """逐镜自动队列：每次 Queue 自动输出下一镜 H3 六段式提示词。

    输入 H3StorySetup 的 h3_prompts（JSON 数组），内部用状态文件记住
    当前镜次，每次执行自动 +1 取下一镜。连续 Queue N 次自动跑完 N 镜，
    不需要手动改 index，也不用复制提示词。

    - prompts 内容变更 / reset=True / start_shot 变化 → 自动回到 start_shot 重排。
    - frame_dir 填上一镜尾帧输出目录时，自动取该目录最新媒体生成
      keyframe 行（任意帧接续），接到 H3ModelLoader.keyframe_paths
      即可实现画面接续。
    """

    STATE_FILE = "h3_shot_queue_state.json"

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ShotQueue",
            display_name="H3 Shot Queue (micxin)",
            category="H3 helper/micxin/AutoDirector",
            description=(
                "逐镜自动队列：每次 Queue 自动输出下一镜 H3 提示词，"
                "连续跑 N 次自动出 N 镜，尾帧自动接续任意帧。"
            ),
            inputs=[
                io.String.Input(
                    "prompts_json",
                    multiline=True,
                    default="[]",
                    placeholder='["prompt 1", "prompt 2", ...]',
                    tooltip=(
                        "JSON 数组格式的 H3 六段式 prompt 列表，来自 "
                        "H3StorySetup 的 h3_prompts 输出。"
                    ),
                ),
                io.Int.Input(
                    "start_shot",
                    default=1,
                    min=1,
                    max=100,
                    step=1,
                    tooltip="从第几镜开始（1-based）。改动它会让队列回到该镜重新排队。",
                ),
                io.Boolean.Input(
                    "reset",
                    default=False,
                    label_on="从头重新排",
                    label_off="继续下一镜",
                    tooltip=(
                        "勾选并 Queue = 回到 start_shot 重新开始；"
                        "取消勾选 = 自动继续下一镜。"
                    ),
                ),
                io.String.Input(
                    "frame_dir",
                    default="",
                    tooltip=(
                        "上一镜尾帧输出目录（可选）。自动取该目录最新图片/视频 "
                        "生成任意帧接续行，接到 H3ModelLoader.keyframe_paths "
                        "即可画面接续。留空则无接续。"
                    ),
                ),
                io.Int.Input(
                    "width",
                    default=1024,
                    min=256,
                    max=2048,
                    step=16,
                    tooltip="视频宽度（接管 H3PromptWriter 的参数输出，屏蔽它也能跑）。",
                ),
                io.Int.Input(
                    "height",
                    default=576,
                    min=256,
                    max=2048,
                    step=16,
                    tooltip="视频高度（接管 H3PromptWriter 的参数输出）。",
                ),
                io.Int.Input(
                    "duration_seconds",
                    default=5,
                    min=2,
                    max=15,
                    step=1,
                    tooltip=(
                        "每镜秒数（v2.4 起为帧数唯一来源，length 输入已移除）。"
                        "帧数 = 秒×24 并对齐 H3 的 17k+5 网格：5秒→124帧、10秒→243帧。"
                        "你机器 1024×576 建议 5 秒（124帧），10 秒（243帧）会爆显存。"
                    ),
                ),
                io.String.Input(
                    "shot_ref_images_json",
                    optional=True,
                    force_input=True,
                    tooltip=(
                        "每镜参考图清单（JSON 数组，每镜=[角色图路径, 场景图路径, ...]），"
                        "来自 H3StorySetup 的 shot_ref_images 输出。连线后，本镜输出对应的 "
                        "ref_image_line，接到 H3 R2VA AIO 的 ref_image_paths 实现每镜精确用图。"
                    ),
                ),
            ],
            outputs=[
                io.String.Output(id="current_prompt", display_name="当前镜提示词"),
                io.Int.Output(id="shot_index", display_name="当前镜号(1-based)"),
                io.Int.Output(id="total_shots", display_name="总镜数"),
                io.String.Output(id="progress", display_name="进度"),
                io.Boolean.Output(id="is_first", display_name="是否第一镜"),
                io.Boolean.Output(id="is_last", display_name="是否最后一镜"),
                io.String.Output(id="keyframe_line", display_name="尾帧任意帧行"),
                io.String.Output(id="next_prompt", display_name="下一镜提示词"),
                io.Int.Output(id="width", display_name="宽度"),
                io.Int.Output(id="height", display_name="高度"),
                io.Int.Output(id="length", display_name="帧数"),
                io.String.Output(id="ref_image_line", display_name="本镜参考图"),
            ],
        )

    @classmethod
    def _state_path(cls):
        try:
            import folder_paths
            d = folder_paths.get_temp_directory()
        except Exception:
            d = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(d, cls.STATE_FILE)

    @classmethod
    def _load_state(cls):
        try:
            with open(cls._state_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def _save_state(cls, state):
        try:
            with open(cls._state_path(), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @classmethod
    def _latest_frame(cls, frame_dir):
        """扫描目录取最新媒体文件，返回绝对路径或 None。"""
        if not frame_dir or not os.path.isdir(frame_dir):
            return None
        IMG = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        VID = (".mp4", ".webm", ".mov", ".mkv", ".avi")
        best, best_t = None, -1.0
        try:
            for name in os.listdir(frame_dir):
                p = os.path.join(frame_dir, name)
                if not os.path.isfile(p):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in IMG and ext not in VID:
                    continue
                t = os.path.getmtime(p)
                if t > best_t:
                    best, best_t = p, t
        except Exception:
            return None
        return best

    @classmethod
    def execute(cls, **kwargs):
        prompts_json = kwargs.get("prompts_json", "[]") or "[]"
        start_shot = int(kwargs.get("start_shot", 1) or 1)
        reset = bool(kwargs.get("reset", False))
        frame_dir = (kwargs.get("frame_dir", "") or "").strip()
        width = int(kwargs.get("width", 1024) or 1024)
        height = int(kwargs.get("height", 576) or 576)
        duration_seconds = int(kwargs.get("duration_seconds", 5) or 5)
        # v2.4：帧数完全由秒数决定（length 输入已移除），不再有双来源冲突
        length = _resolve_length(duration_seconds) if duration_seconds > 0 else 124
        # v2.4：每镜参考图清单（可选，force_input socket）
        shot_ref_images_json = kwargs.get("shot_ref_images_json", None)
        try:
            shot_ref_list = json.loads(shot_ref_images_json) if shot_ref_images_json else []
            if not isinstance(shot_ref_list, list):
                shot_ref_list = []
        except (json.JSONDecodeError, TypeError):
            shot_ref_list = []

        try:
            prompts = json.loads(prompts_json)
            if not isinstance(prompts, list):
                prompts = []
        except (json.JSONDecodeError, TypeError):
            prompts = []
        prompts = [str(p) for p in prompts]
        total = len(prompts)

        if total == 0:
            print("[H3ShotQueue] 提示词列表为空，请先运行 H3StorySetup。", flush=True)
            return io.NodeOutput("", 0, 0, "0/0", True, True, "", "")

        prompts_hash = hashlib.md5(
            "\n".join(prompts).encode("utf-8")
        ).hexdigest()

        state = cls._load_state()
        need_reset = (
            reset
            or state.get("prompts_hash") != prompts_hash
            or state.get("start_shot") != start_shot
        )

        if need_reset:
            shot_index = start_shot
            print(
                f"[H3ShotQueue] 重新排队（{('reset' if reset else 'hash/start_shot变化')}）："
                f"从第 {start_shot} 镜开始。",
                flush=True,
            )
        else:
            shot_index = int(state.get("shot_index", start_shot))

        # 越界回绕到 start_shot（跑完一轮自动从头）
        if shot_index > total:
            shot_index = start_shot

        idx = max(0, shot_index - 1)
        current = prompts[idx]
        next_prompt = prompts[idx + 1] if idx + 1 < total else ""

        # 写回状态：下一镜
        state["prompts_hash"] = prompts_hash
        state["start_shot"] = start_shot
        state["shot_index"] = shot_index + 1
        cls._save_state(state)

        # 尾帧接续（任意帧行：媒体|音频|帧索引|媒体起|媒体止|音频起|音频止）
        keyframe_line = ""
        latest = cls._latest_frame(frame_dir)
        if latest:
            keyframe_line = f"{latest}||0|0|0|0|0"

        is_first = (shot_index == start_shot)
        is_last = (shot_index == total)

        # v2.4: 当前镜参考图（从 shot_ref_images 清单取本镜的图，换行分隔）
        ref_image_line = ""
        if shot_ref_list:
            ri = max(0, idx) if idx < len(shot_ref_list) else -1
            if 0 <= ri < len(shot_ref_list) and shot_ref_list[ri]:
                ref_image_line = "\n".join(str(x) for x in shot_ref_list[ri])

        print(
            f"[H3ShotQueue] 第 {shot_index}/{total} 镜 | "
            f"尾帧 {'有' if keyframe_line else '无'} | "
            f"参考图 {len(ref_image_line.splitlines()) if ref_image_line else 0} 张 | "
            f"下一镜 {'有' if next_prompt else '无'}",
            flush=True,
        )
        return io.NodeOutput(
            current, shot_index, total,
            f"{shot_index}/{total}", is_first, is_last, keyframe_line, next_prompt,
            width, height, length, ref_image_line,
        )


# ============================================================================
# H3ShotSelector：从 JSON 数组 prompt 列表中按索引选一段
# ============================================================================

class H3ShotSelector(io.ComfyNode):
    """从 JSON 数组格式的 prompt 列表中按索引选择一段输出。

    用于单镜头逐次渲染工作流：H3StorySetup 输出 h3_prompts（JSON 数组），
    本节点按 index 选其中一段喂给 H3ModelLoader。每次改 index 运行一次，
    出一个镜头，N 次出 N 个镜头，后期剪映拼接。

    与 FL_PromptSelectorBasic 的区别：
      - FL_PromptSelectorBasic 按换行符分割，无法处理内部含换行的六段式 prompt
      - 本节点解析 JSON 数组，每个元素是完整的六段式 prompt（内部换行保留）
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ShotSelector",
            display_name="H3 Shot Selector (micxin)",
            category="H3 helper/micxin/AutoDirector",
            description=(
                "从 JSON 数组格式的 H3 六段式 prompt 列表中按索引选一段输出。"
                "用于单镜头逐次渲染，改 index 切换镜头。"
            ),
            inputs=[
                io.String.Input(
                    "prompts_json",
                    multiline=True,
                    default="[]",
                    placeholder='["prompt 1", "prompt 2", ...]',
                    tooltip=(
                        "JSON 数组格式的 prompt 列表，来自 H3StorySetup 的 "
                        "h3_prompts 输出。每个元素是完整的 H3 六段式 prompt。"
                    ),
                ),
                io.Int.Input(
                    "index",
                    default=0,
                    min=0,
                    max=9999,
                    step=1,
                    tooltip="选择第几段 prompt（从0开始）。超出范围时自动取模循环。",
                ),
            ],
            outputs=[
                io.String.Output(id="selected", display_name="selected"),
                io.Int.Output(id="count", display_name="count"),
            ],
        )

    @classmethod
    def execute(cls, **kwargs):
        prompts_json = kwargs.get("prompts_json", "[]") or "[]"
        index = int(kwargs.get("index", 0))

        try:
            prompts = json.loads(prompts_json)
            if not isinstance(prompts, list):
                prompts = []
        except (json.JSONDecodeError, TypeError):
            prompts = []

        count = len(prompts)
        if count == 0:
            selected = ""
        else:
            selected = str(prompts[index % count])

        print(
            f"[H3ShotSelector] 共 {count} 段，选中第 {index % count if count else 0} 段 "
            f"(index={index})。",
            flush=True,
        )
        return io.NodeOutput(selected, count)


# ============================================================================
# H3BatchRender：批量渲染节点（内部循环 H3 编码+采样+解码）
# ============================================================================

class H3BatchRender(io.ComfyNode):
    """批量渲染 H3 视频：输入 JSON 数组格式的 prompt 列表，内部循环
    调用 MiniMaxH3ReferenceToVideo 编码 + 采样器 + VAE 解码，输出
    合并后的所有镜头帧。

    替代 MieLoop / H3MultishotSampler 等第三方循环节点，完全可控，
    不依赖循环节点的字符串拆分逻辑，六段式 prompt 不会被拆碎。

    不生成音频（H3 音频 VAE 有缺陷，后期配音），仅输出 IMAGE。
    """

    @classmethod
    def define_schema(cls):
        try:
            import comfy.samplers
            sampler_names = list(comfy.samplers.KSampler.SAMPLERS)
            scheduler_names = list(comfy.samplers.KSampler.SCHEDULERS)
        except Exception:
            sampler_names = ["euler", "euler_ancestral", "heun", "dpmpp_2m",
                             "dpmpp_2m_sde", "lcm", "ddim", "uni_pc", "er_sde"]
            scheduler_names = ["normal", "karras", "exponential", "sgm_uniform",
                               "simple", "ddim_uniform", "beta"]

        return io.Schema(
            node_id="H3BatchRender",
            display_name="H3 Batch Render (micxin)",
            category="H3 helper/micxin/AutoDirector",
            description=(
                "批量渲染 H3 视频：输入 JSON 数组 prompt，内部循环编码+采样+"
                "解码，输出合并帧。替代 MieLoop，六段式 prompt 不会被拆碎。"
            ),
            inputs=[
                io.String.Input(
                    "prompts_json",
                    multiline=True,
                    default="[]",
                    placeholder='["prompt 1", "prompt 2", ...]',
                    tooltip="JSON 数组格式的 H3 六段式 prompt 列表，来自 H3StorySetup 的 h3_prompts。",
                ),
                io.Model.Input("model", extra_dict={"forceInput": True},
                               tooltip="H3 模型（从 H3ModelLoader 输出）。"),
                io.Clip.Input("clip", extra_dict={"forceInput": True},
                              tooltip="H3 CLIP（从 H3ModelLoader 输出）。"),
                io.Vae.Input("vae", extra_dict={"forceInput": True},
                             tooltip="H3 视频 VAE（从 H3ModelLoader 输出）。"),
                io.Int.Input("width", default=480, min=32, max=4096, step=32,
                             tooltip="视频宽度。"),
                io.Int.Input("height", default=832, min=32, max=4096, step=32,
                             tooltip="视频高度。"),
                io.Int.Input("length", default=121, min=5, max=481, step=17,
                             tooltip="每镜头帧数（121≈5s@24fps, 243≈10s）。"),
                io.Int.Input("steps", default=8, min=1, max=50,
                             tooltip="采样步数（turbo LoRA 推荐 8 步）。"),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff,
                             tooltip="随机种子，每个镜头自动递增。"),
                io.Combo.Input("sampler_name", options=sampler_names,
                               default="er_sde", tooltip="采样器。"),
                io.Combo.Input("scheduler", options=scheduler_names,
                               default="sgm_uniform", tooltip="调度器。"),
                io.Float.Input("denoise", default=1.0, min=0.0, max=1.0,
                                tooltip="降噪强度（1.0=全量生成）。"),
            ],
            outputs=[
                io.Image.Output(display_name="IMAGE",
                                tooltip="合并后的所有镜头帧，直接接 VHS_VideoCombine。"),
            ],
        )

    @classmethod
    def execute(cls, **kwargs):
        import torch
        import nodes as comfy_nodes

        prompts_json = kwargs.get("prompts_json", "[]") or "[]"
        model = kwargs["model"]
        clip = kwargs["clip"]
        vae = kwargs["vae"]
        width = kwargs.get("width", 480)
        height = kwargs.get("height", 832)
        length = kwargs.get("length", 121)
        steps = kwargs.get("steps", 8)
        seed = kwargs.get("seed", 0)
        sampler_name = kwargs.get("sampler_name", "er_sde")
        scheduler = kwargs.get("scheduler", "sgm_uniform")
        denoise = kwargs.get("denoise", 1.0)

        # 解析 prompt 列表
        try:
            prompts = json.loads(prompts_json)
            if not isinstance(prompts, list):
                prompts = []
        except (json.JSONDecodeError, TypeError):
            prompts = []

        if not prompts:
            raise ValueError("H3BatchRender: prompts_json 为空或不是有效 JSON 数组。")

        print(f"[H3BatchRender] 开始批量渲染: {len(prompts)} 个镜头, "
              f"{width}x{height}, {length}帧/镜头, {steps}步, "
              f"sampler={sampler_name}, scheduler={scheduler}", flush=True)

        # 获取核心节点类
        ref2va_cls = comfy_nodes.NODE_CLASS_MAPPINGS.get("MiniMaxH3ReferenceToVideo")
        if ref2va_cls is None:
            raise RuntimeError(
                "H3BatchRender: 找不到 MiniMaxH3ReferenceToVideo 节点。"
                "请确认 ComfyUI-H3-helper 已安装。")

        ksampler_select_cls = comfy_nodes.NODE_CLASS_MAPPINGS["KSamplerSelect"]
        scheduler_cls = comfy_nodes.NODE_CLASS_MAPPINGS["BasicScheduler"]
        guider_cls = comfy_nodes.NODE_CLASS_MAPPINGS["BasicGuider"]
        noise_cls = comfy_nodes.NODE_CLASS_MAPPINGS["RandomNoise"]
        sampler_cls = comfy_nodes.NODE_CLASS_MAPPINGS["SamplerCustomAdvanced"]

        # 预创建采样器和调度器实例（不依赖镜头）
        sampler_obj = ksampler_select_cls().get_sampler(sampler_name)
        sigmas_all = scheduler_cls().get_sigmas(model, scheduler, steps, denoise)

        all_frames = []

        for idx, prompt in enumerate(prompts):
            shot_seed = seed + idx
            print(f"[H3BatchRender] 镜头 {idx+1}/{len(prompts)} "
                  f"(seed={shot_seed}) 编码中...", flush=True)

            t0 = time.monotonic()

            # 1. 编码 prompt → conditioning + latent
            ref_out = ref2va_cls.execute(
                clip=clip,
                vae=vae,
                audio_vae=None,
                prompt=str(prompt),
                width=width,
                height=height,
                length=length,
                ref_image_size="match",
                ref_images=[],
                ref_videos=[],
                ref_video_audios=[],
                ref_audios=[],
            )
            positive = ref_out[0]
            latent = ref_out[1]

            # 2. 创建 guider
            guider_obj = guider_cls().get_guider(model, positive)

            # 3. 创建 noise
            noise_obj = noise_cls().get_noise(shot_seed)

            # 4. 采样
            print(f"[H3BatchRender] 镜头 {idx+1}/{len(prompts)} 采样中...", flush=True)
            sample_out = sampler_cls().sample(
                noise_obj, guider_obj, sampler_obj, sigmas_all, latent)
            samples = sample_out[0]  # LATENT

            # 5. VAE 解码
            print(f"[H3BatchRender] 镜头 {idx+1}/{len(prompts)} 解码中...", flush=True)
            frames = vae.decode(samples["samples"])

            elapsed = time.monotonic() - t0
            print(f"[H3BatchRender] 镜头 {idx+1}/{len(prompts)} 完成 "
                  f"({frames.shape[0]}帧, 耗时 {elapsed:.1f}s)", flush=True)

            all_frames.append(frames)

            # 释放中间张量
            del positive, latent, guider_obj, noise_obj, samples, frames
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        # 合并所有镜头帧（batch 维度）
        print(f"[H3BatchRender] 合并 {len(all_frames)} 个镜头...", flush=True)
        merged = torch.cat(all_frames, dim=0)
        print(f"[H3BatchRender] 完成! 总帧数: {merged.shape[0]}, "
              f"分辨率: {merged.shape[2]}x{merged.shape[1]}", flush=True)

        return io.NodeOutput(merged)


# ============================================================================
# H3VideoSource：重跑组专用视频源（从已保存文件读取，不依赖主采样实时链路）
# ============================================================================
class H3VideoSource:
    """扫描目录取最新视频文件，读成 IMAGE + AUDIO 供 H3Retake 段级重跑取料。

    原来 H3Retake 的 images/audio 直接接在 VAEDecode/VAEDecodeAudio 上，
    导致一打开重跑组，主采样（SamplerCustomAdvanced）就整条重新跑一遍
    （等于重跑两次）。本节点改为从 output/video 读取刚保存的成片，
    H3Retake 只依赖：读文件（快）+ model/vae/sampler/sigmas（内存中），
    主采样链不再被重跑组引用，从而不会重新采样。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "video_dir": ("STRING", {
                "default": "video",
                "multiline": False,
                "tooltip": (
                    "扫描该目录取最新视频。相对 output 目录（如主视频默认在 "
                    "output/video，填 video 即可），或填绝对路径。"
                    "重跑对象 = 最近一次跑完保存的那镜成片。"
                ),
            }),
            "filename": ("STRING", {
                "default": "",
                "multiline": False,
                "tooltip": (
                    "可选。留空 = 自动取目录最新视频；"
                    "填文件名（如 H3_keyframe_00005-audio.mp4）则锁定该文件。"
                ),
            }),
        }}

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("IMAGE", "AUDIO", "video_path")
    FUNCTION = "get"
    CATEGORY = "H3-AutoDirector/micxin"

    def get(self, video_dir, filename=""):
        if not os.path.isabs(video_dir):
            import folder_paths
            video_dir = os.path.join(folder_paths.get_output_directory(), video_dir.strip())
        video_dir = video_dir.strip().strip('"')
        if not os.path.isdir(video_dir):
            raise RuntimeError(f"[H3VideoSource] 目录不存在: {video_dir}")
        vids = [f for f in os.listdir(video_dir)
                if f.lower().endswith((".mp4", ".webm", ".mov", ".mkv", ".gif"))
                and os.path.isfile(os.path.join(video_dir, f))]
        if not vids:
            raise RuntimeError(f"[H3VideoSource] 目录无视频文件: {video_dir}")
        if filename and filename.strip():
            fn = filename.strip().strip('"')
            if fn not in vids:
                raise RuntimeError(f"[H3VideoSource] 指定的 {fn} 不在目录 {video_dir}")
            latest = fn
        else:
            latest = max(vids, key=lambda f: os.path.getmtime(os.path.join(video_dir, f)))
        path = os.path.join(video_dir, latest)
        try:
            from videohelpersuite.load_video_nodes import load_video
        except Exception as e:
            raise RuntimeError(f"[H3VideoSource] 无法加载 VHS load_video: {e}")
        images, fc, audio, _info = load_video(
            video=path, force_rate=0, custom_width=0, custom_height=0,
            frame_load_cap=0, skip_first_frames=0, select_every_nth=1, format="None")
        print(f"[H3VideoSource] 读取 {path} ({fc} 帧)", flush=True)
        return (images, audio, path)


# ============================================================================
# 节点注册
# ============================================================================

NODE_CLASS_MAPPINGS = {
    "H3StorySetup": H3StorySetup,
    "H3PromptSplit": H3PromptSplit,
    "H3ShotSelector": H3ShotSelector,
    "H3ShotQueue": H3ShotQueue,
    "H3VideoSource": H3VideoSource,
    # H3BatchRender 已废弃（持续报错，改用 H3ShotSelector 单镜头逐次渲染）
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3StorySetup": "H3 Story Setup (micxin)",
    "H3PromptSplit": "H3 Prompt Split (micxin)",
    "H3ShotSelector": "H3 Shot Selector (micxin)",
    "H3ShotQueue": "H3 Shot Queue (micxin)",
    "H3VideoSource": "H3 Video Source (micxin)",
}