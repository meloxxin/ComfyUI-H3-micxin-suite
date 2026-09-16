# -*- coding: utf-8 -*-
"""Pytest 共享配置：定位 ComfyUI 源码并注入导入路径。

本地跑：设 COMFYUI_ROOT 指向 ComfyUI 源码根目录（含 comfy/ 与 folder_paths.py），
或在默认候选路径（J:\\aki\\ComfyUI、~\\ComfyUI）下找到。找不到时相关用例自动跳过。
"""
import os
import sys

import pytest


def _find_comfyui():
    env = os.environ.get("COMFYUI_ROOT")
    if env and os.path.isdir(os.path.join(env, "comfy")):
        return env
    candidates = [
        os.path.expanduser("~/ComfyUI"),
        os.path.expanduser("~/Documents/ComfyUI"),
    ]
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "comfy")):
            return c
    return None


COMFYUI_ROOT = _find_comfyui()
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if COMFYUI_ROOT and COMFYUI_ROOT not in sys.path:
    sys.path.insert(0, COMFYUI_ROOT)
sys.path.insert(0, os.path.join(REPO, "ComfyUI-H3-helper"))
sys.path.insert(0, os.path.join(REPO, "ComfyUI-H3-AutoDirector"))

requires_comfy = pytest.mark.skipif(
    not COMFYUI_ROOT,
    reason="未找到 ComfyUI 源码，请设置 COMFYUI_ROOT 环境变量",
)
