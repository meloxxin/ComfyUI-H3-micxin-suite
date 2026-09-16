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

from .h3_prompt_fix import (
    NODE_CLASS_MAPPINGS as _M11,
    NODE_DISPLAY_NAME_MAPPINGS as _D11,
)
from .h3_prompt_split_translate import (
    NODE_CLASS_MAPPINGS as _M12,
    NODE_DISPLAY_NAME_MAPPINGS as _D12,
)
from .h3_prompt_translate import (
    NODE_CLASS_MAPPINGS as _M13,
    NODE_DISPLAY_NAME_MAPPINGS as _D13,
)
NODE_CLASS_MAPPINGS = {}
NODE_CLASS_MAPPINGS.update(_M1)
NODE_CLASS_MAPPINGS.update(_M11)
NODE_CLASS_MAPPINGS.update(_M12)
NODE_CLASS_MAPPINGS.update(_M13)

NODE_DISPLAY_NAME_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS.update(_D1)
NODE_DISPLAY_NAME_MAPPINGS.update(_D11)
NODE_DISPLAY_NAME_MAPPINGS.update(_D12)
NODE_DISPLAY_NAME_MAPPINGS.update(_D13)

WEB_DIRECTORY = "./js"  # @-reference editor for H3Screenwriter's concept box
