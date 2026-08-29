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
import torch
import comfy.sd
import comfy.utils
import comfy.model_management as mm
import folder_paths
import nodes
from comfy_api.latest import io
from comfy_extras.nodes_minimax_h3 import MiniMaxH3ReferenceToVideo, MiniMaxH3AddGuide
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
                               tooltip="'auto' falls through to KREA2 (correct branch for Qwen3-VL-4B)."),
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
                # ---- 外部接入 socket（micxin 流程化工作流用）----
                # 连线后优先于内部 tab：让 H3 Asset Library / H3 Shot Queue 直接喂参考图和
                # 任意帧接续行，不必手动在 tab 里一张张拖。断开则退回内部 tab 管理。
                io.String.Input(
                    "ref_image_paths",
                    optional=True,
                    force_input=True,
                    tooltip=("外部参考图路径（每行 path|start|end，或纯路径）。连线 H3 Asset "
                             "Library 的 image_paths 输出后，自动带上角色库全部参考图；"
                             "留空则用内部 tab 上传的图。"),
                ),
                io.String.Input(
                    "ext_keyframe_paths",
                    optional=True,
                    force_input=True,
                    tooltip=("外部任意帧/关键帧行（每行 media|audio|frame_idx|media_start|"
                             "media_end|audio_start|audio_end）。连线 H3 Shot Queue 的尾帧任意帧行 "
                             "输出后实现画面接续；留空则用内部 keyframe tab。"),
                ),
                io.Int.Input("frame_rate", default=DEFAULT_FRAME_RATE, min=1, max=120,
                    tooltip="Video extraction frame rate (fps)"),
                io.Int.Input("max_side", default=DEFAULT_MAX_SIDE, min=0, max=4096, step=8,
                    tooltip="Max side length for image/video frames, 0=original size (kept even)"),
                io.Int.Input("update", default=0, min=0, max=0xffffffffffffffff,
                    extra_dict={"hidden": True},
                    tooltip="Hidden: auto-incremented by the UI on any media change to force re-execution"),
            ],
            outputs=[
                io.Conditioning.Output(display_name="positive"),
                io.Latent.Output(display_name="Latent"),
                io.Model.Output(display_name="MODEL"),
                io.Clip.Output(display_name="CLIP"),
                io.Vae.Output(display_name="VAE (video)"),
                io.Vae.Output(display_name="VAE (audio)"),
            ],
        )

    @classmethod
    def execute(cls, prompt=None, width=1344, height=768, length=124, ref_image_size="match",
                unet_name="", weight_dtype="default", attention_backend="comfy kitchen attention (sage)",
                use_clipproj=False, clip_name="", clip_type="auto", projection="",
                clip_device="cuda:0", clip_load_mode="resident",
                video_vae_name="", audio_vae_name="",
                image_paths="", video_paths="", audio_paths="", keyframe_paths="",
                ref_image_paths=None, ext_keyframe_paths=None,
                frame_rate=DEFAULT_FRAME_RATE, max_side=DEFAULT_MAX_SIDE,
                update=0) -> io.NodeOutput:
        # prompt is an input socket (from H3 Screenwriter); normalize None to empty string
        if prompt is None:
            prompt = ""
        # 外部 socket 优先：H3 Asset Library 全库参考图 / H3 Shot Queue 任意帧接续行
        if ref_image_paths:
            image_paths = ref_image_paths
        if ext_keyframe_paths:
            keyframe_paths = ext_keyframe_paths
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
            # CLIPType has no AUTO member, so "auto" must fall through to KREA2,
            # which is the correct branch for Qwen3-VL-4B.
            ctype = getattr(comfy.sd.CLIPType, clip_type.upper(), comfy.sd.CLIPType.KREA2)
            dev = torch.device(clip_device)
            offload = dev if clip_load_mode == "resident" else mm.text_encoder_offload_device()
            clip = comfy.sd.load_clip(
                ckpt_paths=[path], embedding_directory=embeddings, clip_type=ctype,
                model_options={"load_device": dev, "offload_device": offload},
                disable_dynamic=clip_load_mode in ("resident", "streaming"))

        # 4. Dual VAE - video_vae required, audio_vae optional (None if blank)
        if not video_vae_name:
            raise RuntimeError("H3ModelLoader: video_vae_name is required - pick the H3 video VAE.")
        # VAELoader.load_vae is an *instance* method -> must instantiate the class first.
        vae_loader = nodes.NODE_CLASS_MAPPINGS["VAELoader"]()
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

        # 5a. Guard: MiniMax H3's condition_proj expects a 5120-dim context.
        # With use_clipproj OFF + a Qwen3-VL-4B encoder loaded as KREA2, the
        # Krea2 TE flattens the 12-layer axis into the feature dim (12*2560=
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
        keyframes, _kf_report = load_keyframes(keyframe_paths, frame_rate, max_side)
        for kf in keyframes:
            positive = MiniMaxH3AddGuide.execute(
                positive=positive, latent=latent, frame_idx=kf["frame_idx"],
                vae=video_vae, audio_vae=audio_vae,
                image=kf["image"], audio=kf["audio"],
            )[0]

        # 6. right-side outputs: positive / Latent / MODEL / CLIP / VAE(video) / VAE(audio)
        return io.NodeOutput(positive, latent, model, clip, video_vae, audio_vae)
