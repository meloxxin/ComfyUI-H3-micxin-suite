# -*- coding: utf-8 -*-
"""H3 LLM/工具共享模块（从已弃用的 h3_screenwriter.py 抽取，2026-10-02）。

H3 PromptWriter (micxin) 与 H3 Prompt Translate (micxin) 节点已弃用删除；
H3 Prompt Split+Translate (micxin) 继续依赖本模块提供：
  - 本地 GGUF 加载/调用/卸载（llama-cpp-python，含 16G 显存降级重试）
  - OpenAI 兼容 HTTP 端点调用（api_key / Bearer，云端 API 或本地 llama.cpp server）
  - 参考图过图（ComfyUI 张量 → base64 data URI；路径 → 动态分辨率张量）
  - fullreference 系统提示词与视觉风格契约（含 skills/ 自定义技能目录）
"""
import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from PIL import Image

try:  # available inside ComfyUI
    import folder_paths
    from folder_paths import get_input_directory
except Exception:  # fallback for standalone smoke-test
    folder_paths = None

    def get_input_directory():
        # custom_nodes/<this>/../../input
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))), "input")


# micxin2025 prompt-writer assets (verbatim port: 16 task-mode templates +
# code-level dialogue tagger). Kept as a self-contained copy so this node does
# not depend on that package being installed/loaded.
try:
    import h3_micxin_assets as MX
except Exception:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import h3_micxin_assets as MX


# ---------------------------------------------------------------------------
# 本地 GGUF 缓存：同一 (模型, mmproj, 层数, ctx) 复用同一 Llama 实例，
# 跑完由 _unload_local 卸载释放显存（Split+Translate 默认 keep_loaded=OFF）。
# ---------------------------------------------------------------------------
_LOCAL = {"llm": None, "config": None}


def _resolve_llm_dir():
    if folder_paths is not None and getattr(folder_paths, "models_dir", None):
        return os.path.join(folder_paths.models_dir, "LLM")
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "models", "LLM")


def _list_llm_files(include_mmproj=True, mmproj_only=False):
    try:
        base = _resolve_llm_dir()
        if not os.path.isdir(base):
            return []
        files = [f for f in os.listdir(base) if f.lower().endswith(".gguf")]
        if mmproj_only:
            files = [f for f in files if "mmproj" in f.lower()]
        elif not include_mmproj:
            files = [f for f in files if "mmproj" not in f.lower()]
        return sorted(files)
    except Exception:
        return []


def _default_gguf():
    return ""


# ---------------------------------------------------------------------------
# 本地 GGUF 加载（ComfyUI 内置 llama-cpp-python，无需外部 llama-server）
# ---------------------------------------------------------------------------
def _load_local_llm(gguf_name, mmproj_name, n_gpu_layers, n_ctx):
    import llama_cpp  # noqa: F401  (ensures llama-cpp-python is present)
    from llama_cpp import Llama
    if not gguf_name or gguf_name.strip() == "":
        raise RuntimeError("H3 LLM: GGUF 模型未选择。请在节点的「GGUF 模型」下拉框中选择一个 .gguf 文件（留空无法运行 Local GGUF 模式）。")
    chat_handler = None
    if mmproj_name and mmproj_name != "None":
        mmproj_path = os.path.join(_resolve_llm_dir(), mmproj_name)
        if os.path.exists(mmproj_path):
            try:
                from llama_cpp.llama_chat_format import Qwen3VLChatHandler
                chat_handler = Qwen3VLChatHandler(
                    clip_model_path=mmproj_path, force_reasoning=False,
                    verbose=False, image_min_tokens=1024)
            except Exception as e:
                print(f"[H3 AutoDirector] mmproj/Qwen3VLChatHandler failed "
                      f"({e}); falling back to text-only template.", flush=True)
                chat_handler = None
        else:
            print(f"[H3 AutoDirector] mmproj not found: {mmproj_path}; "
                  f"using text-only template.", flush=True)
    config = (gguf_name, mmproj_name, int(n_gpu_layers), int(n_ctx))
    if _LOCAL["llm"] is not None and _LOCAL["config"] == config:
        return _LOCAL["llm"]
    if _LOCAL["llm"] is not None:
        try:
            _LOCAL["llm"].close()
        except Exception:
            pass
        _LOCAL["llm"] = None
    model_path = os.path.join(_resolve_llm_dir(), gguf_name)
    if not os.path.exists(model_path):
        raise RuntimeError(f"H3 LLM: GGUF not found: {model_path}")
    print(f"[H3 AutoDirector] loading local GGUF {gguf_name} "
          f"(n_gpu_layers={n_gpu_layers}, n_ctx={n_ctx})...", flush=True)
    kwargs = {"model_path": model_path, "n_gpu_layers": int(n_gpu_layers),
              "n_ctx": int(n_ctx), "verbose": False}
    if chat_handler is not None:
        kwargs["chat_handler"] = chat_handler
    # 2026-08-22: 自动降级重试。16G 显存吃紧时，全量 offload + 大上下文
    # 会触发 "Failed to create context with model"。依次尝试：
    #   1) 原始参数
    #   2) n_ctx 减半
    #   3) n_ctx 再减半 + n_gpu_layers=20 (部分 offload)
    #   4) n_ctx=4096 + n_gpu_layers=0 (纯 CPU，保底能跑)
    _fallbacks = [
        {},
        {"n_ctx": max(4096, int(n_ctx) // 2)},
        {"n_ctx": max(4096, int(n_ctx) // 4), "n_gpu_layers": 20},
        {"n_ctx": 4096, "n_gpu_layers": 0},
    ]
    llm = None
    for i, patch in enumerate(_fallbacks):
        try_kwargs = dict(kwargs)
        try_kwargs.update(patch)
        if i > 0:
            print(f"[H3 AutoDirector] retry {i}/3 with "
                  f"n_gpu_layers={try_kwargs['n_gpu_layers']}, "
                  f"n_ctx={try_kwargs['n_ctx']}...", flush=True)
        try:
            llm = Llama(**try_kwargs)
            break
        except (ValueError, RuntimeError) as e:
            print(f"[H3 AutoDirector] load attempt {i + 1} failed: "
                  f"{type(e).__name__}: {e}", flush=True)
            if i == len(_fallbacks) - 1:
                raise
    _LOCAL["llm"] = llm
    _LOCAL["config"] = config
    return llm


class _ContextOverflow(RuntimeError):
    """Raised when the local model's context window is too small for the
    conversation (llama.cpp disables context-shift for Qwen3-VL's M-RoPE, so
    it crashes instead of auto-extending). Lets the caller recover by trimming
    history rather than killing the whole run."""


# 生成输出 token 上限（与上下文窗口 _N_CTX 分开控制）。
# 旧版写死 12288，宽松到足以让本地无审查 8B 模型把 detailed_description 灌成
# 数百个 [Shot N] 微镜头（≈300 个 shot）。单段六段式提示词 800-1600 token 足够，
# 这里收紧到 4096：既留足余量，又用物理上限挡住 300-shot 膨胀（300×~40≈12000>4096）。
# 必须在 _call_local_llm 定义【之前】定义：Python 默认参数在函数定义时即求值，
# 放后面会导致模块加载即 NameError（节点变红、搜不到）。
_MAX_GEN_TOKENS = 4096


def _call_local_llm(llm, messages, temperature, seed, max_tokens=_MAX_GEN_TOKENS):
    gen = {"messages": messages, "temperature": temperature,
           "max_tokens": max_tokens, "stream": False}
    if seed:
        gen["seed"] = int(seed)
    # Qwen3 系模型默认输出思考过程，会污染翻译结构（思考里带换行 → Split 误拆段）。
    # 与 HTTP 路径一致：chat_template_kwargs={"enable_thinking": False} 关闭思考。
    # 旧版 llama-cpp-python 不支持该参数 → TypeError 回退不带参数重试。
    # 魔改/角色扮演微调模板可能忽略 enable_thinking（全程思考 → content 空）。
    # 因此做三级模板重试：no-thinking → default → thinking，三种都空才抛错。
    attempts = [
        ("no-thinking", {"chat_template_kwargs": {"enable_thinking": False}}),
        ("default", {}),
        ("thinking", {"chat_template_kwargs": {"enable_thinking": True}}),
    ]
    for label, extra in attempts:
        try:
            out = llm.create_chat_completion(**gen, **extra)
        except TypeError:
            out = llm.create_chat_completion(**gen)
        except RuntimeError as e:
            msg = str(e)
            if "Context Shift" in msg or "n_ctx" in msg or "context" in msg.lower():
                raise _ContextOverflow(msg) from e
            raise
        choices = out.get("choices") or [{}]
        content = (choices[0].get("message", {}) or {}).get("content") or ""
        if not content.strip():
            content = (choices[0].get("message", {}) or {}).get(
                "reasoning_content", "") or ""
        if content.strip():
            if label != "no-thinking":
                print(f"[H3 AutoDirector] local LLM '{label}' 模式重试后拿到内容。",
                      flush=True)
            return content
        print(f"[H3 AutoDirector] local LLM '{label}' 模式返回空内容，重试下一模式…",
              flush=True)
    raise ValueError(
        "Local LLM returned empty content.（no-thinking / default / thinking "
        "三种模板模式均空回复）建议：① 换 HTTP 后端（llama.cpp server / one-api）；"
        "② 检查该 GGUF 的 chat template 是否为 Qwen3 标准模板；"
        "③ 缩短概念或调小 context_size，避免 n_ctx 降级后 prompt 被截断。")


def _unload_local():
    if _LOCAL["llm"] is not None:
        try:
            _LOCAL["llm"].close()
        except Exception:
            pass
    _LOCAL["llm"] = None
    _LOCAL["config"] = None
    import gc
    gc.collect()
    try:
        import comfy.model_management as mm
        mm.soft_empty_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP 调用（OpenAI 兼容 /v1/chat/completions；原 H3PromptWriter._call_llm）
# 支持本地 llama.cpp server / Ollama / SiliconFlow / OpenAI / DeepSeek 等端点。
# ---------------------------------------------------------------------------
def _call_llm(url, model, api_key, messages, temperature, seed,
              timeout=360, max_retries=1, retry_delay=8,
              disable_thinking=True, overall_timeout=420):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def build_payload():
        p = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": _MAX_GEN_TOKENS,
        }
        if seed:
            p["seed"] = int(seed)
        return p

    last_err = None
    drop_kwargs = False
    # 总超时护栏：无论单次 urlopen 怎么卡，整体最多 overall_timeout 秒后一定
    # （2026-09-05: 120/150 -> 360/420，35B + 41KB 知识 prefill 在 16G 上常超 150s）
    # 失败，避免 ComfyUI 单线程 prompt 执行被长时间阻塞（曾导致前端全局禁用
    # 画布、所有输入框变灰、需刷新浏览器才恢复）。纯单线程 + time.monotonic，
    # 无信号/线程泄漏风险，Windows 安全。
    deadline = time.monotonic() + overall_timeout
    for attempt in range(max_retries):
        payload = build_payload()
        # Best-effort: turn OFF Qwen3 chain-of-thought so the model does
        # not burn its token budget on a "thinking process" before the
        # JSON. Some servers reject the key (HTTP 400) — then we retry
        # without it (don't waste a real retry on a schema error).
        if disable_thinking and not drop_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        # 本次 urlopen 超时 = min(单call上限, 剩余总预算)，确保整体不超 deadline
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            raise RuntimeError(
                f"H3 LLM: LLM overall timeout ({overall_timeout}s) "
                f"reached before attempt {attempt + 1}.")
        call_timeout = max(5, int(min(timeout, remaining)))
        try:
            req = urllib.request.Request(
                url, json.dumps(payload).encode("utf-8"), headers)
            with urllib.request.urlopen(req, timeout=call_timeout) as r:
                raw = r.read()
            data = json.loads(raw.decode("utf-8", "replace"))
            choices = data.get("choices") or [{}]
            content = (choices[0].get("message", {}).get("content") or "")
            if not content.strip():
                # reasoning models may land the answer in reasoning_content
                content = choices[0].get("message", {}).get(
                    "reasoning_content", "") or ""
            if not content.strip():
                raise ValueError("LLM returned empty content.")
            return content
        except urllib.error.HTTPError as e:
            if not drop_kwargs and e.code in (400, 422):
                drop_kwargs = True
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:160]
                except Exception:
                    pass
                print(f"[H3 AutoDirector] thinking-disable key not "
                      f"supported by server, retrying without it: {body}",
                      flush=True)
                continue
            last_err = e
            print(f"[H3 AutoDirector] LLM attempt {attempt + 1}/{max_retries} "
                  f"failed: HTTP {e.code}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(min(retry_delay, max(0.0, deadline - time.monotonic())))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                ValueError, KeyError, IndexError,
                json.JSONDecodeError) as e:
            last_err = e
            snippet = ""
            try:
                b = getattr(e, "read", lambda: b"")()
                if isinstance(b, (bytes, bytearray)):
                    snippet = b.decode("utf-8", "replace")[:160]
            except Exception:
                snippet = ""
            print(f"[H3 AutoDirector] LLM attempt {attempt + 1}/{max_retries} "
                  f"failed: {type(e).__name__}: {e} {snippet}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(min(retry_delay, max(0.0, deadline - time.monotonic())))
                continue
    hint = ""
    if last_err is not None:
        msg = str(last_err).lower()
        if "10061" in msg or "connection refused" in msg or "actively refused" in msg:
            hint = (
                "\n[提示] LLM 服务未启动（连接被拒绝）。请先双击 "
                "K:\\llamacuda131\\Qwen3.6-35B-IQ2_M.bat 启动 35B + 知识库代理（8081），"
                "等窗口显示就绪后再跑；或把 backend 切回 'Local GGUF' 用本地模型。")
    raise RuntimeError(
        f"H3 LLM: LLM call failed after {max_retries} attempts: "
        f"{last_err}{hint}")


# ---------------------------------------------------------------------------
# Custom skill template support（skills/ 目录：<key>.json 或 <name>/SKILL.md）
# ---------------------------------------------------------------------------
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _parse_md_skill(md_path):
    """WorkBuddy SKILL.md / SKILL.cn.md -> skill dict（供任务模式使用）。

    frontmatter 取 name / display_name（可选）/ description；正文（去 frontmatter）
    作为 system_prompt。正文过长会撑爆本地 LLM context：截取前 6000 字符
    （约 3K token），保证 8K ctx 的 Qwen GGUF 可用。standalone=False 表示按
    追加模式挂到 REVERSE_INFERENCE_BASE 之后（保留 JSON 数组输出契约）。
    """
    try:
        with open(md_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        print(f"[H3 AutoDirector] failed to read skill {md_path}: {e}", flush=True)
        return None
    m = _FRONTMATTER_RE.match(raw)
    fm = {}
    body = raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip("|").strip()
        body = raw[m.end():].strip()
    name = fm.get("name") or os.path.splitext(os.path.basename(md_path))[0]
    display_name = fm.get("display_name") or name
    return {
        "display_name": display_name,
        "system_prompt": body[:6000],
        "standalone": False,
        "style_contract": "Cinematic live-action",
        "path": md_path,
    }


def _load_custom_skills():
    """Load custom skill templates from skills/ directory.
    支持两种格式：
    1. skills/<key>.json —— 节点原生格式（key/display_name/system_prompt/
       standalone/style_contract）。
    2. skills/<name>/SKILL.md 或 SKILL.cn.md —— WorkBuddy 格式（frontmatter +
       正文，从 J:/skill 技能库注入）。没有 md 的空目录安全跳过。
    """
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills
    # 与内置 16 风格主题重复的 WorkBuddy 技能：任务模式只保留内置中文版，
    # 避免下拉出现 8 对同主题重复项（文件保留，想用时可从集合移除恢复）。
    _MD_SKILL_IGNORE = {
        "3d-animation-short-generator", "brand-promo-video-generator",
        "co-op-game-intro-generator", "handdrawn-live-video-generator",
        "minimalist-product-ad-generator", "mv-subtitle-skill-confirmed",
        "paper-collage-explainer-generator", "papercraft-stop-motion-explainer",
    }
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if fn in _MD_SKILL_IGNORE:
            continue
        path = os.path.join(SKILLS_DIR, fn)
        if os.path.isfile(path) and fn.lower().endswith(".json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                key = data.get("key") or os.path.splitext(fn)[0]
                display_name = data.get("display_name") or key
                skills[key] = {
                    "display_name": display_name,
                    "system_prompt": data.get("system_prompt", ""),
                    "standalone": bool(data.get("standalone", True)),
                    "style_contract": data.get("style_contract", "Cinematic live-action"),
                    "path": path,
                }
            except Exception as e:
                print(f"[H3 AutoDirector] failed to load skill {fn}: {e}", flush=True)
        elif os.path.isdir(path):
            for mdfn in ("SKILL.md", "SKILL.cn.md"):
                mdp = os.path.join(path, mdfn)
                if os.path.isfile(mdp):
                    info = _parse_md_skill(mdp)
                    if info:
                        # key 用目录名（唯一），display_name 是下拉显示名
                        skills[fn] = info
                    break
    return skills


CUSTOM_SKILLS = _load_custom_skills()
CUSTOM_SKILL_KEYS = list(CUSTOM_SKILLS.keys())

# ---------------------------------------------------------------------------
# 写作增强规则（训练语料归纳，2026-09-13 注入 fullreference 反推模板）
# 来源：MiniMax H3 Singularity Prompt Writing Specification (Enhanced)。
# 只追加到 fullreference（H3PromptWriter 默认模式 + SplitTranslate 反推共用），
# 其余 task_mode 模板不动，避免改变已验收行为。
# ---------------------------------------------------------------------------
H3_WRITING_ENHANCEMENTS = r"""# WRITING QUALITY RULES (corpus-derived, apply to EVERY shot you write)
ACTION CHAIN: never write isolated action labels ("walks", "attacks", "turns",
"explodes"). Expand every primary action into a causal chain: preparation ->
trigger -> acceleration -> primary action -> contact -> reaction -> recovery ->
final state. State the initial state BEFORE the trigger and the resulting state
AFTER the action.
CAMERA SPEC: name one camera idea per shot with all five elements: camera
position / shot size + movement type + direction + speed/amplitude + subject
followed. Bind the camera to the action (e.g. a side-tracking camera matches the
character's running speed; a push-in accelerates toward the instant of impact).
Never write "dynamic camera" / "cinematic camera" without these specifics.
MICRO-ACTING: convert abstract emotions into observable behavior - gaze
direction, blinking, eyebrow movement, lip tension, breathing, posture, hand
tension, and explicit attention (what the character looks at / reacts to).
Instead of "she looks nervous", describe her gaze shifting toward the doorway,
lips tightening, breathing becoming shallow, fingers adjusting their grip.
REFERENCE ROLE SPLIT: a reference image is NOT automatically a video first
frame. In subject_definitions, lock each character's appearance from the
reference with an explicit sentence such as: "The reference image <Picture N>
defines her facial features, hair style, and body proportions." (adapt N to
the actual tag). Use <Picture N> as an independent reference ONLY when it
genuinely anchors the shot as first frame / keyframe / composition anchor.
Never renumber, invent, or drop reference tags.
CROSS-SHOT CONTINUITY: keep screen direction consistent; carry weapon position,
body pose, object ownership, damage, dirt, smoke, and broken props forward
between shots; keep speaker IDs (S1)/(S2) stable; synchronize footsteps /
impacts / cloth movement with visible events.
AUDIO LAYERING: overall_soundscape carries ambient + synchronized diegetic
effects (dialogue, footsteps, impacts); non_diegetic_music carries ONLY the
audience-only score - never mix the score into the physical soundscape.
DISTANT SUBJECTS: when characters are far in the frame, explicitly state they
keep walking / moving throughout the shot at a steady pace - small on-screen
scale must NOT freeze them.
FORBIDDEN FILLER: "cinematic", "epic", "high quality", "dynamic" must never
replace observable visual instructions - every visual claim must be something
the camera can actually show."""


# ---------------------------------------------------------------------------
# H3 台词/动作/语气细化增强（提炼自 J:\H3台词细化SKILL 官方案例方法论，
# 只追加到默认任务模式 h3-prompt-writing-micxin，用户开箱即用；
# 不改动 J:\H3台词细化SKILL\SKILL.md 原文件。其余 task_mode 不注入，
# 避免改变已验收行为。）
# ---------------------------------------------------------------------------
H3_DIALOGUE_REFINEMENT = r"""# DIALOGUE & PERFORMANCE REFINEMENT RULES (from the H3 dialogue-refinement methodology; highest priority for any shot containing speech)

0. SUBJECT COUNT IS FIXED AND SMALL. The number of <Subject N> entries is ALWAYS the number of reference pictures plus the scene (or exactly the number the concept states) — typically 2-4, NEVER dozens. Numbering starts at 1 and is CONTIGUOUS. Each <Subject N> is bound to ONE reference picture via <Picture N>. ABSOLUTELY FORBIDDEN: inventing extra <Subject N> entries for props, objects, actions, expressions, poses, camera moves, or individual lines of dialogue. Everything that is not a defined subject stays inside an existing subject's description. Example: a concept with 2 pictures (scene + one person) has EXACTLY 2 subjects — not 3, not 161.

1. DIALOGUE IS A TIMELINE ACTION, NOT A QUOTE. Never write a line as an isolated quote. Embed it inside the character's action beat, exactly in this shape (REUSE an existing subject number from subject_definitions — do NOT create a new one):
   <Subject 2> (S1) <delivery & action, in English> says: <d>[Language] verbatim line</d>
   The delivery (volume, pacing, emotional tendency) goes INTO the action clause — e.g. "in a muffled voice squeezed from the chest, low and stuffy, as if holding his breath" — never as an emotion adjective ("he says sadly" is FORBIDDEN).

2. EMOTION VIA OBSERVABLE SIGNALS, NOT ADJECTIVES. Never write abstract emotion words (悲伤/压抑/愤怒/喜悦 or sad/angry/nervous). Express emotion with what the camera and microphone can actually show: gaze direction, breathing, jaw tension, head/shoulder posture, hands interacting with props, vocal line. Inner intensity and outer amplitude are independent — an extremely agitated character may stay almost motionless.

3. STABLE SPEAKER IDS (S1)/(S2) ACROSS THE WHOLE VIDEO. Same character keeps the same (Sx) in every shot, mapped to the reference audio number, and each speaker is declared once ("在全片保持一致" / "stays consistent throughout the whole video"). When a subject speaks, REUSE its existing <Subject N> tag from subject_definitions (N already defined above — never add new numbers) and write <Subject N> (Sx) ... says: <d>[Language] ...</d>.

4. ANTI-DRIFT WHITELIST IN CHINESE. The scene subject's single subject_definitions entry ends with the scene/prop list plus one Chinese whitelist sentence (verified Chinese-prompt practice, keep in Chinese), e.g.: "桌上物品以上述为准，全程不得添加、移动或碰触其他物件。" (the listed items are authoritative; nothing may be added, moved, or touched for the whole video). Do NOT create a separate <Subject N> for each listed prop — they stay inside the scene subject's ONE definition. This is the ONE exception to the English-narrative rule.

5. 【约束】CONSISTENCY LINE PER SHOT. End dialogue shots with a consistency line in Chinese (verified practice, keep Chinese): "五官稳定，面部不扭曲，口型与台词同步，画面无跳变；人物外观与服装前后一致，光线一致". Merge exclusions into at most 3 groups, e.g. "排除表情夸张、动作幅度过大、画面过亮或任何笑容轻松表情".

6. PERFORMANCE PRINCIPLES. (a) Character task first, then emotion — never stop at an adjective. (b) Carry forward at most 1-2 state items that truly affect this shot; do not reset emotion to zero at each shot start. (c) Receive before react — the character sees/hears the trigger first, then deviates. (d) End each shot on a choice — a pose/action/pause that shows what the character decided and can connect to the next shot. (e) The signals follow the character's choice, not a fixed emotion-to-muscle formula."""


# ---------------------------------------------------------------------------
# 任务模式 → 视觉风格前缀（注入 user_brief 的 VISUAL STYLE 行）
# ---------------------------------------------------------------------------
TASK_STYLE_MAP = {
    "fullreference": "Cinematic live-action",
    "3d_animation": "3D CG",
    "minimalist_ad": "minimalist product cinematic",
    "papercraft_stopmotion": "Papercraft stop-motion",
    "brand_promo": "cinematic brand film",
    "mv_subtitle": "Music video",
    "coop_game_intro": "Game cinematic",
    "paper_collage": "Paper collage art",
    "handdrawn_live": "Handdrawn-live fusion",
    "firstframe_anchor": "Cinematic / live-action",
    "fl2va": "Cinematic / live-action",
    "action_transfer": "Cinematic / live-action",
    "fixed_firstframe_voice": "Cinematic / live-action",
    "ref_voice_clone": "Cinematic / live-action",
    "dual_dialogue": "Cinematic / live-action",
    "speculative_system_montage": "high-density future-system montage",
    "multiref_multitrack": "Cinematic / live-action",
    "instruction_edit": "Cinematic / live-action",
}


def _build_system_prompt(task_key):
    """Return system prompt for built-in micxin2025 styles or custom skills."""
    if task_key in CUSTOM_SKILLS:
        info = CUSTOM_SKILLS[task_key]
        sp = info["system_prompt"]
        if not sp:
            sp = _build_system_prompt("fullreference")
        elif not info.get("standalone", True):
            # Append-style: base + skill + dialogue preservation rule
            sp = (MX.REVERSE_INFERENCE_BASE + "\n\n" + sp
                  + "\n\n" + MX.DIALOGUE_PRESERVE_RULE)
        else:
            # Standalone: skill + dialogue preservation rule
            sp = sp + "\n\n" + MX.DIALOGUE_PRESERVE_RULE
        # 默认任务模式（micxin 增强）额外注入台词/动作/语气细化增强，
        # 其余自定义 skill 保持原行为不变。
        if task_key == "h3-prompt-writing-micxin":
            sp = sp + "\n\n" + H3_DIALOGUE_REFINEMENT
        return sp
    sp = MX._build_system_prompt(task_key)
    if task_key == "fullreference":
        sp = sp + "\n\n" + H3_WRITING_ENHANCEMENTS
    return sp


def _get_style_contract(task_key):
    if task_key in CUSTOM_SKILLS:
        return CUSTOM_SKILLS[task_key].get("style_contract", "Cinematic live-action")
    return TASK_STYLE_MAP.get(task_key, "Cinematic live-action")


# ---------------------------------------------------------------------------
# 参考图过图（OpenAI 多模态 content / 路径加载张量）
# ---------------------------------------------------------------------------
def _images_to_contents(ref_images):
    """把 ComfyUI IMAGE 张量 (B,H,W,C, 0-1) 转成 OpenAI 多模态 content 列表。

    返回 [{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}, ...]。
    跳过 64x64 黑色占位（H3MultiImageLoader 无图时返回），避免把黑块喂给 VLM。
    无图 / None / 非 4D 张量返回空列表（纯文本模式）。
    """
    contents = []
    if ref_images is None:
        return contents
    try:
        arr = ref_images.cpu().numpy() if hasattr(ref_images, "cpu") else ref_images.numpy()
    except Exception:
        return contents
    if getattr(arr, "ndim", 0) != 4:
        return contents
    for i in range(arr.shape[0]):
        frame = arr[i]
        h, w = frame.shape[:2]
        if h <= 64 or w <= 64:  # 占位图，跳过
            continue
        px = (frame * 255.0).clip(0, 255).astype("uint8")
        im = Image.fromarray(px)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        contents.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return contents


def _load_image_tensor_from_paths(paths_str):
    """从多行路径字符串加载图片，返回 (B,H,W,C, 0-1 float32) torch.Tensor 或 None。

    每行格式: path|start|end（start/end 对图片无意义，取 path 部分即可）。
    路径解析顺序: 绝对路径 → input 目录 → folder_paths.get_annotated_filepath。

    【动态分辨率】按图片数量自动缩放长边，控制总 image token 不超 n_ctx=8192:
      1-3 张 → 长边 1024px (~512-1024 token/张)
      4-6 张 → 长边 768px  (~256-512 token/张)
      7-9 张 → 长边 512px  (~128-256 token/张)
    9 张 512px ≈ 1152-2304 token，加上 system prompt(~2500) + user brief(~800)
    + 输出预留(~2048) ≈ 6500-7650 token，安全在 8192 以内。

    所有图片 padding 到统一最大尺寸（居中），stack 成 batch。无有效图片返回 None。
    """
    if not paths_str or not str(paths_str).strip():
        return None
    try:
        import torch
        import numpy as np
    except Exception:
        return None
    # 先统计有效图片数量，决定动态缩放分辨率
    raw_lines = [l.strip() for l in str(paths_str).split("\n") if l.strip()]
    img_count = len(raw_lines)
    if img_count <= 3:
        max_side = 1024
    elif img_count <= 6:
        max_side = 768
    else:
        max_side = 512
    frames = []
    input_dir = get_input_directory()
    for line in raw_lines:
        path = line.split("|")[0].strip()
        if not path:
            continue
        full_path = path
        if not os.path.isabs(full_path):
            full_path = os.path.join(input_dir, path)
        if not os.path.exists(full_path):
            try:
                full_path = folder_paths.get_annotated_filepath(path)
            except Exception:
                pass
        if not os.path.exists(full_path):
            continue
        try:
            img = Image.open(full_path).convert("RGB")
            # 按图片数量动态缩放长边（LANCZOS 高质量）
            w, h = img.size
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                img = img.resize((new_w, new_h), Image.LANCZOS)
            arr = np.array(img).astype(np.float32) / 255.0
            frames.append(arr)
        except Exception:
            continue
    if not frames:
        return None
    max_h = max(f.shape[0] for f in frames)
    max_w = max(f.shape[1] for f in frames)
    padded = []
    for f in frames:
        h, w = f.shape[:2]
        if h != max_h or w != max_w:
            canvas = np.zeros((max_h, max_w, 3), dtype=np.float32)
            y0 = (max_h - h) // 2
            x0 = (max_w - w) // 2
            canvas[y0:y0 + h, x0:x0 + w] = f
            padded.append(canvas)
        else:
            padded.append(f)
    return torch.from_numpy(np.stack(padded, axis=0)), img_count, max_side
