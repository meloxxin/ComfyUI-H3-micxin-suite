# -*- coding: utf-8 -*-
"""H3PromptTranslate (micxin) 测试：多段收集 / 翻译规则 / schema 默认本地。

不加载真实 GGUF（8B 太重），只测：Autogrow 收集排序、系统提示词规则
（<d> 对话保留 / 字段名不变）、backend 默认 Local GGUF、注册存在。
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
    import h3_ad.h3_prompt_translate  # noqa: F401


_load_ad_package()
from h3_ad.h3_prompt_translate import (  # noqa: E402
    _collect_segment_inputs,
    _TRANSLATE_SYS,
    H3PromptTranslate,
    MAX_SEGMENTS,
)


def test_collect_segment_inputs_ordered():
    """Autogrow 输入按 prompt_N 排序、空值跳过。"""
    raw = {"prompt_2": "seg c", "prompt_0": "seg a", "prompt_1": "", "prompt_3": "seg d"}
    out = _collect_segment_inputs(raw)
    assert out == ["seg a", "seg c", "seg d"]  # 空 prompt_1 跳过，索引升序


def test_collect_segment_inputs_empty():
    assert _collect_segment_inputs(None) == []
    assert _collect_segment_inputs({}) == []
    assert _collect_segment_inputs("not dict") == []


def test_translate_sys_keeps_dialogue_and_fields():
    """翻译规则：<d> 对话必须保留原文；字段名/结构不变。"""
    assert "KEEP the original text EXACTLY" in _TRANSLATE_SYS
    assert "Never translate them" in _TRANSLATE_SYS
    assert "detailed_description:" in _TRANSLATE_SYS
    assert "overall_soundscape:" in _TRANSLATE_SYS
    assert "Output ONLY the translated prompt" in _TRANSLATE_SYS


@requires_comfy
def test_translate_schema_backend_defaults_local():
    schema = H3PromptTranslate.define_schema()
    assert schema.node_id == "H3PromptTranslate"

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert "backend" in inputs
    assert inputs["backend"].default == "Local GGUF"
    assert "segment_prompts" in inputs
    # 输出：32 路 prompt + report
    outs = schema.outputs
    assert len(outs) == MAX_SEGMENTS + 1
    assert getattr(outs[-1], "id", None) == "report"


@requires_comfy
def test_execute_is_classmethod():
    """V3 校验（first_real_override）要求 execute 是绑定方法（classmethod）。
    普通函数会报 'function' object has no attribute '__func__'。"""
    assert type(H3PromptTranslate.__dict__["execute"]) is classmethod


@requires_comfy
def test_get_schema_passes_validation():
    """GET_SCHEMA 完整走一遍 VALIDATE_CLASS（复现用户 object_info 报错路径）。"""
    schema = H3PromptTranslate.GET_SCHEMA()
    assert schema.node_id == "H3PromptTranslate"


def test_fix_migrated_into_suite():
    """H3PromptFix 已迁入 micxin suite（原 ComfyUI-H3PromptFix 包已删）。"""
    import h3_ad.h3_prompt_fix as pf
    assert "H3PromptFix" in pf.NODE_CLASS_MAPPINGS
    assert "H3PromptToConditioning" in pf.NODE_CLASS_MAPPINGS
    # 纯修复器：llm_output 输入口已按用户要求删除
    req = pf.H3PromptFix.INPUT_TYPES()
    assert "llm_output" not in req["optional"]
    # 原始包已删除
    import os
    old = r"J:ki\ComfyUI\custom_nodes\ComfyUI-H3PromptFix"
    assert not os.path.exists(old), "原 H3PromptFix 包应已删除"

def test_translate_registered():
    import h3_ad.h3_prompt_translate as pt
    assert "H3PromptTranslate" in pt.NODE_CLASS_MAPPINGS
