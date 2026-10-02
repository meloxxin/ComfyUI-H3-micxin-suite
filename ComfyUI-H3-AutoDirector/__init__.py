# -*- coding: utf-8 -*-
"""ComfyUI-H3-AutoDirector — H3 提示词工具包（micxin）。

2026-10-02 变更记录：
- H3 Prompt Translate（h3_prompt_translate.py）已弃用删除。
- H3 PromptWriter（h3_screenwriter.py）保留（用户多个工作流的主力节点）；
  其 LLM/工具函数抽取的 h3_llm_utils.py 同时供 H3 Prompt Split+Translate 使用。
"""
import logging

from .h3_screenwriter import (
    NODE_CLASS_MAPPINGS as _M1,
    NODE_DISPLAY_NAME_MAPPINGS as _D1,
)

from .h3_prompt_fix import (
    NODE_CLASS_MAPPINGS as _M11,
    NODE_DISPLAY_NAME_MAPPINGS as _D11,
)
from .h3_prompt_split_translate import (
    NODE_CLASS_MAPPINGS as _M12,
    NODE_DISPLAY_NAME_MAPPINGS as _D12,
)
from .h3_segments_unpack import (
    NODE_CLASS_MAPPINGS as _M14,
    NODE_DISPLAY_NAME_MAPPINGS as _D14,
)
NODE_CLASS_MAPPINGS = {}
NODE_CLASS_MAPPINGS.update(_M1)
NODE_CLASS_MAPPINGS.update(_M11)
NODE_CLASS_MAPPINGS.update(_M12)
NODE_CLASS_MAPPINGS.update(_M14)

NODE_DISPLAY_NAME_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS.update(_D1)
NODE_DISPLAY_NAME_MAPPINGS.update(_D11)
NODE_DISPLAY_NAME_MAPPINGS.update(_D12)
NODE_DISPLAY_NAME_MAPPINGS.update(_D14)

WEB_DIRECTORY = "./js"  # @-reference editor for H3Screenwriter's concept box
