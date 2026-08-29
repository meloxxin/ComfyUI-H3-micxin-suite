"""H3 Separate / Combine AV Latent (micxin).

Split a MiniMax H3 joint AV latent into independent video and audio latents
(and recombine them) so each modality can be routed through ComfyUI's
`SamplerCustomAdvanced` (采样器-自定义-高级) independently.

H3 AV latent contract (see ComfyUI/comfy_extras/nodes_minimax_h3.py):

    {"samples": NestedTensor((video, audio))}
        video : [B, 24, T,  H/16, W/16]   (5-D, 24 in-channels)
        audio : [B, 32, 2,  T_audio]      (3-D, 32 in-channels)

This is the *same* latent shape used by LTXV, which is why the shipped
`LTXVSeparateAVLatent` / `LTXVConcatAVLatent` happen to work for H3 too.
This micxin pair reproduces that proven behaviour (including `noise_mask`
handling and audio length-fitting) with H3-specific shape validation, and
lives under the `H3 helper/micxin` category so it does not depend on the
LTXV node pack being present.

Wiring in the dual-sampling (二采) workflow:
    Ref2VA  -> SamplerCustomAdvanced (whole AV) -> H3 Separate AV Latent
             -> video_latent -> (LatentUpscaleBy) -> SamplerCustomAdvanced
             -> video + audio_latent -> H3 Combine AV Latent
             -> SamplerCustomAdvanced (final) -> VAEDecode / VAEDecodeAudio

This file is the V3 io.ComfyNode form (restored after a temporary V2 port).
"""

import torch
import comfy.nested_tensor
import comfy.utils
from comfy_api.latest import io


def _require_h3_av(samples, where):
    """Validate that `samples` is an H3 joint AV latent (video + audio).

    Returns the unpacked (video, audio) tensors. Raises a clear, actionable
    error otherwise so the user knows they must feed a genuine H3 AV latent
    (Ref2VA / H3 R2VA AIO(micxin) / Empty AV Latent), not a plain 4-D latent.

    Only the discriminative checks are enforced: a NestedTensor with exactly
    two streams, the first of which is the 5-D video latent ``[B,24,T,H,W]``.
    The audio stream is intentionally NOT pinned to a fixed ndim/channel count:
    H3 emits ``[2,32,T]`` for the empty placeholder but ``[B,32,2,T]`` once the
    audio VAE has encoded it, so either must be accepted.
    """
    if not isinstance(samples, comfy.nested_tensor.NestedTensor):
        raise TypeError(
            f"{where}: expected an H3 joint AV latent (NestedTensor holding a video "
            f"and an audio stream), got {type(samples).__name__}. Feed the LATENT "
            f"output of Ref2VA / H3 R2VA AIO(micxin) / Empty AV Latent."
        )
    streams = samples.unbind()
    if len(streams) != 2:
        raise ValueError(
            f"{where}: an H3 AV latent must hold exactly 2 streams (video, audio); "
            f"found {len(streams)}."
        )
    video, audio = streams
    if video.ndim != 5:
        raise ValueError(
            f"{where}: expected the first AV stream to be the 5-D video latent "
            f"[B,24,T,H,W]; got shape {tuple(video.shape)}. Make sure you are feeding "
            f"an H3 joint AV latent, not a plain 4-D latent."
        )
    return video, audio


class H3SeparateAVLatent(io.ComfyNode):
    """Split an H3 joint AV latent into separate video and audio latents.

    Both outputs are standard ComfyUI LATENTs (``{"samples": tensor}``) and can
    each be wired into ``SamplerCustomAdvanced.latent_image``. A ``noise_mask``,
    if present on the joint latent, is split alongside the streams.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3SeparateAVLatent",
            display_name="H3 Separate AV Latent (micxin)",
            category="H3 helper/micxin",
            inputs=[io.Latent.Input("av_latent")],
            outputs=[
                io.Latent.Output(display_name="video_latent"),
                io.Latent.Output(display_name="audio_latent"),
            ],
        )

    @classmethod
    def execute(cls, av_latent) -> io.NodeOutput:
        samples = av_latent["samples"]
        video, audio = _require_h3_av(samples, "H3SeparateAVLatent")

        video_latent = dict(av_latent)  # shallow copy: keeps task/target metadata
        video_latent["samples"] = video
        audio_latent = dict(av_latent)
        audio_latent["samples"] = audio

        if "noise_mask" in av_latent and av_latent["noise_mask"] is not None:
            masks = av_latent["noise_mask"].unbind()
            video_latent["noise_mask"] = masks[0]
            audio_latent["noise_mask"] = masks[1]

        return io.NodeOutput(video_latent, audio_latent)


class H3CombineAVLatent(io.ComfyNode):
    """Recombine separate H3 video and audio latents into a joint AV latent."""

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3CombineAVLatent",
            display_name="H3 Combine AV Latent (micxin)",
            category="H3 helper/micxin",
            inputs=[
                io.Latent.Input("video_latent"),
                io.Latent.Input("audio_latent"),
            ],
            outputs=[io.Latent.Output(display_name="av_latent")],
        )

    @staticmethod
    def fit_audio(reference, audio, noise_mask):
        """Trim or zero-pad the audio stream to the length of the one it replaces.

        Ported verbatim from LTXVConcatAVLatent: the padded tail is left
        unmasked so the model generates it (what a clip shorter than the video
        should do).
        """
        dims = [i for i in range(reference.ndim) if reference.shape[i] != audio.shape[i]]
        if len(dims) == 0:
            return audio, noise_mask
        if len(dims) > 1 or dims[0] < 2:
            raise ValueError(
                "audio latent {} cannot be fitted to {}".format(
                    tuple(audio.shape), tuple(reference.shape)
                )
            )
        dim, length = dims[0], reference.shape[dims[0]]
        if noise_mask is not None:
            noise_mask = comfy.utils.reshape_mask(noise_mask, audio.shape)
        if audio.shape[dim] > length:
            audio = audio.narrow(dim, 0, length)
            if noise_mask is not None:
                noise_mask = noise_mask.narrow(dim, 0, length)
        else:
            pad = torch.zeros_like(audio.narrow(dim, 0, 1)).repeat(
                [length - audio.shape[dim] if i == dim else 1 for i in range(audio.ndim)]
            )
            audio = torch.cat([audio, pad], dim=dim)
            if noise_mask is not None:
                noise_mask = torch.cat([noise_mask, torch.ones_like(pad)], dim=dim)
        return audio, noise_mask

    @classmethod
    def execute(cls, video_latent, audio_latent) -> io.NodeOutput:
        output = {}
        output.update(video_latent)
        output.update(audio_latent)
        video_samples = video_latent["samples"]
        audio_samples = audio_latent["samples"]
        video_noise_mask = video_latent.get("noise_mask", None)
        audio_noise_mask = audio_latent.get("noise_mask", None)

        # If video_latent is itself already a joint AV latent (NestedTensor),
        # keep its video stream and swap in the supplied audio stream.
        if video_samples.is_nested:
            streams = video_samples.unbind()
            video_samples = streams[0]
            if video_noise_mask is not None:
                video_noise_mask = video_noise_mask.unbind()[0]
            audio_samples, audio_noise_mask = cls.fit_audio(
                streams[1], audio_samples, audio_noise_mask
            )

        if video_noise_mask is not None or audio_noise_mask is not None:
            if video_noise_mask is None:
                video_noise_mask = torch.ones_like(video_samples)
            if audio_noise_mask is None:
                audio_noise_mask = torch.ones_like(audio_samples)
            output["noise_mask"] = comfy.nested_tensor.NestedTensor(
                (video_noise_mask, audio_noise_mask)
            )

        output["samples"] = comfy.nested_tensor.NestedTensor(
            (video_samples, audio_samples)
        )
        return io.NodeOutput(output)
