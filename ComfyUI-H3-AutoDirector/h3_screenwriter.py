# -*- coding: utf-8 -*-
"""H3Screenwriter (micxin) — concept -> H3 official skill / full-reference prompt.

Merged with h3-prompt-writing (micxin) 2026-08-20:
  - Keeps the H3 Screenwriter UI (concept box, setup widgets, backend selector,
    @-mention dropdown, bypass LLM, optional vision input via ref_images).
  - Uses the 16 built-in micxin2025 task-mode system-prompt templates.
  - Outputs a SINGLE H3 official full-reference prompt in six sections:
    subject_definitions / summary / retention_analysis / detailed_description /
    overall_soundscape / non_diegetic_music.
  - No external llama_cpp_instruct node required; the LLM call is internal.
  - Custom skill templates can be installed/deleted via H3SkillManager.

Output contracts
-------------------------------------------------------
  prompt -> H3 official skill / full-reference prompt string.
            Wire directly to MiniMaxH3ReferenceToVideo.prompt.
  width / height / length -> render canvas pixels + frame count, fed to
                              Ref2VA (replaces Resolution Selector + Duration).

  bypass_llm: skip the VL call and use the text pasted in the top concept
  box as the final prompt (old multi-shot JSON arrays are joined for backward
  compatibility).

H3 prompt rules (per MiniMax H3 reference guide + micxin2025 six-section spec):
  - Narrative text in ENGLISH (H3 follows English narrative).
  - Dialogue / lyrics / onscreen text stay in ORIGINAL language inside
    <d>[Language] ... </d> and are NEVER translated.
  - 16 task modes cover full-reference, I2VA, FL2VA, action-transfer,
    voice-clone, dual-dialogue, and 8 style overlays.
  - Reference ceilings: images <=9, video <=3, audio <=3, total files <=12.
"""
import base64
import io
import json
import math
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
# System prompt — English meta-instructions; narrative output English;
# dialogue/lyrics/onscreen text keep original language inside <d>[Language].
# ---------------------------------------------------------------------------
H3_SYSTEM_PROMPT = r"""You are the screenwriter / prompt director for MiniMax H3, an open general-purpose multimodal video model that generates video AND native stereo audio together (4-15 seconds, up to 2K).

You turn the user's story CONCEPT (which may be written in Chinese or English) into a structured multi-shot H3 screenplay. You output ONLY a JSON array of strings — one fully self-contained H3 prompt per shot. Do NOT wrap it in markdown code fences. Output nothing but the JSON.

ABSOLUTE OUTPUT RULE: Your very first character MUST be "[" and your last character MUST be "]". No thinking process, no preamble, no explanation, no "Here is", "Sure", "Okay", "Let me", "First", "I will", or any other prose. If you reason, do it silently — never write it out. This is consumed by an automated pipeline that breaks on ANY non-JSON text, so a single stray word before or after the array fails the whole job.

# Hard rules (these are what make H3 follow)
0. PROHIBITED OPENINGS: never start your reply with "Here", "Sure", "Okay", "I ", "Let me", "First", "Thinking", "Below", "```", or any sentence. The reply begins with "[" and ends with "]". Nothing else.
1. NARRATIVE LANGUAGE = ENGLISH. H3 is trained on English narrative prompts; Chinese/other-language narrative is ignored. Write every descriptive sentence in English.
2. DIALOGUE / LYRICS / ON-SCREEN TEXT keep their ORIGINAL language, wrapped verbatim in <d>[Language] ... </d> (e.g. Chinese -> <d>[Chinese]... </d>, Cantonese in Hanzi -> <d>[Cantonese]... </d>, English -> <d>[English]... </d>). NEVER translate the words inside <d>. If there is no dialogue, omit it.
3. EACH SHOT IS SELF-CONTAINED. Restate the persistent scene, the subject's appearance, and the visual style VERBATIM in every shot's prompt (the downstream multishot node chains shots by matching the repeated text, so drift is prevented by repetition — do not "refer back" to shot 1). This is the single most important rule for seamless chaining.
4. ONE CLEAR PHYSICAL ACTION per shot. Keep 2-3 main actions across the whole clip; leave breathing room, do not fill every second.
5. CAMERA: name one move with type + amplitude + speed (e.g. "slow dolly in, small amplitude, at a calm pace", "locked off static wide shot, no push-in, no cuts"). One camera idea per shot.
6. AUDIO (overall_soundscape): 1-4 English sentences of physical / ambient sound (rain, footsteps, room tone, fabric, impacts). Do NOT repeat the dialogue here.
7. NON-DIEGETIC MUSIC: 1-3 English sentences of audience-only score — instruments, tempo, dynamics only, NO abstract emotion words. If none, say "N/A".
8. ON-SCREEN TEXT: if any readable text must appear, write it in double quotes, exactly as it should read.
9. NEGATIVE LIST: a short line of things to refuse (e.g. "no soft dissolves, no subtitles, no watermark, no extra text, do not add Chinese captions").
10. TIMESTAMPS: [Shot 1] has NO timestamp. Later shots: "[Shot N] At MM:SS.mmm, <action>" with timestamps strictly increasing and inside the shot's duration.

# Per-shot prompt shape (write every shot like this)
<Visual style contract: e.g. "Cinematic live-action" / "2D-animated" / "3D CG" / "claymation" / "watercolor" / "vintage film">. <One or two English sentences establishing the persistent scene: location, time of day, lighting, the subject's exact appearance and position. RESTATE this in every shot.>

[Shot 1] <opening action + camera>. <physical sound>. <Speaker (S1) says: <d>[Language] verbatim line</d> if any>.
[Shot 2] At MM:SS.mmm, <next action + camera>. <physical sound>.
...
overall_soundscape: <ambience across the whole clip>
non_diegetic_music: <score>  (or N/A)
onscreen text: "<exact text>"  (or N/A)
negative: <refusals>

# Continuity across the whole video
- Same character keeps the SAME (S1)/(S2) speaker id and the SAME described appearance in every shot.
- Keep lighting, wardrobe, and location consistent unless the story explicitly changes them.
- Total length = the requested TOTAL DURATION in seconds (hard cap 15s for H3 single clip). Compose the requested NUMBER OF SHOTS within that budget. Do not exceed the budget.

# Reference materials (HIGHEST PRIORITY — prevents hallucination)
The user MAY reference assets they loaded into the material loaders by typing "@"
in the concept box, which inserts canonical tags: <Picture N> (images),
<Video N> (videos), <Audio N> (audio clips), numbered from 1. These tags are
the ONLY way you know reference material exists — you CANNOT see the files.
- If the concept contains <Picture N> / <Video N> / <Audio N> tags, they map
  POSITIONALLY to the user's loaded assets (Picture 1 = first loaded image,
  Video 1 = first loaded video, Audio 1 = first loaded audio clip). Use exactly
  those tags, never renumber or invent new ones.
- Weave each referenced tag naturally into the shots where it applies, e.g.
  "the subject's exact appearance comes from <Picture 1>", "the camera move and
  edit follow <Video 1>", "the soundtrack is <Audio 1>". Restate the same tag in
  EVERY shot that uses that asset (shots are self-contained).
- Mode guidance for the H3 model (it picks the final mode from the tags): only
  <Picture N> present -> reference / image-to-video; <Video N> present -> video
  editing / continuation; <Audio N> present -> audio reuse / reference. Mix as the
  concept implies.
- ABSOLUTELY FORBIDDEN to add <Picture N> / <Video N> / <Audio N> labels, or any
  reference-style task type, that the concept did NOT include. NEVER fabricate
  assets. If the concept has NO reference tags, write a pure text-to-video (T2VA)
  screenplay with no reference labels at all.

# Output format (ONLY this, no prose, no fences)
# Exactly one JSON array of strings. Example for 2 shots:
[
  "Cinematic live-action. A small orange cat shelters under the neon awning of a 24h convenience store on a rainy night, the cat drenched and shivering, warm light spilling onto wet pavement. [Shot 1] The cat looks up as the glass door opens; a clerk kneels with a small dried fish. Slow static wide shot, locked off, no push-in. Rain patters on the awning, distant traffic hum. overall_soundscape: steady rain, soft jingle of the door, muffled city rumble. non_diegetic_music: tender solo piano, slow tempo, quiet. onscreen text: N/A. negative: no subtitles, no watermark, no extra text.",
  "Cinematic live-action. A small orange cat shelters under the neon awning of a 24h convenience store on a rainy night, the cat now drying under a paper towel, warm light spilling onto wet pavement. [Shot 2] At 00:04.000, the cat accepts the dried fish and blinks, the clerk smiles. Gentle medium dolly in, small amplitude, calm pace. Rain continues outside, paper rustle. overall_soundscape: rain, soft paper rustle, contented small purr. non_diegetic_music: solo piano continues, slightly warmer. onscreen text: N/A. negative: no subtitles, no watermark, no extra text."
]
# Your reply is that array and nothing else.
"""

# ---------------------------------------------------------------------------
# Appended to micxin2025's task-mode system prompt so the LLM still emits the
# JSON-array-of-shots contract H3Screenwriter's parser expects. Produces a
# single continuous multi-shot prompt (h3_script, blank-line joined) that the
# official MiniMaxH3ReferenceToVideo.prompt consumes, while gaining micxin2025's
# six-section format depth + task modes. (The community Multishot plugin instead
# reads prompts_json, a per-shot JSON list, to loop separate generations.)
# ---------------------------------------------------------------------------
H3_JSON_APPENDIX = r"""
# Output contract for THIS pipeline (APPENDED — highest priority for the wrapper)
You MUST output ONLY a JSON array of strings and nothing else. No markdown
fences, no "thinking", no preamble, no explanation. Your very first character
is "[" and your last character is "]".

- Each array element is the COMPLETE prompt for ONE shot, written in the
  six-section / integrated format the mode above specifies. For single-subject,
  first-frame, or action-transfer modes the array has exactly ONE element.
- Restate the persistent scene, the subject's exact appearance, and the visual
  style VERBATIM in every shot (shots are self-contained; do NOT "refer back"
  to an earlier shot).
- If the concept contains <Picture N> / <Video N> / <Audio N> tags, weave them
  POSITIONALLY (Picture 1 = first loaded image, Video 1 = first loaded video,
  Audio 1 = first loaded audio clip) and restate every used tag in every shot
  that uses that asset. NEVER invent a reference tag the concept did NOT include.
- ABSOLUTE: the reply begins with "[" and ends with "]". Any stray word before
  or after the array breaks the automated pipeline.

# HARD FORMAT RULES (violating these breaks the automated pipeline)
1. ABSOLUTELY FORBIDDEN to emit a "subject_definitions", "definitions",
   "subjects", or any other metadata / key-value block. The top-level array
   contains ONLY finished shot prompts — never field labels.
2. Write each shot prompt as a SINGLE LINE. NEVER put a literal newline
   character inside a string; if you need a line break, use the space
   character. (Real newlines inside JSON strings make the output invalid.)
3. Do NOT wrap the array in ```json fences or any other markdown.
4. Your first character MUST be "[" and your last character MUST be "]".
"""


# ---------------------------------------------------------------------------
# When the model ignores the JSON-only contract, we feed its bad output back
# with a short, blunt correction and ask again. Models obey "now just give me
# valid JSON" far better after seeing their own mistake. This is the single
# most reliable fix for chatty / uncensored local models.
# ---------------------------------------------------------------------------
REPAIR_PROMPT = (
    "Your previous reply was NOT valid JSON the pipeline could use. The two "
    "most common mistakes: (1) you wrote a 'subject_definitions' (or similar) "
    "metadata block instead of ready-to-use shot prompts, or (2) you put "
    "literal newlines inside the strings. Reply with ONLY a JSON array of "
    "strings where each element is a COMPLETE shot prompt written on a SINGLE "
    'line, e.g. ["shot 1 prompt here", "shot 2 prompt here"]. No thinking, no '
    "explanation, no code fences, no metadata blocks. Your first character must "
    "be '[' and your last must be ']'.")


# ---------------------------------------------------------------------------
# JSON extraction. The model sometimes wraps the array in ```json fences,
# adds leading/trailing prose, or (worst of all) drops a stray "[" in its
# chatter before the real JSON array. The old greedy [.*] regex grabbed from
# the first "[" to the last "]" and produced "[prose...]" -> JSONDecodeError
# at char 1. We now scan for a *balanced* bracket pair instead, which ignores
# any stray brackets that appear inside prose.
# ---------------------------------------------------------------------------
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_think(text):
    if not text:
        return text
    # 成对思考块（含 <|think|> / <thinking> / <|thinking|> 变体）
    out = re.sub(
        r"<(?:/)?(?:think|thinking|\|think\||\|thinking\|)[^>]*>[\s\S]*?"
        r"<(?:/)?(?:think|thinking|\|think\||\|thinking\|)[^>]*>",
        "", text, flags=re.IGNORECASE)
    # 剥残留的未闭合思考标签（如模型输出被 max_tokens 截断）
    out = re.sub(r"<(?:/)?(?:think|thinking|\|think\||\|thinking\|)[^>]*>",
                 "", out, flags=re.IGNORECASE)
    # 思考块剥离后常留下大片空行（Split 按空行拆段会被误拆）
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _clean_text(text):
    """Drop BOM and non-printable control chars (keep newline/tab/space)."""
    if not text:
        return ""
    text = text.replace("\ufeff", "")
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\n\r\t " or 32 <= o:
            out.append(ch)
        else:
            out.append(" ")
    return "".join(out).strip()


def _slice_balanced(text, start, open_ch, close_ch):
    """From `start` (which must be `open_ch`), return the substring up to its
    *matching* `close_ch`, respecting string literals and backslash escapes.
    None if the span is unbalanced / runs off the end."""
    if not (0 <= start < len(text)) or text[start] != open_ch:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _balanced_spans(text, open_ch, close_ch):
    """Yield every balanced substring starting at each `open_ch` in `text`
    (e.g. every [...] or {...}). Trying them all lets us skip stray brackets
    that appear inside prose and land on the real JSON."""
    for i, ch in enumerate(text):
        if ch == open_ch:
            span = _slice_balanced(text, i, open_ch, close_ch)
            if span is not None:
                yield span


def _extract_shots(text):
    """Return a list[str] of per-shot prompts from the LLM response.

    Tries, in order: direct parse -> fenced block -> first balanced JSON array
    -> first balanced JSON object (which may carry a 'prompts' list) -> split
    on the '---' shot separator as a last resort. Raises a descriptive error
    (with a snippet of the raw response) if nothing works, instead of a bare
    JSONDecodeError.
    """
    text = _clean_text(_strip_think(text or ""))
    if not text:
        raise ValueError("H3Screenwriter: the LLM returned empty content.")

    candidates = [text]
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    # Try every [...] and {...} span; the one that is genuinely valid JSON
    # wins, so stray brackets in prose are skipped automatically.
    for span in _balanced_spans(text, "[", "]"):
        candidates.append(span)
    for span in _balanced_spans(text, "{", "}"):
        candidates.append(span)

    seen = set()
    ordered = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    last_err = None
    for cand in ordered:
        data = None
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as e:
            # Second chance: some uncensored local models frequently
            # emit REAL newlines (or other control characters) inside JSON
            # string literals, which standard json.loads rejects with
            # "Expecting value". strict=False tolerates those control chars so
            # we can still salvage a string-array response instead of
            # hard-failing. We try the strict parse first so genuinely broken
            # spans still surface a clear error.
            try:
                data = json.loads(cand, strict=False)
            except json.JSONDecodeError as e2:
                last_err = e2
                continue
        if isinstance(data, dict):
            for k in ("prompts", "shots", "screenplay", "scripts",
                      "output", "result", "data"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
        if isinstance(data, list):
            out = []
            for item in data:
                if isinstance(item, str):
                    s = item.strip()
                    # Some models (esp. uncensored Qwen3-VL) occasionally emit
                    # a "subject_definitions:" metadata block as the first (or
                    # only) array element instead of a real, ready-to-use shot
                    # prompt. Strip that label so the remaining description text
                    # can still be salvaged as a (degraded) shot rather than
                    # feeding the literal string "subject_definitions:" to H3.
                    if re.match(r'^["\']?subject_definitions["\']?\s*[:\-]?\s*',
                                s, re.IGNORECASE):
                        s = re.sub(
                            r'^["\']?subject_definitions["\']?\s*[:\-]?\s*',
                            '', s, flags=re.IGNORECASE).strip()
                    if s:
                        out.append(s)
                elif isinstance(item, dict):
                    for k in ("prompt", "text", "shot", "description",
                              "content", "h3_prompt"):
                        if isinstance(item.get(k), str):
                            out.append(item[k].strip())
                            break
            out = [s for s in out if s]
            if out:
                return out

    # last resort: the node's own "---"-separated shot format
    if "\n---\n" in text:
        parts = [b.strip() for b in text.split("\n---\n") if b.strip()]
        if parts:
            return parts

    head = text[:400].replace("\n", "\\n")
    raise ValueError(
        "H3Screenwriter: could not parse a JSON array of shots from the LLM "
        f"response (last JSON error: {last_err}). Response head: {head!r}")


# ---------------------------------------------------------------------------
# Local GGUF backend — load the model INSIDE ComfyUI via llama-cpp-python, so
# the node needs NO external server (no standalone llama-server). This
# mirrors the proven "Llama-cpp Instruct" node's loader: same Llama() params
# + Qwen3VLChatHandler(mmproj, force_reasoning=False) for correct Qwen3-VL
# chat formatting and thinking OFF. The loaded instance is cached and can be
# unloaded after each script to free VRAM for H3.
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


def _default_mmproj():
    return ""






# 方言注入（writer 输出后处理）：粤语自动/标记/强制 → <d>[Cantonese] 台词</d>


def _detect_dialogue_language(text):
    """检测对话语言，返回 H3 标准语言标记（Chinese/English/Japanese/Korean）。"""
    import re as _re
    if _re.search(r'[\u4e00-\u9fff]', text):
        return 'Chinese'
    if _re.search(r'[\u3040-\u30ff]', text):
        return 'Japanese'
    if _re.search(r'[\uac00-\ud7af]', text):
        return 'Korean'
    return 'English'


_TEMPLATE_LEAK_MARKS = ("<persistent", "<action + camera", "<ambience>",
                        "Cinematic live-action. <")


def _translation_failed(original, translated, n_dlg):
    """翻译模式输出异常判定：模板泄漏 / 没翻译 / 空行块数与输入不一致。

    n_dlg>0 时输出含中文对话是正常的，跳过中文占比检查。
    """
    if not translated or not translated.strip():
        return True
    low = translated.lower()
    if any(m in low for m in _TEMPLATE_LEAK_MARKS):
        return True
    if n_dlg == 0:
        cjk = sum(1 for ch in translated if "\u4e00" <= ch <= "\u9fff")
        if cjk / max(1, len(translated)) > 0.25:
            return True
    try:
        in_blocks = _split_concept_segments(original)
        out_blocks = _split_concept_segments(translated)
        if in_blocks and len(in_blocks) != len(out_blocks):
            return True
    except Exception:
        pass
    return False


# v10.4: 行内对话标记——兼容"场景描述 + 她说：「台词」"同行格式
# （_tag_dialogue 要求整行为"说话者：台词"，混排行会被漏掉，台词以裸引号
#   进入 user_brief，弱模型易翻译/丢弃/编造。这里把行内台词预打 <d> 标签。）
_INLINE_DLG_RE = None


def _tag_inline_dialogue_lines(text):
    """把行内 说话者：「台词」 片段标记为 <d>[语言] 台词</d>。

    返回 (标记后文本, 台词原文列表)。不要求整行纯对话。
    例：'镜头一：女主锁门。她说：「下雨了，真冷。」'
        -> '镜头一：女主锁门。她说：<d>[Chinese] 下雨了，真冷。</d>'
    """
    import re as _re
    global _INLINE_DLG_RE
    if _INLINE_DLG_RE is None:
        _INLINE_DLG_RE = _re.compile(
            r'([^：:""\n]{1,15}?)\s*[：:]\s*["“「]([^”"」\n]{1,200}?)["”」]')
    if not text:
        return text, []
    contents = []

    def _repl(m):
        sp = m.group(1).strip()
        content = m.group(2).strip()
        if not content:
            return m.group(0)
        lang = _detect_dialogue_language(content)
        contents.append(content)
        return '%s：<d>[%s] %s</d>' % (sp, lang, content)

    out = _INLINE_DLG_RE.sub(_repl, text)
    return out, contents


def _protect_dialogue(text):
    """把引号内对话与 <d> 标签台词替换为 {{DLG_N}} 占位符（代码级对话保护）。

    LLM 翻译/转换时不会翻译占位符；输出后 _restore_dialogue 还原原文。
    覆盖 <d>[语言] ... </d> 整块（用户概念里已写好的 H3 格式台词——
    原样保护，LLM 不可能改语言标记/加前缀/重写），以及「」『』“” 与英文双引号。
    返回 (保护后文本, 对话列表)。
    """
    dlg = []
    def _repl(m):
        dlg.append(m.group(0))
        return "{{DLG_%d}}" % (len(dlg) - 1)
    # 1) <d> 标签整块（保留原语言标记与原文，还原时原样写回）
    out = re.sub(r'<d>(?:\[[^\]]*\])?\s*.*?</d>', _repl, text, flags=re.S)
    # 2) 引号内对话
    out = re.sub(r'[「『“”"].+?[」』”"“]', _repl, out, flags=re.S)
    return out, dlg


def _restore_dialogue(text, dlg):
    """把 {{DLG_N}} 占位符还原为 H3 标准 <d>[语言] 对话内容</d> 格式。

    <d> 标签块原样还原（语言标记/原文/标点一个不动）；引号对话去掉引号、
    检测语言后按 H3 标准 <d> 包裹。兼容模型输出中占位符已被 <d>...</d>
    包裹的情况：整块替换，避免 <d> 嵌套。还原后再做一轮嵌套清洗与
    残留占位符兜底，保证输出永远是干净的单层 <d>。
    """
    import re as _re
    for i, d in enumerate(dlg):
        ph = "{{DLG_%d}}" % i
        _tag = _re.match(r'^<d>(\[[^\]]*\])?\s*(.*?)</d>$', d, flags=_re.S)
        if _tag:
            # 原 <d> 块：整块原样还原（保留用户写的语言标记，如 [国语]）
            formatted = d
        else:
            content = _re.sub(r'^[「『\u201c\"\u201d]+|[」』\u201d\"\u201c]+$', '', d)
            lang = _detect_dialogue_language(content)
            formatted = '<d>[%s] %s</d>' % (lang, content)
        # 1) 占位符已被 <d> ... </d> 包裹：整块替换为正确标签
        pat = r'<d>(?:\[[^\]]*\])?\s*' + _re.escape(ph) + r'\s*</d>'
        text = _re.sub(pat, lambda m: formatted, text)
        # 2) 裸占位符（模型没有包 <d>）
        text = text.replace(ph, formatted)
    # 3) 嵌套清洗：LLM 可能把概念里的 <d> 块复制出来又套一层 <d>[X]，
    #    产生 <d>[Chinese] <d>[国语] ...</d></d> —— 剥掉外层，保留内层干净块。
    for _ in range(4):
        _new = _re.sub(
            r'<d>(?:\[[^\]]*\])?\s*(<d>(?:\[[^\]]*\])?\s*.*?</d>)\s*</d>',
            lambda m: m.group(1), text, flags=_re.S)
        if _new == text:
            break
        text = _new
    return text


def _split_concept_segments(text):
    """把用户分镜按段拆开（与 H3PromptSplit 同款逻辑）：
    - 空行=段分隔，连续非空行合并为一段
    - 无空行时每行一段
    - // 开头行=注释跳过；# 开头行保留
    拆镜由代码完成，不依赖 LLM。
    """
    lines = [l for l in text.splitlines() if not l.strip().startswith("//")]
    if any(not l.strip() for l in lines):
        blocks = [b.strip() for b in re.split(r"\n\s*\n", "\n".join(lines)) if b.strip()]
        return [re.sub(r"\s*\n\s*", " ", b) for b in blocks]
    return [l.strip() for l in lines if l.strip()]


def _load_local_llm(gguf_name, mmproj_name, n_gpu_layers, n_ctx):
    import llama_cpp  # noqa: F401  (ensures llama-cpp-python is present)
    from llama_cpp import Llama
    if not gguf_name or gguf_name.strip() == "":
        raise RuntimeError("H3 PromptWriter: GGUF 模型未选择。请在节点的「GGUF 模型」下拉框中选择一个 .gguf 文件（留空无法运行 Local GGUF 模式）。")
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
        raise RuntimeError(f"H3Screenwriter: GGUF not found: {model_path}")
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
# Custom skill template support (install / delete via H3SkillManager)
# ---------------------------------------------------------------------------
# Users can drop JSON skill files into ComfyUI-H3-AutoDirector/skills/.
# Each JSON: {
#   "display_name": "显示名",
#   "key": "my_skill",
#   "standalone": true/false,
#   "style_contract": "Cinematic live-action",  // optional
#   "system_prompt": "..."
# }
# If standalone=false, system_prompt is appended to REVERSE_INFERENCE_BASE
# like the style appendices; if true, it replaces the entire system prompt.
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
        return sp
    sp = MX._build_system_prompt(task_key)
    if task_key == "fullreference":
        sp = sp + "\n\n" + H3_WRITING_ENHANCEMENTS
    return sp


_DIALECT_TAG_RE = re.compile(r"<d>\[([^\]]+)\](.*?)</d>", re.S)
_DIALECT_PLAIN_RE = re.compile(r"<d>(?![\[])([^<]*[\u4e00-\u9fff][^<]*)</d>", re.S)


def _fix_dialect_tags(text, src_text):
    """确定性兜底：原文台词附近（±60 字符）有『粤语/广东话/Cantonese』
    字样 → 把该台词的语言标签强制改为 [Cantonese]，不依赖 LLM 自觉。
    同时处理 <d>[Lang] 台词</d>（改标签）与 <d>台词</d>（补标签）。"""
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


def _resolve_task_key(task_mode):
    """Resolve a display name (or internal key) to internal key."""
    key = MX._resolve_style_key(task_mode)
    if key != task_mode:  # built-in resolved
        return key
    for k, info in CUSTOM_SKILLS.items():
        if info["display_name"] == task_mode:
            return k
    return task_mode


# Refresh helper called by H3SkillManager after add/delete.
def _refresh_custom_skills():
    CUSTOM_SKILLS.clear()
    CUSTOM_SKILLS.update(_load_custom_skills())
    CUSTOM_SKILL_KEYS[:] = list(CUSTOM_SKILLS.keys())
    # Rebuild TASK_MODE_OPTIONS in-place so future node placements see new skills.
    TASK_MODE_OPTIONS[:] = [
        dn for dn, _ in MX.STYLE_OPTIONS if dn not in _TASK_MODE_EXCLUDE
    ]
    TASK_MODE_OPTIONS.extend(CUSTOM_SKILLS[k]["display_name"] for k in CUSTOM_SKILL_KEYS)


# --- Visual style contract ------------------------------------------------
# Dropdown shows Chinese labels (user-readable); the canonical English value is
# what actually gets sent to the LLM / H3 (H3's reference guide is English-first).
# Old workflows that stored the raw English string still work via .get() fallback.
STYLE_MAP = {
    "电影感实拍": "Cinematic live-action",
    "2D 动画": "2D-animated",
    "3D 电脑动画": "3D CG",
    "黏土动画": "claymation",
    "水彩画": "watercolor",
    "复古胶片": "vintage film",
    "日式动漫": "anime",
    "极简产品广告": "minimalist product ad",
    "纪录片": "documentary",
    "赛博朋克霓虹": "cyberpunk neon",
}

# 任务模式 → 视觉风格前缀（注入 user_brief 的 VISUAL STYLE 行）。
# v8 之前写死 "Cinematic live-action"（_DEFAULT_STYLE），导致无论选什么模式
# 输出都是电影感、任务模式看起来"没用"。现按任务模式推导风格前缀，
# 仅默认 fullreference 仍保留 Cinematic live-action 以维持原默认行为。
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


def _get_style_contract(task_key):
    if task_key in CUSTOM_SKILLS:
        return CUSTOM_SKILLS[task_key].get("style_contract", "Cinematic live-action")
    return TASK_STYLE_MAP.get(task_key, "Cinematic live-action")


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

# H3Screenwriter 6 个 setup 值直接由本节点 widget 控制（v7.1 起）。
# 历史：v6 之前这 6 个 widget 与 H3 Story Setup 节点重复，删掉本节点 6 个后
# 6 个值由 setup_json 外部输入提供；v7.1 起 setup_json 也删了，6 个值 100%
# 由本节点 widget 控制（顶部 6 个 setup 项）。
_DEFAULT_STYLE = "电影感实拍"
# task_mode 用纯显示名字符串列表（v7.2.2 修）：之前用 MX.STYLE_OPTIONS 二元组
# [显示名, 内部键]，在用户 ComfyUI 版本里整个二元组被当成「一个选项值」存进
# widget.value，导致节点读到的是列表 ["H3 通用全参考模版（默认）","fullreference"]
# 而非字符串，无法解析。改成纯字符串列表后 combo 只存字符串，_resolve_style_key
# 仍把显示名映射到内部键。
# 任务模式下拉排除「H3 通用全参考模版（默认）」（用户不用通用模版；
# fullreference 内部键保留，H3PromptSplitTranslate 反推仍共用）。
_TASK_MODE_EXCLUDE = {"H3 通用全参考模版（默认）"}
TASK_MODE_OPTIONS = [
    dn for dn, _ in MX.STYLE_OPTIONS if dn not in _TASK_MODE_EXCLUDE
] + [
    CUSTOM_SKILLS[k]["display_name"] for k in CUSTOM_SKILL_KEYS
]
_DEFAULT_TASK_MODE = (
    "H3 提示词写作（micxin 增强）"
    if "H3 提示词写作（micxin 增强）" in TASK_MODE_OPTIONS
    else (TASK_MODE_OPTIONS[0] if TASK_MODE_OPTIONS else ""))
_DEFAULT_NUM_SHOTS = 1
_DEFAULT_DURATION = 10
_DEFAULT_ASPECT = "16:9"
_DEFAULT_MP = "0.4"

# 上下文窗口（用户可调）。默认 32768：16G 显存最大化——9B Q4 模型 32K 上下文
# 用 KV cache 量化（Q8_0）可容纳，长剧本/多参考图反推不再截断。
_DEFAULT_CONTEXT_SIZE = 32768
_N_CTX = _DEFAULT_CONTEXT_SIZE  # 兼容旧代码引用


# ===========================================================================
# 剧本模式 (Script Mode) — 为短剧打造的结构化分镜输出
# ---------------------------------------------------------------------------
# 用户仍写自然语言概念，但 LLM 输出结构化 JSON（角色档案+场景档案+
# 分镜列表），节点解析后输出每镜独立的 prompt + 时长 + 角色/场景ID。
# 下游可逐镜渲染、批量生成、单镜重跑。旧的单字符串 prompt 输出保留。
# ===========================================================================

SCRIPT_MODE_SYSTEM_PROMPT = r"""You are a professional short-drama screenwriter and H3 prompt engineer. You convert the user's story concept into a structured JSON screenplay where each shot is a self-contained H3 full-reference prompt.

# OUTPUT FORMAT (strict — output ONLY this JSON, no markdown fences, no prose)
{
  "characters": [
    {"id": "S1", "name": "角色名", "description": "英文外貌描述，1-2句"}
  ],
  "scenes": [
    {"id": "scene1", "name": "场景名", "description": "英文环境描述，1-2句"}
  ],
  "shots": [
    {
      "shot": 1,
      "scene": "scene1",
      "characters": ["S1"],
      "duration": 5,
      "prompt": "Write the ACTUAL H3 prompt in English with the user's concrete content (scene, action, camera, sound, dialogue) — NEVER copy or echo this template or any <...> placeholder. Layout to follow: Cinematic live-action. [restate the shot's scene + character appearance verbatim]. [Shot N] [concrete action + camera move]. overall_soundscape: [concrete ambience]. non_diegetic_music: [concrete score or N/A]."
    }
  ]
}

# HARD RULES
1. NARRATIVE in ENGLISH inside each shot's "prompt". Dialogue / lyrics keep original language inside <d>[Language] ... </d>.
2. EACH SHOT IS SELF-CONTAINED: restate the persistent scene location, the character's exact appearance, and the visual style VERBATIM in every shot's prompt. Do NOT "refer back" to shot 1.
3. SHOT BUDGET: If the user's concept EXPLICITLY marks shots — numbered markers like "镜头一/镜头二" or "Shot 1/Shot 2", explicit transitions like 【切场】/【转场】, or blank-line separated blocks — you MUST output EXACTLY as many shots as the user marked, preserving their order and boundaries. NEVER merge marked shots, never drop one, never add extra shots. Only when the concept has NO shot markers at all (a single narrative paragraph), infer the count from narrative complexity: one continuous scene/action = EXACTLY 1 shot; split only on explicit scene changes, time jumps, or distinctly different action phases. The max shot count is an upper bound, NOT a target — do not pad a simple concept with extra shots. Total duration across all shots must equal the user's requested TOTAL DURATION. Each shot 2-6 seconds; honor per-shot durations if the user tagged them (e.g. "5秒").
4. CHARACTERS: every character gets a stable id (S1, S2, ...) and appears in the "characters" list. A shot's "characters" array lists only who is on-screen.
5. SCENES: every location gets a stable id (scene1, scene2, ...). A shot's "scene" field is exactly one scene id.
6. DIALOGUE: Never invent, fabricate, or add dialogue, singing lyrics, or spoken lines that the user did not explicitly write. If the concept already contains an H3 dialogue block like `<d>[语言] 原句</d>`, copy that WHOLE block VERBATIM (the <d> tags, the language marker, and the text) into the corresponding shot's "prompt" — do NOT add a speaker prefix (no "says:", no "S1 says:"), do NOT change or re-detect the language marker, do NOT rewrite, translate, or re-quote the line. Only when the concept has no <d> block but has exact spoken lines (in quotes or after explicit markers such as 说/唱/对话/交谈), preserve those VERBATIM in the original language as `<d>[Language] 原句</d>` inside the shot prompt. A shot without user-provided lines MUST have NO dialogue and NO speaker block at all — carry its audio only through overall_soundscape / non_diegetic_music (or the reference audio). Never echo example characters, detection lists, or any non-line text as dialogue.
7. CAMERA: one clear camera move per shot (type + amplitude + speed).
8. The "prompt" field for each shot is the COMPLETE H3 prompt for that shot alone — it must render correctly if fed to H3 by itself.
9. FORBIDDEN to output the template itself: never emit literal angle-bracket placeholders such as <action + camera> or <ambience>, and never copy instruction sentences or template labels (like 'concrete physical sound' or 'restate') into the output. Every shot "prompt" must be the user's actual content expanded into concrete English. A prompt that only restates the template is a FAILURE — rewrite it.
10. Output ONLY the JSON object. First character must be "{" and last must be "}". No code fences, no explanation, no thinking out loud.

# REFERENCE MATERIALS
If the user's concept contains <Picture N> / <Video N> / <Audio N> tags, weave them into the relevant shots' prompts. Restate the same tag in EVERY shot that uses that asset. NEVER invent a reference tag the concept did not include."""


SHOT_SPLIT_SYSTEM_PROMPT = r"""You are a professional short-drama screenwriter and H3 prompt engineer. The user gives you a SHOT LIST (分镜). Your ONLY job is to convert each user shot into a structured JSON screenplay — the shot count, order and boundaries are FIXED by the user and must never change.

# OUTPUT FORMAT (strict — output ONLY this JSON, no markdown fences, no prose)
{
  "characters": [
    {"id": "S1", "name": "角色名", "description": "英文外貌描述，1-2句"}
  ],
  "scenes": [
    {"id": "scene1", "name": "场景名", "description": "英文环境描述，1-2句"}
  ],
  "shots": [
    {
      "shot": 1,
      "scene": "scene1",
      "characters": ["S1"],
      "duration": 5,
      "prompt": "Write the ACTUAL H3 prompt in English with the user's concrete content (scene, action, camera, sound, dialogue) — NEVER copy or echo this template or any <...> placeholder. Layout to follow: Cinematic live-action. [restate the shot's scene + character appearance verbatim]. [Shot N] [concrete action + camera move]. overall_soundscape: [concrete ambience]. non_diegetic_music: [concrete score or N/A]."
    }
  ]
}

# HARD RULES
1. NARRATIVE in ENGLISH inside each shot's "prompt". Dialogue / lyrics keep the original language inside <d>[Language] ... </d>.
2. SHOT COUNT IS FIXED: count the user's shots (镜头N / Shot N markers, 【切场】 transitions, blank-line separated blocks, or numbered items). Output EXACTLY the same number of shots in the SAME order. FORBIDDEN: merging two user shots into one, splitting one user shot into multiple, dropping a user shot, adding new shots, or reordering.
3. EACH SHOT IS SELF-CONTAINED: restate the persistent scene location, the character's exact appearance, and the visual style VERBATIM in every shot's prompt. Do NOT "refer back" to shot 1.
4. CHARACTERS: every character gets a stable id (S1, S2, ...). A shot's "characters" array lists only who is on-screen.
5. SCENES: every location gets a stable id (scene1, scene2, ...). A shot's "scene" field is exactly one scene id.
6. DIALOGUE: Every shot with people interacting MUST include dialogue. If the user's shot provides exact lines, preserve them VERBATIM in the original language: `<Subject N> (Sx) says: <d>[Language] 原句</d>`. If the shot mentions talking / conversation / 说 / 聊 / 对话 / 交谈 without exact lines, WRITE a short in-character Chinese line (one short sentence, 4-15 字) for the scene and tag it the same way. NEVER omit dialogue from a conversation scene; only pure action shots may have none.
7. CAMERA: one clear camera move per shot (type + amplitude + speed).
8. DURATION: the sum of all shot durations MUST equal the user's TOTAL DURATION. Honor per-shot durations if the user tagged them (e.g. "5秒"); otherwise divide the total evenly. Each shot 2-6 seconds.
9. FORBIDDEN to output the template itself: never emit literal angle-bracket placeholders such as <action + camera> or <ambience>, and never copy instruction sentences or template labels (like 'concrete physical sound' or 'restate') into the output. Every shot "prompt" must be the user's actual content expanded into concrete English. A prompt that only restates the template is a FAILURE — rewrite it.
10. Output ONLY the JSON object. First character must be "{" and last must be "}". No code fences, no explanation.

# REFERENCE MATERIALS
If the user's concept contains <Picture N> / <Video N> / <Audio N> tags, weave them into the relevant shots' prompts. Restate the same tag in EVERY shot that uses that asset. NEVER invent a reference tag the concept did not include."""

SHOT_CONVERT_SYSTEM_PROMPT = r"""You are a professional prompt engineer for the MiniMax H3 video generation model.

TASK: The user will input ONE shot description (may be Chinese). Convert it into ONE six-section structured prompt.

SIX-SECTION STRUCTURE (titles in English, in this exact order):
【1. Subject & Features】
【2. Action & Behavior】
【3. Scene & Environment】
【4. Camera & Composition】
【5. Lighting & Color】
【6. Style & Quality】

CORE RULES (violating any rule is an error):
1. Except dialogue, ALL descriptive content (subject appearance, action, scene, camera, lighting, style) MUST be and ONLY be in English.
2. DIALOGUE RULE (highest priority): if the user input contains any dialogue, lines, or speech content (including Mandarin, Cantonese, dialect, mixed Chinese-English, Hong Kong Mandarin, etc.), you MUST copy these dialogue contents VERBATIM and UNCHANGED into 【2. Action & Behavior】. Absolutely FORBIDDEN to translate, transcribe, rewrite, or omit. Whatever language and characters the dialogue is in, write exactly that — not one punctuation mark changed.
3. In 【2. Action & Behavior】, describe the speaking action and accent in English (e.g. speaking with a Cantonese accent), then place the verbatim dialogue in English quotation marks. Format example: speaking with a Cantonese accent: "你食咗饭未啊？"
4. If the user input contains <Picture N> / <Video N> / <Audio N> tags, restate the exact same tag VERBATIM in the relevant section. NEVER invent a tag the input did not include.
5. Strictly process ONLY the current input. Absolutely FORBIDDEN to quote, imitate, reference, or output any examples, historical dialogue, or dialogue from training data. Write exactly what the user wrote.
6. Do not output any explanation, greeting, or extra text. Output ONLY the six-section prompt.
Your role is a format converter: translate descriptive parts into English, treat dialogue as an unchangeable constant string embedded verbatim."""

TRANSLATE_SYSTEM_PROMPT = r"""You are a FORMAT CONVERTER (NOT a translator) for video shot prompts (分镜).

OUTPUT CONTRACT (highest priority, violating any of these is a severe error):
- Output the converted shot list ONLY. Nothing else.
- FORBIDDEN: thinking, reasoning, analysis, explanation, summary, preface, commentary, notes, or any text outside the converted shots.
- Do NOT use <think>, <thinking>, or any reasoning tags. Do NOT start with "Here is", "Sure", "好的", "以下是" or any preamble. Do NOT end with closing remarks.
- The converted shot text is your ENTIRE response, from the first character to the last.

SEGMENT RULE (this structure is how downstream splits shots):
- The user input is a shot list: each shot is one block, and blocks are separated by BLANK LINES.
- Count the input blocks. Output EXACTLY the same number of blocks, separated by BLANK LINES, in the same order.
- One input block = one output block. NEVER merge two blocks, NEVER split one block into several, NEVER add, drop, or reorder blocks.
- Preserve each block's internal line breaks and shot markers (镜头一 / Shot 1 / 【切场】) exactly as in the input.
- If the input has NO blank-line-separated blocks (it is a single paragraph with no shot markers), output it as ONE single block — do not invent splits.

TASK:
- Translate ONLY descriptive text (subject, action, scene, camera, lighting, style) into English.
- Everything else stays exactly as in the input.

CORE RULES (violating any rule is an error):
1. PLACEHOLDER RULE (highest priority): dialogue inside quotation marks has already been replaced by {{DLG_N}} placeholders (N is a number). {{DLG_N}} is an UNCHANGEABLE CONSTANT STRING — copy it VERBATIM and UNCHANGED into the output, in exactly the same position. Absolutely FORBIDDEN to translate, transcribe, rewrite, omit, move, or expand any {{DLG_N}}. If you see {{DLG_0}}, output exactly {{DLG_0}}. (After LLM output, the system will automatically convert each {{DLG_N}} into H3 standard format <d>[language] dialogue text</d> — the LLM does NOT need to write <d> tags itself.)
2. NO INVENTIONS: restate ONLY the tags and markers that already exist in the input. If the input has no <Picture N> / <Video N> / <Audio N> tag, do NOT invent one. If the input has no "Shot N" numbering, do NOT add any numbering. If the input has no 【切场】, do NOT add it.
3. TEMPLATE LEAK FORBIDDEN: the output must contain ONLY the converted shot text. Never output lines like "VISUAL STYLE:", "ASPECT RATIO:", "NOTE:", "TASK:", or any instruction text — those are template lines, not content.
4. Strictly process ONLY the current input. FORBIDDEN to quote examples, historical dialogue, or training data.

Your role is a format converter: descriptive parts become English, {{DLG_N}} dialogue placeholders are unchangeable constant strings, blank-line blocks are preserved one-to-one."""


def _extract_script_json(text):
    """从 LLM 输出中提取剧本模式 JSON，返回 (dict, error_msg)。

    尝试顺序：直接解析 → 去 markdown 代码块 → 平衡括号提取 → 失败。
    """
    text = _clean_text(_strip_think(text or ""))
    if not text:
        return None, "LLM returned empty content."

    candidates = [text]
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    # 尝试每个平衡 {...} 块
    for span in _balanced_spans(text, "{", "}"):
        candidates.append(span)

    seen = set()
    ordered = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    last_err = None
    for cand in ordered:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as e:
            try:
                data = json.loads(cand, strict=False)
            except json.JSONDecodeError as e2:
                last_err = e2
                continue
        if isinstance(data, dict) and "shots" in data:
            return data, None

    head = text[:300].replace("\n", "\\n")
    return None, (f"could not parse script JSON (last error: {last_err}). "
                  f"Response head: {head!r}")


def _validate_and_normalize_script(data, total_duration):
    """校验并归一化剧本 JSON，返回 (characters, scenes, shots_list)。

    shots_list 每个元素: {"shot":int, "scene":str, "characters":list,
                           "duration":int, "prompt":str}
    自动修正：时长总和对齐 total_duration；补全缺失字段。
    """
    characters = data.get("characters", [])
    scenes = data.get("scenes", [])
    shots = data.get("shots", [])

    if not shots:
        raise ValueError("Script JSON has no 'shots' array.")

    # 归一化每个 shot
    normalized = []
    for i, sh in enumerate(shots):
        if not isinstance(sh, dict):
            continue
        shot_num = sh.get("shot", i + 1)
        scene_id = sh.get("scene", scenes[0]["id"] if scenes else "scene1")
        chars = sh.get("characters", [])
        if isinstance(chars, str):
            chars = [chars]
        duration = int(sh.get("duration", max(2, total_duration // len(shots))))
        duration = max(2, min(15, duration))
        prompt = sh.get("prompt", "").strip()
        # v10.6: 不再静默丢弃空 prompt 镜头（否则如"第三镜丢失"会无声发生），
        # 保留由上层兜底注入台词/告警。
        if not prompt:
            prompt = ""
        normalized.append({
            "shot": int(shot_num),
            "scene": str(scene_id),
            "characters": [str(c) for c in chars],
            "duration": duration,
            "prompt": prompt,
        })

    if not normalized:
        raise ValueError("Script JSON has no valid shots (all empty or invalid).")

    # 时长对齐：如果总和不等于 total_duration，按比例调整最后一镜
    total = sum(s["duration"] for s in normalized)
    if total != total_duration and normalized:
        diff = total_duration - total
        normalized[-1]["duration"] = max(2, normalized[-1]["duration"] + diff)

    return characters, scenes, normalized


def _shots_to_frame_list(shots_list):
    """把每镜 duration(秒) 转成 H3 帧数，对齐 length % 17 == 5。"""
    frames = []
    for s in shots_list:
        f = max(5, round(float(s["duration"]) * 24))
        f = f + (5 - (f % 17)) % 17
        frames.append(int(f))
    return frames


def _shots_to_joined_prompt(shots_list):
    """把分镜列表拼成一个完整 prompt 字符串（兼容旧输出）。"""
    parts = []
    for s in shots_list:
        parts.append(s["prompt"])
    return "\n\n".join(parts)


# ===========================================================================
# 资产库联动 (Asset Library Integration) — v10.1 新增
# ---------------------------------------------------------------------------
# 接受外部资产库 JSON（角色/场景/道具的图片路径+描述），生成提示词时
# 自动引用资产，把资产图同步给 AIO，形成"资产→分镜→渲染"闭环。
#
# 支持两种 JSON 格式：
# 格式A（分类）: {"characters":[{"id":"S1",...}], "scenes":[...], "props":[...]}
# 格式B（扁平）: {"S1":{"name":"女主","image":"..."}, "scene1":{...}}
# ===========================================================================

def _parse_asset_library(json_str):
    """解析资产库 JSON，返回 (char_map, scene_map, prop_map)。

    每个 map 以 id 为 key，value 为 dict（含 name/description/image/voice 等）。
    解析失败或为空时返回三个空 dict。
    """
    if not json_str or not str(json_str).strip():
        return {}, {}, {}
    try:
        data = json.loads(str(json_str).strip())
    except (json.JSONDecodeError, TypeError):
        return {}, {}, {}

    char_map, scene_map, prop_map = {}, {}, {}

    def _normalize_item(item, default_id):
        if not isinstance(item, dict):
            return None
        out = dict(item)
        if "id" not in out:
            out["id"] = default_id
        return out

    # 格式A：分类结构
    if isinstance(data, dict):
        for category, target_map in (("characters", char_map), ("scenes", scene_map), ("props", prop_map)):
            items = data.get(category, [])
            if isinstance(items, list):
                for i, item in enumerate(items):
                    norm = _normalize_item(item, f"{category[:-1]}_{i}")
                    if norm:
                        target_map[norm["id"]] = norm
        # 格式B：扁平结构（key 就是 id）
        if not char_map and not scene_map and not prop_map:
            for key, val in data.items():
                if isinstance(val, dict):
                    norm = _normalize_item(val, key)
                    if norm:
                        # 简单判断：S开头=角色，scene/scene开头=场景，其他=道具
                        if str(key).startswith("S") or "character" in str(key).lower():
                            char_map[key] = norm
                        elif "scene" in str(key).lower() or str(key).startswith("scene"):
                            scene_map[key] = norm
                        else:
                            prop_map[key] = norm

    return char_map, scene_map, prop_map


def _build_asset_brief(char_map, scene_map, prop_map):
    """把资产库转成文本描述，注入 user_brief，让 LLM 知道有哪些资产可用。

    同时告诉 LLM 每个资产有对应的参考图，在 prompt 中用 <Picture N> 引用。
    """
    if not char_map and not scene_map and not prop_map:
        return ""
    lines = ["\n=== ASSET LIBRARY (reference images available) ==="]
    pic_idx = 1
    # 角色
    if char_map:
        lines.append(f"CHARACTERS ({len(char_map)}):")
        for cid, c in char_map.items():
            name = c.get("name", cid)
            desc = c.get("description", "")
            has_img = "image" in c and c["image"]
            lines.append(f"  - {cid} ({name}): {desc}"
                         + (f" [reference image <Picture {pic_idx}>]" if has_img else ""))
            if has_img:
                pic_idx += 1
    # 场景
    if scene_map:
        lines.append(f"SCENES ({len(scene_map)}):")
        for sid, s in scene_map.items():
            name = s.get("name", sid)
            desc = s.get("description", "")
            has_img = "image" in s and s["image"]
            lines.append(f"  - {sid} ({name}): {desc}"
                         + (f" [reference image <Picture {pic_idx}>]" if has_img else ""))
            if has_img:
                pic_idx += 1
    # 道具
    if prop_map:
        lines.append(f"PROPS ({len(prop_map)}):")
        for pid, p in prop_map.items():
            name = p.get("name", pid)
            desc = p.get("description", "")
            has_img = "image" in p and p["image"]
            lines.append(f"  - {pid} ({name}): {desc}"
                         + (f" [reference image <Picture {pic_idx}>]" if has_img else ""))
            if has_img:
                pic_idx += 1
    lines.append("Use these asset IDs in the 'characters' and 'scene' fields of each shot. "
                 "If an asset has a reference image, reference it with <Picture N> in that shot's prompt.")
    lines.append("=== END ASSET LIBRARY ===\n")
    return "\n".join(lines)


def _merge_assets_into_output(characters, scenes, char_map, scene_map):
    """把资产库的图片路径/描述合并到输出的 characters/scenes 列表。

    LLM 生成的 characters/scenes 可能只有 id/name/description，
    这里把资产库中的 image/voice 等字段合并进去，下游可直接用。
    """
    def _merge_list(items, asset_map):
        merged = []
        for item in items:
            if not isinstance(item, dict):
                merged.append(item)
                continue
            out = dict(item)
            aid = out.get("id", "")
            if aid in asset_map:
                for k, v in asset_map[aid].items():
                    if k not in out or not out[k]:
                        out[k] = v
            merged.append(out)
        return merged

    return _merge_list(characters, char_map), _merge_list(scenes, scene_map)


def _extract_asset_image_paths(char_map, scene_map, prop_map):
    """提取资产库中所有图片路径，用于同步给 AIO 的 _aio_ref_paths。

    返回多行路径字符串（每行一个路径），按角色→场景→道具顺序排列，
    与 _build_asset_brief 中的 <Picture N> 编号一致。
    """
    paths = []
    for asset_map in (char_map, scene_map, prop_map):
        for _, item in asset_map.items():
            img = item.get("image", "")
            if img and str(img).strip():
                paths.append(str(img).strip())
    return "\n".join(paths)


# ────────────────────────────────────────────────────────────
# LLM 输出缓存（v10.8）：同一概念 + 同一组参数下 LLM 只反推一次。
# 之后命中缓存直接复用上次反推的最终分镜 —— 既省 LLM 加载+推理时间，
# 又保证提示词字节级不变（下游 ClipChain 的段缓存可稳定命中）。
# 缓存自动失效：改概念/参数/参考图/资产库 → key 变 → 自动重新反推并覆盖。
# 无需手动清理：文件超上限自动 LRU 删最旧；损坏/版本不符自动当没缓存。
# ────────────────────────────────────────────────────────────
_LLM_CACHE_VERSION = 2
_LLM_CACHE_MAX_FILES = 24


def _llm_cache_dir():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _refs_mtime(refs):
    """参考图路径行 → (path, mtime, size) 列表；图片内容变了自动失效。"""
    out = []
    for line in (refs or "").splitlines():
        line = (line or "").strip()
        if not line:
            continue
        p = line.split("|")[0].strip()
        if not p:
            continue
        if not os.path.isabs(p):
            try:
                cand = os.path.join(folder_paths.get_input_directory(), p)
                if os.path.exists(cand):
                    p = cand
            except Exception:
                pass
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime, st.st_size))
        except OSError:
            pass
    return out


def _make_llm_cache_key(concept_text, task_key, script_mode, aspect_ratio,
                        resolution_mp, dur, backend, gguf_name, mmproj_name,
                        context_size, model, llm_base_url, csp, refs, assets):
    import hashlib
    payload = {
        "v": _LLM_CACHE_VERSION,
        "concept": (concept_text or "").strip(),
        "task_key": task_key,
        "script_mode": bool(script_mode),
        "aspect": str(aspect_ratio), "mp": str(resolution_mp), "dur": int(dur),
        "backend": backend, "gguf": gguf_name, "mmproj": mmproj_name,
        "ctx": int(context_size), "model": model, "url": llm_base_url,
        "csp": (csp or "").strip(), "assets": (assets or "").strip(),
        "refs": _refs_mtime(refs),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _llm_cache_load(key, force):
    if force:
        return None
    p = os.path.join(_llm_cache_dir(), "writer_llm_%s.json" % key)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("key") != key or d.get("version") != _LLM_CACHE_VERSION:
            return None
        return d.get("result")
    except Exception:
        return None


def _llm_cache_save(key, result):
    try:
        d = {
            "key": key, "version": _LLM_CACHE_VERSION,
            "created": time.time(),
            "result": result,
        }
        p = os.path.join(_llm_cache_dir(), "writer_llm_%s.json" % key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
        # LRU 清理：超过上限删最旧，无需用户手动清理
        files = []
        for fn in os.listdir(_llm_cache_dir()):
            if fn.startswith("writer_llm_") and fn.endswith(".json"):
                fp = os.path.join(_llm_cache_dir(), fn)
                try:
                    files.append((os.path.getmtime(fp), fp))
                except OSError:
                    pass
        if len(files) > _LLM_CACHE_MAX_FILES:
            for _m, _f in sorted(files)[:len(files) - _LLM_CACHE_MAX_FILES]:
                try:
                    os.remove(_f)
                except OSError:
                    pass
    except Exception as e:
        print(f"[H3 AutoDirector] LLM 缓存保存失败: {e}", flush=True)


class H3PromptWriter:
    """Concept -> H3 multi-shot prompt (auto prompt writer).

    Calls any OpenAI-compatible /chat/completions endpoint (local llama.cpp
    server by default; also works with Ollama, SiliconFlow, OpenAI, DeepSeek,
    MiniMax, ...).
    """

    @classmethod
    def INPUT_TYPES(cls):
        """节点 widget 区从上到下的布局（v9，2026-08-25）：

        v9 变更：
          - 删除 camera_motion / camera_motion_timing（运镜短语及出现时机），
            用户反馈导致 ComfyUI 闪退。
          - 新增 context_size（上下文窗口调节），替换原运镜位置。
          - concept_text 输入框高度缩小一半（320→160px），加滚动条。

        +----------------------------------------------------------+
        | 0) 概念 (LiteGraph 原生 STRING widget, 节点最顶部最显眼)  |
        |    concept_text (widgets[0]) — multiline textarea,        |
        |    @ 弹 Picture/Video/Audio/Subject 下拉 (JS 挂 keyup)   |
        +----------------------------------------------------------+
        | 1) 4 个 setup widget（最常用，调参）                      |
        |    task_mode / duration_seconds / aspect_ratio /          |
        |    resolution_mp                                           |
        +----------------------------------------------------------+
        | 2) 上下文调节 (v9 新增 — 紧跟 resolution_mp)              |
        |    context_size — LLM n_ctx 窗口 (2048~65536)             |
        +----------------------------------------------------------+
        | 3) 模型选择                                                |
        |    backend / gguf_name / mmproj_name                       |
        |    — LLM 后端选型, 偶尔调一次                              |
        |    ↓ advanced_settings 折叠开关 (BOOL, 默认收起)            |
        |    ↓ 展开后: llm_base_url / model / api_key / 7 LLM 写入    |
        +----------------------------------------------------------+
        | 4) HTTP-only widgets（选 backend='HTTP endpoint' 才显示）|
        |    llm_base_url / model / api_key (JS 折叠)                |
        +----------------------------------------------------------+
        | 5) 7 个 LLM 写入参数（节点 widget 区底部）                |
        |    temperature / seed / auto_save / filename /             |
        |    n_gpu_layers / n_ctx / keep_loaded                      |
        +----------------------------------------------------------+

        v7.2 起 schema 完全用 LiteGraph 原生 widget，**不再**用 addDOMWidget
        / unshift / hideWidget。后端 INPUT_TYPES 是真理，前端只负责
        label 美化 + @ 弹窗 + backend 折叠。"""
        return {
            "required": {
                # === 0) 概念（节点最顶部，最显眼输入）===
                # v7.2 决定：彻底抛弃 addDOMWidget + unshift（ComfyUI 1.x 下
                # addDOMWidget 在 ensureEditor 时机不可靠、LiteGraph 会在
                # widget.value 反序列化后重排数组，unshift 到 widgets[0] 经常
                # 失效）。改为 LiteGraph 原生 STRING widget——ComfyUI 默认建
                # 一个 <textarea>，稳定。
                # JS 端给 widget.computeSize 设大尺寸（≥320px）+ 给 widget.element
                # 加 @ keyup 监听触发 @ 弹窗（替代 contenteditable）。
                "concept_text": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "在此撰写提示词。输入 @ 选择已上传的图/视/音素材标签。可中文。",
                    "tooltip": "[最关键的输入] 正常模式 = 提示词；绕过模式 = 上一轮提示词。"
                               "输入 @ 弹出下拉菜单，仅列出已在 H3 R2VA AIO(micxin) 上传的 "
                               "Picture / Video / Audio 素材标签（动态，不上传不出现）。"
                               "开启 bypass_llm 后：把上一轮提示词粘贴到顶部概念框，"
                               "跳过 LLM 直接输出。"}),
                # === 1) 节点 widget 区顶部：4 个 setup widget（v8 删 style / num_shots）===
                # v8 起 style 硬编码 Cinematic live-action、num_shots 硬编码 1，
                # 不再暴露 widget。视觉风格由概念描述 + LLM 推断，单镜最稳。
                "task_mode": (TASK_MODE_OPTIONS, {
                    "default": _DEFAULT_TASK_MODE,
                    "tooltip": "任务模式（16 套 system prompt 模板：通用全参考 / I2VA / FL2VA / "
                               "动作迁移 / 语言克隆 / 双人对话 / 高密度蒙太奇 ...）。"
                               "由本节点 widget 直接控制。"}),
                "duration_seconds": ("INT", {"default": _DEFAULT_DURATION, "min": 2, "max": 15, "step": 1,
                                              "tooltip": "本段渲染总秒数。H3 单段硬上限 15s。"}),
                "aspect_ratio": (["16:9", "9:16", "1:1", "21:9", "4:3"], {
                    "default": _DEFAULT_ASPECT,
                    "tooltip": "画幅。16:9=横屏, 9:16=竖屏, 1:1=方形, 21:9=影院宽屏, 4:3=经典。"}),
                "resolution_mp": ([str(x / 10) for x in range(2, 21)], {
                    "default": _DEFAULT_MP,
                    "tooltip": "渲染分辨率档位（百万像素）。0.2-0.5MP 稳、1.0MP 出片、1.5-2.0MP 达 2K。"
                               "渲染分辨率 = √(MP × 比例), 全部对齐到 32 倍数。"}),
                # === 1.5) 剧本模式（script_mode）已于 v10.10 删除 ===
                # 分镜/多段 JSON 输出改用 H3 无限时长编剧 (H3InfiniteStoryWriter)——
                # 多段六段式 JSON，比旧剧本模式更稳。
            },
            "optional": {
                # === 2) 上下文调节（替换原运镜短语/时机）===
                # 控制本地 GGUF 的 n_ctx 上下文窗口。默认 8192，多图/长概念时调大。
                # 16G 显存建议 ≤8192；24G+ 可试 16384/32768。
                "context_size": ("INT", {
                    "default": _DEFAULT_CONTEXT_SIZE,
                    "min": 2048, "max": 65536, "step": 1024,
                    "tooltip": "LLM 上下文窗口 (n_ctx)。默认 8192。"
                               "多参考图或长概念描述导致 Context Shift 报错时调大。"
                               "16G 显存建议 ≤8192；调太大会爆显存 (Failed to create context)。"}),
                # === 3) 模型选择（v8 删 sequel_auto 后上提）===
                # 之前 v7.2 紧跟 6 setup，挡住创意组；v7.2.1 下移到续集之后。
                # 选型工作流偶尔调一次，移到中段不抢戏。
                # v7.2 删 h3_model_row 横向 DOM 容器（ComfyUI 1.x 下 addDOMWidget
                # 在 ensureEditor 时机不稳，会被 LiteGraph 异步重排）。
                "backend": (["Local GGUF", "HTTP endpoint"], {
                    "default": "Local GGUF",
                    "tooltip": "Local GGUF: 在 ComfyUI 内直接加载 GGUF（推荐，默认）。"
                               "HTTP endpoint: 调 OpenAI 兼容的外部服务（如本地 llama-server）。"}),
                "gguf_name": ([""] + (_list_llm_files(False) or []), {
                    "default": _default_gguf(),
                    "tooltip": "Local GGUF 模式下加载哪个 GGUF（在 ComfyUI/models/LLM 下）。留空则不加载本地模型。"}),
                "mmproj_name": ([""] + (_list_llm_files(True, mmproj_only=True) or []), {
                    "default": _default_mmproj(),
                    "tooltip": "多模态投影文件（视觉模型必需）。留空 = 纯文本模式，不加载视觉。"}),
                # === 4.5) 高级 LLM 设置折叠开关（v7.2.2 新增）===
                # 默认 False = 折叠下方 10 个极少用到的选项（HTTP 三件 + 7 LLM 写入）。
                # 这是真实 BOOLEAN widget（跨重启保留状态），JS 据此 fold/unfold
                # 后续 10 个 widget 并动态收起节点高度。放在 mmproj_name 之后、
                # llm_base_url 之前，正好把「模型选型」和「高级 LLM 参数」切开。
                "advanced_settings": ("BOOLEAN", {
                    "default": False,
                    "label_on": "展开高级 LLM 设置",
                    "label_off": "▸ 高级 LLM 设置（点击展开）",
                    "tooltip": "折叠开关：默认收起下方 7 个选项（llm_base_url / model / "
                               "api_key / temperature / seed / n_gpu_layers / keep_loaded / "
                               "bypass_llm）。很少用到时保持折叠，节点更紧凑、"
                               "接线更清爽；需要时勾选展开。"}),
                # === 5) HTTP 模式才显示（与 backend 联动折叠）===
                "llm_base_url": ("STRING", {
                    "default": "http://127.0.0.1:8080/v1/chat/completions",
                    "tooltip": "backend='HTTP endpoint' 时使用。Local GGUF 自动忽略。"
                               "支持 OpenAI 兼容端点：Ollama / SiliconFlow / OpenAI / DeepSeek ...。"}),
                "model": ("STRING", {
                    "default": "",
                    "placeholder": "llama.cpp model id / GGUF basename",
                    "tooltip": "endpoint 用的模型 id（llama.cpp 用 GGUF basename，"
                               "Ollama 用 qwen2.5:14b，云端用 deepseek-v3 等）。"}),
                "api_key": ("STRING", {"default": "", "password": True,
                                       "tooltip": "Bearer token。backend='HTTP endpoint' 时才会用。"
                                                  "Local GGUF 模式下被 JS 自动折叠（foldWidget）。"}),
                # === 6) 7 个 LLM 写入参数（节点 widget 区底部，最常调参）===
                "temperature": ("FLOAT", {"default": 0.65, "min": 0.0, "max": 2.0, "step": 0.05,
                                          "tooltip": "生成温度。0.65 偏高，创意更发散；"
                                                     "若 JSON 契约不稳（乱码/丢字段）再降到 0.3-0.4。"
                                                     "默认 0.65（2026-08-19 调高）。"}),
                "seed": ("INT", {"default": 0, "min": 0, "control_after_generate": False,
                                 "tooltip": "0 = let the endpoint decide."}),
                "n_gpu_layers": ("INT", {"default": -1, "min": -1, "max": 200, "step": 1,
                                         "tooltip": "Local GGUF only. 把模型多少层卸载到 GPU。"
                                                    "-1=全部 0=只在 CPU 与 H3 渲染共存。"}),
                "keep_loaded": ("BOOLEAN", {"default": False,
                                            "label_on": "keep in VRAM",
                                            "label_off": "unload after",
                                            "tooltip": "Local GGUF only. Unload "
                                                       "after each script frees "
                                                       "VRAM for H3 (default). "
                                                       "Keep if you call the "
                                                       "node many times."}),
                # === 6) 绕过 LLM（v2026-08-19；v8 改用顶部 concept_box 粘贴提示词）===
                # bypass_llm=True 时跳过 LLM 调用，直接用顶部 concept_text 概念框
                # 里粘贴的提示词，省一次大模型推理（第二遍不满意时复用上一轮脚本）。
                "bypass_llm": ("BOOLEAN", {
                    "default": False,
                    "label_on": "跳过 LLM (用顶部概念框粘贴提示词)",
                    "label_off": "正常跑 LLM",
                    "tooltip": "开启后不再调用 LLM / 本地 GGUF，直接把顶部"
                               "『概念』编辑器 (concept_text) 的内容当作最终提示词"
                               "输出。适合『跑完一遍不满意、第二遍复用上轮提示词"
                               "微调』的场景，省一次大模型推理。"
                               "尺寸仍由 aspect_ratio / resolution_mp / "
                               "duration_seconds 控件决定。"
                               "开启时把上一轮的 h3_script 或 prompts_json "
                               "直接粘贴到顶部概念框即可。"}),
                # === 7) AIO 自动同步图片路径（隐藏，JS 端自动填充）===
                # JS 端扫描画布上的 H3ModelLoader (H3 R2VA AIO) 节点，读取其隐藏
                # image_paths widget，写入此处。Python 端从这些路径加载图片给 LLM，
                # 无需连线，从根本上避免 AIO→Screenwriter→AIO 循环。
                "_aio_ref_paths": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "隐藏：JS 自动从 H3 R2VA AIO(micxin) 同步图片路径，无需手动填写。"}),
                # === 8) 自定义系统提示词外接入口 ===
                # forceInput 强制为输入口，可接 ComfyUI 字符串节点。非空时完全替换
                # task_mode 内置 system prompt；为空时使用内置模板（与旧行为一致）。
                "custom_system_prompt": ("STRING", {
                    "default": "",
                    "forceInput": True,
                    "tooltip": "可选：外接自定义 system prompt。连接字符串节点后，"
                               "将完全替代当前 task_mode 的内置 system prompt；"
                               "不连接时仍使用 task_mode 内置模板。"
                               "注意：自定义 system prompt 会负责最终输出格式，"
                               "但节点仍会把 concept / 风格 / 时长 / 分辨率 / 运镜"
                               "作为 user message 追加。"}),
            },
        }

    # 返回顺序（v10 2026-08-25）：新增剧本模式结构化输出。
    # 普通模式(script_mode=False)：shots/durations/characters/scenes 返回空列表，
    #   characters_json/scenes_json 返回空字符串 — 旧工作流不受影响。
    # 剧本模式(script_mode=True)：返回每镜独立数据，下游可逐镜渲染/批量生成。
    RETURN_TYPES = ("STRING", "INT", "INT", "INT")
    RETURN_NAMES = (
        "prompt",
        "width", "height", "length",
    )
    FUNCTION = "write"
    CATEGORY = "H3 helper/micxin/AutoDirector"

    # 画面比例 → 比例系数（宽/高）。用于把 MP 档位换算成实际像素。
    ASPECT_FACTORS = {
        "16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0,
        "21:9": 21 / 9, "4:3": 4 / 3,
    }

    def _resolve_resolution(self, aspect_ratio, resolution_mp):
        """aspect_ratio + MP 档位 → 实际渲染像素。

        标准 Resolution Selector (Size) 算法：
            MP(像素) = resolution_mp × 1_000_000
            宽 = sqrt(MP × 比例), 高 = sqrt(MP / 比例)
        全部对齐到 32 的倍数（H3 canvas multiple, 见 nodes_minimax_h3.CANVAS_MULTIPLE）。

        注意：这里【不】调用 Ref2VA 的 adapt_canvas（768短边/1.03MP 面积上限）——
        那个函数只用于参考视频的尺寸适配，不作用于生成 latent 的 width/height。
        Ref2VA 的 _empty_av_latent 直接用传入的 width/height 建 latent
        （video = zeros[..., height//16, width//16]），所以 1.0~2.0 档真实生效
        （即 H3 标称的 "up to 2K"）。只是 >1.03MP 超出训练舒适区，偏软偏慢。
        """
        # 1.0MP 16:9 特判为官方推荐表值 1376x768（不按公式 1344x736）
        if aspect_ratio == "16:9" and abs(float(resolution_mp) - 1.0) < 1e-9:
            return 1376, 768
        a = self.ASPECT_FACTORS.get(aspect_ratio, 16 / 9)
        mp_px = float(resolution_mp) * 1_000_000.0
        w = math.sqrt(mp_px * a)
        h = math.sqrt(mp_px / a)
        w = max(32, int(round(w / 32.0)) * 32)
        h = max(32, int(round(h / 32.0)) * 32)
        return w, h

    @staticmethod
    def _resolve_length(duration_seconds):
        # 与原 MathExpression 公式一致：秒×24 → 帧，再对齐到 length % 17 == 5
        # （H3 的 length 约束，默认 124 = 5 mod 17；训练范围 ~124-362）
        f = max(5, round(float(duration_seconds) * 24))
        f = f + (5 - (f % 17)) % 17
        return int(f)

    def write(self,
              # 0) 概念（节点最顶部）—— LiteGraph 原生 STRING widget。
              # v8：bypass_llm 开启时此框直接粘贴上一轮提示词。
              concept_text="",
              # 1) 4 个 setup widget（v8 删 style / num_shots）
              task_mode=_DEFAULT_TASK_MODE, duration_seconds=_DEFAULT_DURATION,
              aspect_ratio=_DEFAULT_ASPECT, resolution_mp=_DEFAULT_MP,
              # 1.5) 剧本模式（script_mode）已删除（v10.10）：分镜/多段 JSON 输出
              # 改用 H3 无限时长编剧 (H3InfiniteStoryWriter)。这里恒为 False。
              script_mode=False,
              # 2) 上下文调节（替换原运镜短语/时机）
              context_size=_DEFAULT_CONTEXT_SIZE,
              # 3) 模型选择（v8 删 sequel_auto 后上提）
              backend="Local GGUF", gguf_name="", mmproj_name="None",
              # 4) 高级 LLM 设置折叠开关
              advanced_settings=False,
              # 5) HTTP 模式才显示
              llm_base_url="http://127.0.0.1:8080/v1/chat/completions",
              model="",
              api_key="",
              # 6) LLM 写入参数（v8 删 auto_save / filename / n_ctx）
              temperature=0.65, seed=0,
              n_gpu_layers=-1, keep_loaded=False, bypass_llm=False,
              _aio_ref_paths="",
              custom_system_prompt="",
              ):
        # ---- 概念源：仅从顶部 concept_text 原生 STRING widget 拿 ----
        # v7.2 改用 LiteGraph 原生 STRING widget（不再用 addDOMWidget）。
        # prompt_text 兜底初始化：任何路径下都保证有值，避免 UnboundLocalError。
        prompt_text = ""

        # v10.1: 资产库输入口已删除（用户否决 A→Writer/A→AIO 自动联动），
        # 资产图改由 AIO 手动填 + JS 同步给 LLM 看图。这里恒为空。
        char_map, scene_map, prop_map = {}, {}, {}
        has_assets = False

        # task_mode 兜底（v7.2.2 修）：旧工作流曾把 combo 二元组整个存成列表
        # ["H3 通用全参考模版（默认）","fullreference"]，导致节点收不到字符串。
        # 任何非字符串（list/tuple/其他）都规整为字符串；_resolve_style_key 会把
        # 显示名或内部键都映射到合法的 system prompt 键，不会因此报错。
        # ---- bypass: 跳过 LLM，直接复用顶部概念框粘贴的提示词（v8 改）----
        if bypass_llm:
            ov = (concept_text or "").strip()
            if not ov:
                raise ValueError(
                    "H3Screenwriter: bypass_llm 已开启，但顶部概念框为空 — "
                    "把上一轮的 H3 prompt 粘贴到顶部『概念』编辑器"
                    "（不再调用 LLM）。")
            dur = int(duration_seconds)
            if dur < 2 or dur > 15:
                raise ValueError(
                    f"H3Screenwriter: duration_seconds({dur}) 必须 2-15s "
                    "(bypass 模式仍用此值决定渲染帧数)。")
            width, height = self._resolve_resolution(aspect_ratio, resolution_mp)
            length = self._resolve_length(dur)
            # Bypass 模式：顶部概念框内容即最终 prompt。如果用户粘贴了旧版多镜
            # JSON array，则把元素用空行拼成一段文本，保持向后兼容。
            prompt_text = ov
            try:
                data = json.loads(ov)
                if isinstance(data, list):
                    prompt_text = "\n\n".join(str(p) for p in data)
                elif isinstance(data, dict):
                    prompt_text = "\n\n".join(str(p) for p in data.get("prompts", []))
            except Exception:
                pass
            print(f"[H3 AutoDirector] bypass_llm ON: reused prompt "
                  f"({len(prompt_text)} chars); LLM skipped.", flush=True)
            # bypass 模式不调用 LLM：无 {{DLG_N}} 占位符需要还原/校验，
            # 提前初始化避免下方 `if dlg_list:` 触发 UnboundLocalError。
            dlg_list = []
            # bypass 模式下如果开启了剧本模式，尝试解析用户粘贴的 JSON 剧本
            if script_mode:
                data, _err = _extract_script_json(ov)
                if data is not None:
                    characters, scenes, shots_list = _validate_and_normalize_script(data, dur)
            # v10.3: 还原 {{DLG_N}} -> <d>[语言] 原文</d>（代码级对话保留）
            if dlg_list:
                for _s in shots_list:
                    _s["prompt"] = _restore_dialogue(_s["prompt"], dlg_list)
            # v10.3b: 对话硬校验——用户台词原文必须出现在输出里，缺失则修正重试一次
            if dlg_list:
                import re as _re2
                _dlg_contents = [_re2.sub(r'^[「『\u201c\"\u201d]+|[」』\u201d\"\u201c]+$', '', d)
                                 for d in dlg_list]
                _joined = "".join(_s.get("prompt", "") for _s in shots_list)
                _missing = [c for c in _dlg_contents if c and c not in _joined]
                if _missing:
                    print(f"[H3 AutoDirector] 剧本模式检测到台词丢失 {_missing}，修正重试一次...",
                          flush=True)
                    _retry_msgs = messages + [
                        {"role": "assistant", "content": prompt_text},
                        {"role": "user", "content": (
                            "CORRECTION: you dropped the script's exact dialogue lines. "
                            "You MUST include each of these lines VERBATIM inside "
                            "<d>[Language] ... </d> in the matching shot's prompt: "
                            + json.dumps(_dlg_contents, ensure_ascii=False)
                            + ". Never translate or paraphrase them. Regenerate the "
                            "full JSON screenplay now.")}
                    ]
                    _retry_text = self._generate(_retry_msgs, temperature, seed, backend,
                                                 gguf_name, mmproj_name, n_gpu_layers,
                                                 context_size, keep_loaded,
                                                 llm_base_url, model, api_key) or ""
                    _retry_data, _retry_err = _extract_script_json(_retry_text)
                    if not _retry_err:
                        _rc, _rs, _rshots = _validate_and_normalize_script(_retry_data, dur)
                        if _rshots:
                            characters, scenes, shots_list = _rc, _rs, _rshots
                            print(f"[H3 AutoDirector] 修正重试成功: {len(shots_list)} 镜，台词已恢复。",
                                  flush=True)
                    else:
                        print(f"[H3 AutoDirector] 修正重试仍失败: {_retry_err} (保留第一次输出)",
                              flush=True)
                    # v10.1: 合并资产库信息
                    if has_assets:
                        characters, scenes = _merge_assets_into_output(
                            characters, scenes, char_map, scene_map)
                    shot_frames = _shots_to_frame_list(shots_list)
                    joined_prompt = _shots_to_joined_prompt(shots_list)
                    shots_prompts = [s["prompt"] for s in shots_list]
                    shot_characters = [",".join(s["characters"]) for s in shots_list]
                    shot_scenes = [s["scene"] for s in shots_list]
                    characters_json = json.dumps(characters, ensure_ascii=False)
                    scenes_json = json.dumps(scenes, ensure_ascii=False)
                    total_length = sum(shot_frames)
                    print(f"[H3 AutoDirector] bypass+剧本模式: {len(shots_list)} 镜.", flush=True)
                    return (json.dumps(shots_prompts, ensure_ascii=False), width, height, total_length)
                # 手打模式：concept_text 不是 JSON 剧本，则按行拆分为多镜。
                # bypass_llm + 剧本模式 下 Writer 变身手打分镜输入器，等价于 H3PromptSplit：
                # 每行 = 一个 clip，回车换行 = 下一段（# // 开头行跳过）。
                # 先试 JSON 数组文本（["镜一","镜二"]）→ 元素=段
                _json_parsed = None
                if ov.strip().startswith("["):
                    try:
                        _json_parsed = json.loads(ov)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        _json_parsed = None
                if isinstance(_json_parsed, list):
                    _lines = [str(x).strip() for x in _json_parsed
                              if str(x).strip() and not str(x).strip().startswith("//")]
                else:
                    _split_text = ov
                    _ov_lines = [l for l in _split_text.split("\n")
                                 if not l.strip().startswith("//")]
                    if any(not l.strip() for l in _ov_lines):
                        # 空行分组：空行=段分隔，镜头内多行合并为一段
                        _blocks = [b.strip() for b in re.split(r"\n\s*\n", "\n".join(_ov_lines))
                                   if b.strip()]
                        _lines = [re.sub(r"\s*\n\s*", " ", b) for b in _blocks]
                        _lines = [l for l in _lines
                                  if l and not l.strip().startswith("#")]
                    else:
                        _lines = [l.strip() for l in _ov_lines
                                  if l.strip() and not l.strip().startswith("#")]
                if len(_lines) >= 1:
                    _n = len(_lines)
                    _per = max(2.0, round(dur / _n, 1))
                    shot_frames = [self._resolve_length(_per)] * _n
                    joined_prompt = "\n\n".join(_lines)
                    print(f"[H3 AutoDirector] bypass+剧本模式(手打): {_n} 镜, 每镜 {_per}s; "
                          f"概念框按行拆分（不调用 LLM）。", flush=True)
                    return (joined_prompt, width, height, sum(shot_frames))
            _line_cnt = (concept_text or "").count("\n") + 1
            if not script_mode and _line_cnt > 1:
                print(f"[H3 AutoDirector] 提示：顶部概念框有 {_line_cnt} 行文本，但剧本模式(script_mode)未开启——"
                      "普通模式只输出 1 段。需要多段分镜：打开『剧本模式（结构化分镜输出）』，"
                      "或把手打分镜写到 H3PromptSplit 的输入框（每行一段，回车换行=下一段）。",
                      flush=True)
            return (prompt_text, width, height, length)

        if isinstance(task_mode, (list, tuple)):
            task_mode = task_mode[0] if task_mode else _DEFAULT_TASK_MODE
        if not isinstance(task_mode, str):
            task_mode = str(task_mode)
        # textarea 上挂 @ keyup 监听触发下拉菜单，文本写入 concept_text.value，
        # write() 直接用 concept_text 作主概念源。删除 concept_in 外部 STRING
        # 口（textarea 已支持 @ 插入参考素材，外接 STRING 不能 @ 是冗余）。
        concept = (concept_text or "").strip()

        # ---- setup 值全部来自本节点 widget（v8 删 style / num_shots）----
        # num_shots 已不再使用；视觉风格不再写死：改由 task_key 经
        # _get_style_contract() 推导（修复 v8 写死电影感导致任务模式"无效"）。
        # 注意：保留的形参默认值仅作兜底，绝不能在此再赋 _DEFAULT_*，否则
        # 会把用户在界面上的选择（画幅 / 时长 / 分辨率 …）强行覆盖回默认值。
        # 注意：保留的形参默认值仅作兜底，绝不能在此再赋 _DEFAULT_*，否则
        # 会把用户在界面上的选择（画幅 / 时长 / 分辨率 …）强行覆盖回默认值。

        if not concept:
            raise ValueError(
                "H3Screenwriter: 概念为空 — 在节点顶部『概念 (concept)』编辑器中输入 "
                "（按 @ 可选已加载的 Picture/Video/Audio 素材）。"
            )
        # duration_seconds 是实际渲染总秒数（硬控制）。H3 单段上限 15s。
        dur = int(duration_seconds)
        if dur < 2 or dur > 15:
            raise ValueError(
                f"H3Screenwriter: duration_seconds({dur}) 必须 2-15s（H3 单段视频上限）。"
            )
        # 镜头数硬上限：默认 fullreference 基础模板（REVERSE_INFERENCE_BASE）只说
        # "按镜头逐段写、尽量详细"，没有封顶；本地无审查 8B 模型在极大 max_tokens
        # 下会把 detailed_description 灌成数百个 [Shot N] 微镜头（≈300 个 shot，
        # 精确到每帧）。这里按时长给一个宏镜头预算（每 2-3 秒一个），配合上面收紧的
        # max_tokens 双重挡住 300-shot 膨胀（指令引导 + token 物理上限）。
        # shot 上限：每 3 秒一个（保守上限，配合"简单场景只出 1 镜"指令）。
        # 之前 ceil(dur/2)+2 导致 60s 视频被拆成 32 镜，LLM 把上限当目标。
        shot_cap = max(1, math.ceil(dur / 3))
        if backend == "HTTP endpoint":
            if not llm_base_url.strip():
                raise ValueError("H3Screenwriter: 'llm_base_url' is empty.")
            if not model.strip():
                raise ValueError("H3Screenwriter: 'model' is empty.")
        else:
            if not (gguf_name or "").strip():
                raise ValueError("H3Screenwriter: 'gguf_name' is empty for "
                                 "Local GGUF mode.")

        # 视觉风格契约改到任务模式解析之后注入（见下方 task_key 解析后），
        # 不再写死 Cinematic live-action —— 让任务模式真正决定风格。

        # code-level dialogue tagging (ported from micxin2025): guarantees
        # dialogue survives inside <d>[Language] ... </d> regardless of how weak
        # the local LLM is — the model can no longer "lose" the spoken lines.
        tagged_concept, _ = MX._tag_dialogue(concept)
        # v10.3: 剧本模式开启代码级对话保护（引号内台词 -> {{DLG_N}} 占位符 ->
        # 输出后还原 <d>[语言]原文</d>），弱模型不再可能把台词翻译成英文。
        if script_mode:
            protected_concept, dlg_list = _protect_dialogue(concept_text)
        else:
            protected_concept, dlg_list = concept, []
        # v10.4: 行内「」对话标记（兼容"场景描述 + 她说：「台词」"同行格式），
        # user_brief 用预打 <d> 标签版本；台词原文列表用于输出端校验。
        _inline_tagged, _inline_contents = _tag_inline_dialogue_lines(concept_text)
        if _inline_contents:
            dlg_list = ["「" + c + "」" for c in _inline_contents]

        # micxin2025 task-mode system prompt (16 templates incl. action transfer
        # / voice clone / dual dialogue). H3 JSON shot-array contract removed:
        # this node now outputs a single H3 official full-reference 6-section prompt.
        # v10: 剧本模式(script_mode=True)使用专用 JSON 输出提示词，输出结构化分镜。
        task_key = _resolve_task_key(task_mode)
        csp = (custom_system_prompt or "").strip()
        if script_mode:
            # 剧本模式：专用 JSON 分镜输出提示词（优先级最高，覆盖 task_mode 和 custom_system_prompt）
            # task_mode 选择「分镜模式」时用更严格的分镜转换提示词（镜头数量/顺序与用户一致）
            if task_key == "shot_split_mode":
                system_prompt = SHOT_SPLIT_SYSTEM_PROMPT
            elif task_key in CUSTOM_SKILLS:
                # 自定义 skill 叠加：保留剧本 JSON 输出契约，把 skill 的导演方法论追加在其后
                # （skill 的 system_prompt 是纯方法论覆盖层，不含六段式基础，不会与剧本契约冲突）
                _skill_sp = CUSTOM_SKILLS[task_key]["system_prompt"]
                system_prompt = SCRIPT_MODE_SYSTEM_PROMPT + "\n\n" + _skill_sp
            else:
                system_prompt = SCRIPT_MODE_SYSTEM_PROMPT
            # 对话保留规则（最高优先级）——所有剧本模式路径统一追加，
            # 防止本地弱模型把 <d> 内台词翻译/丢弃/编造。
            _copy_rule = (r"""# Dialogue Copy Rule (script mode, highest priority)
# The <d>[Language] ... </d> blocks inside the STORY CONCEPT are the user's EXACT
# dialogue lines. Copy each one VERBATIM (tags + language marker + text) into the
# corresponding shot's "prompt". NEVER translate, paraphrase, replace, or discard
# them; NEVER invent different dialogue when a concept line already exists. Losing
# or rewriting a dialogue block is a hard failure.""")
            system_prompt = (system_prompt + "\n\n" + MX.DIALOGUE_PRESERVE_RULE
                             + "\n\n" + _copy_rule)
        elif csp:
            system_prompt = csp
        else:
            system_prompt = _build_system_prompt(task_key)
        # 视觉风格契约：按任务模式推导（修复 v8 写死电影感导致其他模式"无效"）。
        style_contract = _get_style_contract(task_key)

        # 先解出实际渲染分辨率 / 帧数（写进 user_brief 让模型按画布构图，
        # 也直接作为返回值喂给 Ref2VA 的 width/height/length）。
        width, height = self._resolve_resolution(aspect_ratio, resolution_mp)
        length = self._resolve_length(dur)

        # v10.1: 资产库已在方法开头解析（char_map/scene_map/prop_map/has_assets）
        if has_assets and script_mode:
            print(f"[H3 AutoDirector] 资产库联动: {len(char_map)}角色, "
                  f"{len(scene_map)}场景, {len(prop_map)}道具.", flush=True)

        # ---- 翻译模式（script_mode + 分镜模式）：LLM 只做忠实翻译 ----
        # 不拆镜（拆段由 Split 按手写空行完成）、不扩写、不重构六段式——
        # 措辞忠实于用户手写，画面可控；输入不规范也不会被错误拆成多段。
        if script_mode and task_key == "shot_split_mode":
            if not (concept_text or "").strip():
                raise ValueError("H3Screenwriter: 分镜模式概念为空 — 请在概念框填写分镜。")
            # 代码级对话保护：引号内对话 → {{DLG_N}} 占位符，LLM 不会翻译占位符，
            # 输出后还原原文——对话保留不依赖模型自觉。
            protected_concept, dlg_list = _protect_dialogue(concept_text)
            # user message 只放分镜文本：8B 模型会把 user message 里的任何模板行
            # （VISUAL STYLE / ASPECT RATIO / NOTE）当内容照抄进输出，必须删干净。
            trans_msg = (
                f"SHOT LIST (分镜) to convert:\n{protected_concept}"
            )
            msgs = [
                {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
                {"role": "user", "content": trans_msg},
            ]
            translated = _clean_text(_strip_think(
                self._generate(msgs, temperature, seed, backend, gguf_name,
                               mmproj_name, n_gpu_layers, context_size, keep_loaded,
                               llm_base_url, model, api_key) or ""))
            if not translated:
                raise ValueError("H3Screenwriter: 翻译模式返回为空 — 请检查 LLM 配置。")
            if dlg_list:
                translated = _restore_dialogue(translated, dlg_list)
            # ---- 兜底：LLM 输出废了（模板泄漏/没翻译/空行结构被破坏）→ 直通用户原文 ----
            # 直通后 Split 按用户手写空行拆段照常工作，ClipChain AV 的
            # auto_h3_compile + auto_dialogue 自动补六段式和 <d> 对话标签。
            if _translation_failed(concept_text, translated, len(dlg_list)):
                print(f"[H3 AutoDirector] 翻译模式输出异常，降级直通用户原文 "
                      f"({len((concept_text or '').strip())} chars)——由 Split "
                      f"按空行拆段、ClipChain 自动六段式+<d>对话包装。", flush=True)
                translated = (concept_text or "").strip()
            print(f"[H3 AutoDirector] 翻译模式: 输出 {len(translated)} chars "
                  f"(对话保护 {len(dlg_list)} 处, 拆段由 Split 按空行完成).", flush=True)
            # 输出一段翻译文本（保留空行结构）→ Split 按用户手写空行拆段
            # length 用总时长帧数（段级时长由 ClipChain 面板控制）
            return (translated, width, height, self._resolve_length(dur))

        user_brief = (
            f"STORY CONCEPT (may be Chinese): {_inline_tagged if _inline_contents else tagged_concept}\n"
            f"VISUAL STYLE: {style_contract}\n"
            f"TOTAL DURATION: {dur} seconds (H3 hard cap 15s). "
            f"Plan the detailed_description timeline within this budget.\n"
            f"ASPECT RATIO: {aspect_ratio}  (render canvas {width}x{height}, ~{resolution_mp} MP)\n"
        )

        # v10.1: 剧本模式下注入资产库描述，让 LLM 知道有哪些资产可用
        if script_mode and has_assets:
            user_brief += _build_asset_brief(char_map, scene_map, prop_map)
        # v7.2 删 extra_instructions（旧位置 user_brief += EXTRA DIRECTION 行已删除）
        # 运镜短语/时机已移除（用户反馈导致闪退，改为纯概念驱动镜头语言）。
        # v10: 剧本模式用 JSON 分镜输出要求，普通模式用六段式文本要求。
        if script_mode:
            if task_key == "shot_split_mode":
                user_brief += (
                    f"SHOT BUDGET (STRICT SPLIT MODE): the user input IS a shot list — split EXACTLY "
                    f"along the user's markers (镜头N / Shot N / 【切场】 / blank-line separated blocks), "
                    f"same count, same order. FORBIDDEN: merge, add, drop, re-split or reorder. "
                    f"Total duration MUST equal {dur} seconds; honor per-shot durations if tagged. "
                    f"Each shot 2-6 seconds. Output the structured JSON screenplay now "
                    f"(characters + scenes + shots). Each shot's 'prompt' field is a complete "
                    f"self-contained H3 prompt for that shot alone."
                )
            else:
                user_brief += (
                    f"SHOT BUDGET: total duration = {dur} seconds. If the concept EXPLICITLY marks shots "
                    f"(镜头N / Shot N / 【切场】 / blank-line separated blocks), output EXACTLY that many "
                    f"shots in the same order — never merge marked shots. Only when NO shot markers exist, "
                    f"infer the count from narrative complexity: one continuous scene = EXACTLY 1 shot; "
                    f"split only on explicit scene changes / time jumps / distinct action phases. "
                    f"{shot_cap} is the absolute upper bound, NOT a target. Each shot 2-6 seconds. "
                    f"The sum of all shot durations MUST equal {dur}. "
                    f"Output the structured JSON screenplay now (characters + scenes + shots). "
                    f"Each shot's 'prompt' field is a complete self-contained H3 prompt for that shot alone."
                )
        else:
            user_brief += (
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
                f"wrap in JSON; output the plain text prompt directly."
            )

        # 视觉反推：有图时把图片随 user 消息一起送进 VLM（Local GGUF 走 mmproj /
        # HTTP 走 data URI）。repair 循环复用 messages，首条 user 消息里的图片
        # 会随上下文保留，无需重复注入。
        # 图片来源：_aio_ref_paths（JS 自动从 AIO 节点同步的图片路径，无需连线）。
        # v10.1: 资产库联动 — 把资产库中的角色/场景/道具图片路径合并到 _aio_ref_paths，
        # 这样 AIO 会自动加载这些参考图，Screenwriter 生成的提示词用 <Picture N> 引用。
        if has_assets:
            asset_paths = _extract_asset_image_paths(char_map, scene_map, prop_map)
            if asset_paths:
                if _aio_ref_paths and _aio_ref_paths.strip():
                    _aio_ref_paths = _aio_ref_paths.strip() + "\n" + asset_paths
                else:
                    _aio_ref_paths = asset_paths
                print(f"[H3 AutoDirector] 资产库图片已同步到 AIO 参考图.", flush=True)
        # 动态分辨率：按图片数量自动缩放，9 张也不会爆 n_ctx=8192。
        _loaded = _load_image_tensor_from_paths(_aio_ref_paths)
        if _loaded is not None:
            ref_images, img_count, used_max_side = _loaded
            _, h, w, _ = ref_images.shape
            print(f"[H3 AutoDirector] 从 AIO 自动同步加载 {img_count} 张参考图片 "
                  f"(动态缩放长边≤{used_max_side}, 实际 {w}x{h}, 无需连线)。", flush=True)
        else:
            ref_images = None
        img_contents = _images_to_contents(ref_images)
        if img_contents:
            if backend == "Local GGUF" and (not mmproj_name or mmproj_name == "None"):
                print("[H3 AutoDirector] 收到图片但 Local GGUF 的 mmproj 为 None，"
                      "视觉反推无效 —— 退回纯文本（请设置 mmproj_name）。",
                      flush=True)
                img_contents = []
            elif script_mode:
                # v10.7: 剧本模式放开视觉反推（限量）。
                # v10.2 曾一刀切跳过：资产库联动会把 28 张图全塞给 VLM，
                # 导致内存不足 / Media evaluation failed。少量参考图（四视图
                # 等 ≤ _SCRIPT_VISUAL_MAX 张）不存在该问题——LLM 看到图才能
                # 把人物穿搭/外貌锁进每镜描述，否则生成时穿搭随机。
                _SCRIPT_VISUAL_MAX = 4
                if len(img_contents) > _SCRIPT_VISUAL_MAX:
                    print(f"[H3 AutoDirector] 剧本模式：{len(img_contents)} 张参考图超过视觉反推"
                          f"上限({_SCRIPT_VISUAL_MAX})，跳过看图（LLM 只用文本描述，图片供渲染使用）。",
                          flush=True)
                    # 但仍要告诉 LLM「有图、要保留 <Picture N> 标签」——
                    # 否则弱模型不知道图片存在，生成的 prompt 里丢标签，首帧/参考图全失效。
                    user_brief += (
                        f"\nREFERENCE IMAGES AVAILABLE: {len(img_contents)} image(s) are attached "
                        f"for RENDERING (not for visual analysis — do not describe their pixels). "
                        f"If the user's concept references them with <Picture N> tags, you MUST "
                        f"restate the exact same <Picture N> tag in EVERY shot prompt that uses that "
                        f"image (verbatim). NEVER invent a <Picture N> the concept did not include.\n"
                    )
                    img_contents = []
                else:
                    print(f"[H3 AutoDirector] 剧本模式：{len(img_contents)} 张参考图送视觉反推"
                          f"(上限 {_SCRIPT_VISUAL_MAX}，VLM 看图锁定人物/穿搭)。", flush=True)
                    user_brief += (
                        f"\nREFERENCE IMAGES ATTACHED (visual): {len(img_contents)} image(s) are "
                        f"provided visually — ANALYZE them and LOCK the subject's identity, outfit, "
                        f"hair, props and scene into EVERY shot's detailed_description so the "
                        f"generated video stays consistent with the reference. Restate the exact "
                        f"<Picture 1>..<Picture {len(img_contents)}> tag verbatim in every shot "
                        f"prompt that shows the subject. NEVER invent assets beyond these images.\n"
                    )
            else:
                user_brief += (
                    f"\nREFERENCE IMAGES ATTACHED: {len(img_contents)} image(s) are "
                    f"provided visually (see attached). Reference them in the prompt "
                    f"with <Picture 1>..<Picture {len(img_contents)}> as the system "
                    f"rules describe; do NOT invent assets beyond these.\n"
                )

        if img_contents:
            user_content = [{"type": "text", "text": user_brief}] + img_contents
        else:
            user_content = user_brief

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # v10.8: LLM 输出缓存 —— 同一概念/参数只反推一次。
        # 命中时跳过 LLM（含修正重试），直接复用上次反推的最终分镜，
        # 保证提示词字节级不变 → 下游 ClipChain 段缓存可稳定命中。
        _llm_cache_key = None
        if script_mode and task_key != "shot_split_mode":
            _llm_cache_key = _make_llm_cache_key(
                concept_text, task_key, script_mode, aspect_ratio, resolution_mp,
                dur, backend, gguf_name, mmproj_name, context_size, model,
                llm_base_url, custom_system_prompt, _aio_ref_paths, asset_library)
            _cached = _llm_cache_load(_llm_cache_key)
            if _cached is not None:
                try:
                    _c_characters, _c_scenes, _c_shots = _cached
                    if not _c_shots:
                        raise ValueError("缓存空分镜")
                    shot_frames = _shots_to_frame_list(_c_shots)
                    joined_prompt = _shots_to_joined_prompt(_c_shots)
                    shots_prompts = [s["prompt"] for s in _c_shots]
                    shot_characters = [",".join(s["characters"]) for s in _c_shots]
                    shot_scenes = [s["scene"] for s in _c_shots]
                    characters_json = json.dumps(_c_characters, ensure_ascii=False)
                    scenes_json = json.dumps(_c_scenes, ensure_ascii=False)
                    total_length = sum(shot_frames)
                    print(f"[H3 AutoDirector] LLM 输出缓存命中: {len(_c_shots)} 镜"
                          f"(key {_llm_cache_key[:8]}…)，跳过 LLM 反推。", flush=True)
                    return (json.dumps(shots_prompts, ensure_ascii=False),
                            width, height, total_length)
                except Exception as e:
                    print(f"[H3 AutoDirector] LLM 缓存命中但重建输出失败，"
                          f"重新反推: {e}", flush=True)
                    _llm_cache_key = None

        try:
            prompt_text = self._generate(messages, temperature, seed, backend, gguf_name,
                                       mmproj_name, n_gpu_layers, context_size, keep_loaded,
                                       llm_base_url, model, api_key)
        except _ContextOverflow:
            if backend == "Local GGUF" and not keep_loaded:
                _unload_local()
            raise ValueError(
                f"H3Screenwriter: 上下文窗口不足 (context_size={context_size})。"
                "请调大节点上的『上下文调节』参数，或缩短概念描述/减少参考图数量。")

        prompt_text = (prompt_text or "").strip()
        print(f"[H3 AutoDirector] generated prompt ({len(prompt_text)} chars).",
              flush=True)
        if backend == "Local GGUF" and not keep_loaded:
            _unload_local()

        # v10: 剧本模式 — 解析 JSON 分镜，输出结构化数据
        if script_mode:
            data, err = _extract_script_json(prompt_text)
            if err:
                raise ValueError(f"H3Screenwriter 剧本模式解析失败: {err}")
            characters, scenes, shots_list = _validate_and_normalize_script(data, dur)
            # v10.9: 主路径还原 {{DLG_N}} -> <d>[语言] 原文</d>（代码级对话保留）。
            # LLM 反推时概念里的 <d> 块/引号台词已被保护为占位符，输出必须还原，
            # 否则弱模型要么丢弃占位符、要么自己包一层格式错误的 <d>。
            if dlg_list:
                for _s in shots_list:
                    _s["prompt"] = _restore_dialogue(_s["prompt"], dlg_list)
            # v10.6: 对话保底（主 LLM 路径）——台词原文必须出现在输出里，
            # 空 prompt 镜头也会被发现；缺失则修正重试一次，再兜底注入台词。
            if dlg_list:
                import re as _re3
                _dlg_contents = [_re3.sub(r'^[「『\u201c\"\u201d]+|[」』\u201d\"\u201c]+$', '', d)
                                 for d in dlg_list]

                def _miss_of(_shots):
                    _j = "".join(_s.get("prompt", "") for _s in _shots)
                    return [c for c in _dlg_contents if c and c not in _j]

                _has_empty = any(not _s.get("prompt", "").strip() for _s in shots_list)
                _missing = _miss_of(shots_list)
                if _missing or _has_empty:
                    print(f"[H3 AutoDirector] 剧本模式检测到台词丢失 {_missing}"
                          + ("/空镜头" if _has_empty else "") + "，修正重试一次...", flush=True)
                    _retry_msgs = messages + [
                        {"role": "assistant", "content": prompt_text},
                        {"role": "user", "content": (
                            "CORRECTION: you dropped script dialogue or left a shot's "
                            "detailed_description empty. You MUST include each of these "
                            "lines VERBATIM inside <d>[Language] ... </d> in the matching "
                            "shot's prompt: " + json.dumps(_dlg_contents, ensure_ascii=False)
                            + ". Keep exactly %d shots (one per story beat) and give EVERY "
                              "shot a complete non-empty detailed_description. Never "
                              "translate or paraphrase. Regenerate the full JSON screenplay now."
                              % len(shots_list))}
                    ]
                    _retry_text = self._generate(_retry_msgs, temperature, seed, backend,
                                                 gguf_name, mmproj_name, n_gpu_layers,
                                                 context_size, keep_loaded,
                                                 llm_base_url, model, api_key) or ""
                    _retry_data, _retry_err = _extract_script_json(_retry_text)
                    if not _retry_err:
                        _rc, _rs, _rshots = _validate_and_normalize_script(_retry_data, dur)
                        if _rshots:
                            _miss2 = _miss_of(_rshots)
                            _empty2 = any(not _s.get("prompt", "").strip() for _s in _rshots)
                            _score1 = len(_missing) + (1 if _has_empty else 0)
                            _score2 = len(_miss2) + (1 if _empty2 else 0)
                            if _score2 < _score1:
                                characters, scenes, shots_list = _rc, _rs, _rshots
                                print(f"[H3 AutoDirector] 修正重试成功: {len(shots_list)} 镜，"
                                      f"缺失降至 {len(_miss2)}。", flush=True)
                            else:
                                print("[H3 AutoDirector] 修正重试未改善，保留第一次输出。",
                                      flush=True)
                    else:
                        print(f"[H3 AutoDirector] 修正重试仍失败: {_retry_err} (保留第一次输出)",
                              flush=True)
                # v10.6b: 兜底注入——仍缺失的台词按概念顺序补进对应镜头（第N句->第N镜）
                _jf = "".join(_s.get("prompt", "") for _s in shots_list)
                _still = [c for c in _dlg_contents if c and c not in _jf]
                for _k, _c in enumerate(_dlg_contents):
                    if _c in _still:
                        _tgt = shots_list[_k] if _k < len(shots_list) else shots_list[-1]
                        # <d> 块原样注入（保留语言标记）；引号台词按 H3 格式包裹
                        if _re3.match(r'^<d>(\[[^\]]*\])?\s*.*?</d>$', _c, flags=_re3.S):
                            _inject = _c
                        else:
                            _lang = _detect_dialogue_language(_c)
                            _inject = '<d>[%s] %s</d>' % (_lang, _c)
                        _tgt["prompt"] = (_tgt.get("prompt", "").rstrip() + " " + _inject)
                if _still:
                    print(f"[H3 AutoDirector] 台词兜底注入 {len(_still)} 句: {_still}", flush=True)
                _jf2 = "".join(_s.get("prompt", "") for _s in shots_list)
                _still2 = [c for c in _dlg_contents if c and c not in _jf2]
                _empty3 = [i + 1 for i, _s in enumerate(shots_list)
                           if not _s.get("prompt", "").strip()]
                if _still2 or _empty3:
                    print(f"[H3 AutoDirector] 警告: 最终仍有台词缺失 {_still2} / 空镜头 {_empty3}",
                          flush=True)
            # v10.1: 把资产库的图片路径/描述合并到输出的 characters/scenes
            if has_assets:
                characters, scenes = _merge_assets_into_output(
                    characters, scenes, char_map, scene_map)
            # v10.8: 写 LLM 输出缓存（同一概念/参数下次直接命中，无需重跑 LLM）
            if _llm_cache_key:
                _llm_cache_save(_llm_cache_key, [characters, scenes, shots_list])
            shot_frames = _shots_to_frame_list(shots_list)
            joined_prompt = _shots_to_joined_prompt(shots_list)
            shots_prompts = [s["prompt"] for s in shots_list]
            shot_characters = [",".join(s["characters"]) for s in shots_list]
            shot_scenes = [s["scene"] for s in shots_list]
            characters_json = json.dumps(characters, ensure_ascii=False)
            scenes_json = json.dumps(scenes, ensure_ascii=False)
            total_length = sum(shot_frames)
            print(f"[H3 AutoDirector] 剧本模式: {len(shots_list)} 镜, "
                  f"总时长 {dur}s, 总帧数 {total_length}."
                  + (f" 资产联动: {len(char_map)}角色/{len(scene_map)}场景." if has_assets else ""),
                  flush=True)
            return (json.dumps(shots_prompts, ensure_ascii=False), width, height, total_length)

        # 普通模式：把分辨率 / 时长直接作为 INT 输出，驱动下游 Ref2VA。
        # 新增的 6 个剧本模式端口返回空值，旧工作流不受影响。
        _line_cnt = (concept_text or "").count("\n") + 1
        if not script_mode and _line_cnt > 1:
            print(f"[H3 AutoDirector] 提示：顶部概念框有 {_line_cnt} 行文本，但剧本模式(script_mode)未开启——"
                  "普通模式只输出 1 段。需要多段分镜：打开『剧本模式（结构化分镜输出）』，"
                  "或把手打分镜写到 H3PromptSplit 的输入框（每行一段，回车换行=下一段）。",
                  flush=True)
        prompt_text = _fix_dialect_tags(prompt_text, concept_text or "")
        return (prompt_text, width, height, length)

    # ---- LLM call ----------------------------------------------------------
    @staticmethod
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
                    f"H3Screenwriter: LLM overall timeout ({overall_timeout}s) "
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
            f"H3Screenwriter: LLM call failed after {max_retries} attempts: "
            f"{last_err}{hint}")

    def _generate(self, messages, temperature, seed, backend, gguf_name,
                  mmproj_name, n_gpu_layers, n_ctx, keep_loaded,
                  llm_base_url, model, api_key):
        if backend == "Local GGUF":
            llm = _load_local_llm((gguf_name or "").strip(),
                                  (mmproj_name or "None").strip(),
                                  n_gpu_layers, n_ctx)
            return _call_local_llm(llm, messages, temperature, seed)
        return self._call_llm(llm_base_url.strip(), model.strip(),
                              api_key.strip(), messages, temperature, seed)

    # ---- helpers -----------------------------------------------------------
    # 运镜短语/时机已移除（2026-08-25，用户反馈导致闪退）。


NODE_CLASS_MAPPINGS = {"H3PromptWriter": H3PromptWriter}
NODE_DISPLAY_NAME_MAPPINGS = {"H3PromptWriter": "H3 PromptWriter (micxin)"}
