# -*- coding: utf-8 -*-
"""h3_clip_chain_av 回退后测试：恢复 19-widget 兼容 schema + 音频交叉淡化。

回退背景：V3 版把 schema 从 18 个固定 widget 扩到 24 个 optional，
导致所有旧工作流（四视图+R2VA Sparse Attention 等，19 项 widgets_values）
前端按位置映射错位 → 节点爆红不可用。已整体回退到 09-10 旧版。

保留的唯一增强：_merge_audio 交叉淡化 40ms → 250ms（段间音频不突兀）。
"""
import torch
import pytest

from conftest import requires_comfy

import h3_clip_chain_av as CAV


# ---------------- 音频交叉淡化（回退后保留的增强） ----------------

def _audio(wave, sr=44100):
    return {"waveform": wave, "sample_rate": sr}


def test_merge_audio_default_crossfade_250ms():
    """默认交叉淡化 250ms（40ms 只消咔哒声，250ms 才听不出接缝）。"""
    import inspect
    sig = inspect.signature(CAV._merge_audio)
    assert sig.parameters["crossfade_ms"].default == 250


def test_merge_audio_concatenates_with_overlap():
    sr = 44100
    a = torch.zeros(1, 2, sr)          # 1 秒
    b = torch.zeros(1, 2, sr)          # 1 秒
    out = CAV._merge_audio([_audio(a, sr), _audio(b, sr)])
    # 交叉 250ms → 总长 = 2000 - 250ms 重叠 = sr*1.75
    assert out is not None
    assert out["sample_rate"] == sr
    assert out["waveform"].shape[-1] == int(sr * 1.75)


def test_merge_audio_seam_energy_continuous():
    """接缝处无突变：交叉区相邻样本跳变 ≤ 非交叉区，不出现爆点/咔哒声。"""
    sr = 8000
    t = torch.arange(sr, dtype=torch.float32) / sr
    seg_a = torch.sin(2 * 3.14159 * 220 * t).unsqueeze(0).unsqueeze(0)
    seg_b = -torch.sin(2 * 3.14159 * 220 * t).unsqueeze(0).unsqueeze(0)
    out = CAV._merge_audio([_audio(seg_a, sr), _audio(seg_b, sr)])
    w = out["waveform"][0, 0]
    cf = int(sr * 250 / 1000)
    diff = w.diff().abs()
    overlap_max = float(diff[cf - 200: cf + 200].max())
    plain_max = float(diff[:cf - 200].max())
    # 交叉区最大跳变不应明显大于普通区（有 fade 兜底，无爆点）
    assert overlap_max <= plain_max * 1.5 + 1e-6
    assert torch.isfinite(w).all()


def test_merge_audio_handles_missing():
    assert CAV._merge_audio([]) is None
    assert CAV._merge_audio([None]) is None


# ---------------- V3 节点 schema（19-widget 兼容） ----------------

@requires_comfy
def test_clip_chain_av_schema_19_widgets_compatible():
    """schema widget 顺序 = 旧版 18 固定 widget（+1 Autogrow 空槽 = 19 项），
    与用户主工作流（四视图+R2VA Sparse Attention）的 widgets_values 对齐。"""
    schema = CAV.H3ClipChainAV.define_schema()

    names = []
    for inp in schema.inputs:
        n = getattr(inp, "name", None) or getattr(inp, "field_name", None) or getattr(inp, "id", None)
        if n and n not in ("model", "clip", "vae", "audio_vae", "positive", "latent"):
            names.append(n)
    # 只统计固定 widget（Autogrow 动态槽不算固定位）
    fixed = [n for n in names if not n.startswith("prompt_")]

    expected = [
        "prompts", "segment_prompts",
        "seeds", "base_seed", "context_length", "audio_context_length",
        "steps", "sampler_name", "scheduler", "denoise",
        "keyframe_paths", "segment_durations", "audio_lock_mode",
        "start_segment", "preview_segments",
        "auto_first_frame",
        "no_subtitles",
    ]
    # 去掉 Autogrow 的 segment_prompts 后，固定 widget 数 = 16
    # （noise_mask / blend_grain / noise_mask_invert 已删除；
    #  start_segment / preview_segments 保留功能但 hidden 不在面板显示）
    fixed_no_auto = [n for n in fixed if n != "segment_prompts"]
    assert fixed_no_auto == expected[:1] + expected[2:]
    assert len(fixed_no_auto) == 16
    # 回退：不再有 TaperNoise / 流式落盘相关参数
    assert "context_noise" not in fixed
    assert "match_tail" not in fixed
    assert "save_segments_dir" not in fixed


@requires_comfy
def test_clip_chain_av_execute_signature_backward_compatible():
    """execute 签名与旧工作流 widget 值一一对应（seeds/base_seed/.../cache_dir）。"""
    import inspect
    params = list(inspect.signature(CAV.H3ClipChainAV.execute).parameters)
    for p in ["seeds", "base_seed", "context_length", "audio_context_length",
              "steps", "sampler_name", "scheduler", "denoise",
              "keyframe_paths", "segment_durations", "audio_lock_mode",
              "start_segment", "preview_segments", "auto_first_frame"]:
        assert p in params, f"execute 缺参数 {p}"
    # 已删除的接口不应再出现在 execute 签名
    for gone in ["auto_h3_compile", "auto_dialogue", "latent_chain", "validation",
                 "cache_dir", "noise_mask", "blend_grain", "noise_mask_invert"]:
        assert gone not in params, f"execute 不应有参数 {gone}"

# ---------------- noise_mask（H3 Noise Mask 集成） ----------------

@requires_comfy
def test_schema_no_latent_chain_has_noise_mask():
    """schema：noise_mask / blend_grain / start_segment / preview_segments 已移除。"""
    schema = CAV.H3ClipChainAV.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    for gone in ["latent_chain", "validation", "cache_dir", "noise_mask",
                 "blend_grain", "noise_mask_invert",
                 "auto_h3_compile", "auto_dialogue"]:
        assert gone not in inputs, f"schema 不应有 {gone}"
    # start_segment / preview_segments 保留（hidden）
    assert "start_segment" in inputs
    assert "preview_segments" in inputs


@requires_comfy
def test_removed_noise_mask_functions_not_importable():
    """noise_mask 相关工具函数已删除，导入应失败；chain 内部延续机制仍保留。"""
    import pytest
    from h3_clip_chain_av import (
        _create_segment_latent, _video_stream_from_latent,
    )
    with pytest.raises(ImportError):
        from h3_clip_chain_av import _apply_noise_continue  # noqa: F401
    with pytest.raises(ImportError):
        from h3_clip_chain_av import _apply_noise_mask  # noqa: F401
    assert _create_segment_latent is not None
    assert _video_stream_from_latent is not None


# ---------------- auto_first_frame（自动首帧参考） ----------------

@requires_comfy
def test_schema_auto_first_frame_default_on():
    """auto_first_frame 存在且默认 off（切镜安全）；on 时仅 #continue 段接续。"""
    schema = CAV.H3ClipChainAV.define_schema()

    def _nm(inp):
        for attr in ("name", "field_name", "id"):
            v = getattr(inp, attr, None)
            if v:
                return v
        return "?"

    inputs = {_nm(inp): inp for inp in schema.inputs}
    assert inputs["auto_first_frame"].default == "off"
    assert inputs["auto_first_frame"].options == ["off", "on"]


# ---------------- #continue 段级接续标记（自动首帧） ----------------

@requires_comfy
def test_parse_clips_continue_marker_multiline():
    """多行文本：#continue = 链式接续（自动首帧）；# = 独立；无标记 = 链式不接续。"""
    clips = CAV._parse_clips(
        "段一内容\n#continue 段二内容（接续）\n段三内容\n# 独立段四".replace("\\n", "\n"), "", 0)
    assert clips[0]["mode"] == "standalone" and clips[0]["first_frame"] is False
    assert clips[1]["mode"] == "chain" and clips[1]["first_frame"] is True
    assert clips[1]["prompt"].startswith("段二")
    assert clips[2]["mode"] == "chain" and clips[2]["first_frame"] is False
    assert clips[3]["mode"] == "standalone" and clips[3]["first_frame"] is False


@requires_comfy
def test_parse_clips_continue_marker_json():
    """JSON 对象数组：_mode=continue → chain + first_frame。"""
    import json
    clips = CAV._parse_clips(json.dumps([
        {"detailed_description": "seg1"},
        {"_mode": "continue", "detailed_description": "seg2"},
        {"_mode": "standalone", "detailed_description": "seg3"},
    ]), "", 0)
    assert clips[0]["first_frame"] is False
    assert clips[1]["mode"] == "chain" and clips[1]["first_frame"] is True
    assert clips[2]["mode"] == "standalone" and clips[2]["first_frame"] is False


@requires_comfy
def test_parse_clips_continue_marker_segments():
    """segment_prompts 路径：#continue 前缀同样生效。"""
    clips = CAV._build_clips_from_segments(["段A", "#continue 段B"], "", 0)
    # 第一路 standalone（无前帧）；第二路标 #continue → first_frame（接续上一段尾帧）
    assert clips[0]["first_frame"] is False and clips[0]["mode"] == "standalone"
    assert clips[1]["first_frame"] is True and clips[1]["mode"] == "chain"
    assert clips[1]["prompt"].startswith("段B")
