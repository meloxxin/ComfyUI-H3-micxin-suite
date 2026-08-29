# -*- coding: utf-8 -*-
"""ComfyUI-H3-micxin-suite — root entrypoint.
V2 nodes via NODE_CLASS_MAPPINGS. Helper V3 nodes (io.ComfyNode subclasses)
are also registered via NODE_CLASS_MAPPINGS — ComfyUI auto-detects ComfyNode
subclasses and handles them, so this works even when comfy-env skips the
comfy_entrypoint V3 detection path.
"""
import os
import sys
import importlib.util
import traceback

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_subpackage(dir_name, module_name):
    pkg_dir = os.path.join(_ROOT, dir_name)
    init_path = os.path.join(pkg_dir, "__init__.py")
    if not os.path.exists(init_path):
        print(f"[H3-suite] {dir_name}/__init__.py not found", flush=True)
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, init_path,
            submodule_search_locations=[pkg_dir],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        print(f"[H3-suite] OK loaded sub-package: {dir_name}", flush=True)
        return module
    except Exception as e:
        print(f"[H3-suite] FAIL load {dir_name}: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        return None


# ---- Load AutoDirector (V2 nodes) ----
_ad = _load_subpackage("ComfyUI-H3-AutoDirector", "h3_autodirector_pkg")
if _ad is not None:
    if hasattr(_ad, "NODE_CLASS_MAPPINGS"):
        NODE_CLASS_MAPPINGS.update(_ad.NODE_CLASS_MAPPINGS)
    if hasattr(_ad, "NODE_DISPLAY_NAME_MAPPINGS"):
        NODE_DISPLAY_NAME_MAPPINGS.update(_ad.NODE_DISPLAY_NAME_MAPPINGS)
    print(f"[H3-suite] AutoDirector V2 nodes: {list(NODE_CLASS_MAPPINGS.keys())}", flush=True)

# ---- Load helper (V3 nodes, register via V2 NODE_CLASS_MAPPINGS) ----
_helper = _load_subpackage("ComfyUI-H3-helper", "h3_helper_pkg")
if _helper is not None:
    try:
        from h3_helper_pkg.h3_model_loader import H3ModelLoader
        from h3_helper_pkg.h3_av_latent import H3SeparateAVLatent, H3CombineAVLatent

        # Register V3 ComfyNode subclasses via V2 NODE_CLASS_MAPPINGS.
        # ComfyUI auto-detects io.ComfyNode subclasses and handles them
        # through the V3 execution path regardless of registration channel.
        NODE_CLASS_MAPPINGS["H3ModelLoader"] = H3ModelLoader
        NODE_CLASS_MAPPINGS["H3SeparateAVLatent"] = H3SeparateAVLatent
        NODE_CLASS_MAPPINGS["H3CombineAVLatent"] = H3CombineAVLatent

        NODE_DISPLAY_NAME_MAPPINGS["H3ModelLoader"] = "H3 R2VA AIO (micxin)"
        NODE_DISPLAY_NAME_MAPPINGS["H3SeparateAVLatent"] = "H3 Separate AV Latent (micxin)"
        NODE_DISPLAY_NAME_MAPPINGS["H3CombineAVLatent"] = "H3 Combine AV Latent (micxin)"

        print(f"[H3-suite] OK registered helper V3 nodes via V2 channel: H3ModelLoader, H3SeparateAVLatent, H3CombineAVLatent", flush=True)
    except Exception as e:
        print(f"[H3-suite] FAIL register helper nodes: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()


WEB_DIRECTORY = "./web"

print(f"[H3-suite] init done. Total NODE_CLASS_MAPPINGS: {len(NODE_CLASS_MAPPINGS)} -> {list(NODE_CLASS_MAPPINGS.keys())}", flush=True)

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
