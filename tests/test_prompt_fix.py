# -*- coding: utf-8 -*-
"""H3PromptFix (micxin) 测试：格式修复 + 宽高长输出（与 H3 PromptWriter 算法一致）。"""
import importlib.util
import os
import sys
import types

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
    import h3_ad.h3_prompt_fix  # noqa: F401


_load_ad_package()
from h3_ad.h3_prompt_fix import H3PromptFix, fix_prompt  # noqa: E402


def test_fix_prompt_rebuilds_json():
    messy = (
        "```json\n"
        '{"0": "detailed_description: S1 says <d>你凭什么删我东西……</d> lips quiver.\\n\\n'
        'overall_soundscape: quiet.\\n\\nnon_diegetic_music: N/A"}\n'
        "```"
    )
    fixed, status = fix_prompt(messy, expected_segments=0)
    import json
    data = json.loads(fixed)
    assert "0" in data


def test_fix_returns_two_values():
    """fix 返回 2 元组：fixed_json, status（纯原始提示词→JSON，不输出宽高长）。"""
    out = H3PromptFix().fix(
        '{"0": "detailed_description: test scene.", "1": "detailed_description: second."}',
        expected_segments=0)
    assert len(out) == 2
    fixed, status = out
    import json
    data = json.loads(fixed)
    assert "0" in data and "1" in data


@requires_comfy
def test_input_types_minimal():
    """最简：raw_text + expected_segments；无宽高长控件、无 llm_output 输入口。"""
    it = H3PromptFix.INPUT_TYPES()
    assert set(it["required"]) == {"raw_text", "expected_segments"}
    assert "aspect_ratio" not in it["required"]
    assert "resolution_mp" not in it["required"]
    assert "duration_seconds" not in it["required"]
    assert "llm_output" not in it["optional"]


def test_registered():
    import h3_ad.h3_prompt_fix as m
    assert "H3PromptFix" in m.NODE_CLASS_MAPPINGS
    assert "H3PromptToConditioning" in m.NODE_CLASS_MAPPINGS
