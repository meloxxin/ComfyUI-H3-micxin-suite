# -*- coding: utf-8 -*-
"""ComfyUI-H3-AutoDirector — concept -> H3 multi-shot screenplay (auto writer).

A small, dependency-free pack that automates the "写剧本" half of the
MiniMax H3 pipeline and drops straight into ComfyUI-H3-Multishot's Seamless
Chain: H3Screenwriter writes a {'prompts': [...]} JSON into
<input>/rift_prompts/, and the existing chain renders + stitches it.

See h3_screenwriter.py for the node logic, and
README.md for the wiring guide.
"""
import logging

from .h3_screenwriter import (
    NODE_CLASS_MAPPINGS as _M1,
    NODE_DISPLAY_NAME_MAPPINGS as _D1,
)
from .h3_reference_builder import (
    NODE_CLASS_MAPPINGS as _M3,
    NODE_DISPLAY_NAME_MAPPINGS as _D3,
)
from .h3_story_setup_node import (
    NODE_CLASS_MAPPINGS as _M5,
    NODE_DISPLAY_NAME_MAPPINGS as _D5,
)
from .h3_skill_manager import (
    NODE_CLASS_MAPPINGS as _M6,
    NODE_DISPLAY_NAME_MAPPINGS as _D6,
)
from .h3_asset_library import (
    NODE_CLASS_MAPPINGS as _M7,
    NODE_DISPLAY_NAME_MAPPINGS as _D7,
)

NODE_CLASS_MAPPINGS = {}
NODE_CLASS_MAPPINGS.update(_M1)
NODE_CLASS_MAPPINGS.update(_M3)
NODE_CLASS_MAPPINGS.update(_M5)
NODE_CLASS_MAPPINGS.update(_M6)
NODE_CLASS_MAPPINGS.update(_M7)

NODE_DISPLAY_NAME_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS.update(_D1)
NODE_DISPLAY_NAME_MAPPINGS.update(_D3)
NODE_DISPLAY_NAME_MAPPINGS.update(_D5)
NODE_DISPLAY_NAME_MAPPINGS.update(_D6)
NODE_DISPLAY_NAME_MAPPINGS.update(_D7)

WEB_DIRECTORY = "./js"  # @-reference editor for H3Screenwriter's concept box

# ---------------------------------------------------------------------------
# H3 Asset Library v3.0 API 路由在 h3_asset_library_api.py 模块加载时
# 通过 PromptServer.instance.routes 装饰器自动注册，无需额外调用。
# ---------------------------------------------------------------------------
try:
    from . import h3_asset_library_api  # noqa: F401  触发路由注册
except Exception as e:
    print(f"[H3 AssetLibrary] 模块加载失败: {e}", flush=True)
