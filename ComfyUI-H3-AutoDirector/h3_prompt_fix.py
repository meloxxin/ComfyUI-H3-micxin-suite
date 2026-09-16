"""
H3 Prompt Fix - ComfyUI 自定义节点
功能：把 Llama-cpp / 其他 LLM 输出的不规范文本自动修复为规范的 H3 分段 JSON
作者：Yuanbao 辅助生成

支持的问题：
1. 输出没有大括号 { } —— 自动补全
2. 没有严格按输入段数分段 —— 按镜X / 空行重新切分补齐
3. 仍在使用 <d> 标签 —— 转换为自然语言内联对话
4. 六段之间用单 \n —— 统一为 \n\n
5. 字段缺失 —— 用默认值补齐
6. 混入 markdown 代码块 / 多余解释文字 —— 清洗
"""

import re
import json
from typing import Tuple, List, Dict


# ---------- 工具函数 ----------

def _strip_code_fences(text: str) -> str:
    """去掉 ```json ... ``` 或 ``` ... ``` 代码块标记"""
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    return text.strip()


def _extract_json_object(text: str) -> str:
    """从可能混杂解释文字的文本中，提取第一个完整 JSON 对象字符串"""
    # 先尝试找最外层 { ... }
    start = text.find("{")
    if start == -1:
        return text  # 没有大括号，交给上层按裸文本处理
    # 用括号计数找到匹配的 }
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]  # 没找到匹配，退化为从 { 到结尾


# ---------- 分段解析 ----------

# 匹配 "镜X" / "镜头X" / "Segment X" 等分段标记
_SEG_HEAD = re.compile(r"(?:^|\n)\s*(?:镜\s*头?\s*(\d+)|[Ss]egment\s*(\d+)|#\s*(\d+))\s*[:：]?", re.MULTILINE)


def split_by_segment_markers(text: str) -> List[Tuple[int, str]]:
    """按 镜X / Segment X 标记切分，返回 [(index, body), ...]"""
    matches = list(_SEG_HEAD.finditer(text))
    if len(matches) < 2:
        return []  # 找不到至少两个标记，交给空行切分
    results = []
    for i, m in enumerate(matches):
        idx = int(m.group(1) or m.group(2) or m.group(3))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            results.append((idx, body))
    return results


def split_by_blank_lines(text: str) -> List[str]:
    """按一个或多个空行切分"""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


# ---------- 六段式字段处理 ----------

# 标准六字段（顺序固定）
FIELDS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]


def _find_field(text: str, field: str) -> str:
    """在文本中查找某个字段的值（不区分大小写，支持中英文冒号）"""
    pattern = re.compile(
        rf"{field}\s*[:：]\s*(.*?)(?=(?:{'|'.join(re.escape(f) for f in FIELDS)})\s*[:：]|$)",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(text)
    if m:
        return m.group(1).strip().strip('"').strip()
    return ""


def _normalize_newlines(text: str) -> str:
    """统一换行形式：先把转义的 \\n 还原成真换行，再把多空行压成单个空行"""
    # 保护已被引号包裹的字段值内部换行：先把 字面量 "\\n" 变真换行
    text = text.replace("\\\\n", "\n").replace("\\n", "\n")
    # 连续 2 个及以上空行 → 单个空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _convert_dialogue_tags(text: str) -> str:
    """把 <d>...</d> 标签转换为 H3 自然语言内联对话"""
    def repl(m):
        content = m.group(1).strip()
        # 尝试提取 时间戳 + 说话人
        ts = re.search(r"(\d{2}:\d{2}(?:\s*-\s*\d{2}:\d{2})?)", content)
        speaker = re.search(r"\(([^)]+)\)\s*:\s*", content)
        dialogue = re.sub(r"^[\d:]+\s*-\s*[\d:]+\s*", "", content).strip()
        dialogue = re.sub(r"^\([^)]+\)\s*:\s*", "", dialogue).strip()
        dialogue = dialogue.strip('"').strip()

        parts = []
        if speaker:
            parts.append(f"{speaker.group(1).strip()} says")
        if dialogue:
            parts.append(f'"{dialogue}"')
        if ts:
            parts.append(f"from {ts.group(1).replace(' ', '')}")
        return " ".join(parts) + ". " if parts else f'"{dialogue}". '
    result = re.sub(r"<d>(.*?)</d>", repl, text, flags=re.DOTALL)
    return result


def _split_into_fields(body: str, seg_index: int) -> Dict[str, str]:
    """把一个片段的正文解析为六字段字典"""
    # 归一化换行（处理转义的 \n 与真实换行的混合情况）
    body = _normalize_newlines(body)
    # 先转换 <d> 标签
    body = _convert_dialogue_tags(body)

    fields = {}
    for f in FIELDS:
        val = _find_field(body, f)
        fields[f] = val

    # 兜底：如果 detailed_description 完全为空，把整段正文塞进去
    if not fields["detailed_description"].strip() and body:
        fields["detailed_description"] = body

    # 兜底：summary 缺失时自动生成
    if not fields["summary"].strip():
        fields["summary"] = f"Segment {seg_index}, auto-generated summary."

    # non_diegetic_music 默认 N/A
    if not fields["non_diegetic_music"].strip():
        fields["non_diegetic_music"] = "N/A"

    return fields


def _ensure_double_newlines(fields: Dict[str, str]) -> str:
    """把六字段拼接为 \n\n 分隔的字符串。

    字段值内部可能含换行（如多句描述），统一压成单换行；字段之间固定 \n\n。
    """
    parts = []
    for f in FIELDS:
        val = fields[f].strip()
        # 值内部的连续空行压成单换行，避免 \n\n\n\n
        val = re.sub(r"\n{2,}", "\n", val)
        parts.append(f"{f}: {val}")
    return "\n\n".join(parts)


# ---------- 主修复逻辑 ----------

def fix_prompt(raw_text: str, expected_segments: int = 0) -> Tuple[str, str]:
    """
    主入口：修复 LLM 输出
    expected_segments: 期望的段数（0 = 自动检测）
    返回 (json_string, status_message)
    """
    raw_text = _strip_code_fences(raw_text)

    # 尝试直接解析现有 JSON，能解析且结构合理就补全后返回
    candidate = _extract_json_object(raw_text)
    parsed = None
    try:
        if candidate.startswith("{"):
            parsed = json.loads(candidate)
    except Exception:
        parsed = None

    segments = parsed if isinstance(parsed, dict) else None

    if segments:
        # 已有 JSON，做字段级修复（补全字段、转换 <d>、统一 \n\n）
        max_key = -1
        for k in segments:
            try:
                max_key = max(max_key, int(k))
            except ValueError:
                pass
        for i in range(max_key + 1):
            key = str(i)
            if key not in segments:
                # 缺失的段用空占位，后面统一补齐
                segments[key] = ""
        fixed = {}
        for k, v in segments.items():
            if not isinstance(v, str):
                v = str(v)
            fields = _split_into_fields(v, int(k) if k.isdigit() else 0)
            fixed[k] = _ensure_double_newlines(fields)
        result = json.dumps(fixed, ensure_ascii=False, indent=2)
        return result, f"Parsed existing JSON, fixed {len(fixed)} segment(s)."

    # ---- 没有合法 JSON，按文本重新切分 ----
    seg_blocks = split_by_segment_markers(raw_text)
    if not seg_blocks:
        # 退化：按空行切
        blocks = split_by_blank_lines(raw_text)
        seg_blocks = [(i, b) for i, b in enumerate(blocks)]

    fixed = {}
    for idx, body in seg_blocks:
        fields = _split_into_fields(body, idx)
        fixed[str(idx)] = _ensure_double_newlines(fields)

    # 如果调用方指定了期望段数，补齐缺失段。
    # 注意：若输入用「镜10/11/12」这类非连续编号，expected_segments 应设为实际段数（3），
    # 占位会从「已存在最大 key + 1」开始编号，绝不覆盖已有的镜X编号。
    if expected_segments > 0:
        existing = [int(k) for k in fixed if k.isdigit()]
        next_key = (max(existing) + 1) if existing else 0
        for i in range(len(fixed), expected_segments):
            key = str(next_key)
            next_key += 1
            placeholder = _ensure_double_newlines({
                "subject_definitions": "",
                "summary": f"Segment {key}, placeholder (LLM did not generate this segment).",
                "retention_analysis": "",
                "detailed_description": f"Segment {key} content missing from LLM output.",
                "overall_soundscape": "",
                "non_diegetic_music": "N/A",
            })
            fixed[key] = placeholder

    result = json.dumps(fixed, ensure_ascii=False, indent=2)
    msg = f"Rebuilt JSON from raw text, {len(fixed)} segment(s)."
    if expected_segments > 0 and len(fixed) != expected_segments:
        msg += f" (expected {expected_segments}, got {len(fixed)})"
    return result, msg


# ---------- ComfyUI 节点定义 ----------

class H3PromptFix:
    """修复 LLM 输出的 H3 视频提示词，确保为标准分段 JSON"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "raw_text": ("STRING", {"multiline": True, "default": ""}),
                "expected_segments": ("INT", {"default": 0, "min": 0, "max": 32}),
            },
            "optional": {},
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("fixed_json", "status")
    FUNCTION = "fix"
    CATEGORY = "H3/Utilities"

    def fix(self, raw_text: str, expected_segments: int = 0):
        fixed, status = fix_prompt(raw_text, expected_segments)
        return (fixed, status)


class H3PromptToConditioning:
    """把修复后的 JSON 拆成逐段 detailed_description，供 H3 文本编码器逐段采样"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "fixed_json": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("segment_prompts",)
    FUNCTION = "split"
    CATEGORY = "H3/Utilities"

    def split(self, fixed_json: str):
        try:
            data = json.loads(fixed_json)
        except Exception as e:
            return (f"[ERROR parsing JSON: {e}]",)
        # 按数字 key 排序，输出每段可直接喂给文本编码器的提示词
        prompts = []
        for k in sorted(data.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            v = data[k]
            # 优先用 detailed_description，退化为整段
            m = re.search(r"detailed_description:\s*(.*?)(?=\n\noverall_soundscape:|\Z)", v, re.DOTALL)
            desc = m.group(1).strip() if m else v
            prompts.append(f"=== Segment {k} ===\n{desc}")
        return ("\n\n".join(prompts),)


# ---------- 注册 ----------
NODE_CLASS_MAPPINGS = {
    "H3PromptFix": H3PromptFix,
    "H3PromptToConditioning": H3PromptToConditioning,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3PromptFix": "H3 Prompt Fix (LLM→JSON)",
    "H3PromptToConditioning": "H3 Prompt → Segment Prompts",
}


if __name__ == "__main__":
    # 本地测试：用你上一条的真实 LLM 输出
    test = '''subject_definitions: S1 locked from Picture 2.\\n\\nsummary: Segment 0...\\n\\ndetailed_description: S1 says "你凭什么删我东西……"\\n\\noverall_soundscape: quiet\\n\\nnon_diegetic_music: N/A

subject_definitions: S2 locked from Picture 1.\\n\\nsummary: Segment 1...\\n\\ndetailed_description: S2 says "凭我是你爸！" pointing at table\\n\\noverall_soundscape: rustle\\n\\nnon_diegetic_music: N/A'''

    # 模拟真实场景：带 markdown、缺大括号、含 <d> 标签
    messy = """```json
{
  "0": "subject_definitions: S1 from <Picture 2>.\\n\\nsummary: 5s close-up.\\n\\nretention_analysis: S1 preserved.\\n\\ndetailed_description: S1 says <d>你凭什么删我东西……</d> lips quiver.\\n\\noverall_soundscape: quiet.\\n\\nnon_diegetic_music: N/A",
  "1": "subject_definitions: S2 from <Picture 1>.\\n\\nsummary: 5s medium.\\n\\ndetailed_description: S2 dumps bag. He says <d>凭我是你爸！跳舞能当饭吃吗？</d>\\n\\noverall_soundscape: rustle.\\n\\nnon_diegetic_music: N/A"
}
"""
    fixed, status = fix_prompt(messy, expected_segments=3)
    print("STATUS:", status)
    print("---")
    print(fixed)
