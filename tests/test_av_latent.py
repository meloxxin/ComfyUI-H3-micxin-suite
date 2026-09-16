# -*- coding: utf-8 -*-
"""H3SeparateAVLatent / H3CombineAVLatent 测试：AV latent 拆分合并往返。"""
import torch
import pytest

import comfy.nested_tensor
from conftest import requires_comfy
from h3_av_latent import H3SeparateAVLatent, H3CombineAVLatent


def _nested(video, audio):
    return comfy.nested_tensor.NestedTensor((video, audio))


@requires_comfy
def test_separate_shapes():
    video = torch.zeros(1, 24, 124, 48, 84)
    audio = torch.zeros(1, 32, 2, 44100)
    av = {"samples": _nested(video, audio)}
    out = H3SeparateAVLatent.execute(av)
    v_lat, a_lat = out[0], out[1]
    assert tuple(v_lat["samples"].shape) == (1, 24, 124, 48, 84)
    assert tuple(a_lat["samples"].shape) == (1, 32, 2, 44100)


@requires_comfy
def test_separate_rejects_plain_latent():
    av = {"samples": torch.zeros(1, 24, 124, 48, 84)}  # 非 NestedTensor
    with pytest.raises(TypeError):
        H3SeparateAVLatent.execute(av)


@requires_comfy
def test_combine_roundtrip():
    video = torch.rand(1, 24, 124, 48, 84)
    audio = torch.rand(1, 32, 2, 44100)
    v_lat = {"samples": video}
    a_lat = {"samples": audio}
    out = H3CombineAVLatent.execute(v_lat, a_lat)
    av = out[0]
    assert isinstance(av["samples"], comfy.nested_tensor.NestedTensor)
    v2, a2 = av["samples"].unbind()
    assert torch.equal(v2, video)
    assert torch.equal(a2, audio)


@requires_comfy
def test_combine_fit_audio_length():
    # 视频 latent 本身是联合 AV latent 时，音频短则补齐到被替换音频的长度（尾部 unmask）
    video = torch.rand(1, 24, 124, 48, 84)
    audio = torch.rand(1, 32, 2, 44100)
    joint = comfy.nested_tensor.NestedTensor((video, audio))
    short_audio = torch.rand(1, 32, 2, 1000)
    out = H3CombineAVLatent.execute({"samples": joint}, {"samples": short_audio})
    v2, a2 = out[0]["samples"].unbind()
    assert a2.shape[-1] == 44100


@requires_comfy
def test_combine_keeps_noise_mask():
    video = torch.rand(1, 24, 124, 48, 84)
    audio = torch.rand(1, 32, 2, 44100)
    vm = torch.zeros_like(video)
    am = torch.ones_like(audio)
    v_lat = {"samples": video, "noise_mask": vm}
    a_lat = {"samples": audio, "noise_mask": am}
    out = H3CombineAVLatent.execute(v_lat, a_lat)
    assert "noise_mask" in out[0]
    nv, na = out[0]["noise_mask"].unbind()
    assert torch.equal(nv, vm)
    assert torch.equal(na, am)
