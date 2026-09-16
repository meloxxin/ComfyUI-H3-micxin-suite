# -*- coding: utf-8 -*-
"""h3_media_utils 测试：行解析 / 大小检查 / 全素材加载（含视频用途分流）。"""
import os
import tempfile

import numpy as np
import pytest

import av
from PIL import Image

import h3_media_utils as M
from conftest import requires_comfy


# ---------------- 行解析 ----------------

def test_parse_lines_3field():
    items = M._parse_lines("a.png\nb.png|1|2\n\nc.png|3")
    assert items == [("a.png", 0.0, 0.0), ("b.png", 1.0, 2.0), ("c.png", 3.0, 0.0)]


def test_parse_video_lines():
    items = M._parse_video_lines("a.mp4|0|5\nb.mp4\nc.mp4|1|3")
    assert items[0] == ("a.mp4", 0.0, 5.0)
    assert items[1] == ("b.mp4", 0.0, 0.0)
    assert items[2] == ("c.mp4", 1.0, 3.0)
    assert len(items) == 3

def test_parse_keyframe_lines():
    items = M._parse_keyframe_lines("img.png|aud.wav|60|0|0|0|0\npic2.png||-1||||")
    assert items[0]["frame_idx"] == 60
    assert items[0]["media_path"] == "img.png"
    assert items[1]["frame_idx"] == -1


def test_check_file_size():
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "x.bin")
    with open(p, "wb") as f:
        f.write(b"\x00" * 1024)
    ok, mb, warn = M._check_file_size(p, 0.001)  # ~1MB 上限 → 不超限
    assert ok and mb < 1 and warn is None
    ok, mb, warn = M._check_file_size(p, 0.0005)  # 512B 上限 → 1024B 超限
    assert not ok and warn is not None


# ---------------- 全素材加载 ----------------

def _make_media(tmp):
    # 图片
    img = os.path.join(tmp, "ref.png")
    Image.new("RGB", (64, 48), (200, 30, 30)).save(img)
    # 视频（带音频）
    vid = os.path.join(tmp, "clip.mp4")
    c = av.open(vid, "w")
    vs = c.add_stream("libx264", rate=24)
    vs.width = 64
    vs.height = 48
    vs.pix_fmt = "yuv420p"
    as_ = c.add_stream("aac", rate=44100)
    as_.layout = "stereo"
    as_.format = "fltp"
    for i in range(8):
        f = av.VideoFrame.from_ndarray(np.full((48, 64, 3), 60, np.uint8), format="rgb24")
        f.pts = i
        for p in vs.encode(f):
            c.mux(p)
    n = 22050
    t = np.arange(n) / 44100.0
    wav = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    af = av.AudioFrame.from_ndarray(np.stack([wav, wav]), format="fltp", layout="stereo")
    af.sample_rate = 44100
    af.pts = 0
    for p in as_.encode(af):
        c.mux(p)
    for p in as_.encode(None):
        c.mux(p)
    for p in vs.encode(None):
        c.mux(p)
    c.close()
    return img, vid


@requires_comfy
def test_load_all_media_video():
    tmp = tempfile.mkdtemp()
    img, vid = _make_media(tmp)

    # 视频进 R2V 参考（运动/外观来源），音频同步
    images, videos, vids_audio, audios, multi, report = M.load_all_media(
        f"{img}|0|0", f"{vid}|0|0", "", 24, 1024)
    assert "ref_image_0" in images
    assert "ref_video_0" in videos
    assert "ref_video_audio_0" in vids_audio
    assert "[视频 0]" in report


@requires_comfy
def test_load_all_media_pure_audio_warn():
    tmp = tempfile.mkdtemp()
    img, vid = _make_media(tmp)
    # 纯音频 → 规则警告
    _, _, _, _, _, report = M.load_all_media("", "", f"{vid}", 24, 1024)
    assert "不允许纯音频" in report
