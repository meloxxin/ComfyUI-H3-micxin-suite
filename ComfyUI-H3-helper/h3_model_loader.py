"""H3 Reference to Video (micxin) - one-stop super node with embedded media loader.

Merges the H3 model/CV loader + MiniMaxH3ReferenceToVideo + media uploader into
a single node: loads UNet + sage attention + CLIP + dual VAE, loads reference
media (image/video/audio) directly inside the node via drag-and-drop tabs, then
in-process invokes the Ref2VA execution path.

Reference media is managed by three hidden multiline string widgets
(image_paths / video_paths / audio_paths), each line `path|start_sec|end_sec`.
The companion JS (web/h3_aio_media.js) provides a tabbed drag-upload grid with
drag-handle reordering, replace, up/down arrows, and per-clip trim. No external
wiring is needed -- drag or upload a file and it is connected automatically.

Outputs include multi_output (batched reference images) for LLM reverse-prompt use.

This file is the V3 io.ComfyNode form.
"""

import os
import json
import torch
import torchaudio
import comfy.sd
import comfy.utils
import comfy.model_management as mm
import comfy.nested_tensor
import folder_paths
import nodes
from comfy_api.latest import io
import math
import node_helpers
from comfy_extras.nodes_minimax_h3 import (
    MiniMaxH3ReferenceToVideo, MiniMaxH3AddGuide,
    _resize, adapt_canvas, CANVAS_MULTIPLE, REF_IMAGE_SHORT_EDGE, FPS)
from comfy.ldm.minimax.model import FRAME_PER_TOKEN, FRAME_RESCALE
from .h3_media_utils import load_all_media, load_keyframes, DEFAULT_FRAME_RATE, DEFAULT_MAX_SIDE


# ---------------------------------------------------------------------------
# Folder registration guard (so the projection dropdown is populated even if
# ComfyUI-ClipProj hasn't been imported yet, e.g. load-order changes).
# ---------------------------------------------------------------------------
def _projections():
    folder = "clip_projections"
    path = os.path.join(folder_paths.models_dir, folder)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    existing = folder_paths.folder_names_and_paths.get(folder)
    if existing is None:
        folder_paths.folder_names_and_paths[folder] = ([path], {".pt", ".safetensors"})
    elif path not in existing[0]:
        existing[0].append(path)
    try:
        return [f for f in folder_paths.get_filename_list(folder)
                if f.lower().endswith((".safetensors", ".pt"))]
    except Exception:
        return []


# Sage acceleration is forward-hook on attention, NOT a weight op.
# "None" -> do not override -> ComfyUI default (pytorch) attention.
ATTENTION_MAP = {
    "comfy kitchen attention (sage)": "comfy_kitchen_int8",
    "不使用 (pytorch 默认)": None,
}


def _gpu_devices():
    """Canonical torch device strings: cuda:N for each GPU, then cpu."""
    devs = []
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            devs.append("cuda:%d" % i)
    devs.append("cpu")
    return devs


def _clip_types():
    """All ComfyUI CLIPType names plus the three MiniMax ones, 'auto' first."""
    names = sorted(t.name.lower() for t in comfy.sd.CLIPType)
    for first in ("minimax", "boogu", "krea2"):
        if first in names:
            names.remove(first)
            names.insert(0, first)
    return ["auto"] + names


# ---------------------------------------------------------------------------
# 音频锁源（Audio Lock）—— 在 AddGuide 定帧锚定之上叠加 latent 层锁源。
#
# 背景：MiniMaxH3AddGuide 把源音频作为 guide 放进 conditioning（minimax_keyframes），
# 模型每步 re-inject 且 never-denoised，能"定帧"驱动口型；但最终 VAEDecodeAudio
# 解码的是主 AV latent 的音频半区，guide 只是条件，模型仍可能改动/重生成该段。
# 这里把源音频 latent 直接写进主 latent 的音频半区，并对其设 zero-denoise mask
# （noise_mask=0 表示采样时保持固定），保证成片音频就是自定义源音频。
# 与 VRGDG_MiniMaxH3AudioDrive 同理，但支持按 frame_idx 精确定位（定帧锁源）。
# ---------------------------------------------------------------------------
def _encode_audio_for_lock(audio_vae, audio):
    """AUDIO dict -> H3 音频 latent [1, 32, 2, T]（与官方 _encode_ref_audio 等价）。"""
    waveform = audio["waveform"]  # [B, C, L]
    sr = audio["sample_rate"]
    vae_sr = getattr(audio_vae, "audio_sample_rate", 32000)
    if int(sr) != vae_sr:
        waveform = torchaudio.functional.resample(waveform, int(sr), vae_sr)
    return audio_vae.encode(waveform[:1].movedim(1, -1))


def _fit_audio_latent(encoded, template):
    """把 [1,32,2,T] 对齐到 template [B,32,2,T40]（batch 广播 + 长度截断/对齐）。"""
    if encoded.ndim != 4 or template.ndim != 4:
        raise ValueError("H3 音频 latent 必须是 [batch, channels, stereo, time]")
    if encoded.shape[1:-1] != template.shape[1:-1]:
        raise ValueError(
            "编码源音频与 H3 音频 latent 布局不符："
            f"got {tuple(encoded.shape[1:-1])}, expected {tuple(template.shape[1:-1])}")
    batch = template.shape[0]
    if encoded.shape[0] == 1 and batch > 1:
        encoded = encoded.repeat(batch, 1, 1, 1)
    target_t = template.shape[-1]
    if encoded.shape[-1] > target_t:
        encoded = encoded[..., :target_t]
    return encoded.to(device=template.device, dtype=template.dtype)


def _audio_lock_latent(latent, keyframes, audio_vae, mode, post_sample=False):
    """把关键帧的源音频按 frame_idx 锁进 H3 AV latent 的音频半区。

    mode='off'   : 原样返回，不锁。
    mode='anchor': 锚定段锁死为源音频（zero-denoise），其余段仍由模型生成。
    mode='full'  : 锚定段锁死为源音频，其余段锁死为静音（整轨自定义，彻底不用模型音频）。
    post_sample=True: 采样后调用，只写音频 latent 不设 noise_mask（采样已结束，
                      用于补偿 comfy.sample 不识别 NestedTensor noise_mask 的问题）。
    返回 (new_latent, report)。无带音频关键帧时不改动。
    """
    if mode == "off":
        return latent, ""
    if audio_vae is None:
        raise RuntimeError(
            "audio_lock 需要 audio_vae（H3 音频 VAE）。请在 H3ModelLoader 选择 audio_vae_name。")
    samples = latent.get("samples")
    if not getattr(samples, "is_nested", False) or len(samples.tensors) != 2:
        raise RuntimeError(
            "audio_lock 需要 MiniMax H3 的 AV latent（嵌套 video+audio）。"
            "请确认 H3ModelLoader 输出了正确的 Ref2VA latent。")
    video, template_audio = samples.tensors[0], samples.tensors[1]
    if template_audio.ndim != 4:
        raise RuntimeError(
            f"H3 音频 latent 应为 [B,32,2,T]，实际 {tuple(template_audio.shape)}")
    total_len = template_audio.shape[-1]
    frame_count = sum(FRAME_PER_TOKEN[k % 5] for k in range(video.shape[2]))

    placements = []  # (start_rt, use, z)
    for kf in keyframes:
        audio = kf.get("audio")
        if audio is None:
            continue
        z = _encode_audio_for_lock(audio_vae, audio)
        z = _fit_audio_latent(z, template_audio)
        resolved = kf["frame_idx"] if kf["frame_idx"] >= 0 else frame_count + kf["frame_idx"]
        start_rt = int(round(FRAME_RESCALE * resolved))  # 像素帧 -> 音频 latent 帧
        if start_rt < 0 or start_rt >= total_len:
            continue
        use = min(z.shape[-1], total_len - start_rt)
        if use <= 0:
            continue
        placements.append((start_rt, use, z))

    if not placements:
        return latent, "audio_lock: 关键帧无带音频条目，未锁定任何源音频"

    locked_audio = template_audio.clone()
    if mode == "full":
        locked_audio = torch.zeros_like(template_audio)  # 非锚定段 = 静音
    for start_rt, use, z in placements:
        locked_audio[..., start_rt:start_rt + use] = z[..., :use]

    out = dict(latent)
    out["samples"] = comfy.nested_tensor.NestedTensor((video, locked_audio))

    if not post_sample:
        # noise_mask: 1 = 参与去噪，0 = 保持固定
        # 注意：comfy.sample.sample 的 noise_mask 参数不识别 NestedTensor 格式，
        # 所以这里的 noise_mask 实际可能被忽略。采样后需再调一次 post_sample=True 确保音频锁定。
        mask = torch.zeros_like(template_audio) if mode == "full" else torch.ones_like(template_audio)
        for start_rt, use, _z in placements:
            mask[..., start_rt:start_rt + use] = 0.0
        out["noise_mask"] = comfy.nested_tensor.NestedTensor((torch.ones_like(video), mask))

    label = "整轨自定义" if mode == "full" else "锚定段自定义"
    post_tag = " [post-sample]" if post_sample else ""
    return out, f"audio_lock({label}){post_tag}: 已锁定 {len(placements)} 段源音频于音频 latent"


def _ingest_ref_images(tensor, image_paths):
    """把外部 IMAGE 张量（Qwen Edit 2511 / 文生图四视图等）保存进 input/h3_aio/，
    并把路径行（rel|0|0）前置并入 image_paths —— 本轮 load_all_media 立即读取，
    文件留在 input/ 供素材库网格显示与工作流持久化。
    返回 (new_image_paths, report)。
    """
    if tensor is None:
        return image_paths, ""
    try:
        import time
        import random
        from PIL import Image as PILImage
    except Exception as e:
        return image_paths, f"[外部入库] 缺少 PIL: {e}"
    try:
        subdir = os.path.join(folder_paths.get_input_directory(), "h3_aio")
        os.makedirs(subdir, exist_ok=True)
    except OSError as e:
        return image_paths, f"[外部入库] 创建目录失败: {e}"
    batch = tensor.cpu().float()
    if batch.ndim == 3:
        batch = batch.unsqueeze(0)
    ts = time.strftime("%Y%m%d_%H%M%S")
    rand = random.randint(1000, 9999)
    lines = []
    reports = []
    for i in range(batch.shape[0]):
        img_t = batch[i].clamp(0.0, 1.0)
        if img_t.ndim == 3 and img_t.shape[0] in (1, 3):  # CHW → HWC
            img_t = img_t.permute(1, 2, 0)
        arr = (img_t * 255.0).to(torch.uint8).numpy()
        name = f"ref_{ts}_{rand}_{i}.png"
        rel = os.path.join("h3_aio", name).replace("\\", "/")
        try:
            PILImage.fromarray(arr).save(os.path.join(subdir, name))
            lines.append(f"{rel}|0|0")
            reports.append(f"[外部入库] {rel}")
        except Exception as e:
            reports.append(f"[外部入库] 第{i}张保存失败: {e}")
    if not lines:
        return image_paths, "\n".join(reports)
    existing = [ln for ln in (image_paths or "").splitlines() if ln.strip()]
    new_paths = "\n".join(lines + existing)
    reports.append(f"[外部入库] 共 {len(lines)} 张，已前置进图片素材库")
    return new_paths, "\n".join(reports)


def _build_ref_items(ref_images, ref_videos, ref_video_audios, ref_audios,
                     width, height, ref_image_size="match",
                     frame_count=None, fps=24):
    """重建官方 MiniMaxH3ReferenceToVideo 内部的 ref_items（Qwen 视觉 token 输入）。

    官方节点在 execute 内部构造 ref_items 后直接 tokenize；AIO 委托官方节点时
    拿不到这份数据。这里按官方相同逻辑重建（图/视频/音频全覆盖），存入 positive
    extra 的 minimax_ref_items，供下游（ClipChain 分段重编码）重新喂给 Qwen
    看图——否则分段重编码丢失参考图语义锚，人物（人脸）一致性失效。
    """
    ref_items = []
    for img in (ref_images or {}).values():
        if img is None:
            continue
        h, w = img.shape[1], img.shape[2]
        if ref_image_size == "match":
            scale = min(1.0, math.sqrt((width * height) / (w * h)))
        else:
            scale = min(1.0, REF_IMAGE_SHORT_EDGE / min(w, h))
        tw = max(CANVAS_MULTIPLE, round(w * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
        th = max(CANVAS_MULTIPLE, round(h * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
        resized = _resize(img[:1], tw, th, "disabled")
        ref_items.append({"type": "image", "data": resized})

    for name, video_frames in (ref_videos or {}).items():
        if video_frames is None:
            continue
        soundtrack = (ref_video_audios or {}).get(
            "ref_video_audio_" + name.rsplit("_", 1)[-1])
        vh, vw = video_frames.shape[1], video_frames.shape[2]
        cw, ch = adapt_canvas(vw, vh)
        if vw * vh < cw * ch:
            cw = max(CANVAS_MULTIPLE, round(vw / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
            ch = max(CANVAS_MULTIPLE, round(vh / CANVAS_MULTIPLE) * CANVAS_MULTIPLE)
        frames = _resize(video_frames, cw, ch, "disabled")
        if frame_count and frames.shape[0] > frame_count:
            frames = frames[:frame_count]
        n = frames.shape[0]
        if n < 5:
            raise ValueError("MiniMax H3 reference videos need at least 5 frames (~0.2s at 24 fps)")
        while n % 17 != 5:
            n -= 1
        frames = frames[:n]
        if soundtrack is not None:
            ref_items.append({"type": "audio"})
        sample_idx = list(range(0, frames.shape[0], max(1, fps // 2)))
        qwen_frames = frames[sample_idx]
        ref_items.append({
            "type": "video", "data": qwen_frames,
            "timestamps": [i / 2.0 for i in range(len(sample_idx))]})

    for audio in (ref_audios or {}).values():
        if audio is None:
            continue
        ref_items.append({"type": "audio"})

    return ref_items



class H3ModelLoader(io.ComfyNode):
    """All-in-one H3 loader + Ref2VA - emits 6 ports
    (positive / Latent / MODEL / CLIP / VAE(video) / VAE(audio))."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ModelLoader",
            display_name="H3 R2VA AIO(micxin)",
            description=(
                "All-in-one H3 loader + Ref2VA + embedded media uploader: loads UNet + "
                "sage attention + CLIP + dual VAE, loads reference media (image/video/audio) "
                "via drag-and-drop tabs inside the node, then runs Ref2VA. prompt / width / "
                "height / length are input sockets — connect from H3 Screenwriter (micxin). "
                "No external media wiring needed."
            ),
            category="H3 helper/micxin",
            inputs=[
                io.String.Input(
                    "prompt",
                    display_name="prompt",
                    optional=True,
                    force_input=True,
                    tooltip="Prompt input socket — connect from H3 Screenwriter (micxin) h3_script output. No in-node text editor.",
                ),
                io.Int.Input("width", default=1344, min=32, max=nodes.MAX_RESOLUTION, step=32,
                    optional=True, force_input=True,
                    tooltip="Width input socket — connect from H3 Screenwriter width output."),
                io.Int.Input("height", default=768, min=32, max=nodes.MAX_RESOLUTION, step=32,
                    optional=True, force_input=True,
                    tooltip="Height input socket — connect from H3 Screenwriter height output."),
                io.Int.Input("length", default=124, min=5, max=3600, step=17,
                    optional=True, force_input=True,
                    tooltip="Frame count input socket — connect from H3 Screenwriter length output (124 = ~5s)."),
                io.Combo.Input("ref_image_size", options=["match", "max"], default="match",
                               tooltip="Reference image sizing. 'match' scales each ref to the generation pixel area; 'max' uses 2048px short edge for best identity fidelity (slower)."),
                io.Combo.Input("unet_name",
                    options=[""] + list(folder_paths.get_filename_list("diffusion_models"))
                            + list(folder_paths.get_filename_list("unet_gguf")),
                    default="",
                    tooltip="H3 diffusion model (UNet). Supports .safetensors and .gguf."),
                io.Combo.Input("weight_dtype", options=["default", "fp16", "bf16", "fp32"], default="default"),
                io.Combo.Input("attention_backend", options=list(ATTENTION_MAP.keys()),
                               default="comfy kitchen attention (sage)",
                               tooltip="Sage attention is a forward-hook (survives downstream LoRA), not a weight op."),
                io.Boolean.Input("use_clipproj", default=False,
                                 label_on="ClipProj（投影）", label_off="普通 CLIP（无投影）",
                                 tooltip="OFF = plain CLIP (no projection); ON = ClipProj projection mode (needs ComfyUI-ClipProj + a projection entry)."),
                io.Combo.Input("clip_name", options=[""] + list(folder_paths.get_filename_list("text_encoders")), default="",
                               tooltip="Qwen3-VL text encoder. Required."),
                io.Combo.Input("clip_type", options=_clip_types(), default="auto",
                               tooltip="'auto' picks the CLIP type matching the loaded text encoder (correct branch for Qwen3-VL-4B)."),
                io.Combo.Input("projection", options=[""] + list(_projections()), default="",
                               tooltip="clip_projections entry, e.g. h3_qwen3vl_4b_tap24. Only used when use_clipproj is ON."),
                io.Combo.Input("clip_device", options=_gpu_devices(),
                               default="cuda:0" if torch.cuda.is_available() else "cpu"),
                io.Combo.Input("clip_load_mode", options=["resident", "streaming", "dynamic"], default="resident"),
                io.Combo.Input("video_vae_name", options=[""] + list(folder_paths.get_filename_list("vae")), default="",
                               tooltip="H3 video VAE. Required."),
                io.Combo.Input("audio_vae_name", options=[""] + list(folder_paths.get_filename_list("vae")), default="",
                               tooltip="H3 audio VAE. Leave blank for LTX / H3-without-audio pipelines."),
                # Reference media - hidden multiline widgets managed by the embedded
                # drag-and-drop tab UI (web/h3_aio_media.js). Each line: path|start|end.
                io.String.Input("image_paths", multiline=True, default="",
                    extra_dict={"hidden": True},
                    tooltip="Hidden: reference image paths, managed by the embedded upload tab"),
                io.String.Input("video_paths", multiline=True, default="",
                    extra_dict={"hidden": True},
                    tooltip="Hidden: reference video paths, managed by the embedded upload tab"),
                io.String.Input("audio_paths", multiline=True, default="",
                    extra_dict={"hidden": True},
                    tooltip="Hidden: reference audio paths, managed by the embedded upload tab"),
                io.String.Input("keyframe_paths", multiline=True, default="",
                    extra_dict={"hidden": True},
                    tooltip=("Hidden: keyframe guide entries (Add Guide), managed by the embedded "
                             "keyframe tab. Each line: media_path|audio_path|frame_idx|"
                             "media_start|media_end|audio_start|audio_end")),
                # ---- 外部图片入口（IMAGE 张量直接入库）----
                # 上游 Qwen Edit 2511 / 文生图四视图 / 任意 IMAGE 输出连线后，图片自动
                # 保存进 input/h3_aio/ 并前置为参考图（本轮立即生效 + 素材库持久化）。
                io.Image.Input(
                    "ref_images_in",
                    optional=True,
                    tooltip=("外部图片入口：接 IMAGE 张量（Qwen Edit 2511 编辑结果 / "
                             "文生图四视图 / LoadImage 等）。连线后图片自动保存进 "
                             "input/h3_aio/ 并作为参考图前置使用，同时出现在素材库图片 tab。"),
                ),
                io.Int.Input("frame_rate", default=DEFAULT_FRAME_RATE, min=1, max=120,
                    tooltip="Video extraction frame rate (fps)"),
                io.Int.Input("max_side", default=DEFAULT_MAX_SIDE, min=0, max=4096, step=8,
                    tooltip="Max side length for image/video frames, 0=original size (kept even)"),
                io.Boolean.Input("bypass_keyframes", default=False,
                    tooltip=("Bypass keyframe guide injection (Add Guide). ON = skip keyframe_paths "
                             "entirely, positive carries no keyframes. Use when H3ClipChain injects "
                             "per-segment keyframes itself, or when keyframe_paths is shared with "
                             "other single-segment workflows and would exceed this latent's frame count.")),
                io.Combo.Input("audio_lock_mode", options=["off", "full"], default="off",
                    tooltip=("音频锁源：采样后把关键帧源音频直接写回 latent，保证成片音频=自定义源音频。"
                             "off=不锁（纯模型生成音频，关键帧音频仅作 AddGuide 引导）；full=整轨自定义（关键帧位置源音频，其余静音）。"
                             "需选择 audio_vae。")),
                io.Int.Input("update", default=0, min=0, max=0xffffffffffffffff,
                    extra_dict={"hidden": True},
                    tooltip="Hidden: auto-incremented by the UI on any media change to force re-execution"),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
                io.Clip.Output(display_name="clip"),
                io.Vae.Output(display_name="vae"),
                io.Vae.Output(display_name="audio_vae"),
                io.Conditioning.Output(display_name="positive"),
                io.Latent.Output(display_name="latent"),
                io.String.Output(display_name="media_report"),
            ],
        )

    @classmethod
    def execute(cls, prompt=None, width=1344, height=768, length=124, ref_image_size="match",
                unet_name="", weight_dtype="default", attention_backend="comfy kitchen attention (sage)",
                use_clipproj=False, clip_name="", clip_type="auto", projection="",
                clip_device="cuda:0", clip_load_mode="resident",
                video_vae_name="", audio_vae_name="",
                image_paths="", video_paths="", audio_paths="", keyframe_paths="",
                ref_images_in=None,
                frame_rate=DEFAULT_FRAME_RATE, max_side=DEFAULT_MAX_SIDE,
                bypass_keyframes=False, audio_lock_mode="off", update=0) -> io.NodeOutput:
        # prompt is an input socket (from H3 Screenwriter); normalize None to empty string
        if prompt is None:
            prompt = ""
        # 外部 IMAGE 入口：上游图片（Qwen Edit 2511 / 文生图四视图）直接入库，
        # 保存到 input/h3_aio/ 并前置进 image_paths（本轮立即生效 + 素材库可见）。
        _ingest_report = ""
        if ref_images_in is not None:
            image_paths, _ingest_report = _ingest_ref_images(ref_images_in, image_paths)
        # Load all reference media from the hidden path widgets (managed by the
        # embedded drag-and-drop tab UI). Returns 0-indexed dicts matching Ref2VA.
        ref_images, ref_videos, ref_video_audios, ref_audios, _multi_output, _report = \
            load_all_media(image_paths, video_paths, audio_paths, frame_rate, max_side)

        # 1. MODEL - GGUF or native, auto-detect by extension.
        if not unet_name:
            raise RuntimeError(
                "H3ModelLoader: unet_name is required - pick an H3 diffusion model.")

        if unet_name.lower().endswith(".gguf"):
            # GGUF path — delegate to the registered UnetLoaderGGUF node.
            loader_cls = nodes.NODE_CLASS_MAPPINGS.get("UnetLoaderGGUF")
            if loader_cls is None:
                raise RuntimeError(
                    "H3ModelLoader: ComfyUI-GGUF not installed - cannot load .gguf model.")
            model = loader_cls().load_unet(unet_name)[0]
        else:
            # native safetensors path
            unet_path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
            model_options = {}
            if weight_dtype != "default":
                model_options["weight_dtype"] = getattr(torch, weight_dtype)
            model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)

        # 2. Sage attention (forward-hook; survives downstream LoRA clones)
        attn_name = ATTENTION_MAP.get(attention_backend)
        if attn_name is not None:
            attn_fn = comfy.ldm.modules.attention.get_attention_function(attn_name, None)
            if attn_fn is None:
                attn_fn = comfy.ldm.modules.attention.get_attention_function("pytorch")
            if attn_fn is not None:
                model = model.clone()
                model.set_model_optimized_attention(attn_fn)

        # 3. CLIP - default = plain CLIP (no ClipProj); ClipProj is opt-in via use_clipproj
        if not clip_name:
            raise RuntimeError("H3ModelLoader: clip_name is required - pick a Qwen3-VL encoder.")
        if use_clipproj:
            loader = nodes.NODE_CLASS_MAPPINGS.get("ClipProjLoader")
            if loader is None:
                raise RuntimeError(
                    "use_clipproj is ON but ClipProjLoader not found. Install "
                    "ComfyUI-ClipProj, or turn use_clipproj OFF for plain CLIP.")
            if not projection:
                raise RuntimeError(
                    "use_clipproj is ON but projection is empty - pick a "
                    "clip_projections entry (e.g. h3_qwen3vl_4b_tap24).")
            clip = loader().load(clip_name, clip_type, projection, clip_device, clip_load_mode)[0]
        else:
            path = folder_paths.get_full_path_or_raise("text_encoders", clip_name)
            embeddings = folder_paths.get_folder_paths("embeddings")
            # CLIPType has no AUTO member, so "auto" must fall through to the encoder CLIP type,
            # which is the correct branch for Qwen3-VL-4B.
            ctype = getattr(comfy.sd.CLIPType, clip_type.upper(), comfy.sd.CLIPType.KREA2)
            dev = torch.device(clip_device)
            offload = dev if clip_load_mode == "resident" else mm.text_encoder_offload_device()
            clip = comfy.sd.load_clip(
                ckpt_paths=[path], embedding_directory=embeddings, clip_type=ctype,
                model_options={"load_device": dev, "offload_device": offload},
                disable_dynamic=clip_load_mode in ("resident", "streaming"))

        # 4. Dual VAE - external video_vae socket takes precedence over video_vae_name.
        # VAELoader.load_vae is an *instance* method -> must instantiate the class first.
        vae_loader = nodes.NODE_CLASS_MAPPINGS["VAELoader"]()
        if not video_vae_name:
            raise RuntimeError(
                "H3ModelLoader: video_vae_name is required - pick the H3 video VAE.")
        video_vae = vae_loader.load_vae(video_vae_name)[0]
        audio_vae = vae_loader.load_vae(audio_vae_name)[0] if audio_vae_name else None

        # 5. Ref2VA execution - delegated to the upstream node.
        ref_out = MiniMaxH3ReferenceToVideo.execute(
            clip=clip, vae=video_vae, audio_vae=audio_vae, prompt=prompt,
            width=width, height=height, length=length,
            ref_image_size=ref_image_size,
            ref_images=ref_images,
            ref_videos=ref_videos,
            ref_video_audios=ref_video_audios,
            ref_audios=ref_audios,
        )
        positive = ref_out[0]
        latent = ref_out[1]

        # 5a0. 语义锚透传（人物一致性）：重建官方 ref_items 存入 positive extra。
        #      下游（ClipChain 分段重编码）用 clip.tokenize(prompt,
        #      minimax_ref_items=...) 重新喂给 Qwen 看图；否则分段重编码丢失
        #      参考图语义，人脸锚定失效（只剩 ref2va 像素弱约束）。
        try:
            _ref_items = _build_ref_items(
                ref_images, ref_videos, ref_video_audios, ref_audios,
                width, height, ref_image_size, frame_count=length, fps=frame_rate)
            if _ref_items:
                positive = node_helpers.conditioning_set_values(
                    positive, {"minimax_ref_items": _ref_items})
        except Exception as _e:
            print("[H3ModelLoader] minimax_ref_items rebuild skipped: %s" % _e, flush=True)

        # 5a. Guard: MiniMax H3's condition_proj expects a 5120-dim context.
        # With use_clipproj OFF + a Qwen3-VL-4B encoder loaded in its native CLIP type, the
        # TE flattens the 12-layer axis into the feature dim (12*2560=
        # 30720), which cannot feed the H3 DiT. Surface a clear message instead
        # of an opaque matmul error from inside the model.
        ctx_dim = positive[0][0].shape[-1]
        if ctx_dim != 5120:
            raise RuntimeError(
                "H3ModelLoader: CLIP context is %d-dim, but MiniMax H3 expects "
                "5120. This usually means a Qwen3-VL-4B encoder was loaded "
                "without ClipProj projection (use_clipproj OFF). Fix: set "
                "use_clipproj=ON and pick projection h3_qwen3vl_4b_tap24, or "
                "use a native Qwen3-VL-32B MiniMax encoder." % ctx_dim)

        # 5b. Keyframe guides (Add Guide for MiniMax H3) - delegated to the SAME
        # upstream node class, one keyframe = one chained AddGuide.execute() call.
        # This reuses the original node's frame-count math, resize/encode path and
        # bounds checks verbatim, so wiring is identical to chaining the native
        # node -- no re-implementation, no contradiction, no double-encoding.
        # minimax_refs (from Ref2VA) and minimax_keyframes (from AddGuide) coexist
        # in model_base extra_conds, so both can be present at once.
        keyframes = []
        _kf_report = ""
        if bypass_keyframes:
            print("[H3ModelLoader] bypass_keyframes=ON: keyframe guide injection skipped.", flush=True)
        else:
            keyframes, _kf_report = load_keyframes(keyframe_paths, frame_rate, max_side)
            for kf in keyframes:
                positive = MiniMaxH3AddGuide.execute(
                    positive=positive, latent=latent, frame_idx=kf["frame_idx"],
                    vae=video_vae, audio_vae=audio_vae,
                    image=kf["image"], audio=kf["audio"],
                )[0]

        # 5c. Audio lock source（定帧 + 锁源）—— 在 AddGuide conditioning 锚定之上，
        # 把源音频 latent 锁进主 AV latent 音频半区并 zero-denoise 锁死，保证成片
        # 音频就是自定义源音频（anchor=锚定段锁死，full=整轨自定义）。
        if not bypass_keyframes and audio_lock_mode != "off":
            latent, _lock_report = _audio_lock_latent(latent, keyframes, audio_vae, audio_lock_mode)
            if _lock_report:
                _kf_report = (_kf_report + "\n" + _lock_report).strip() if _kf_report else _lock_report

        # 6. right-side outputs: positive / Latent / MODEL / CLIP / VAE(video) / VAE(audio) / report
        report = _report
        if _ingest_report and _ingest_report.strip():
            report = (report + "\n" + _ingest_report).strip()
        if _kf_report and _kf_report.strip():
            report = (report + "\n" + _kf_report).strip()
        return io.NodeOutput(model, clip, video_vae, audio_vae, positive, latent, report)


class H3ModelOnlyLoader(io.ComfyNode):
    """精简模型加载 + T2VA — 输出 positive / latent / model / clip / vae / audio_vae。

    与 H3 R2VA AIO (micxin) 输出口完全对齐，可直接连 H3 Clip Chain (micxin)。
    不加载参考素材、不执行 Ref2VA / AddGuide，退化为纯文生视频（T2VA）：
    创建 Empty AV Latent + CLIP 编码 prompt。复用 H3ModelLoader 相同的加载链路
    （UNet GGUF/native + sage attention + ClipProj/plain CLIP + 双 VAE），
    配置一致，模型行为与 AIO 完全一致。
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3ModelOnlyLoader",
            display_name="H3 模型加载 (micxin)",
            description=(
                "精简模型加载 + T2VA：输出 positive / latent / model / clip / vae / audio_vae，"
                "与 H3 R2VA AIO (micxin) 输出口完全对齐，可直接连 H3 Clip Chain (micxin)。"
                "不加载参考素材、不执行 Ref2VA / AddGuide，退化为纯文生视频（Empty AV Latent + CLIP 编码）。"
                "加载链路与 AIO 完全一致（UNet + sage attention + ClipProj/plain CLIP + 双 VAE）。"
            ),
            category="H3 helper/micxin",
            inputs=[
                io.String.Input("prompt", multiline=True, default="",
                                tooltip="外接提示词字符串。支持 H3 六段式或自然语言描述。"),
                io.Int.Input("width", default=832, min=256, max=4096, step=32,
                             tooltip="视频宽度（像素）。"),
                io.Int.Input("height", default=480, min=256, max=4096, step=32,
                             tooltip="视频高度（像素）。"),
                io.Int.Input("length", default=124, min=5, max=10000, step=1,
                             tooltip="视频帧数（像素帧数，非 latent 帧数）。H3 规则：length % 17 == 5。"),
                io.Combo.Input("unet_name",
                    options=[""] + list(folder_paths.get_filename_list("diffusion_models"))
                            + list(folder_paths.get_filename_list("unet_gguf")),
                    default="",
                    tooltip="H3 diffusion model (UNet). Supports .safetensors and .gguf."),
                io.Combo.Input("weight_dtype", options=["default", "fp16", "bf16", "fp32"], default="default"),
                io.Combo.Input("attention_backend", options=list(ATTENTION_MAP.keys()),
                               default="comfy kitchen attention (sage)",
                               tooltip="Sage attention is a forward-hook (survives downstream LoRA), not a weight op."),
                io.Boolean.Input("use_clipproj", default=True,
                                 label_on="ClipProj（投影）", label_off="普通 CLIP（无投影）",
                                 tooltip="OFF = plain CLIP (no projection); ON = ClipProj projection mode (needs ComfyUI-ClipProj + a projection entry). H3 Qwen3-VL-4B 必须 ON。"),
                io.Combo.Input("clip_name", options=[""] + list(folder_paths.get_filename_list("text_encoders")), default="",
                               tooltip="Qwen3-VL text encoder. Required."),
                io.Combo.Input("clip_type", options=_clip_types(), default="auto",
                               tooltip="'auto' picks the CLIP type matching the loaded text encoder (correct branch for Qwen3-VL-4B)."),
                io.Combo.Input("projection", options=[""] + list(_projections()), default="",
                               tooltip="clip_projections entry, e.g. h3_qwen3vl_4b_tap24. Only used when use_clipproj is ON."),
                io.Combo.Input("clip_device", options=_gpu_devices(),
                               default="cuda:0" if torch.cuda.is_available() else "cpu"),
                io.Combo.Input("clip_load_mode", options=["resident", "streaming", "dynamic"], default="resident"),
                io.Combo.Input("video_vae_name", options=[""] + list(folder_paths.get_filename_list("vae")), default="",
                               tooltip="H3 video VAE. Required."),
                io.Combo.Input("audio_vae_name", options=[""] + list(folder_paths.get_filename_list("vae")), default="",
                               tooltip="H3 audio VAE. Leave blank for LTX / H3-without-audio pipelines."),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
                io.Clip.Output(display_name="clip"),
                io.Vae.Output(display_name="vae"),
                io.Vae.Output(display_name="audio_vae"),
                io.Conditioning.Output(display_name="positive"),
                io.Latent.Output(display_name="latent"),
            ],
        )

    @classmethod
    def execute(cls, prompt="", width=832, height=480, length=124,
                unet_name="", weight_dtype="default",
                attention_backend="comfy kitchen attention (sage)",
                use_clipproj=True, clip_name="", clip_type="auto", projection="",
                clip_device="cuda:0", clip_load_mode="resident",
                video_vae_name="", audio_vae_name="") -> io.NodeOutput:
        # normalize None prompt
        if prompt is None:
            prompt = ""
        # 1. MODEL - GGUF or native, auto-detect by extension (同 H3ModelLoader)
        if not unet_name:
            raise RuntimeError(
                "H3ModelOnlyLoader: unet_name is required - pick an H3 diffusion model.")
        if unet_name.lower().endswith(".gguf"):
            loader_cls = nodes.NODE_CLASS_MAPPINGS.get("UnetLoaderGGUF")
            if loader_cls is None:
                raise RuntimeError(
                    "H3ModelOnlyLoader: ComfyUI-GGUF not installed - cannot load .gguf model.")
            model = loader_cls().load_unet(unet_name)[0]
        else:
            unet_path = folder_paths.get_full_path_or_raise("diffusion_models", unet_name)
            model_options = {}
            if weight_dtype != "default":
                model_options["weight_dtype"] = getattr(torch, weight_dtype)
            model = comfy.sd.load_diffusion_model(unet_path, model_options=model_options)

        # 2. Sage attention (forward-hook)
        attn_name = ATTENTION_MAP.get(attention_backend)
        if attn_name is not None:
            attn_fn = comfy.ldm.modules.attention.get_attention_function(attn_name, None)
            if attn_fn is None:
                attn_fn = comfy.ldm.modules.attention.get_attention_function("pytorch")
            if attn_fn is not None:
                model = model.clone()
                model.set_model_optimized_attention(attn_fn)

        # 3. CLIP - ClipProj / plain (同 H3ModelLoader)
        if not clip_name:
            raise RuntimeError("H3ModelOnlyLoader: clip_name is required - pick a Qwen3-VL encoder.")
        if use_clipproj:
            loader = nodes.NODE_CLASS_MAPPINGS.get("ClipProjLoader")
            if loader is None:
                raise RuntimeError(
                    "use_clipproj is ON but ClipProjLoader not found. Install "
                    "ComfyUI-ClipProj, or turn use_clipproj OFF for plain CLIP.")
            if not projection:
                raise RuntimeError(
                    "use_clipproj is ON but projection is empty - pick a "
                    "clip_projections entry (e.g. h3_qwen3vl_4b_tap24).")
            clip = loader().load(clip_name, clip_type, projection, clip_device, clip_load_mode)[0]
        else:
            path = folder_paths.get_full_path_or_raise("text_encoders", clip_name)
            embeddings = folder_paths.get_folder_paths("embeddings")
            ctype = getattr(comfy.sd.CLIPType, clip_type.upper(), comfy.sd.CLIPType.KREA2)
            dev = torch.device(clip_device)
            offload = dev if clip_load_mode == "resident" else mm.text_encoder_offload_device()
            clip = comfy.sd.load_clip(
                ckpt_paths=[path], embedding_directory=embeddings, clip_type=ctype,
                model_options={"load_device": dev, "offload_device": offload},
                disable_dynamic=clip_load_mode in ("resident", "streaming"))

        # 4. Dual VAE - video_vae required, audio_vae optional
        if not video_vae_name:
            raise RuntimeError("H3ModelOnlyLoader: video_vae_name is required - pick the H3 video VAE.")
        vae_loader = nodes.NODE_CLASS_MAPPINGS["VAELoader"]()
        video_vae = vae_loader.load_vae(video_vae_name)[0]
        audio_vae = vae_loader.load_vae(audio_vae_name)[0] if audio_vae_name else None

        # 5. T2VA — 调用 Ref2VA 节点但传入空参考素材，退化为纯文生视频
        #    （Empty AV Latent + CLIP 编码 prompt），与 AIO 节点行为完全一致。
        ref_out = MiniMaxH3ReferenceToVideo.execute(
            clip=clip, vae=video_vae, audio_vae=audio_vae, prompt=prompt,
            width=width, height=height, length=length,
            ref_image_size="match",
            ref_images=[],
            ref_videos=[],
            ref_video_audios=[],
            ref_audios=[],
        )
        positive = ref_out[0]
        latent = ref_out[1]

        # 5a. 5120 context 校验（与 AIO 节点一致）
        ctx_dim = positive[0][0].shape[-1]
        if ctx_dim != 5120:
            raise RuntimeError(
                "H3ModelOnlyLoader: CLIP context is %d-dim, but MiniMax H3 expects "
                "5120. This usually means a Qwen3-VL-4B encoder was loaded "
                "without ClipProj projection (use_clipproj OFF). Fix: set "
                "use_clipproj=ON and pick projection h3_qwen3vl_4b_tap24, or "
                "use a native Qwen3-VL-32B MiniMax encoder." % ctx_dim)

        # 6. 输出口与 ClipChain 输入口对齐：positive / latent / model / clip / vae / audio_vae
        return io.NodeOutput(model, clip, video_vae, audio_vae, positive, latent)
