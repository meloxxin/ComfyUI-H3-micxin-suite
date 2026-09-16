# -*- coding: utf-8 -*-
"""H3 Prompt Split+Translate (micxin) — 拆分 + 翻译一体节点（轻量版）。

H3PromptSplit + H3PromptTranslate 一体：
  0. 内嵌格式修复：raw_text（原始中文剧本/提示词）→ 内部修复为六段式 JSON（fixed_json 输出预览）
  1. 输入 JSON / 多行文本 / 单段 → 拆分成最多 4 段（电脑配置友好）
  2. 每段翻译：**非对话中文 → 英文**；**对话原文保留**（<d> 标签 + 引号对话双重保护）
  3. 输出 prompt_0..3，直接对接 H3 Clip Chain (micxin) 的 segment_prompts
  4. 参考图过图（与 H3 R2VA AIO 结合）：JS 自动同步 AIO 的 image_paths 到
     _aio_ref_paths，LLM（本地 Qwen3-VL）看图锁定人物/场景，生成提示词不再是
     盲写，模型知道加了参考图
  5. 宽高长输出（与 H3 PromptWriter 相似）：aspect_ratio + resolution_mp +
     duration_seconds → width / height / length，直接接 AIO 的宽高长输入口

对话保护（防 LLM 把对话也翻译）：
  - 系统提示词明确 <d> 不翻译
  - 代码层：翻译前把「引号内中文」（S2说："你凭什么删我东西……" / S1 says: "..."）
    替换成占位符，翻译完再还原 —— LLM 物理上碰不到对话原文。
"""
import json
import math
import re

from comfy_api.latest import io

from .h3_screenwriter import (
    _load_local_llm,
    _call_local_llm,
    _unload_local,
    _list_llm_files,
    _load_image_tensor_from_paths,
    _images_to_contents,
    _build_system_prompt,
    _get_style_contract,
)
from .h3_prompt_fix import fix_prompt

_H3_SPLIT_REF_FIELDS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]

_H3_SPLIT_BASE_FIELDS = [
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
]

_H3_SPLIT_META_FIELDS = {
    "_mode", "id", "clip_id", "seed", "duration", "validated",
    "frame_count", "start_frame", "end_frame",
}


def _dict_to_h3_prompt(fields_dict):
    """把字段 dict 按 H3 标准顺序拼成纯文本提示词（对象数组元素用）。"""
    ordered = []


def _dict_to_h3_prompt(fields_dict):
    """把字段 dict 按 H3 标准顺序拼成纯文本提示词（对象数组元素用）。"""
    ordered = []
    used = set()
    # REF/BASE 两表有重叠字段，用 used 去重
    for field in _H3_SPLIT_REF_FIELDS + _H3_SPLIT_BASE_FIELDS:
        if field in used:
            continue
        if field in fields_dict and str(fields_dict[field]).strip():
            ordered.append((field, str(fields_dict[field]).strip()))
            used.add(field)
    for field, value in fields_dict.items():
        if field in _H3_SPLIT_META_FIELDS:
            continue
        if field not in used and field != "_mode" and str(value).strip():
            ordered.append((field, str(value).strip()))
    if not ordered:
        return ""
    return "\n\n".join(f"{name}: {value}" for name, value in ordered)





def _split_to_prompt_list(raw):
    """把输入拆成提示词列表，支持四种来源：

      - Python list（上游直接传列表，如 H3PromptWriter 剧本模式的 shots 输出）
      - JSON 数组字符串（字符串数组 / 对象数组，对象优先取 "prompt" 字段）
      - 多行纯文本（每行一段；行首 "#" 保留为独立模式标记；"//" 注释跳过；
）
      - 单条字符串（视为一段）

    返回 str 列表；空输入返回 []。
    """
    items = []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, dict):
        # 直接 dict：按 key 排序取值（兼容 H3InfiniteStoryWriter 的 {"0": "...", "1": ...}）
        items = [raw[k] for k in sorted(raw, key=lambda x: int(x) if str(x).isdigit() else 0)]
    elif isinstance(raw, str):
        text = raw.strip()
        parsed = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
        elif text.startswith("{"):
            # dict JSON：H3InfiniteStoryWriter 输出的 {"0": 六段式, "1": 六段式, ...}
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                items = [parsed[k] for k in sorted(parsed, key=lambda x: int(x) if str(x).isdigit() else 0)]
        if isinstance(parsed, list):
            items = parsed
        elif text and not items:
            # 空行分组：存在连续空行时，空行=段分隔，连续非空行合并为一段（镜头内多行）
            # 无空行时保持每行一段（兼容既有写法）。// 行=注释，先按行剔除。
            raw_lines = text.splitlines()
            body_lines = [l for l in raw_lines if not l.strip().startswith("//")]
            if any(not l.strip() for l in body_lines):
                blocks = [b.strip() for b in re.split(r"\n\s*\n", "\n".join(body_lines))
                          if b.strip()]
                items = [re.sub(r"\s*\n\s*", " ", b) for b in blocks]
            else:
                items = [l.strip() for l in body_lines if l.strip()]
    else:
        return []

    out = []
    for it in items:
        if isinstance(it, dict):
            # 嵌套 {"0": "..."} 再剥一层
            if "0" in it and len(it) == 1:
                it = it["0"]
            if isinstance(it, dict):
                prompt_text = it.get("prompt")
                if isinstance(prompt_text, str) and prompt_text.strip():
                    out.append(prompt_text.strip())
                else:
                    p = _dict_to_h3_prompt(it)
                    if p.strip():
                        out.append(p.strip())
            else:
                t = str(it).strip()
                if t:
                    out.append(t)
        elif isinstance(it, str):
            t = it.strip()
            if not t or t.startswith("//"):
                continue
            out.append(t)
    return out




MAX_SEGMENTS = 4

_TRANSLATE_SYS = """You are a professional translator for H3 video generation prompts (Chinese to English).
Translate the Chinese description text into English. STRICT RULES:
1. <d>...</d> dialogue blocks: KEEP the original text EXACTLY as-is. Never translate them.
2. ANY quoted dialogue (e.g. S1 says: "你凭什么删我东西……", 她说："不要走") — the text inside quotes is DIALOGUE: keep it verbatim, never translate.
3. Everything else (subject definitions, actions, camera moves, scene, emotions, soundscape, music descriptions): translate into English.
4. Keep all field names unchanged: subject_definitions:, summary:, retention_analysis:, detailed_description:, overall_soundscape:, non_diegetic_music:.
5. Keep the overall structure, field order and line breaks. Do not add or remove segments or fields.
6. Reference images are attached: describe the subject/background so the translated prompt stays consistent with the reference images.
7. Output ONLY the translated prompt text. No explanations, no notes, no code fences."""

# 引号内中文对话 → 占位符（保护不翻译）
_QUOTED_CN = re.compile(r'([""\u201c\u2018\u300c\u300e])([^""\u201d\u2019\u300d\u300f]{1,200}?)([""\u201d\u2019\u300d\u300f])', re.S)


_D_BLOCK_RE = re.compile(r"<d>.*?</d>", re.S)


def _protect_dialogues(text):
    """翻译/反推前：把台词换成占位符，返回 (masked, protected)。

    两类台词都保护（LLM 物理上碰不到原文）：
      1. <d>...</d> 整块（无论内部是否有引号，时间戳/说话人/台词一体保护）；
      2. 引号内含中文的裸台词（S1说："..." 之类）。
    protected 项：(content, q_open, q_close)；<d> 块 q_open/q_close 为空，
    _restore_dialogues 直接恢复整块。
    """
    protected = []

    def repl_d(m):
        protected.append((m.group(0), "", ""))
        return f" <<<DIALOGUE_{len(protected) - 1}>>> "

    text = _D_BLOCK_RE.sub(repl_d, text)

    def repl(m):
        q_open, content, q_close = m.group(1), m.group(2), m.group(3)
        if re.search(r"[\u4e00-\u9fff]", content):
            protected.append((content, q_open, q_close))
            return f" <<<DIALOGUE_{len(protected) - 1}>>> "
        return m.group(0)

    masked = _QUOTED_CN.sub(repl, text)
    return masked, protected


def _restore_dialogues(text, protected):
    for i, (content, q_open, q_close) in enumerate(protected):
        text = text.replace(f"<<<DIALOGUE_{i}>>>", f"{q_open}{content}{q_close}")
    return text


# 裸引号中文对话 → H3 <d> 块（带每段时间戳；<d> 块内部不动）
_D_QUOTE_RE = re.compile(
    r'([""\u201c\u2018])([^""\u201d\u2019]{1,300}?)([""\u201d\u2019])')


def _quotes_to_d_blocks(text, seg_seconds=5.0):
    """把输出里裸引号中文对话转成 H3 标准对话块。

    H3 把 <d>...</d> 识别为对白（模型按台词朗读，不会当成描述/画面文字）。
    翻译输出是 `whispers: “台词”` 这种裸引号 → 转成 `<d>台词</d>`。
    已有 <d> 块（反推输出）保留时间戳/说话人结构，原样不动。
    只处理含中文的引号对，英文引号/锁定句（含 <Picture N>）不误转。
    """
    if not text or not re.search(r"[\u4e00-\u9fff]", text):
        return text
    protected = []

    def repl_d(m):
        blk = m.group(0)
        protected.append(blk)
        return f" <<<KEEP_D_{len(protected) - 1}>>> "

    t = _D_BLOCK_RE.sub(repl_d, text)

    def repl(m):
        content = m.group(2)
        if not re.search(r"[\u4e00-\u9fff]", content):
            return m.group(0)
        return f"<d>{content}</d>"

    t = _D_QUOTE_RE.sub(repl, t)
    for i, blk in enumerate(protected):
        t = t.replace(f"<<<KEEP_D_{i}>>>", blk)
    return t


def _extract_dialogues(text):
    """按出现顺序提取台词（<d> 整块优先，其次引号中文），供强制恢复。"""
    lines = []
    t = _D_BLOCK_RE.sub(lambda m: lines.append(m.group(0)) or "", text)
    _QUOTED_CN.sub(lambda m: lines.append(m.group(0)) or "", t)
    return lines


def _strip_quotes(line):
    """去掉裸台词外层的引号（“…” / “…” / "…" / ‘…’）。"""
    t = line.strip()
    return re.sub(r'^["\u201c\u2018](.*)["\u201d\u2019]$', r'\1', t)


def _force_restore_dialogues(text, protected, src_text=None):
    """恢复占位符；若 LLM 反推输出丢了占位符（自己编了台词），
    用 src_text 的台词按顺序强制覆盖输出里的 <d> 块，保证台词逐字忠实。
    覆盖时保留 <d>[Lang] 外壳：src 台词（可能带引号）去引号后填入，
    方言标签兜底（_fix_dialect_tags）才能继续工作。"""
    text = _restore_dialogues(text, protected)
    if not src_text or "<<<DIALOGUE_" in text:
        # 仍有占位符未恢复（LLM 没带全）→ 保持现状（至少不越改越错）
        return text
    src_lines = _extract_dialogues(src_text)
    if not src_lines:
        return text
    counter = [0]
    def repl(m):
        if counter[0] < len(src_lines):
            line = src_lines[counter[0]]
            counter[0] += 1
            blk = m.group(0)
            if blk.startswith("<d>"):
                inner = blk[3:-4]
                tm = re.match(r"^\[([^\]]+)\](.*)$", inner, re.S)
                if tm:
                    return f"<d>[{tm.group(1)}]{_strip_quotes(line)}</d>"
                return f"<d>{_strip_quotes(line)}</d>"
            return line
        return m.group(0)
    return _D_BLOCK_RE.sub(repl, text)


_DIALECT_TAG_RE = re.compile(r"<d>\[([^\]]+)\](.*?)</d>", re.S)
_DIALECT_PLAIN_RE = re.compile(r"<d>(?![\[])([^<]*[\u4e00-\u9fff][^<]*)</d>", re.S)


def _fix_dialect_tags(text, src_text):
    """确定性兜底：原文台词附近（±60 字符）有『粤语/广东话/Cantonese』
    字样 → 把该台词的语言标签强制改为 [Cantonese]，不依赖 LLM 自觉。

    同时处理两种形态：
      - <d>[Lang] 台词</d>：改标签
      - <d>台词</d>（无标签，_quotes_to_d_blocks 产生）：补 [Cantonese] 标签
    """
    if not src_text or "<d>" not in text:
        return text
    def _has_canto(content):
        core = re.sub(r"[，。！？、；：\s“”()（）!?;:.]", "", content)
        if len(core) < 2:
            return False
        needle = re.escape(core[:6])
        for hit in re.finditer(needle, src_text):
            ctx = src_text[max(0, hit.start() - 60): hit.end() + 60]
            if re.search(r"粤语|广东话|Cantonese|cantonese", ctx):
                return True
        return False
    def repl_tag(m):
        tag, content = m.group(1), m.group(2)
        if tag.upper() == "CANTONESE":
            return m.group(0)
        if _has_canto(content):
            return f"<d>[Cantonese]{content}</d>"
        return m.group(0)
    text = _DIALECT_TAG_RE.sub(repl_tag, text)
    def repl_plain(m):
        content = m.group(1)
        if _has_canto(content):
            return f"<d>[Cantonese]{content}</d>"
        return m.group(0)
    return _DIALECT_PLAIN_RE.sub(repl_plain, text)


def _translate_leftover_cn(text, llm, temperature, seed, ref_contents=None, max_tokens=2048):
    """反推输出兜底：把 <d> 块与引号之外残留的中文翻译成英文（编号批量翻译）。

    <d> 块与引号台词先保护（占位符），只翻译剩余的连续中文片段；
    解析失败/无残留 → 原文返回。"""
    if not re.search(r"[\u4e00-\u9fff]", text):
        return text
    protected = []
    def repl_d(m):
        protected.append(("D", m.group(0)))
        return f" <<<L2E_{len(protected) - 1}>>> "
    t1 = _D_BLOCK_RE.sub(repl_d, text)
    def repl_q(m):
        protected.append(("Q", m.group(0)))
        return f" <<<L2E_{len(protected) - 1}>>> "
    t2 = _QUOTED_CN.sub(repl_q, t1)
    if not re.search(r"[\u4e00-\u9fff]", t2):
        return text
    cn_runs = re.findall(
        r"[\u4e00-\u9fff][\u4e00-\u9fff\uff0c\u3002\uff1f\uff01\uff1a\uff08\uff09\u3001\u2014\u2026\s]*",
        t2)
    cn_runs = [c.strip() for c in cn_runs if c.strip()]
    if not cn_runs:
        return text
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(cn_runs))
    sys_cn = ("Translate each numbered Chinese phrase below into fluent English. "
              "Output ONLY the numbered translations, one per line, e.g. '1. ...' "
              "— no explanations, no extra text.")
    try:
        out = _call_local_llm(
            llm,
            [{"role": "system", "content": sys_cn},
             {"role": "user", "content": numbered}],
            temperature, seed, max_tokens)
        out = (out or "").strip()
        trans = {}
        for line in out.splitlines():
            m = re.match(r"^\s*(\d+)[.)、:：]?\s*(.+)$", line.strip())
            if m:
                idx = int(m.group(1))
                if 1 <= idx <= len(cn_runs):
                    trans[idx] = m.group(2).strip()
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 残留中文翻译失败({type(e).__name__}: {e})，保留原文",
              flush=True)
        return text
    if not trans:
        return text
    for idx in sorted(trans):
        i = idx - 1
        t2 = t2.replace(cn_runs[i], trans[idx], 1)
    for i, (kind, content) in enumerate(protected):
        t2 = t2.replace(f"<<<L2E_{i}>>>", content)
    return t2


def _translate_segment(seg_text, llm, temperature, seed, ref_contents=None, max_tokens=4096):
    masked, protected = _protect_dialogues(seg_text)
    if ref_contents:
        user_content = list(ref_contents) + [{"type": "text", "text": masked}]
        messages = [
            {"role": "system", "content": _TRANSLATE_SYS},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": _TRANSLATE_SYS},
            {"role": "user", "content": masked},
        ]
    try:
        out = _call_local_llm(llm, messages, temperature, seed, max_tokens)
        out = (out or "").strip()
        if out:
            return _ensure_subject_defs(_restore_dialogues(out, protected))
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 翻译失败({type(e).__name__}: {e})，保留原文", flush=True)
    return seg_text


# ---- 整批反推（三段 = 三个独立提示词组，按分段标记分隔输出）----
# 反推模板（fullreference 全参考）是"整片级"的：逐段单独喂时，模板会把一段当整片
# 扩展、把多段内容一起写进每段 → 拼接崩。改为把全部段一次输入，在 user_brief 里
# 明确"N 段 = N 个独立提示词组，以 === SEGMENT N === 标记分隔输出"，LLM 按标记
# 输出 N 个独立六段式，代码层按标记切回 N 段。解析失败 → 降级逐段忠实翻译。
_SEG_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(?:(?:#{1,3}|\*{1,3}|\[)\s*)?(?:[=\-]{2,}\s*)?"
    r"(?:[\u3010]\s*)?(?:SEGMENT\s*(\d+)|[\u6bb5]\s*(\d+))"
    r"(?:\s*[\u3011])?(?:\s*[=\-]{2,}|\s*[:\uff1a])?"
    r"(?:\s*[\]\*#])?\s*\n?",
    re.I)


def _split_segments(text, n):
    """按分段标记（=== SEGMENT N === / --- SEGMENT N --- / SEGMENT N: / 段 N / 【段N】）
    切分，返回恰好 n 段 body 列表；标记不足/空段返回 None。"""
    if not text:
        return None
    ms = []
    for m in _SEG_MARKER_RE.finditer(text):
        num = m.group(1) or m.group(2)
        if num is None:
            continue
        ms.append((int(num), m))
    if len(ms) < n:
        return None
    segs = []
    for i in range(n):
        _, m = ms[i]
        start = m.end()
        end = ms[i + 1][1].start() if i + 1 < len(ms) else len(text)
        body = text[start:end].strip()
        if not body:
            return None
        segs.append(body)
    if len(segs) == n:
        return segs
    # 标记不足但六段式字段齐全 → 按 subject_definitions: 兜底切分
    by_sections = _split_by_sections(text, n)
    if by_sections is not None:
        print(f"[H3PromptSplitTranslate] 分段标记未识别，按 subject_definitions 兜底切 {n} 段",
              flush=True)
        return by_sections
    return None


def _split_by_sections(text, n):
    """按六段式字段 subject_definitions: 切分（LLM 漏了分段标记时兜底）。

    LLM 若把 N 组六段式连续输出（没写 === SEGMENT N === 标记），
    每组都以 subject_definitions: 开头 → 按该字段出现次数切 N 段。"""
    if not text:
        return None
    idxs = [m.start() for m in re.finditer(r"(?m)^\s*subject_definitions\s*:", text)]
    if len(idxs) != n:
        return None
    bodies = []
    for i, st in enumerate(idxs):
        en = idxs[i + 1] if i + 1 < len(idxs) else len(text)
        body = text[st:en].strip()
        if not body:
            return None
        bodies.append(body)
    return bodies


def _ensure_subject_defs(text):
    """反推输出兜底：subject_definitions 为空/过短时，从 detailed_description 提取
    <Picture N> 注入锁定句（The reference image <Picture N> defines the subject's
    facial features, hair style, and body proportions.），不依赖 LLM 自觉。
    """
    if not text:
        return text
    m = re.search(r'^subject_definitions:\s*(.*?)(?=^summary:)', text, re.S | re.M)
    has_sd = bool(m)
    sd_body = m.group(1).strip() if m else ""
    if has_sd and len(sd_body) >= 10:
        return text
    pics = sorted(set(re.findall(r'<Picture\s*(\d+)>', text)), key=lambda x: int(x))
    if not pics:
        return text
    locks = " ".join(
        f"The reference image <Picture {n}> defines the subject's facial features, "
        f"hair style, and body proportions." for n in pics)
    if has_sd:
        return text[:m.start(1)] + " " + locks + text[m.end(1):]
    return "subject_definitions: " + locks + "\n" + text


def _batch_rewrite_segments(segs, llm, temperature, seed, ref_contents=None,
                            seg_seconds=5.0, aspect_ratio="16:9",
                            resolution_mp=1.0, max_tokens=8192, strict=False):
    """整批反推：N 段 = N 个独立提示词组。

    - 三段（或 N 段）合并为一份输入，标记行 '--- SEGMENT N ---' 分隔；
    - user_brief 明确"每个 segment 是独立提示词组，输出恰好 N 个独立六段式，
      以 === SEGMENT N === 标记分隔，禁止合并/串扰/增删段"；
    - 输出按标记切回 N 段；strict=True（fullreference）时逐段校验六段式字段，
      缺字段该段降级忠实翻译；strict=False（通用模式）不强校验；
    - 整批解析失败 → 全部降级逐段忠实翻译（最稳路径）。
    """
    n = len(segs)
    if n <= 0:
        return []
    if n == 1:
        # 单段：退回逐段反推（无跨段问题）
        return [_generic_rewrite_segment(
            segs[0], llm, temperature, seed, ref_contents,
            seg_seconds, aspect_ratio, resolution_mp, max_tokens)]
    joined = "\n\n".join(
        f"--- SEGMENT {i + 1} ---\n{s.strip()}" for i, s in enumerate(segs))
    masked, protected = _protect_dialogues(joined)
    dur = max(2, min(15, int(round(float(seg_seconds or 5.0)))))
    shot_cap = max(1, math.ceil(dur / 3))
    try:
        sys_p = _build_system_prompt("fullreference")
        style = _get_style_contract("fullreference")
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 整批反推模板不可用({e})，降级忠实翻译", flush=True)
        return [_translate_segment(s, llm, temperature, seed, ref_contents, max_tokens)
                for s in segs]
    w, h = _resolve_resolution(aspect_ratio, resolution_mp)
    user_brief = (
        f"DIALOGUE RULES (ABSOLUTE): every <<<DIALOGUE_N>>> placeholder in the input "
        f"is a dialogue line that MUST appear verbatim (exact same words, original "
        f"language) inside <d>...</d> in your output. NEVER invent, translate, "
        f"paraphrase, shorten, or reorder dialogue; never turn placeholders into "
        f"English or drop them.\n"
        f"STORY CONCEPT (may be Chinese): the input below is SPLIT INTO {n} SEGMENTS — "
        f"{n} INDEPENDENT prompt groups (三段代表三个不同的提示词组). Each segment has "
        f"its own subject / scene / action / dialogue.\n"
        f"You MUST output EXACTLY {n} independent H3 full-reference prompts "
        f"(six sections each: subject_definitions / summary / retention_analysis / "
        f"detailed_description / overall_soundscape / non_diegetic_music), ONE per "
        f"segment, IN THE SAME ORDER.\n"
        f"Separate them with the exact marker lines '=== SEGMENT 1 ===', "
        f"'=== SEGMENT 2 ===', ... — one marker line before each prompt.\n"
        f"FORBIDDEN: merge segments into one prompt; mix content between segments; "
        f"add / drop / reorder segments; or put all segments' content into a single "
        f"detailed_description. Each segment's detailed_description describes ONLY "
        f"that segment's own action and dialogue.\n"
        f"Each segment duration: {dur} seconds (H3 hard cap 15s).\n"
f"REFERENCE ROLE SPLIT: a reference image is NOT automatically a video first "
f"frame; when a character\'s look must come from a reference image, LOCK it in "
f"subject_definitions with an explicit sentence such as: \"The reference image "
f"<Picture N> defines her facial features, hair style, and body proportions.\" "
f"(adapt N to the actual tag number, e.g. <Picture 1>). Use <Picture N> as an "
f"independent reference ONLY when the input actually uses that tag.\n"
f"SUBJECT_DEFINITIONS IS REQUIRED AND MUST BE NON-EMPTY: for every character "
f"whose look is locked by a reference image, subject_definitions MUST contain "
f"that locking sentence. NEVER leave subject_definitions empty or 'N/A'.\n"
f"SUMMARY IS REQUIRED: write a real 1-2 sentence plot summary of the segment; "
f"NEVER output placeholders such as 'Segment N, auto-generated summary.'\n"
f"NO CHINESE LEAKAGE: translate ALL non-dialogue Chinese into English; NEVER "
f"leave raw Chinese words in the description \u2014 not even with parenthetical "
f"explanations like (\u5c48\u8fb1 means ...).\n"
        f"VISUAL STYLE: {style}\n"
        f"ASPECT RATIO: {aspect_ratio}  (render canvas {w}x{h}, ~{resolution_mp} MP)\n"
        f"HARD SHOT BUDGET per segment (violating breaks the render pipeline): each "
        f"segment's detailed_description may contain at most {shot_cap} [Shot N] beat "
        f"markers (≈ one MACRO-beat every 2-3 seconds). Each [Shot N] is a 2-4 second "
        f"MACRO-beat describing a real change in action / camera / scene — NEVER "
        f"sub-second or per-frame micro-shots. Simple scenes stay ONE continuous shot.\n"
        f"Do NOT wrap in JSON; output plain text prompts, separated by the marker lines.\n"
        f"--- INPUT SEGMENTS ---\n{masked}"
    )
    if ref_contents:
        user_content = list(ref_contents) + [{"type": "text", "text": user_brief}]
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_brief},
        ]
    try:
        out = _call_local_llm(llm, messages, temperature, seed, max_tokens)
        out = (out or "").strip()
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 整批反推失败({type(e).__name__}: {e})，"
              f"降级逐段忠实翻译", flush=True)
        return [_translate_segment(s, llm, temperature, seed, ref_contents, max_tokens)
                for s in segs]
    bodies = _split_segments(out, n)
    if not bodies:
        _head = out[:200].replace("\n", "\\n")
        print(f"[H3PromptSplitTranslate] 整批反推输出未按分段标记输出"
              f"({len(out)} chars，需要 {n} 段)，降级逐段忠实翻译。"
              f"输出开头: {_head}", flush=True)
        return [_translate_segment(s, llm, temperature, seed, ref_contents, max_tokens)
                for s in segs]
    results = []
    for i, body in enumerate(bodies):
        restored = _force_restore_dialogues(body, protected, segs[i])
        if strict and not _six_section_ok(restored):
            print(f"[H3PromptSplitTranslate] 段 {i} 缺六段式字段，该段降级忠实翻译", flush=True)
            restored = _translate_segment(
                segs[i], llm, temperature, seed, ref_contents, max_tokens)
        restored = _ensure_subject_defs(restored)
        if restored:
            restored = _translate_leftover_cn(
                restored, llm, temperature, seed, ref_contents, max_tokens)
        results.append(restored)
    return results


# ---- 六段式反推（H3 PromptWriter 同款模板，一步替代 翻译→再反推 两步）----
_SIX_SECTIONS = [
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
]


def _six_section_ok(text):
    # 反推输出必须含 H3 六段式全部字段才算合格；缺 → fallback 忠实翻译
    if not text:
        return False
    return all(k in text for k in _SIX_SECTIONS)


def _rewrite_segment(seg_text, llm, temperature, seed, ref_contents=None,
                     seg_seconds=5.0, aspect_ratio="16:9", max_tokens=4096):
    # 把一段六段式中文提示词反推为 H3 PromptWriter 风格的英文六段式。
    # 复用 H3PromptWriter 的 fullreference 反推 system prompt（16 模板之最通用）：
    # subject_definitions / summary / retention_analysis / detailed_description /
    # overall_soundscape / non_diegetic_music 六段式骨架 + 参考图/角色锁定描述，
    # 比忠实直译更贴合 H3 生成契约（用户实测反推效果好）。
    # 引号内中文对话仍用占位符保护（LLM 物理上碰不到台词）。
    # 输出缺六段式字段 → fallback _translate_segment（忠实翻译）→ 再失败原文。
    masked, protected = _protect_dialogues(seg_text)
    try:
        sys_p = _build_system_prompt("fullreference")
        style = _get_style_contract("fullreference")
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 反推模板不可用({e})，降级忠实翻译", flush=True)
        return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)
    dur = max(2, min(15, int(round(float(seg_seconds or 5.0)))))
    user_brief = (
        f"DIALOGUE RULES (ABSOLUTE): every <<<DIALOGUE_N>>> placeholder in the input "
        f"is a dialogue line that MUST appear verbatim (exact same words, original "
        f"language) inside <d>...</d> in your output. NEVER invent, translate, "
        f"paraphrase, shorten, or reorder dialogue; never turn placeholders into "
        f"English or drop them.\n"
        f"STORY CONCEPT (may be Chinese): {masked}\n"
        f"VISUAL STYLE: {style}\n"
        f"TOTAL DURATION: {dur} seconds (H3 hard cap 15s). "
        f"Plan the detailed_description timeline within this budget.\n"
        f"ASPECT RATIO: {aspect_ratio}\n"
    )
    if ref_contents:
        user_content = list(ref_contents) + [{"type": "text", "text": user_brief}]
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_brief},
        ]
    try:
        out = _call_local_llm(llm, messages, temperature, seed, max_tokens)
        out = (out or "").strip()
        if out and _six_section_ok(out):
            out = _force_restore_dialogues(out, protected, seg_text)
            out = _fix_dialect_tags(out, seg_text)
            out = _translate_leftover_cn(out, llm, temperature, seed, ref_contents, max_tokens)
            return out
        if out:
            print(f"[H3PromptSplitTranslate] 反推输出缺六段式字段，降级忠实翻译"
                  f"(输出 {len(out)} chars)", flush=True)
            return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 反推失败({type(e).__name__}: {e})，"
              f"降级忠实翻译", flush=True)
        return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)
    return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)



def _generic_rewrite_segment(seg_text, llm, temperature, seed, ref_contents=None,
                             seg_seconds=5.0, aspect_ratio="16:9",
                             resolution_mp=1.0, max_tokens=4096):
    """通用模式（默认）：与 H3PromptWriter 的 fullreference（H3 通用全参考模版）同款。

    完整复用 writer 的 user_brief 组装（STORY CONCEPT / VISUAL STYLE / TOTAL
    DURATION / ASPECT RATIO+实际分辨率 / HARD SHOT BUDGET 硬镜头预算），system
    prompt 用同一份 fullreference 模板（含写作增强）。

    与旧『六段式反推』（_rewrite_segment）的差别：
      1. user_brief 完整版（含渲染分辨率 + 硬镜头预算），不是手拼精简版；
      2. 不强校验六段式字段——LLM 输出跟随模板自然成型，缺字段也照用
         （用户实测：强校验 + 精简 brief 的反推不如忠实翻译；writer 同款
         完整路径才是被验证过的效果）。

    引号内中文对话仍用占位符保护（LLM 物理上碰不到台词）。
    空输出 / 异常 → fallback 忠实翻译（再失败原文）。
    """
    masked, protected = _protect_dialogues(seg_text)
    dur = max(2, min(15, int(round(float(seg_seconds or 5.0)))))
    shot_cap = max(1, math.ceil(dur / 3))
    try:
        sys_p = _build_system_prompt("fullreference")
        style = _get_style_contract("fullreference")
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 通用模式模板不可用({e})，降级忠实翻译", flush=True)
        return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)
    w, h = _resolve_resolution(aspect_ratio, resolution_mp)
    user_brief = (
        f"STORY CONCEPT (may be Chinese): {masked}\n"
        f"VISUAL STYLE: {style}\n"
        f"TOTAL DURATION: {dur} seconds (H3 hard cap 15s). "
        f"Plan the detailed_description timeline within this budget.\n"
f"REFERENCE ROLE SPLIT: a reference image is NOT automatically a video first "
f"frame; when a character\'s look must come from a reference image, LOCK it in "
f"subject_definitions with an explicit sentence such as: \"The reference image "
f"<Picture N> defines her facial features, hair style, and body proportions.\" "
f"(adapt N to the actual tag number, e.g. <Picture 1>). Use <Picture N> as an "
f"independent reference ONLY when the input actually uses that tag.\n"
f"SUBJECT_DEFINITIONS IS REQUIRED AND MUST BE NON-EMPTY: for every character "
f"whose look is locked by a reference image, subject_definitions MUST contain "
f"that locking sentence. NEVER leave subject_definitions empty or 'N/A'.\n"
f"SUMMARY IS REQUIRED: write a real 1-2 sentence plot summary of the segment; "
f"NEVER output placeholders such as 'Segment N, auto-generated summary.'\n"
f"NO CHINESE LEAKAGE: translate ALL non-dialogue Chinese into English; NEVER "
f"leave raw Chinese words in the description \u2014 not even with parenthetical "
f"explanations like (\u5c48\u8fb1 means ...).\n"
        f"ASPECT RATIO: {aspect_ratio}  (render canvas {w}x{h}, ~{resolution_mp} MP)\n"
        f"HARD SHOT BUDGET (violating breaks the render pipeline): the "
        f"detailed_description may contain at most {shot_cap} [Shot N] beat markers "
        f"for this {dur}s clip (≈ one MACRO-beat every 2-3 seconds; e.g. a 10s clip "
        f"→ ≤6 beats). Each [Shot N] is a 2-4 second MACRO-beat describing a real "
        f"change in action / camera / scene — NEVER a sub-second or per-frame "
        f"micro-shot, and NEVER one [Shot N] per second or per moment. If the "
        f"selected task-mode template explicitly calls for more beats (e.g. "
        f"high-density montage 8-12), follow that template's count; otherwise stay "
        f"within the budget. Write the prompt now as the H3 full-reference format: "
        f"the six sections (subject_definitions, summary, retention_analysis, "
        f"detailed_description, overall_soundscape, non_diegetic_music). Do NOT "
        f"wrap in JSON; output the plain text prompt directly.\n"
    )
    if ref_contents:
        user_content = list(ref_contents) + [{"type": "text", "text": user_brief}]
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_content},
        ]
    else:
        messages = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_brief},
        ]
    try:
        out = _call_local_llm(llm, messages, temperature, seed, max_tokens)
        out = (out or "").strip()
        if out:
            out = _force_restore_dialogues(out, protected, seg_text)
            out = _translate_leftover_cn(out, llm, temperature, seed, ref_contents, max_tokens)
            return out
    except Exception as e:
        print(f"[H3PromptSplitTranslate] 通用模式失败({type(e).__name__}: {e})，"
              f"降级忠实翻译", flush=True)
    return _translate_segment(seg_text, llm, temperature, seed, ref_contents, max_tokens)


# 画面比例 → 比例系数（与 H3 Prompt Writer 一致）
_ASPECT_FACTORS = {
    "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0,
    "21:9": 21 / 9, "4:3": 4 / 3,
}

# 与 H3 PromptWriter (micxin) 完全一致：19 档 0.2~2.0（0.1 步进），
# 像素走 MP 公式 32 对齐（不查表）。
_H3_RES_OPTIONS = [str(x / 10) for x in range(2, 21)]
_H3_RES_MP_SET = {round(x / 10, 2) for x in range(2, 21)}
_DEFAULT_RES_OPTION = "0.4"


def _parse_res_option(v):
    """下拉值（如 "0.4"）→ MP 值。只认 19 档（0.2~2.0，0.1 步进）；
    旧值/表外值（含 0.98、0.25）回退默认 0.4。
    """
    if v is None:
        return float(_DEFAULT_RES_OPTION)
    try:
        val = float(str(v).strip())
    except (TypeError, ValueError):
        print(f"[H3PromptSplitTranslate] 分辨率档位 {v!r} 无法解析，回退默认 0.4", flush=True)
        return float(_DEFAULT_RES_OPTION)
    rv = round(val, 2)
    if rv not in _H3_RES_MP_SET:
        print(f"[H3PromptSplitTranslate] 分辨率档位 {v!r} 不在 0.2~2.0 档位表，回退默认 0.4", flush=True)
        return float(_DEFAULT_RES_OPTION)
    return rv


def _resolve_resolution(aspect_ratio, resolution_mp):
    """与 PromptWriter 一致：MP 公式 32 对齐。
    宽 = sqrt(MP × 比例), 高 = sqrt(MP / 比例)，全部对齐到 32 倍数。
    1.0MP 16:9 特判为官方推荐表值 1376x768（不按公式 1344x736）。
    """
    mp = _parse_res_option(resolution_mp)
    if aspect_ratio == "16:9" and abs(mp - 1.0) < 1e-9:
        return 1376, 768
    a = _ASPECT_FACTORS.get(aspect_ratio, 16 / 9)
    mp_px = mp * 1_000_000.0
    w = max(32, int(round((mp_px * a) ** 0.5 / 32.0)) * 32)
    h = max(32, int(round((mp_px / a) ** 0.5 / 32.0)) * 32)
    return w, h


def _resolve_length(duration_seconds):
    """秒 → H3 帧数，对齐 length % 17 == 5（与 writer 一致）。"""
    f = max(5, round(float(duration_seconds) * 24))
    f = f + (5 - (f % 17)) % 17
    return int(f)


class H3PromptSplitTranslate(io.ComfyNode):
    """拆分（≤4 段）+ 翻译（对话保留）+ 参考图过图 + 宽高长输出。"""
    # OUTPUT_NODE：让输出进入前端 executed 事件 / history，
    # 供 h3_prompt_split_report.js 内嵌 report 显示（不落盘、不影响下游）。
    OUTPUT_NODE = True

    @classmethod
    def define_schema(cls):
        ggufs = _list_llm_files(include_mmproj=False) or [""]
        mmprojs = _list_llm_files(mmproj_only=True) or ["", "None"]
        return io.Schema(
            node_id="H3PromptSplitTranslate",
            display_name="H3 Prompt Split+Translate (micxin)",
            category="H3 helper/micxin",
            description=(
                "修复+拆分+翻译一体：raw_text 原始提示词 → 内部修复为六段式 JSON（fixed_json "
                "预览）→ 拆成最多 4 段，每段把非对话中文翻译成英文，<d> 标签和引号内对话保留"
                "原文。prompt_0..3 直接接 H3 Clip Chain (micxin) 的 segment_prompts。\n"
                "参考图过图：自动同步 H3 R2VA AIO(micxin) 的图片路径，本地 Qwen3-VL 看图"
                "翻译，模型知道参考图内容（不是盲写文生）。\n"
                "宽高长输出：aspect_ratio/resolution_mp/duration_seconds → width/height/length，"
                "直接接 AIO 的宽高长输入口（与 H3 PromptWriter 相似）。"
            ),
            inputs=[
                io.String.Input(
                    "raw_text",
                    multiline=True,
                    default="",
                    tooltip=(
                        "原始提示词（中文/自然语言）。内部自动修复为六段式 JSON "
                        "，再拆分翻译。\n"
                        "对话写法：S1说：\"中文对话\" 或 <d>中文对话</d>（都会保留原文）。\n"
                        "也可直接粘贴已修复的六段式 JSON（内部保留原结构，只做字段补全）。"
                    ),
                ),
                io.Int.Input(
                    "expected_segments",
                    default=0,
                    min=0,
                    max=32,
                    tooltip="期望段数。0=自动识别（修复 JSON 时用）。",
                ),
                # ---- 参考图过图（与 H3 R2VA AIO 结合）----
                io.String.Input(
                    "_aio_ref_paths",
                    multiline=True,
                    default="",
                    extra_dict={"hidden": True},
                    tooltip=("隐藏：JS 自动从 H3 R2VA AIO(micxin) 同步图片路径。"
                             "Python 端加载这些图给本地 Qwen3-VL 看图翻译。"),
                ),
                # ---- 宽高长（与 H3 PromptWriter 相似，接 AIO）----
                io.Combo.Input("aspect_ratio", options=list(_ASPECT_FACTORS.keys()), default="16:9",
                               tooltip=("画面比例。16:9=严格按官方表 14 档精确分辨率；"
                                        "9:16/1:1/21:9/4:3=按表格同档 MP 公式 32 对齐换算"
                                        "（官方表只有 16:9，其他画幅无法查表）。")),
                io.Combo.Input("resolution_mp", options=_H3_RES_OPTIONS,
                               default=_DEFAULT_RES_OPTION,
                               tooltip=("渲染分辨率档位（0.2~2.0，19 档，与 PromptWriter 一致）。\n"
                                        "16:9 时严格对齐官方表 14 档精确分辨率"
                                        "（如 1.0=1376x768）；9:16/1:1/21:9/4:3 按该档 MP"
                                        "公式 32 对齐换算。")),
                io.Float.Input("duration_seconds", default=5.0, min=1.0, max=60.0, step=0.5,
                               tooltip="每段时长（秒）。输出 length 自动对齐 H3 帧约束（length % 17 == 5）。"),
                # ---- LLM 参数 ----
                io.Combo.Input("backend", options=["Local GGUF", "HTTP"], default="Local GGUF",
                               tooltip="只用本地 GGUF（默认）。HTTP 仅当你自己起了 llama.cpp server 时才切。"),
                io.Combo.Input("gguf_name", options=ggufs,
                               default="",
                               tooltip="本地 GGUF 模型（ComfyUI/models/LLM 下）。过图需带 mmproj 的 VLM（如 Qwen3-VL）。"),
                io.Combo.Input("mmproj_name", options=mmprojs, default="",
                               tooltip="VLM 投影层（看图用）。纯文本翻译可留空。"),
                io.Int.Input("context_size", default=8192, min=512, max=131072,
                             tooltip="上下文窗口。"),
                io.Int.Input("n_gpu_layers", default=-1, min=-1, max=200,
                             tooltip="卸载多少层到 GPU。-1=全部。"),
                io.Float.Input("temperature", default=0.4, min=0.0, max=2.0, step=0.05,
                               tooltip="翻译温度，建议 0.3-0.5。"),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFF, tooltip="随机种子。0=不固定。"),
                io.Boolean.Input("keep_loaded", default=False,
                                 tooltip="ON=模型常驻（占用显存）；OFF=跑完即卸载（默认，省显存）。"),
                io.Boolean.Input("bypass_llm", default=False,
                                 tooltip=("ON=绕过 LLM：不加载模型、不翻译，prompt_0..3 直接输出"
                                          "修复后的原始段文本。第二次重跑（只想再出视频）时打开，"
                                          "省显存省时间。")),
                io.String.Input("llm_base_url", default="http://127.0.0.1:8080/v1/chat/completions",
                                tooltip="backend=HTTP 时使用。"),
                io.String.Input("model", default="", tooltip="backend=HTTP 时的模型名。"),
                io.Combo.Input(
                    "rewrite_mode",
                    options=["忠实翻译", "H3 通用全参考模版", "fullreference"],
                    default="忠实翻译",
                    tooltip=("提示词模式：\n"
                             "· 忠实翻译（默认）= 只翻译非对话部分，保留原结构（多段时最稳）。\n"
                             "· H3 通用全参考模版 = 整批反推：N 段作为 N 个独立"
                             "提示词组整体输入，用户提示词里明确「三段 = 三个不同的"
                             "提示词组，以分段标记 === SEGMENT N === 分隔输出」，代码层按标记切回 N 段，"
                             "避免每段写进全部内容；解析失败自动降级逐段忠实翻译。\n"
                             "· fullreference = 旧『六段式反推』：手拼精简 brief + 强制"
                             "校验六段式字段，缺字段降级忠实翻译。"),
                ),
                # ---- 内嵌 report 显示（JS 从 executed 事件写入此槽，节点内预览）----
                io.String.Input("report_inline", multiline=True, default="",
                                tooltip="内嵌 report：执行后自动写入分段翻译/修复摘要（无需外接 report 输出）。"),
            ],
            outputs=[
                io.String.Output(id=f"prompt_{i}", display_name=f"prompt_{i}")
                for i in range(MAX_SEGMENTS)
            ] + [
                io.Int.Output(id="width", display_name="width"),
                io.Int.Output(id="height", display_name="height"),
                io.Int.Output(id="length", display_name="length"),
                io.String.Output(id="fixed_json", display_name="fixed_json"),
                io.String.Output(id="report", display_name="report"),
            ],
        )

    @classmethod
    def execute(cls, **kwargs):
        preview_json = ""
        raw_text = (kwargs.get("raw_text") or "").strip()
        if raw_text:
            fixed, fix_status = fix_prompt(
                raw_text, int(kwargs.get("expected_segments", 0) or 0))
            raw = fixed
            preview_json = fixed
            print(f"[H3PromptSplitTranslate] 内嵌修复: {fix_status}", flush=True)
        else:
            raise ValueError(
                "H3PromptSplitTranslate: 没有输入。请在 raw_text 填原始提示词"
                "（也支持直接粘贴已修复的六段式 JSON，内部会保留）。")
        segs = _split_to_prompt_list(raw)
        if not segs:
            raise ValueError(
                "H3PromptSplitTranslate: 没有可拆分的提示词。请在 raw_text 填写原始提示词，"
                "或把已修复 JSON 填入 prompts_json。")
        if len(segs) > MAX_SEGMENTS:
            print(f"[H3PromptSplitTranslate] 输入 {len(segs)} 段 > 上限 {MAX_SEGMENTS}，"
                  f"只取前 {MAX_SEGMENTS} 段。", flush=True)
            segs = segs[:MAX_SEGMENTS]

        backend = kwargs.get("backend", "Local GGUF") or "Local GGUF"
        gguf_name = kwargs.get("gguf_name", "") or ""
        mmproj_name = kwargs.get("mmproj_name", "") or ""
        context_size = int(kwargs.get("context_size", 8192) or 8192)
        n_gpu_layers = int(kwargs.get("n_gpu_layers", -1) or -1)
        temperature = float(kwargs.get("temperature", 0.4) or 0.4)
        seed = int(kwargs.get("seed", 0) or 0)
        keep_loaded = bool(kwargs.get("keep_loaded", False))
        bypass = bool(kwargs.get("bypass_llm", False))

        # ---- 参考图过图：从 _aio_ref_paths 加载图（JS 已从 AIO 同步）----
        ref_contents = None
        aio_paths = (kwargs.get("_aio_ref_paths") or "").strip()
        if aio_paths and not bypass:
            try:
                ref_images, n_imgs, _ = _load_image_tensor_from_paths(aio_paths)
                if ref_images is not None:
                    ref_contents = _images_to_contents(ref_images)
                    print(f"[H3PromptSplitTranslate] 参考图过图: {n_imgs} 张（来自 AIO）", flush=True)
            except Exception as e:
                print(f"[H3PromptSplitTranslate] 参考图加载失败: {e}", flush=True)

        llm = None
        if backend == "Local GGUF" and not bypass:
            llm = _load_local_llm(gguf_name, mmproj_name, n_gpu_layers, context_size)

        rewrite_mode = kwargs.get("rewrite_mode", "忠实翻译") or "忠实翻译"
        # 兼容旧工作流值：『六段式反推』→ fullreference（旧命名）；通用全参考模版保留原值走整批反推
        if rewrite_mode == "六段式反推":
            rewrite_mode = "fullreference"
        elif rewrite_mode == "H3 通用全参考模版 (默认)":
            rewrite_mode = "H3 通用全参考模版"
        aspect_ratio = kwargs.get("aspect_ratio", "16:9") or "16:9"
        duration_seconds = float(kwargs.get("duration_seconds", 5.0) or 5.0)
        seg_seconds = duration_seconds / max(1, len(segs))
        outputs = {}
        report = []
        # 反推类模式：整批反推（N 段 = N 个独立提示词组，
        # 按 === SEGMENT N === 标记输出，代码层切回 N 段）
        if not bypass and rewrite_mode in ("H3 通用全参考模版", "H3 通用全参考模版 (默认)", "fullreference"):
            print(f"[H3PromptSplitTranslate] 整批反推: rewrite_mode={rewrite_mode!r}, {len(segs)} 段",
                  flush=True)
            strict = (rewrite_mode == "fullreference")
            out_list = _batch_rewrite_segments(
                segs, llm, temperature, seed, ref_contents,
                seg_seconds=seg_seconds, aspect_ratio=aspect_ratio,
                resolution_mp=_parse_res_option(kwargs.get("resolution_mp", _DEFAULT_RES_OPTION)),
                strict=strict)
            for i, out_text in enumerate(out_list):
                outputs[f"prompt_{i}"] = out_text
                changed = (f"反推[{rewrite_mode}]" if out_text != segs[i] else "原样")
                report.append(f"seg {i}: {changed}")
                print(f"[H3PromptSplitTranslate] seg {i}: {changed}", flush=True)
        else:
            for i, s in enumerate(segs):
                if bypass:
                    out_text = s
                    changed = "绕过LLM(原样)"
                elif rewrite_mode == "忠实翻译":
                    out_text = _translate_segment(s, llm, temperature, seed, ref_contents)
                    changed = "翻译" if out_text != s else "原样"
                else:
                    out_text = _translate_segment(s, llm, temperature, seed, ref_contents)
                    changed = "翻译(兜底)" if out_text != s else "原样"
                outputs[f"prompt_{i}"] = out_text
                report.append(f"seg {i}: {changed}")
                print(f"[H3PromptSplitTranslate] seg {i}: {changed}", flush=True)

        if backend == "Local GGUF" and not keep_loaded and not bypass:
            _unload_local()

        # ---- 对话转 H3 <d> 块（粤语/中文台词进 <d>，模型按对白处理）----
        for i in range(MAX_SEGMENTS):
            if outputs.get(f"prompt_{i}"):
                outputs[f"prompt_{i}"] = _quotes_to_d_blocks(
                    outputs[f"prompt_{i}"], seg_seconds)
                if i < len(segs):
                    outputs[f"prompt_{i}"] = _fix_dialect_tags(
                        outputs[f"prompt_{i}"], segs[i])

        # ---- 宽高长（与 writer 一致，接 AIO）----
        w, h = _resolve_resolution(
            aspect_ratio, kwargs.get("resolution_mp", _DEFAULT_RES_OPTION))
        length = _resolve_length(duration_seconds)

        result = [outputs.get(f"prompt_{i}", "") for i in range(MAX_SEGMENTS)]
        result.append(w)
        result.append(h)
        result.append(length)
        result.append(preview_json)  # fixed_json：修复后的六段式 JSON 预览
        report_text = "\n".join(report)  # report 放最后
        result.append(report_text)
        print(f"[H3PromptSplitTranslate] 完成 {len(segs)} 段拆分+翻译 | {w}x{h} len={length}",
              flush=True)
        # ui 包装：report 随 executed 事件下发，V3 前端在节点底部显示文本
        # （与 ShowText 的 {"text": (…,)} 结构一致；不落盘、不影响下游数据流）
        return io.NodeOutput(*result, ui={"text": (report_text,)})


NODE_CLASS_MAPPINGS = {"H3PromptSplitTranslate": H3PromptSplitTranslate}
NODE_DISPLAY_NAME_MAPPINGS = {"H3PromptSplitTranslate": "H3 Prompt Split+Translate (micxin)"}
