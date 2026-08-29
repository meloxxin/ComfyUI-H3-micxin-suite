# -*- coding: utf-8 -*-
"""H3ReferenceBuilder — 从上一段输出视频自动抽取参考图 + 关键信息。

解决的问题
----------
第二段视频想接住第一段的人物外观 / 场景 / 画风时，最稳的手段就是把第一段视频
的关键帧直接喂给 Ref2VA。本节点一键完成：

  输入：一个视频文件路径（或 VHS_VideoCombine 的输出文件路径）
  输出：
    - IMAGE（抽出的参考图张量 [N, H, W, 3]）
    - INT / STRING（抽出的关键帧时间戳 mm:ss.mmm）
    - STRING（自动 summary：每张参考图的简短文字描述，复制给 H3Screenwriter
       的 concept 框或 prev_settings 直接当「续集」写入用）

抽帧模式（mode 下拉）
----------------------
  last_frame       只抽最后一帧。最常用，最稳连接。
  first_frame      只抽第一帧。开篇承接。
  first_last       首帧 + 末帧两张。
  quarter_frames   总长 1/4、2/4、3/4 各抽一帧（3 张），便于多角度参考。
  scene_grid       把首尾两端与 4 等分位置共 6 张拼成一张 3x2 grid 作为参考图。

输出 IMAGE 的 tensor 是 4D `(N,H,W,3)` ComfyUI 标准格式，单图时 N=1。
"""
import os
import sys
import time

# 显式 import 上层 comfy（避免 comfy_env 隔离陷阱）
try:
    import folder_paths  # noqa: F401
    from comfy.utils import common_upscale
except Exception:
    folder_paths = None
    common_upscale = None

try:
    import numpy as np
except Exception:
    np = None

try:
    from PIL import Image
except Exception:
    Image = None

try:
    import torch
except Exception:
    torch = None

try:
    import av  # PyAV
except Exception:
    av = None


def _to_comfy_image(pil_image):
    """Convert a single PIL image to ComfyUI IMAGE tensor (1, H, W, 3) float32."""
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    arr = np.asarray(pil_image, dtype=np.float32) / 255.0  # (H, W, 3)
    t = torch.from_numpy(arr).unsqueeze(0)  # (1, H, W, 3)
    return t


def _grid_3x2(images_pil, target_side):
    """把 6 张 PIL 拼成 3x2 grid，返回单张 PIL。"""
    if not images_pil:
        return None
    cols = 3
    rows = 2
    cell_w = target_side
    cell_h = target_side
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (16, 16, 16))
    for i, im in enumerate(images_pil[:6]):
        if im.mode != "RGB":
            im = im.convert("RGB")
        im2 = im.copy()
        im2.thumbnail((cell_w, cell_h), Image.LANCZOS)
        # center-crop into cell
        cw, ch = im2.size
        if cw < cell_w or ch < cell_h:
            # pad with black
            bg = Image.new("RGB", (cell_w, cell_h), (16, 16, 16))
            ox = (cell_w - cw) // 2
            oy = (cell_h - ch) // 2
            bg.paste(im2, (ox, oy))
            im2 = bg
        cx = (i % cols) * cell_w
        cy = (i // cols) * cell_h
        canvas.paste(im2, (cx, cy))
    return canvas


def _ts(seconds):
    mm = int(seconds // 60)
    ss_mmm = seconds - mm * 60
    return f"{mm:02d}:{ss_mmm:06.3f}"


class H3ReferenceBuilder:
    """从视频自动提取关键帧 + 一份简短场景摘要，作为下一段 H3 生成的参考素材。

    输出三样东西：
      - `images`         IMAGE  [N, H, W, 3] — 直接接 H3MultiImageLoader 的 image_paths
                         或者 Ref2VA 的 ref_image_0 槽位（用 IMAGE 类型）
      - `timestamps`     STRING  "first@00:00.000, last@00:12.345"  (供 prompt 参考)
      - `description`    STRING  简短场景描述文本（英文，自动写的，用户复制走人）

    mode 决定抽几张 + 怎么排：
      last_frame       (1 张, 末尾帧)
      first_frame      (1 张, 开头帧)
      first_last       (2 张, 首+末)
      quarter_frames   (3 张, 1/4, 2/4, 3/4)
      scene_grid       (6 张拼接成 3x2 grid)
    """

    @classmethod
    def INPUT_TYPES(cls):
        # 列出 ComfyUI output 目录下 video 文件做 path 选择
        choices = cls._list_video_files()
        return {
            "required": {
                "video_path": (choices if choices else ["(no videos found)"], {
                    "default": choices[0] if choices else "",
                    "tooltip": "上一段视频的输出路径（VHS_VideoCombine 默认在 output/video/）。"
                               "或者直接粘绝对路径如 C:\\ComfyUI\\output\\video\\xxx.mp4。"}),
                "mode": (["last_frame", "first_frame", "first_last",
                          "quarter_frames", "scene_grid"], {
                    "default": "last_frame",
                    "tooltip": "抽帧模式：末尾 / 开头 / 首尾 / 3 个等分位置 / 首尾+4 个等分点拼 3x2。"}),
                "max_side": ("INT", {"default": 768, "min": 256, "max": 2048,
                                     "tooltip": "抽出的帧最大边像素（保持原比例）。"}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "timestamps", "description")
    FUNCTION = "build"
    CATEGORY = "H3 helper/micxin/AutoDirector"

    @staticmethod
    def _list_video_files():
        try:
            if folder_paths is None:
                return []
            base = folder_paths.get_output_directory()
            vdir = os.path.join(base, "video")
            if not os.path.isdir(vdir):
                return []
            return sorted(
                [os.path.join("video", f).replace("\\", "/")
                 for f in os.listdir(vdir)
                 if f.lower().endswith((".mp4", ".mov", ".webm", ".mkv"))]
            )
        except Exception:
            return []

    def build(self, video_path, mode, max_side):
        # resolve full path
        if not video_path:
            raise ValueError("H3ReferenceBuilder: video_path is empty.")
        if not os.path.isabs(video_path):
            if folder_paths is not None:
                candidate = os.path.join(folder_paths.get_output_directory(), video_path)
                if os.path.exists(candidate):
                    video_path = candidate
            if not os.path.exists(video_path):
                # try under ComfyUI/input
                try:
                    candidate = os.path.join(
                        folder_paths.get_input_directory(),
                        os.path.basename(video_path))
                    if os.path.exists(candidate):
                        video_path = candidate
                except Exception:
                    pass

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"H3ReferenceBuilder: video not found: {video_path}")
        if av is None:
            raise RuntimeError("H3ReferenceBuilder: av (PyAV) not available — install PyAV (av>=12).")

        # open video & pick frames
        frames_pil, timestamps = self._extract_frames(video_path, mode)
        if not frames_pil:
            raise RuntimeError(f"H3ReferenceBuilder: no frames extracted from {video_path}")

        # resize each frame
        resized = []
        for im in frames_pil:
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            resized.append(im)

        # if scene_grid, compose 3x2 grid
        if mode == "scene_grid":
            grid = _grid_3x2(resized, max_side // 2)
            if grid is not None:
                # keep timestamps as the 6 source timestamps
                final_pil = [grid]
                ts_str = ", ".join(f"f{i}@{t}" for i, t in enumerate(timestamps))
            else:
                final_pil = resized
                ts_str = ", ".join(f"f{i}@{t}" for i, t in enumerate(timestamps))
        else:
            final_pil = resized
            ts_str = ", ".join(f"f{i}@{t}" for i, t in enumerate(timestamps))

        # convert to IMAGE tensor
        import torch  # noqa
        tensors = [torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0).unsqueeze(0)
                   for im in final_pil]
        images_tensor = torch.cat(tensors, dim=0)  # (N, H, W, 3)

        # auto description text — useful as input to H3Screenwriter concept / prev_settings
        desc = (
            f"Auto-extracted {len(resized)} reference frame(s) from {os.path.basename(video_path)} "
            f"using mode='{mode}'. Timestamps (mm:ss.mmm): {ts_str}. "
            f"These frames anchor the next segment to the visual content of the previous one — "
            f"use them as <Picture 1> / <Picture 2> / ... references for H3."
        )

        return (images_tensor, ts_str, desc)

    @staticmethod
    def _extract_frames(video_path, mode):
        """Return (list_of_PIL_Image, list_of_timestamp_strings)."""
        results = []
        ts = []
        container = av.open(video_path)
        try:
            vstream = container.streams.video[0]
            duration_s = float(vstream.duration * vstream.time_base) if vstream.duration else 0.0
            n_frames = vstream.frames if vstream.frames else 0
            if duration_s <= 0:
                # estimate from avg_rate
                try:
                    avg_rate = vstream.average_rate
                    if avg_rate and vstream.frames:
                        duration_s = vstream.frames / float(avg_rate)
                except Exception:
                    duration_s = 0.0
            if duration_s <= 0:
                duration_s = 10.0  # fallback 10s

            # iterate all packets, keep decoded frames, record pts in seconds
            decoded = []
            for packet in container.demux(vstream):
                for frame in packet.decode():
                    if frame is None:
                        continue
                    decoded.append(frame)
                    # keep working set bounded — last few seconds only
                    if len(decoded) > 720:  # ~30s @ 24fps; do not keep more
                        decoded = decoded[-720:]

            if not decoded:
                return results, ts

            n = len(decoded)

            def pts_to_sec(f):
                try:
                    return float(f.pts * f.time_base) if f.pts is not None else 0.0
                except Exception:
                    return 0.0

            # helper to get a frame index
            def take(idx):
                idx = max(0, min(n - 1, idx))
                return decoded[idx], pts_to_sec(decoded[idx])

            if mode == "last_frame":
                f, s = take(n - 1)
                results = [_pil_from_frame(f)]
                ts = [_ts(s)]
            elif mode == "first_frame":
                f, s = take(0)
                results = [_pil_from_frame(f)]
                ts = [_ts(s)]
            elif mode == "first_last":
                f1, s1 = take(0)
                f2, s2 = take(n - 1)
                results = [_pil_from_frame(f1), _pil_from_frame(f2)]
                ts = [_ts(s1), _ts(s2)]
            elif mode == "quarter_frames":
                # 1/4, 2/4, 3/4 of timeline
                positions = [n // 4, 2 * n // 4, 3 * n // 4]
                for p in positions:
                    f, s = take(p)
                    results.append(_pil_from_frame(f))
                    ts.append(_ts(s))
            elif mode == "scene_grid":
                # 6 positions: 0, 1/5..5/5
                positions = [0, n // 5, 2 * n // 5, 3 * n // 5,
                             4 * n // 5, n - 1]
                for p in positions:
                    f, s = take(p)
                    results.append(_pil_from_frame(f))
                    ts.append(_ts(s))
            else:
                # default: last frame
                f, s = take(n - 1)
                results = [_pil_from_frame(f)]
                ts = [_ts(s)]
        finally:
            try:
                container.close()
            except Exception:
                pass

        return results, ts


def _pil_from_frame(frame):
    """Convert PyAV VideoFrame to PIL.Image."""
    if Image is None:
        raise RuntimeError("PIL not available — install Pillow.")
    img = frame.to_image()  # av>=12: directly returns PIL.Image (deprecated path)
    if hasattr(img, "mode") and img.mode == "RGB":
        return img
    if hasattr(img, "convert"):
        return img.convert("RGB")
    # fallback: ndarray
    arr = frame.to_ndarray(format="rgb24")
    return Image.fromarray(arr)


NODE_CLASS_MAPPINGS = {"H3ReferenceBuilder": H3ReferenceBuilder}
NODE_DISPLAY_NAME_MAPPINGS = {"H3ReferenceBuilder": "H3 Reference Builder (micxin)"}
