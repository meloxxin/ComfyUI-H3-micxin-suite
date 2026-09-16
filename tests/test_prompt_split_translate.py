# -*- coding: utf-8 -*-
"""H3PromptSplitTranslate (micxin) 测试：拆分 / 对话占位保护 / schema（4 段上限）。

不加载真实 GGUF，只测：_split_to_prompt_list 复用、引号对话占位往返、
系统提示词规则、backend 默认 Local、execute 是 classmethod、GET_SCHEMA 校验。
"""
import importlib.util
import os
import sys
import types

import pytest

from conftest import requires_comfy


def _load_ad_package():
    if "h3_ad" in sys.modules:
        return
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ad = os.path.join(repo, "ComfyUI-H3-AutoDirector")
    pkg = types.ModuleType("h3_ad")
    pkg.__path__ = [ad]
    pkg.__package__ = "h3_ad"
    sys.modules["h3_ad"] = pkg
    import h3_ad.h3_prompt_split_translate  # noqa: F401


_load_ad_package()
from h3_ad.h3_prompt_split_translate import (  # noqa: E402
    _protect_dialogues,
    _restore_dialogues,
    _translate_segment,
    _rewrite_segment,
    _six_section_ok,
    _TRANSLATE_SYS,
    H3PromptSplitTranslate,
    MAX_SEGMENTS,
    _split_segments,
    _batch_rewrite_segments,
    _ensure_subject_defs,
)


# ---------------- 对话占位保护（核心：防 LLM 翻对话） ----------------

def test_protect_quoted_chinese_dialogue():
    """S2说："你凭什么删我东西……" 引号内中文 → 占位符，LLM 碰不到。"""
    text = 'summary: S2说："你凭什么删我东西……" lips trembling.'
    masked, protected = _protect_dialogues(text)
    assert "你凭什么删我东西" not in masked
    assert "<<<DIALOGUE_0>>>" in masked
    assert len(protected) == 1
    assert protected[0][0] == "你凭什么删我东西……"


def test_restore_dialogues_roundtrip():
    text = 'S2 says: "你凭什么删我东西……"'
    masked, protected = _protect_dialogues(text)
    # 模拟 LLM 翻译：占位符保留、周围变英文
    translated = "The woman says: <<<DIALOGUE_0>>>"
    restored = _restore_dialogues(translated, protected)
    assert restored == 'The woman says: "你凭什么删我东西……"'
    assert "你凭什么删我东西" in restored


def test_protect_ignores_english_quotes():
    """引号内无中文不保护（英文直接翻译）。"""
    text = 'summary: S1 says: "Hello world"'
    masked, protected = _protect_dialogues(text)
    assert "Hello world" in masked
    assert protected == []


def test_translate_sys_protects_quoted_dialogue():
    assert "ANY quoted dialogue" in _TRANSLATE_SYS
    assert "KEEP the original text EXACTLY" in _TRANSLATE_SYS
    assert "keep it verbatim" in _TRANSLATE_SYS


# ---------------- 拆分（复用 H3PromptSplit 逻辑） ----------------

@requires_comfy
def test_split_json_array():
    """JSON 数组 → 按段拆分（复用 H3PromptSplit 的 _split_to_prompt_list）。"""
    from h3_ad.h3_prompt_split_translate import _split_to_prompt_list
    raw = '["镜1 场景。S1说：\\"你好\\"", "镜2 场景。S2说：\\"你凭什么删我东西……\\""]'
    segs = _split_to_prompt_list(raw)
    assert len(segs) == 2
    assert "镜1" in segs[0] and "镜2" in segs[1]


# ---------------- schema ----------------

@requires_comfy
def test_schema_max_4_and_local_backend():
    schema = H3PromptSplitTranslate.define_schema()
    assert schema.node_id == "H3PromptSplitTranslate"

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert inputs["backend"].default == "Local GGUF"
    # 输出 = 4 路 prompt + report + width + height + length + fixed_json
    outs = schema.outputs
    assert len(outs) == MAX_SEGMENTS + 5
    assert MAX_SEGMENTS == 4


@requires_comfy
def test_execute_is_classmethod_and_validates():
    assert type(H3PromptSplitTranslate.__dict__["execute"]) is classmethod
    schema = H3PromptSplitTranslate.GET_SCHEMA()  # 走 VALIDATE_CLASS，复现用户报错路径
    assert schema.node_id == "H3PromptSplitTranslate"


def test_registered():
    import h3_ad.h3_prompt_split_translate as m
    assert "H3PromptSplitTranslate" in m.NODE_CLASS_MAPPINGS
    # 旧 H3PromptTranslate 已从 __init__ 注册移除（合并进本节点）
    init_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "ComfyUI-H3-AutoDirector", "__init__.py")
    init_src = open(init_path, encoding="utf-8").read()
    assert "h3_prompt_translate" not in init_src
    assert "h3_prompt_split_translate" in init_src

# ---------------- 宽高长 + 参考图过图 ----------------

def test_resolve_resolution_length():
    """分辨率只认官方表 14 档（严格 32 对齐精确值），表外值一律回退默认。"""
    from h3_ad.h3_prompt_split_translate import (
        _resolve_resolution, _resolve_length, _H3_RES_OPTIONS,
    )
    # 19 档（0.2~2.0，0.1 步进）全部走 MP 公式，32 对齐（与 PromptWriter 一致）
    for opt in _H3_RES_OPTIONS:
        w, h = _resolve_resolution("16:9", opt)
        assert w % 32 == 0 and h % 32 == 0, f"{opt}MP -> {w}x{h} 未对齐 32"
    # 1.0MP 16:9 官方推荐表值 = 1376x768（特判，不按公式 1344x736）
    assert _resolve_resolution("16:9", "1.0") == (1376, 768)
    assert _resolve_resolution("16:9", "1") == (1376, 768)
    # 表外值（0.95 / 0.98 / 0.25 不在 19 档）→ 回退默认 0.4 → 公式 832x480
    for bad in ("0.95", "0.98", "0.25"):
        assert _resolve_resolution("16:9", bad) == (832, 480), bad
    # 1.3 是合法档（19 档内）→ 公式 1536x864
    assert _resolve_resolution("16:9", "1.3") == (1536, 864)
    # 其他画幅：同档 MP 公式 32 对齐（画幅可切换）
    w, h = _resolve_resolution("9:16", "1.0")
    assert w % 32 == 0 and h % 32 == 0 and w < h and (w, h) != (1344, 736)
    assert _resolve_resolution("1:1", "0.5")[0] == _resolve_resolution("1:1", "0.5")[1]
    assert _resolve_length(5.0) == 124  # 5s → 120 帧 → 对齐 length % 17 == 5


@requires_comfy
def test_schema_has_wh_and_aio_ref():
    schema = H3PromptSplitTranslate.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert "_aio_ref_paths" in inputs  # 参考图过图（JS 从 AIO 同步）
    assert "report_inline" in inputs  # 内嵌 report 槽（JS 从 executed 事件写入，ui 包装双保险）
    assert inputs["aspect_ratio"].default == "16:9"
    # resolution_mp 是纯档位下拉（19 档 0.2~2.0，0.1 步进），与 promptwriter 一致
    assert getattr(inputs["resolution_mp"], "options", None) is not None
    assert len(inputs["resolution_mp"].options) == 19
    assert inputs["resolution_mp"].options[0] == "0.2"
    assert inputs["resolution_mp"].options[8] == "1.0"
    assert inputs["resolution_mp"].options[-1] == "2.0"
    assert inputs["resolution_mp"].default == "0.4"
    assert "x" not in inputs["resolution_mp"].options[0]  # 不带横屏尺寸

    outs = [getattr(o, "id", None) or getattr(o, "name", None) for o in schema.outputs]
    assert "width" in outs and "height" in outs and "length" in outs
    # 顺序：prompt_0..3 → width/height/length → fixed_json → report（最后）
    assert outs[:4] == ["prompt_0", "prompt_1", "prompt_2", "prompt_3"]
    assert outs[4:7] == ["width", "height", "length"]
    assert outs[7] == "fixed_json"
    assert outs[8] == "report"  # report 放最后

# ---------------- 内嵌 H3PromptFix（raw_text → 修复 → 拆分） ----------------

def test_embedded_fix_path():
    """raw_text 原始剧本 → 内部 fix_prompt → 六段式 JSON → 拆段（合并 H3PromptFix 核心）。"""
    from h3_ad.h3_prompt_split_translate import _split_to_prompt_list
    from h3_ad.h3_prompt_fix import fix_prompt
    story = (
        "镜头一（雨夜·便利店外景）：<Picture 1> 女主在门口锁门，路灯下等人，雨声渐密。"
        "她说：「下雨了，真冷。」\n\n"
        "镜头二（黎明·便利店室内）：女主在收银台擦杯子，男人推门进来。男人说：「一杯拿铁，谢谢。」"
    )
    fixed, status = fix_prompt(story, 0)
    assert "2 segment" in status or "Rebuilt" in status
    segs = _split_to_prompt_list(fixed)
    assert len(segs) >= 1
    joined = "\n".join(segs)
    # 对话原文必须保留在拆出的段里（中文对话不丢）
    assert "下雨了" in joined or "真冷" in joined


@requires_comfy
def test_schema_has_raw_text_and_fixed_json_out():
    schema = H3PromptSplitTranslate.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert "raw_text" in inputs
    assert "expected_segments" in inputs
    assert "prompts_json" not in inputs  # 已删除：外部 JSON 直接贴 raw_text（fix 会保留）

    outs = [getattr(o, "id", None) or getattr(o, "name", None) for o in schema.outputs]
    assert outs[7] == "fixed_json"  # JSON 预览输出
    assert outs[8] == "report"      # report 最后
    assert len(outs) == 9

# ---------------- 省显存 & 绕过 LLM ----------------

@requires_comfy
def test_schema_keep_loaded_off_and_bypass():
    """keep_loaded 默认 OFF（跑完即卸）；bypass_llm 存在；无 segment_prompts。"""
    schema = H3PromptSplitTranslate.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert inputs["keep_loaded"].default is False
    assert "bypass_llm" in inputs
    assert inputs["bypass_llm"].default is False
    assert not any(str(k).startswith("segment_prompts") for k in inputs)


# ---------------- 六段式反推（一步替代 翻译→再接 PromptWriter 两步） ----------------

def test_rewrite_mode_in_schema():
    """rewrite_mode 参数存在且默认"六段式反推"（一步出反推级提示词）。"""
    schema = H3PromptSplitTranslate.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert "rewrite_mode" in inputs
    assert inputs["rewrite_mode"].default == "忠实翻译"
    assert inputs["rewrite_mode"].options == ["忠实翻译", "H3 通用全参考模版", "fullreference"]


def test_six_section_ok():
    """反推输出必须含 H3 六段式全部字段才合格。"""
    good = (
        "subject_definitions: <Subject 1> face locked from <Picture 1>.\n"
        "summary: Segment 0, 5 seconds.\n"
        "retention_analysis: fully_preserved.\n"
        "detailed_description: locked camera.\n"
        "overall_soundscape: room tone.\n"
        "non_diegetic_music: N/A"
    )
    assert _six_section_ok(good)
    assert not _six_section_ok("summary: only one field")
    assert not _six_section_ok("")
    assert not _six_section_ok(None)


def test_rewrite_segment_message_assembly():
    """反推复用 H3PromptWriter 的 fullreference system prompt（含六段式契约）。"""
    from h3_ad.h3_prompt_split_translate import _call_local_llm
    captured = {}

    def fake_llm(llm, messages, temperature, seed, max_tokens):
        captured["sys"] = messages[0]["content"]
        captured["user"] = messages[1]["content"]
        return (
            "subject_definitions: <Subject 1> face locked from <Picture 1>.\n"
            "summary: Segment 0, 5 seconds.\n"
            "retention_analysis: fully_preserved.\n"
            "detailed_description: the woman says: <<<DIALOGUE_0>>>.\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A"
        )

    _orig = _call_local_llm
    import h3_ad.h3_prompt_split_translate as m
    m._call_local_llm = fake_llm
    try:
        out = _rewrite_segment(
            'subject_definitions: ... summary: ... 女主说："你凭什么删我东西……"',
            llm="fake", temperature=0.4, seed=0, seg_seconds=5.0, aspect_ratio="16:9")
    finally:
        m._call_local_llm = _orig
    # 反推模板：六段式骨架关键词出现在 system prompt
    assert "subject_definitions" in captured["sys"] or "six-section" in captured["sys"]         or "detailed_description" in captured["sys"]
    assert "STORY CONCEPT" in captured["user"]
    assert "TOTAL DURATION" in captured["user"]
    assert "VISUAL STYLE" in captured["user"]
    assert "ASPECT RATIO" in captured["user"]
    # 对话还原：占位符变回中文原文
    assert "你凭什么删我东西" in out
    assert "<<<DIALOGUE_0>>>" not in out


def test_rewrite_segment_fallback_to_translate():
    """反推输出缺六段式字段 → 降级忠实翻译（不把烂结果直接交给 H3）。"""
    import h3_ad.h3_prompt_split_translate as m
    calls = []

    def fake_llm(llm, messages, temperature, seed, max_tokens):
        calls.append(messages[0]["content"])
        return "summary: 只有一段（缺字段）"

    _orig = m._call_local_llm
    m._call_local_llm = fake_llm
    try:
        out = _rewrite_segment("summary: 中文段", llm="fake", temperature=0.4, seed=0)
    finally:
        m._call_local_llm = _orig
    # 降级路径：第二次调用用忠实翻译 system prompt（含 ANY quoted dialogue）
    assert len(calls) == 2
    assert "ANY quoted dialogue" in calls[1]


def test_rewrite_segment_exception_fallback():
    """反推抛异常 → 也降级忠实翻译。"""
    import h3_ad.h3_prompt_split_translate as m

    def boom(llm, messages, temperature, seed, max_tokens):
        raise RuntimeError("llm down")

    _orig = m._call_local_llm
    m._call_local_llm = boom
    try:
        out = _rewrite_segment("summary: 中文段", llm="fake", temperature=0.4, seed=0)
    finally:
        m._call_local_llm = _orig
    assert out == "summary: 中文段"  # 兜底：保留原文


# ---------------- 写作增强规则（训练语料归纳注入 fullreference） ----------------

def test_fullreference_template_has_writing_enhancements():
    """fullreference 反推模板已注入写作增强：动作链/运镜五要素/微表演/参考图分工/
    跨段连续/音频分层/禁模糊词。"""
    from h3_ad.h3_screenwriter import _build_system_prompt
    sp = _build_system_prompt("fullreference")
    for marker in (
        "ACTION CHAIN", "CAMERA SPEC", "MICRO-ACTING",
"REFERENCE ROLE SPLIT", "CROSS-SHOT CONTINUITY",
        "AUDIO LAYERING", "DISTANT SUBJECTS", "FORBIDDEN FILLER",
):
        assert marker in sp, f"missing: {marker}"


def test_other_task_modes_not_enhanced():
    """非 fullreference 模板不动（不追加增强段），避免改变已验收行为。"""
    from h3_ad.h3_screenwriter import _build_system_prompt
    sp = _build_system_prompt("3d_animation")
    assert "ACTION CHAIN" not in sp
    assert "FORBIDDEN FILLER" not in sp


def test_rewrite_segment_inherits_enhancements():
    """SplitTranslate 反推（_rewrite_segment）走 _build_system_prompt 自动继承增强。"""
    import h3_ad.h3_prompt_split_translate as m
    captured = {}

    def fake_llm(llm, messages, temperature, seed, max_tokens):
        captured["sys"] = messages[0]["content"]
        return (
            "subject_definitions: ...\nsummary: ...\nretention_analysis: ...\n"
            "detailed_description: ...\noverall_soundscape: ...\nnon_diegetic_music: N/A"
        )

    _orig = m._call_local_llm
    m._call_local_llm = fake_llm
    try:
        m._rewrite_segment("summary: 中文段", llm="fake", temperature=0.4, seed=0)
    finally:
        m._call_local_llm = _orig
    assert "ACTION CHAIN" in captured["sys"]
    assert "REFERENCE ROLE SPLIT" in captured["sys"]



# ---------------- 整批反推（N 段 = N 个独立提示词组，按 === SEGMENT N === 分隔） ----------------

def test_split_segments_ok():
    """按 === SEGMENT N === 标记切回恰好 n 段。"""
    out = (
        "=== SEGMENT 1 ===\n"
        "subject_definitions: A.\nsummary: s1.\n"
        "=== SEGMENT 2 ===\n"
        "subject_definitions: B.\nsummary: s2.\n"
        "=== SEGMENT 3 ===\n"
        "subject_definitions: C.\nsummary: s3.\n"
    )
    segs = _split_segments(out, 3)
    assert segs is not None and len(segs) == 3
    assert "subject_definitions: A." in segs[0]
    assert "subject_definitions: B." in segs[1]
    assert "subject_definitions: C." in segs[2]
    assert "SEGMENT" not in segs[0]


def test_split_segments_loose_markers():
    """容错：SEGMENT N: 与 段 N / 【段N】 标记都能切。"""
    out = (
        "SEGMENT 1: subject_definitions: A.\n"
        "summary: s1.\n"
        "\u3010\u6bb52\u3011 subject_definitions: B.\n"
        "summary: s2.\n"
        "\u6bb5 3: subject_definitions: C.\n"
        "summary: s3.\n"
    )
    segs = _split_segments(out, 3)
    assert segs is not None and len(segs) == 3
    assert "subject_definitions: A." in segs[0]
    assert "subject_definitions: B." in segs[1]
    assert "subject_definitions: C." in segs[2]


def test_split_segments_too_few():
    """标记不足 → None（触发降级）。"""
    out = "=== SEGMENT 1 ===\nbody only"
    assert _split_segments(out, 3) is None
    assert _split_segments("no markers at all", 2) is None


def test_batch_rewrite_three_segments(monkeypatch):
    """整批反推：三段整批输入，LLM 按标记输出三段，对话占位符还原。"""
    import h3_ad.h3_prompt_split_translate as M

    captured = {}

    def fake_llm(llm, messages, temperature, seed, max_tokens):
        u = messages[1]["content"]
        captured["user"] = u if isinstance(u, str) else ""
        return (
            "=== SEGMENT 1 ===\n"
            "subject_definitions: <Subject 1> locked.\n"
            "summary: Segment 0, 5 seconds.\n"
            "retention_analysis: fully_preserved.\n"
            "detailed_description: locked camera. <<<DIALOGUE_0>>>\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A\n"
            "=== SEGMENT 2 ===\n"
            "subject_definitions: <Subject 2> locked.\n"
            "summary: Segment 1, 5 seconds.\n"
            "retention_analysis: fully_preserved.\n"
            "detailed_description: wide shot.\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A\n"
            "=== SEGMENT 3 ===\n"
            "subject_definitions: <Subject 3> locked.\n"
            "summary: Segment 2, 5 seconds.\n"
            "retention_analysis: fully_preserved.\n"
            "detailed_description: medium shot. <<<DIALOGUE_1>>>\n"
            "overall_soundscape: room tone.\n"
            "non_diegetic_music: N/A"
        )

    monkeypatch.setattr(M, "_call_local_llm", fake_llm)
    segs = [
        "雨夜便利店外景，女主锁门。她说：「下雨了。」",
"黎明室内，男人推门进来。",
        "吧台前两人相视。她说：「欢迎光临。」",
]
    out = M._batch_rewrite_segments(
        segs, llm=None, temperature=0.4, seed=0,
        seg_seconds=5.0, aspect_ratio="16:9", resolution_mp=1.0, strict=False)
    assert len(out) == 3
    assert "「下雨了。」" in out[0]
    assert "「欢迎光临。」" in out[2]
    assert "SEGMENT" not in out[0]
    assert "SPLIT INTO 3 SEGMENTS" in captured["user"]
    assert "FORBIDDEN" in captured["user"]
    assert "=== SEGMENT 1 ===" not in out[1]


def test_batch_rewrite_fallback(monkeypatch):
    """LLM 未按标记输出 → 全部降级逐段忠实翻译。"""
    import h3_ad.h3_prompt_split_translate as M

    def fake_llm(llm, messages, temperature, seed, max_tokens):
        return "a single blob without segment markers"

    monkeypatch.setattr(M, "_call_local_llm", fake_llm)
    monkeypatch.setattr(M, "_translate_segment", lambda s, llm, t, sd, rc, mt: "TR:" + s[:5])
    segs = ["镜头一内容", "镜头二内容", "镜头三内容"]
    out = M._batch_rewrite_segments(
        segs, llm=None, temperature=0.4, seed=0,
        seg_seconds=5.0, aspect_ratio="16:9", resolution_mp=1.0, strict=False)
    assert out == ["TR:镜头一内容", "TR:镜头二内容", "TR:镜头三内容"]




def test_ensure_subject_defs_injects_lock_when_empty():

    """反推兜底：subject_definitions 为空时，从 detailed_description 提取 <Picture N> 注入锁定句。"""

    text = ("subject_definitions: \n"

            "summary: Segment 1, auto-generated summary.\n"

            "detailed_description: <Picture 1> The female lead stands... "

            "<Picture 2> The male lead leans...")

    out = _ensure_subject_defs(text)

    assert "The reference image <Picture 1> defines the subject's" in out

    assert "The reference image <Picture 2> defines the subject's" in out





def test_ensure_subject_defs_keeps_nonempty():

    """已有非空 subject_definitions 时不重复注入。"""

    text = ("subject_definitions: The reference image <Picture 1> defines her facial features.\n"

            "summary: ok")

    assert _ensure_subject_defs(text) == text





def test_ensure_subject_defs_no_picture():

    """无 <Picture N> 标签时原样返回。"""

    text = "subject_definitions: \nsummary: ok"

    assert _ensure_subject_defs(text) == text
# ---------------- 反推忠实度：<d> 整块保护 / 强制恢复 / 残留中文翻译 ----------------

def test_protect_dialogues_covers_d_blocks():
    """<d> 整块（无引号台词）必须被保护，LLM 物理上碰不到。"""
    from h3_ad.h3_prompt_split_translate import _protect_dialogues, _restore_dialogues
    src = (
        "subject_definitions: hi\n"
        "detailed_description: <d>00:00-00:03 (S1): 你凭什么删我东西……</d> "
        "她说：\"下雨了，真冷。\""
    )
    masked, protected = _protect_dialogues(src)
    assert "你凭什么删我东西" not in masked
    assert "下雨了" not in masked
    assert "DIALOGUE_0" in masked and "DIALOGUE_1" in masked
    restored = _restore_dialogues(masked, protected)
    assert "你凭什么删我东西" in restored
    assert "下雨了" in restored


def test_force_restore_dialogues_overrides_llm_invention():
    """LLM 反推丢了占位符、自己编台词 → 按源台词顺序强制覆盖 <d> 块。"""
    from h3_ad.h3_prompt_split_translate import _force_restore_dialogues, _protect_dialogues
    src = (
        "detailed_description: <d>00:00-00:03 (S1): 你凭什么删我东西……</d> "
        "她说：\"下雨了，真冷。\""
    )
    _, protected = _protect_dialogues(src)
    llm_out = (
        "detailed_description: <d>00:00-00:03 (S1): You have no right to delete my stuff...</d> "
        "<d>00:03-00:05 (S2): It is raining, so cold.</d>"
    )
    restored = _force_restore_dialogues(llm_out, protected, src)
    assert "你凭什么删我东西" in restored
    assert "下雨了" in restored
    assert "no right" not in restored  # LLM 编的台词被覆盖


def test_translate_leftover_cn_mocks_llm():
    """反推输出兜底：<d>/引号外残留中文被翻译，台词原样保留。"""
    from h3_ad.h3_prompt_split_translate import _translate_leftover_cn

    class FakeLLM:
        def create_chat_completion(self, **kwargs):
            return {"choices": [{"message": {"content":
                "1. The night rain is falling harder, the streetlights dim.\n"
                "2. A faint bell rings from afar."}}]}

    text = (
        "summary: 雨夜街道，路灯昏暗，远处传来隐约铃声。\n"
        "detailed_description: <d>00:00-00:03 (S1): 你凭什么删我东西……</d>"
    )
    out = _translate_leftover_cn(text, FakeLLM(), 0.0, 0)
    assert "你凭什么删我东西" in out        # 台词原样
    assert "The night rain is falling harder" in out  # 残留中文已译
    assert "雨夜街道" not in out


# ---------------- 对话转 H3 <d> 块 ----------------


# ---------------- 对话转 H3 <d> 块（方言标签） ----------------


# ---------------- 对话转 H3 <d> 块（智能方言） ----------------


# ---------------- 对话转 H3 <d> 块 ----------------

def test_quotes_to_d_blocks_basic():
    """裸引号中文台词 → <d>台词</d>（无方言标签）。"""
    from h3_ad.h3_prompt_split_translate import _quotes_to_d_blocks
    text = ("The female lead whispers: \u201c你当初走嗰阵时，有冇谂过我会有几难过？\u201d "
            "The male lead explains: \u201c我当时太懦弱。\u201d")
    out = _quotes_to_d_blocks(text, 5.0)
    assert '<d>你当初走嗰阵时，有冇谂过我会有几难过？</d>' in out
    assert '<d>我当时太懦弱。</d>' in out


def test_quotes_to_d_blocks_keeps_existing_d():
    """已有 <d> 块（反推输出）原样保留，不重复包。"""
    from h3_ad.h3_prompt_split_translate import _quotes_to_d_blocks
    text = ("detailed_description: scene at night. "
            "<d>00:00-00:03 (S1): \u201c你有冇搞错啊？\u201d</d> "
            "The woman says: \u201c你凭什么删我东西……\u201d done.")
    out = _quotes_to_d_blocks(text, 5.0)
    assert '<d>00:00-00:03 (S1): \u201c你有冇搞错啊？\u201d</d>' in out
    assert '<d>你凭什么删我东西……</d>' in out


def test_quotes_to_d_blocks_english_untouched():
    """无中文引号对不转换。"""
    from h3_ad.h3_prompt_split_translate import _quotes_to_d_blocks
    text = ('subject_definitions: "The reference image <Picture 1> defines her '
            'facial features, hair style, and body proportions."')
    assert _quotes_to_d_blocks(text, 5.0) == text
    assert _quotes_to_d_blocks('No Chinese here: \u201chello\u201d', 5.0) == \
        'No Chinese here: \u201chello\u201d'
