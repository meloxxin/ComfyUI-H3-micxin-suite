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
    return re.sub(r"<think>[\s\S]*?</think>|<thinking>[\s\S]*?<\/thinking>",
                  "", text, flags=re.IGNORECASE).strip()


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
    # 2026-08-22: 自动降级重试。16G 显卡与 Krea2 共存时，全量 offload + 大上下文
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
    try:
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
    if not content.strip():
        raise ValueError("Local LLM returned empty content.")
    return content


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


def _load_custom_skills():
    """Load custom skill templates from skills/ directory."""
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(SKILLS_DIR, fn)
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
    return skills


CUSTOM_SKILLS = _load_custom_skills()
CUSTOM_SKILL_KEYS = list(CUSTOM_SKILLS.keys())


def _build_system_prompt(task_key):
    """Return system prompt for built-in micxin2025 styles or custom skills."""
    if task_key in CUSTOM_SKILLS:
        info = CUSTOM_SKILLS[task_key]
        sp = info["system_prompt"]
        if not sp:
            sp = MX._build_system_prompt("fullreference")
        elif not info.get("standalone", True):
            # Append-style: base + skill + dialogue preservation rule
            sp = (MX.REVERSE_INFERENCE_BASE + "\n\n" + sp
                  + "\n\n" + MX.DIALOGUE_PRESERVE_RULE)
        else:
            # Standalone: skill + dialogue preservation rule
            sp = sp + "\n\n" + MX.DIALOGUE_PRESERVE_RULE
        return sp
    return MX._build_system_prompt(task_key)


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
    TASK_MODE_OPTIONS[:] = [dn for dn, _ in MX.STYLE_OPTIONS]
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
TASK_MODE_OPTIONS = [dn for dn, _ in MX.STYLE_OPTIONS] + [
    CUSTOM_SKILLS[k]["display_name"] for k in CUSTOM_SKILL_KEYS
]
_DEFAULT_TASK_MODE = TASK_MODE_OPTIONS[0] if TASK_MODE_OPTIONS else ""
_DEFAULT_NUM_SHOTS = 1
_DEFAULT_DURATION = 10
_DEFAULT_ASPECT = "16:9"
_DEFAULT_MP = "0.4"

# 上下文窗口（用户可调）。默认 8192：16G 显卡 + Krea2 共存时 32K KV cache(~4G)
# 会导致 "Failed to create context with model"。screenwriter 输出上限 4096 token，
# 输入+系统提示约 1-2K，单图反推 1024 token/图，8192 足够日常使用。
# 多图反推或长概念时可调大到 16384/32768（显存充足时）。
_DEFAULT_CONTEXT_SIZE = 8192
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
      "prompt": "Cinematic live-action. <persistent scene + character appearance, restated verbatim>. [Shot 1] <action + camera>. <physical sound>. <Speaker (S1) says: <d>[Language] verbatim line</d> if any>. overall_soundscape: <ambience>. non_diegetic_music: <score or N/A>."
    }
  ]
}

# HARD RULES
1. NARRATIVE in ENGLISH inside each shot's "prompt". Dialogue / lyrics keep original language inside <d>[Language] ... </d>.
2. EACH SHOT IS SELF-CONTAINED: restate the persistent scene location, the character's exact appearance, and the visual style VERBATIM in every shot's prompt. Do NOT "refer back" to shot 1.
3. SHOT BUDGET: total duration across all shots must equal the user's requested TOTAL DURATION. Each shot 2-6 seconds. Max 6 shots for a 15s clip.
4. CHARACTERS: every character gets a stable id (S1, S2, ...) and appears in the "characters" list. A shot's "characters" array lists only who is on-screen.
5. SCENES: every location gets a stable id (scene1, scene2, ...). A shot's "scene" field is exactly one scene id.
6. DIALOGUE: if the concept contains dialogue, tag it with the speaker id inside the shot prompt: `<Subject N> (Sx) says: <d>[Language] verbatim line</d>`. Use code-level tagging rules: Cantonese detection for 嘅哋咗佢嘢乜唔咁啲㗎喇喎啩咩冇噶.
7. CAMERA: one clear camera move per shot (type + amplitude + speed).
8. The "prompt" field for each shot is the COMPLETE H3 prompt for that shot alone — it must render correctly if fed to H3 by itself.
9. Output ONLY the JSON object. First character must be "{" and last must be "}". No code fences, no explanation, no thinking out loud.

# REFERENCE MATERIALS
If the user's concept contains <Picture N> / <Video N> / <Audio N> tags, weave them into the relevant shots' prompts. Restate the same tag in EVERY shot that uses that asset. NEVER invent a reference tag the concept did not include."""


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
        if not prompt:
            continue
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
                               "开启 bypass_llm 后，把上一轮的 h3_script 或 prompts_json "
                               "直接粘贴到这里复用，不再调用 LLM。"}),
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
                # === 1.5) 剧本模式开关（v10 新增 — 为短剧打造）===
                # 开启后 LLM 输出结构化 JSON（角色档案+场景档案+分镜列表），
                # 节点解析后输出每镜独立的 prompt + 时长 + 角色/场景ID。
                # 关闭时保持旧行为：输出单一 prompt 字符串。
                "script_mode": ("BOOLEAN", {
                    "default": False,
                    "label_on": "剧本模式（结构化分镜输出）",
                    "label_off": "普通模式（单段提示词）",
                    "tooltip": "【为短剧打造】开启后 LLM 输出结构化分镜 JSON，节点解析后输出："
                               "shots(每镜prompt列表) / shot_durations(每镜帧数) / "
                               "shot_characters(每镜角色ID) / shot_scenes(每镜场景ID) / "
                               "characters_json(角色档案) / scenes_json(场景档案)。"
                               "下游可逐镜渲染、批量生成、单镜重跑。"
                               "关闭时保持旧行为：仅输出单一 prompt 字符串。"}),
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
                "seed": ("INT", {"default": 0, "min": 0,
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
                # === 9) 资产库输入（v10.1 新增 — 资产联动）===
                # 接受外部资产库 JSON（角色/场景/道具的图片路径+描述），
                # 剧本模式下自动引用资产，把资产图同步给 AIO。
                # 格式A: {"characters":[{"id":"S1","name":"女主","image":"path.png"}], "scenes":[...], "props":[...]}
                # 格式B: {"S1":{"name":"女主","image":"path.png"}, "scene1":{...}}
                "asset_library": ("STRING", {
                    "default": "",
                    "forceInput": True,
                    "tooltip": "【资产联动】外接资产库 JSON（剧本模式下生效）。"
                               "包含角色/场景/道具的图片路径和描述。"
                               "连接后：①LLM生成提示词时自动引用资产ID；"
                               "②资产图片路径自动同步给 H3 R2VA AIO（无需连线）；"
                               "③输出的 characters_json/scenes_json 包含图片路径，"
                               "可直接驱动资产生成工作流。"
                               "格式: {\"characters\":[{\"id\":\"S1\",\"name\":\"女主\",\"description\":\"...\",\"image\":\"path.png\"}], \"scenes\":[...], \"props\":[...]}"}),
            },
        }

    # 返回顺序（v10 2026-08-25）：新增剧本模式结构化输出。
    # 普通模式(script_mode=False)：shots/durations/characters/scenes 返回空列表，
    #   characters_json/scenes_json 返回空字符串 — 旧工作流不受影响。
    # 剧本模式(script_mode=True)：返回每镜独立数据，下游可逐镜渲染/批量生成。
    RETURN_TYPES = ("STRING", "INT", "INT", "INT",
                    "STRING", "INT", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "prompt (H3 官方 skill 提示词)",
        "width", "height", "length",
        "shots (每镜prompt列表)",
        "shot_durations (每镜帧数)",
        "shot_characters (每镜角色ID)",
        "shot_scenes (每镜场景ID)",
        "characters_json (角色档案)",
        "scenes_json (场景档案)",
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
              # 1.5) 剧本模式开关（v10 新增 — 为短剧打造）
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
              # 9) 资产库（v10.1 新增 — 资产联动）
              asset_library=""):
        # ---- 概念源：仅从顶部 concept_text 原生 STRING widget 拿 ----
        # v7.2 改用 LiteGraph 原生 STRING widget（不再用 addDOMWidget）。

        # v10.1: 提前解析资产库（bypass 模式和正常模式都要用）
        char_map, scene_map, prop_map = _parse_asset_library(asset_library)
        has_assets = bool(char_map or scene_map or prop_map)

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
            # bypass 模式下如果开启了剧本模式，尝试解析用户粘贴的 JSON 剧本
            if script_mode:
                data, _err = _extract_script_json(ov)
                if data is not None:
                    characters, scenes, shots_list = _validate_and_normalize_script(data, dur)
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
                    print(f"[H3 AutoDirector] bypass+剧本模式: {len(shots_list)} 镜."
                          + (f" 资产联动: {len(char_map)}角色/{len(scene_map)}场景." if has_assets else ""),
                          flush=True)
                    return (joined_prompt, width, height, total_length,
                            shots_prompts, shot_frames, shot_characters, shot_scenes,
                            characters_json, scenes_json)
            return (prompt_text, width, height, length,
                    [], [], [], [], "", "")

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
        shot_cap = max(2, math.ceil(dur / 2) + 2)
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

        # micxin2025 task-mode system prompt (16 templates incl. action transfer
        # / voice clone / dual dialogue). H3 JSON shot-array contract removed:
        # this node now outputs a single H3 official full-reference 6-section prompt.
        # v10: 剧本模式(script_mode=True)使用专用 JSON 输出提示词，输出结构化分镜。
        task_key = _resolve_task_key(task_mode)
        csp = (custom_system_prompt or "").strip()
        if script_mode:
            # 剧本模式：专用 JSON 分镜输出提示词（优先级最高，覆盖 task_mode 和 custom_system_prompt）
            system_prompt = SCRIPT_MODE_SYSTEM_PROMPT
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

        user_brief = (
            f"STORY CONCEPT (may be Chinese): {tagged_concept}\n"
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
            user_brief += (
                f"SHOT BUDGET: total duration = {dur} seconds. Split into {shot_cap} shots max, "
                f"each shot 2-6 seconds. The sum of all shot durations MUST equal {dur}. "
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
                # v10.2: 剧本模式下不把资产库图片传给 LLM。
                # LLM 只需要资产的名称+描述（已在 user_brief 中通过 _build_asset_brief 注入），
                # 图片是给 H3 渲染节点用的（通过 _aio_ref_paths 同步到 AIO）。
                # 把 28 张图传给 Qwen3-VL 会导致内存不足 / Media evaluation failed。
                print(f"[H3 AutoDirector] 剧本模式：跳过 {len(img_contents)} 张参考图的视觉反推"
                      f"（LLM 只用文本描述，图片供渲染使用）。", flush=True)
                img_contents = []
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
            # v10.1: 把资产库的图片路径/描述合并到输出的 characters/scenes
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
            print(f"[H3 AutoDirector] 剧本模式: {len(shots_list)} 镜, "
                  f"总时长 {dur}s, 总帧数 {total_length}."
                  + (f" 资产联动: {len(char_map)}角色/{len(scene_map)}场景." if has_assets else ""),
                  flush=True)
            return (joined_prompt, width, height, total_length,
                    shots_prompts, shot_frames, shot_characters, shot_scenes,
                    characters_json, scenes_json)

        # 普通模式：把分辨率 / 时长直接作为 INT 输出，驱动下游 Ref2VA。
        # 新增的 6 个剧本模式端口返回空值，旧工作流不受影响。
        return (prompt_text, width, height, length,
                [], [], [], [], "", "")

    # ---- LLM call ----------------------------------------------------------
    @staticmethod
    def _call_llm(url, model, api_key, messages, temperature, seed,
                  timeout=120, max_retries=1, retry_delay=8,
                  disable_thinking=True, overall_timeout=150):
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
        raise RuntimeError(
            f"H3Screenwriter: LLM call failed after {max_retries} attempts: "
            f"{last_err}")

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