# -*- coding: utf-8 -*-
"""
h3_media_utils.py — H3 R2VA AIO(micxin) 内嵌素材加载工具。

从 ComfyUI-H3-Prompt-Writing-micxin2025/h3_media_loader_node.py 提取的核心
加载函数，供 H3ModelLoader 内嵌素材上传使用。

支持：图片(≤9) / 视频(≤3, 含内嵌音轨) / 音频(≤3)，每行格式 `路径|起始秒|结束秒`。
"""

import os
import numpy as np
import torch
import torch.nn.functional as F
import folder_paths
import av
from PIL import Image

VID_EXTS = (".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".flv", ".wmv")
AUD_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".aiff", ".opus")
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")

MAX_CLIP_SEC = 15.0
MIN_CLIP_SEC = 2.0
MAX_ITEMS = 3
MAX_IMAGES = 9
MAX_TOTAL_FILES = 12
MAX_KEYFRAMES = 8
DEFAULT_FRAME_RATE = 24
DEFAULT_MAX_SIDE = 1024

SIZE_IMG_MB = 30
SIZE_VID_MB = 50
SIZE_AUD_MB = 15
SIZE_TOTAL_MB = 64

# 关键帧（Add Guide for MiniMax H3）条目上限 —— "理论上任意多个"，给一个
# 足够大的硬上限防误填；前端/后端共用，改动后保持两端一致。
MAX_KEYFRAMES = 32
# H3 帧率（fps），与 _shots_to_frame_list 的换算一致
H3_FPS = 24


def _check_file_size(path, limit_mb, label="文件"):
    try:
        if path and os.path.isfile(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            if size_mb > limit_mb:
                return False, size_mb, (
                    f"[{label}] {os.path.basename(path)}: "
                    f"{size_mb:.1f}MB > {limit_mb}MB 上限"
                )
            return True, size_mb, None
    except OSError:
        pass
    return True, 0.0, None


def _resolve_path(path):
    if not path:
        return path
    if os.path.isabs(path) and os.path.isfile(path):
        return path
    c = os.path.join(folder_paths.get_input_directory(), path)
    return c if os.path.isfile(c) else path


def _parse_lines(text, max_items=MAX_ITEMS):
    items = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        path = parts[0].strip()
        if not path:
            continue
        start = end = 0.0
        if len(parts) > 1:
            try:
                start = float(parts[1])
            except ValueError:
                start = 0.0
        if len(parts) > 2:
            try:
                end = float(parts[2])
            except ValueError:
                end = 0.0
        items.append((path, start, end))
        if len(items) >= max_items:
            break
    return items


def _colorspace_setup(orig_w, orig_h, video_stream):
    try:
        from av.video.reformatter import Colorspace, ColorRange
        fallback_cs = Colorspace.ITU709 if max(orig_w, orig_h) >= 720 else Colorspace.ITU601
        fallback_cr = ColorRange.MPEG
        dst_range = ColorRange.JPEG
    except ImportError:
        fallback_cs = "itu709" if max(orig_w, orig_h) >= 720 else "itu601"
        fallback_cr = "mpeg"
        dst_range = "jpeg"

    src_colorspace = fallback_cs
    src_color_range = fallback_cr
    if video_stream and video_stream.codec_context:
        cc = video_stream.codec_context
        c_space = getattr(cc, "colorspace", getattr(cc, "color_space", None))
        if c_space and hasattr(c_space, "name") and c_space.name != "UNSPECIFIED":
            src_colorspace = c_space
        elif c_space and isinstance(c_space, str) and "unspecified" not in c_space.lower():
            src_colorspace = c_space
        c_range = getattr(cc, "color_range", None)
        if c_range and hasattr(c_range, "name") and c_range.name != "UNSPECIFIED":
            src_color_range = c_range
        elif c_range and isinstance(c_range, str) and "unspecified" not in c_range.lower():
            src_color_range = c_range
    return src_colorspace, src_color_range, dst_range


def _resize_frame_np(rgb_np, max_side):
    h, w = rgb_np.shape[:2]
    if max_side and max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        nh, nw = max(2, int(round(h * scale))), max(2, int(round(w * scale)))
    else:
        nh, nw = h, w
    nh -= nh % 2
    nw -= nw % 2
    if nh != h or nw != w:
        t = torch.from_numpy(rgb_np).permute(2, 0, 1).unsqueeze(0).float()
        t = F.interpolate(t, size=(nh, nw), mode="bicubic", align_corners=False)
        out = t.squeeze(0).permute(1, 2, 0).numpy().clip(0, 255).astype(np.uint8)
        return out
    return rgb_np


def _extract_video(video_path, start, end, frame_rate, max_side):
    container = av.open(video_path)
    vstream = container.streams.video[0] if len(container.streams.video) > 0 else None
    if not vstream:
        container.close()
        raise ValueError("视频无视频流")
    astream = container.streams.audio[0] if len(container.streams.audio) > 0 else None

    vdur = 0.0
    if vstream.duration and vstream.time_base:
        vdur = float(vstream.duration * vstream.time_base)
    orig_w = vstream.codec_context.width or 512
    orig_h = vstream.codec_context.height or 512

    src_cs, src_cr, dst_range = _colorspace_setup(orig_w, orig_h, vstream)

    actual_start = max(0.0, start)
    if end <= start:
        actual_end = vdur if vdur > 0 else float("inf")
    else:
        actual_end = end
    if actual_end - actual_start > MAX_CLIP_SEC:
        actual_end = actual_start + MAX_CLIP_SEC
    if actual_end <= 0:
        actual_end = float("inf")

    frame_interval = 1.0 / float(frame_rate) if frame_rate > 0 else 1.0 / 24.0
    expected_target_time = actual_start

    frames = []
    audio_data = []
    sample_rate = 44100
    first_audio_time = None
    resampler = av.AudioResampler(format="fltp") if astream is not None else None

    streams = (vstream, astream) if astream is not None else (vstream,)
    for packet in container.demux(*streams):
        if packet.stream is vstream:
            for frame in vstream.decode(packet):
                ft = frame.time
                if ft is None:
                    ft = float(frame.pts * float(vstream.time_base)) if frame.pts and vstream.time_base else 0.0
                if ft < actual_start:
                    continue
                if ft > actual_end + frame_interval:
                    continue
                try:
                    frame = frame.reformat(
                        format="rgb24",
                        src_colorspace=src_cs,
                        src_color_range=src_cr,
                        dst_color_range=dst_range,
                    )
                    rgb = frame.to_ndarray(format="rgb24")
                except Exception:
                    rgb = frame.to_ndarray(format="rgb24")
                rgb = _resize_frame_np(rgb, max_side)
                while expected_target_time <= ft and expected_target_time < actual_end - 1e-5:
                    frames.append(rgb)
                    expected_target_time += frame_interval
        elif astream is not None and packet.stream is astream:
            for frame in astream.decode(packet):
                ft = frame.time
                if ft is None:
                    ft = float(frame.pts * float(astream.time_base)) if frame.pts and astream.time_base else 0.0
                if ft > actual_end + 1.0:
                    continue
                if first_audio_time is None:
                    first_audio_time = ft
                if resampler is not None:
                    for r in resampler.resample(frame):
                        audio_data.append(r.to_ndarray())
                else:
                    audio_data.append(frame.to_ndarray())

    if frames:
        arr = np.array(frames, dtype=np.float32) / 255.0
        image_tensor = torch.from_numpy(arr)
    else:
        image_tensor = torch.zeros((1, 64, 64, 3), dtype=torch.float32)

    audio_dict = {"waveform": torch.zeros((1, 1, 44100)), "sample_rate": 44100}
    if audio_data:
        waveform_np = np.concatenate(audio_data, axis=1)
        waveform = torch.from_numpy(waveform_np).float()
        sample_rate = getattr(astream, "rate", 44100) or 44100
        offset = max(0.0, actual_start - (first_audio_time or 0.0))
        start_sample = int(offset * sample_rate)
        dur_sec = (actual_end - actual_start) if actual_end != float("inf") else (vdur - actual_start)
        end_sample = start_sample + int(dur_sec * sample_rate)
        if end_sample > start_sample:
            waveform = waveform[:, start_sample:end_sample]
        else:
            waveform = waveform[:, start_sample:]
        if waveform.shape[1] == 0:
            waveform = torch.zeros((waveform.shape[0], 1))
        audio_dict = {"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate}

    container.close()
    return image_tensor, audio_dict


def _f32_pcm(wav):
    if wav.dtype.is_floating_point:
        return wav
    if wav.dtype == torch.int16:
        return wav.float() / (2 ** 15)
    if wav.dtype == torch.int32:
        return wav.float() / (2 ** 31)
    return wav.float()


def _extract_audio(audio_path, start, end):
    with av.open(audio_path) as af:
        if not af.streams.audio:
            raise ValueError("音频文件无音轨")
        stream = af.streams.audio[0]
        sr = stream.codec_context.sample_rate or 44100
        n_channels = stream.channels
        frames = []
        for packet in af.demux(stream):
            for frame in stream.decode(packet):
                buf = torch.from_numpy(frame.to_ndarray())
                if buf.shape[0] != n_channels:
                    buf = buf.view(-1, n_channels).t()
                frames.append(buf)
        if not frames:
            raise ValueError("无解码音频帧")
        wav = _f32_pcm(torch.cat(frames, dim=1))

    start_frame = int(max(0.0, start) * sr)
    if end > start:
        end_frame = int(end * sr)
        end_frame = min(end_frame, wav.shape[1])
    else:
        end_frame = wav.shape[1]
    start_frame = min(start_frame, end_frame)
    trimmed = wav[:, start_frame:end_frame]
    if trimmed.shape[1] == 0:
        trimmed = torch.zeros((wav.shape[0], 1))
    return {"waveform": trimmed.unsqueeze(0), "sample_rate": sr}


_EMPTY_IMAGE = lambda: torch.zeros((1, 64, 64, 3), dtype=torch.float32)
_EMPTY_AUDIO = lambda: {"waveform": torch.zeros((1, 1, 1)), "sample_rate": 44100}


def _load_image(image_path, max_side):
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        if max_side and max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            nw = max(2, int(round(w * scale)))
            nh = max(2, int(round(h * scale)))
            nw -= nw % 2
            nh -= nh % 2
            img = img.resize((nw, nh), Image.BICUBIC)
        arr = np.array(img).astype(np.float32) / 255.0
        return torch.from_numpy(arr).unsqueeze(0)


def _batch_multi_output(imgs):
    valid = [
        t for t in imgs
        if isinstance(t, torch.Tensor) and t.dim() == 4 and t.shape[1] > 0 and t.shape[2] > 0
    ]
    if not valid:
        return torch.zeros((1, 64, 64, 3), dtype=torch.float32)
    max_h = max(t.shape[1] for t in valid)
    max_w = max(t.shape[2] for t in valid)
    pieces = []
    for t in valid:
        b, h, w, c = t.shape
        scale = min(max_h / float(h), max_w / float(w))
        nh = max(1, int(round(h * scale)))
        nw = max(1, int(round(w * scale)))
        if nh != h or nw != w:
            tt = t.permute(0, 3, 1, 2).contiguous()
            tt = F.interpolate(tt, size=(nh, nw), mode="bilinear", align_corners=False)
            tt = tt.permute(0, 2, 3, 1)
        else:
            tt = t
        pad_top = (max_h - nh) // 2
        pad_bottom = max_h - nh - pad_top
        pad_left = (max_w - nw) // 2
        pad_right = max_w - nw - pad_left
        if pad_top or pad_bottom or pad_left or pad_right:
            tt = F.pad(tt.permute(0, 3, 1, 2),
                       (pad_left, pad_right, pad_top, pad_bottom),
                       mode="constant", value=0.0)
            tt = tt.permute(0, 2, 3, 1)
        pieces.append(tt)
    return torch.cat(pieces, dim=0)


def load_all_media(image_paths, video_paths, audio_paths, frame_rate, max_side):
    """加载全部三类素材，返回 (ref_images_dict, ref_videos_dict, ref_video_audios_dict,
    ref_audios_dict, multi_output, report_text)。

    dict 的 key 为 ref_image_0..8 / ref_video_0..2 / ref_video_audio_0..2 / ref_audio_0..2，
    仅包含成功加载且非空的条目。
    """
    reports = []
    total_size_mb = 0.0

    # ---- 图片 ----
    img_items = _parse_lines(image_paths, MAX_IMAGES)
    ref_images = {}
    real_imgs = []
    for i in range(min(len(img_items), MAX_IMAGES)):
        path, start, end = img_items[i]
        full = _resolve_path(path)
        if not full or not os.path.isfile(full):
            reports.append(f"[图片 {i}] 文件不存在: {path}")
            continue
        ok, sz_mb, warn = _check_file_size(full, SIZE_IMG_MB, f"图片 {i}")
        if warn:
            reports.append(warn)
        total_size_mb += sz_mb
        try:
            img = _load_image(full, max_side)
            ref_images[f"ref_image_{i}"] = img
            real_imgs.append(img)
            reports.append(f"[图片 {i}] {os.path.basename(path)} ({sz_mb:.1f}MB) -> {list(img.shape)}")
        except Exception as e:
            reports.append(f"[图片 {i}] 加载失败: {e}")

    if len(img_items) > MAX_IMAGES:
        reports.append(f"图片超过 {MAX_IMAGES} 张，仅使用前 {MAX_IMAGES} 张（H3 上限）。")

    multi_output = _batch_multi_output(real_imgs)
    if real_imgs:
        reports.append(f"[multi_output] 批合并 {len(real_imgs)} 张 -> {list(multi_output.shape)}")

    # ---- 视频 ----
    vid_items = _parse_lines(video_paths)
    ref_videos = {}
    ref_video_audios = {}
    for i in range(min(len(vid_items), MAX_ITEMS)):
        path, start, end = vid_items[i]
        full = _resolve_path(path)
        if not full or not os.path.isfile(full):
            reports.append(f"[视频 {i}] 文件不存在: {path}")
            continue
        ok, sz_mb, warn = _check_file_size(full, SIZE_VID_MB, f"视频 {i}")
        if warn:
            reports.append(warn)
        total_size_mb += sz_mb
        if end > start and (end - start) < MIN_CLIP_SEC:
            reports.append(f"[视频 {i}] 裁剪时长 {end-start:.1f}s < {MIN_CLIP_SEC}s 最小建议")
        try:
            img, aud = _extract_video(full, start, end, frame_rate, max_side)
            ref_videos[f"ref_video_{i}"] = img
            ref_video_audios[f"ref_video_audio_{i}"] = aud
            out_dur = img.shape[0] / max(1, frame_rate)
            parts = [f"[视频 {i}] {os.path.basename(path)} ({sz_mb:.1f}MB) -> 帧 {list(img.shape)}"]
            if end > start:
                parts.append(f", 裁剪 {start:.1f}-{end:.1f}s 输出 {out_dur:.1f}s")
            reports.append("".join(parts))
        except Exception as e:
            reports.append(f"[视频 {i}] 加载失败: {e}")

    if len(vid_items) > MAX_ITEMS:
        reports.append(f"视频超过 {MAX_ITEMS} 段，仅使用前 {MAX_ITEMS} 段（H3 上限）。")

    # ---- 音频 ----
    aud_items = _parse_lines(audio_paths)
    ref_audios = {}
    for i in range(min(len(aud_items), MAX_ITEMS)):
        path, start, end = aud_items[i]
        full = _resolve_path(path)
        if not full or not os.path.isfile(full):
            reports.append(f"[音频 {i}] 文件不存在: {path}")
            continue
        ok, sz_mb, warn = _check_file_size(full, SIZE_AUD_MB, f"音频 {i}")
        if warn:
            reports.append(warn)
        total_size_mb += sz_mb
        if end > start and (end - start) < MIN_CLIP_SEC:
            reports.append(f"[音频 {i}] 裁剪时长 {end-start:.1f}s < {MIN_CLIP_SEC}s 最小建议")
        try:
            aud = _extract_audio(full, start, end)
            ref_audios[f"ref_audio_{i}"] = aud
            out_dur = aud["waveform"].shape[-1] / aud["sample_rate"]
            parts = [f"[音频 {i}] {os.path.basename(path)} ({sz_mb:.1f}MB) -> 采样 {aud['waveform'].shape}"]
            if end > start:
                parts.append(f", 裁剪 {start:.1f}-{end:.1f}s 输出 {out_dur:.1f}s")
            reports.append("".join(parts))
        except Exception as e:
            reports.append(f"[音频 {i}] 加载失败: {e}")

    if len(aud_items) > MAX_ITEMS:
        reports.append(f"音频超过 {MAX_ITEMS} 段，仅使用前 {MAX_ITEMS} 段（H3 上限）。")

    # ---- 总校验 ----
    total_files = len(img_items) + len(vid_items) + len(aud_items)
    if total_files > MAX_TOTAL_FILES:
        reports.insert(0,
            f"[⚠ 总数] 素材合计 {total_files} 个 > {MAX_TOTAL_FILES} 个 H3 上限（图片{len(img_items)}+视频{len(vid_items)}+音频{len(aud_items)}）")
    if total_size_mb > SIZE_TOTAL_MB:
        reports.insert(0,
            f"[⚠ 总容量] 全部素材总大小 {total_size_mb:.1f}MB > {SIZE_TOTAL_MB}MB 官方上限")
    if len(aud_items) > 0 and len(img_items) == 0 and len(vid_items) == 0:
        reports.insert(0, "[⚠ 规则] H3 不允许纯音频输入，必须搭配至少一张图片或一段视频")
    if not reports:
        reports.append("（无素材已加载）")

    return ref_images, ref_videos, ref_video_audios, ref_audios, multi_output, "\n".join(reports)


# ===========================================================================
# 关键帧（Add Guide）素材
# ---------------------------------------------------------------------------
# 行格式（每行一个关键帧，| 分隔 7 段，空段留空）：
#   media_path|audio_path|frame_idx|media_start|media_end|audio_start|audio_end
#   - media_path : 图片 或 视频片段（扩展名区分）。视频会抽帧成多帧 clip，
#                  由 AddGuide 按 17k+5 网格自动裁剪成合法长度。
#   - audio_path : 可选音频（可裁切）。
#   - frame_idx  : 锚定帧号（0 起始；负数 = 从视频结尾倒数），与原生 AddGuide 一致。
#   - media_start/media_end : 视频片段裁切秒（图片忽略）。
#   - audio_start/audio_end : 音频裁切秒。
# 帧↔秒换算沿用 H3 Screenwriter 的数学表达式：秒→帧 round(s*24)，
# 对齐 %17==5；帧→秒 = frame / 24。
# ===========================================================================

def _parse_keyframe_lines(text, max_items=MAX_KEYFRAMES):
    """解析关键帧行文本 -> list of dict，每项含原始字段。"""
    items = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        while len(parts) < 7:
            parts.append("")
        media, audio = parts[0], parts[1]
        if not media and not audio:
            continue
        try:
            frame_idx = int(parts[2]) if parts[2] else 0
        except ValueError:
            frame_idx = 0

        def _sec(p):
            try:
                return float(p) if p else 0.0
            except ValueError:
                return 0.0

        items.append({
            "media_path": media,
            "audio_path": audio,
            "frame_idx": frame_idx,
            "media_start": _sec(parts[3]),
            "media_end": _sec(parts[4]),
            "audio_start": _sec(parts[5]),
            "audio_end": _sec(parts[6]),
        })
        if len(items) >= max_items:
            break
    return items


def load_keyframes(keyframe_paths, frame_rate=DEFAULT_FRAME_RATE, max_side=DEFAULT_MAX_SIDE):
    """加载关键帧条目 -> (keyframes, report_text)。

    keyframes 是 list of dict：
        {"frame_idx": int, "image": torch.Tensor|None, "audio": dict|None}
    每个条目至少含 image 或 audio 之一（None 由下游 MiniMaxH3AddGuide 校验，
    与该原生节点“至少一个”的规则一致，不在此处二次拦截）。
    """
    reports = []
    total_size_mb = 0.0
    items = _parse_keyframe_lines(keyframe_paths)
    keyframes = []

    for i, it in enumerate(items):
        parts = []
        frame_idx = it["frame_idx"]
        image = None
        audio = None

        # ---- 媒体（图片 / 视频片段）----
        if it["media_path"]:
            full = _resolve_path(it["media_path"])
            if not full or not os.path.isfile(full):
                reports.append(f"[关键帧 {i}] 文件不存在: {it['media_path']}")
                continue
            ext = os.path.splitext(full)[1].lower()
            if ext in IMG_EXTS:
                ok, sz_mb, warn = _check_file_size(full, SIZE_IMG_MB, f"关键帧{i} 图片")
                if warn:
                    reports.append(warn)
                total_size_mb += sz_mb
                try:
                    image = _load_image(full, max_side)
                    parts.append(f"图片 {os.path.basename(full)} {list(image.shape)}")
                except Exception as e:
                    reports.append(f"[关键帧 {i}] 图片加载失败: {e}")
                    continue
            elif ext in VID_EXTS:
                ok, sz_mb, warn = _check_file_size(full, SIZE_VID_MB, f"关键帧{i} 视频")
                if warn:
                    reports.append(warn)
                total_size_mb += sz_mb
                if it["media_end"] > it["media_start"] and (it["media_end"] - it["media_start"]) < MIN_CLIP_SEC:
                    reports.append(
                        f"[关键帧 {i}] 视频裁剪 {it['media_start']}-{it['media_end']}s "
                        f"< {MIN_CLIP_SEC}s 最小建议")
                try:
                    image, _aud = _extract_video(full, it["media_start"], it["media_end"],
                                                 frame_rate, max_side)
                    parts.append(f"视频 {os.path.basename(full)} 帧 {list(image.shape)}")
                except Exception as e:
                    reports.append(f"[关键帧 {i}] 视频加载失败: {e}")
                    continue
            else:
                reports.append(f"[关键帧 {i}] 不支持的媒体扩展名: {ext}")
                continue

        # ---- 音频 ----
        if it["audio_path"]:
            afull = _resolve_path(it["audio_path"])
            if not afull or not os.path.isfile(afull):
                reports.append(f"[关键帧 {i}] 音频文件不存在: {it['audio_path']}")
                continue
            ok, sz_mb, warn = _check_file_size(afull, SIZE_AUD_MB, f"关键帧{i} 音频")
            if warn:
                reports.append(warn)
            total_size_mb += sz_mb
            try:
                audio = _extract_audio(afull, it["audio_start"], it["audio_end"])
                parts.append(f"音频 {os.path.basename(afull)} 采样 {list(audio['waveform'].shape)}")
            except Exception as e:
                reports.append(f"[关键帧 {i}] 音频加载失败: {e}")
                continue

        if image is None and audio is None:
            reports.append(f"[关键帧 {i}] 无有效素材，已跳过")
            continue

        # 帧→秒换算（沿用 H3 数学表达式：秒 = 帧 / 24，固定 H3_FPS）
        sec_show = frame_idx / float(H3_FPS)
        parts.append(f"帧 {frame_idx} (= {sec_show:.2f}s)")
        keyframes.append({"frame_idx": frame_idx, "image": image, "audio": audio})
        reports.append(f"[关键帧 {i}] {' | '.join(parts)}")

    if len(items) > MAX_KEYFRAMES:
        reports.append(f"关键帧超过 {MAX_KEYFRAMES} 个，仅使用前 {MAX_KEYFRAMES} 个。")
    if total_size_mb > SIZE_TOTAL_MB:
        reports.insert(0,
            f"[⚠ 总容量] 关键帧素材总大小 {total_size_mb:.1f}MB > {SIZE_TOTAL_MB}MB 官方上限")
    if not keyframes:
        reports.append("（无关键帧）")

    return keyframes, "\n".join(reports)
