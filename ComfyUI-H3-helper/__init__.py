# -*- coding: utf-8 -*-
"""ComfyUI-H3-helper (micxin) — H3 R2VA AIO + AV Latent helper nodes.

This package hosts the V3 io.ComfyNode nodes:

  * H3ModelLoader               (H3 R2VA AIO — loader + Ref2VA + embedded drag-and-drop media uploader)
  * H3SeparateAVLatent          (split a joint H3 AV latent into video + audio)
  * H3CombineAVLatent           (recombine video + audio latents into a joint AV latent)

All three register through a single V3 comfy_entrypoint (ComfyExtension), under
the `H3 helper/micxin` category. The companion V2 node packs
(ComfyUI-H3-AutoDirector, ComfyUI-H3-Prompt-Writing-micxin2025) live in their
own folders — ComfyUI v0.31 loads one registration mechanism per package, so a
V3 package cannot also host classic V2 nodes in the same folder.

WEB_DIRECTORY loads the embedded media uploader UI (web/h3_aio_media.js).
"""

from comfy_api.latest import ComfyExtension, io
from typing_extensions import override

from .h3_model_loader import H3ModelLoader
from .h3_av_latent import H3SeparateAVLatent, H3CombineAVLatent


class H3HelperExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [H3ModelLoader, H3SeparateAVLatent, H3CombineAVLatent]


async def comfy_entrypoint() -> H3HelperExtension:
    return H3HelperExtension()


WEB_DIRECTORY = "./web"
