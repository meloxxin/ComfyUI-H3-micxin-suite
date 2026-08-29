# -*- coding: utf-8 -*-
"""H3 Asset Library (micxin) — 资产库管理节点 v3.0（浮动面板版）。

参考 ComfyUI_GJJ_Nodes 的资产管理设计：
- 节点本身无输入框，只有一个"📂 打开资产管理面板"按钮（前端 JS 添加）
- 点击按钮弹出浮动面板，可视化管理角色/场景/道具
- 数据持久化到 ComfyUI/user/default/h3_assets/library.json
- 节点运行时从 JSON 文件加载资产库并输出

输出口（与 v2.x 兼容）：
  asset_library    — 完整资产库 JSON
  characters_json  — 角色列表 JSON
  scenes_json      — 场景列表 JSON
  props_json       — 道具列表 JSON
  image_paths      — 所有图片路径（换行分隔）
  character_count  — 角色数量
  scene_count      — 场景数量
  prop_count       — 道具数量
"""

import json
import os
import torch
import numpy as np
from comfy_api.latest import io


def _load_image_tensor(path):
    """Load an image file to a ComfyUI IMAGE tensor (1, H, W, 3) float32."""
    from PIL import Image
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)


class H3AssetLibrary:
    """资产库管理节点 v3.0 — 浮动面板 + 后端 API。"""

    @classmethod
    def INPUT_TYPES(cls):
        # v3.0：无任何输入框，所有数据通过浮动面板管理
        # 前端 JS 会给节点添加"📂 打开资产管理面板"按钮
        return {
            "required": {},
            "optional": {},
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "INT", "INT", "INT")
    RETURN_NAMES = (
        "asset_library (完整资产库JSON)",
        "characters_json (角色列表)",
        "scenes_json (场景列表)",
        "props_json (道具列表)",
        "image_paths (所有图片路径)",
        "character_count",
        "scene_count",
        "prop_count",
    )
    FUNCTION = "build"
    CATEGORY = "H3/micxin"
    DESCRIPTION = (
        "H3 资产库管理节点 v3.0。点击节点上的「📂 打开资产管理面板」按钮，"
        "可视化管理角色/场景/道具。数据自动保存，运行时输出资产库 JSON，"
        "直接接 H3 Story Setup 或 H3 PromptWriter 的 asset_library 输入口。"
    )

    def build(self, **kwargs):
        """从后端加载资产库并输出。"""
        # 延迟导入，避免循环依赖
        try:
            from .h3_asset_library_api import load_library
        except ImportError:
            from h3_asset_library_api import load_library

        library = load_library()

        characters = library.get("characters", [])
        scenes = library.get("scenes", [])
        props = library.get("props", [])

        # 收集所有图片路径
        image_paths = []
        for item in characters + scenes + props:
            img = item.get("image", "")
            if img and img not in image_paths:
                image_paths.append(img)

        asset_library_json = json.dumps(library, ensure_ascii=False, indent=2)
        characters_json = json.dumps(characters, ensure_ascii=False, indent=2)
        scenes_json = json.dumps(scenes, ensure_ascii=False, indent=2)
        props_json = json.dumps(props, ensure_ascii=False, indent=2)
        image_paths_str = "\n".join(image_paths)

        print(
            f"[H3 AssetLibrary] 加载完成: {len(characters)}角色, "
            f"{len(scenes)}场景, {len(props)}道具, {len(image_paths)}张图片。",
            flush=True,
        )

        return (
            asset_library_json,
            characters_json,
            scenes_json,
            props_json,
            image_paths_str,
            len(characters),
            len(scenes),
            len(props),
        )


class H3AssetLoader(io.ComfyNode):
    """从 H3AssetLibrary 的 image_paths（换行分隔绝对路径）加载图片，
    输出最多 9 张 IMAGE 到独立端口，供 Extender 的 Reference Pack Bridge 使用。

    不足 9 张时，多余的端口重复最后一张有效图（未连线的端口不会使用该值）。
    """

    MAX_IMAGES = 9

    @classmethod
    def define_schema(cls):
        outputs = [
            io.Image.Output(id=f"image_{i}", display_name=f"image_{i}")
            for i in range(cls.MAX_IMAGES)
        ]
        outputs.append(io.Int.Output(id="count", display_name="count"))
        return io.Schema(
            node_id="H3AssetLoader",
            display_name="H3 Asset Loader (micxin)",
            category="H3 helper/micxin/AutoDirector",
            description=(
                "把 H3AssetLibrary 的 image_paths（每行一个绝对路径）加载成图片，"
                "输出最多 9 张 IMAGE。用于把角色库参考图喂给 "
                "MiniMax H3 Reference Pack Bridge（Ref 1..9）。"
            ),
            inputs=[
                io.String.Input(
                    "image_paths",
                    multiline=True,
                    default="",
                    tooltip="换行分隔的图片绝对路径，来自 H3AssetLibrary 的 image_paths 输出。",
                ),
            ],
            outputs=outputs,
        )

    @classmethod
    def execute(cls, **kwargs):
        image_paths = (kwargs.get("image_paths", "") or "").strip()
        paths = [p.strip() for p in image_paths.splitlines() if p.strip()]

        loaded = []
        for p in paths[: cls.MAX_IMAGES]:
            try:
                loaded.append(_load_image_tensor(p))
            except Exception as e:
                print(f"[H3AssetLoader] 加载失败 {p}: {e}", flush=True)
                loaded.append(None)

        valid = [x for x in loaded if x is not None]
        outputs = []
        for i in range(cls.MAX_IMAGES):
            if i < len(loaded) and loaded[i] is not None:
                outputs.append(loaded[i])
            elif valid:
                outputs.append(valid[-1])
            else:
                outputs.append(torch.zeros(1, 64, 64, 3))
        outputs.append(len(valid))
        print(
            f"[H3AssetLoader] 已加载 {len(valid)}/{min(len(paths), cls.MAX_IMAGES)} 张图。",
            flush=True,
        )
        return io.NodeOutput(*outputs)


NODE_CLASS_MAPPINGS = {
    "H3AssetLibrary": H3AssetLibrary,
    "H3AssetLoader": H3AssetLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3AssetLibrary": "H3 Asset Library (micxin)",
    "H3AssetLoader": "H3 Asset Loader (micxin)",
}
