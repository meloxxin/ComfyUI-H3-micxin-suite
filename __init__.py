# -*- coding: utf-8 -*-
"""ComfyUI-H3-micxin-suite — root entrypoint for ComfyUI-Manager.

This repo bundles two node packs:
  - ComfyUI-H3-AutoDirector  (screenplay / story / prompt writer + asset library)
  - ComfyUI-H3-helper         (H3 R2VA AIO loader + AV latent helpers)

ComfyUI-Manager requires __init__.py at the repo root to recognize a valid
extension. This file dynamically imports both sub-packs and merges their
node registrations (V2 NODE_CLASS_MAPPINGS + V3 comfy_entrypoint).
"""
import os
import sys
import importlib.util
import logging

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_ROOT = os.path.dirname(os.path.abspath(__file__))

# Each entry: (directory name, safe python module name)
_SUB_PACKS = [
    ("ComfyUI-H3-AutoDirector", "h3_autodirector_pkg"),
    ("ComfyUI-H3-helper", "h3_helper_pkg"),
]

_loaded_modules = {}


def _load_subpackage(dir_name, module_name):
    """Dynamically load a sub-package that uses relative imports."""
    pkg_dir = os.path.join(_ROOT, dir_name)
    init_path = os.path.join(pkg_dir, "__init__.py")
    if not os.path.exists(init_path):
        logger.warning("[H3-micxin-suite] %s/__init__.py not found, skipping", dir_name)
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_path,
            submodule_search_locations=[pkg_dir],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        logger.info("[H3-micxin-suite] loaded %s", dir_name)
        return module
    except Exception as e:
        logger.error("[H3-micxin-suite] failed to load %s: %s", dir_name, e, exc_info=True)
        return None


for _dir, _mod in _SUB_PACKS:
    _m = _load_subpackage(_dir, _mod)
    if _m is None:
        continue
    _loaded_modules[_dir] = _m

    # V2 style: merge NODE_CLASS_MAPPINGS (AutoDirector uses this)
    if hasattr(_m, "NODE_CLASS_MAPPINGS"):
        NODE_CLASS_MAPPINGS.update(_m.NODE_CLASS_MAPPINGS)
    if hasattr(_m, "NODE_DISPLAY_NAME_MAPPINGS"):
        NODE_DISPLAY_NAME_MAPPINGS.update(_m.NODE_DISPLAY_NAME_MAPPINGS)


# V3 style: comfy_entrypoint for helper pack (H3ModelLoader etc.)
_helper_module = _loaded_modules.get("ComfyUI-H3-helper")


async def comfy_entrypoint():
    """V3 entrypoint — delegates to the helper sub-package extension."""
    if _helper_module is not None and hasattr(_helper_module, "comfy_entrypoint"):
        return await _helper_module.comfy_entrypoint()
    return None


# Frontend: merged web directory at repo root (contains JS from both packs)
WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "comfy_entrypoint",
]
