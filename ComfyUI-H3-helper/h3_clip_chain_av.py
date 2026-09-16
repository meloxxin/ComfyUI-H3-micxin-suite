"""H3 Clip Chain — 纯分段采样器。

接收外部 Ref2VA（如 H3 R2VA AIO）输出的 positive + latent，只做：
  1. 分段换提示词重新编码（继承外部 positive 的参考图/视频/音频/关键帧）
  2. Motion Context 接续（链式）或独立采样
  3. 采样 → 解码 → 裁重叠帧 → 合并

宽高长由外部 Ref2VA 节点控制，本节点不重复。

分段提示词格式（prompts 多行文本，每行一段）：
  - 行首 "# " 开头 = 独立模式（standalone）
  - 无标记 = 链式模式（chain，第一段自动独立）
  示例：
    # 场景A：一个女人走进房间
    她转身看向镜头
    # 场景B：切到室外街道
    她沿着街道走
"""

import re
import os
import torch
import json
import comfy.samplers
from comfy_api.latest import io
from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide
try:
    from .h3_media_utils import load_keyframes, DEFAULT_FRAME_RATE, DEFAULT_MAX_SIDE
except ImportError:  # 测试顶层导入时回退
    from h3_media_utils import load_keyframes, DEFAULT_FRAME_RATE, DEFAULT_MAX_SIDE

# 音频锁定（参考 AIO H3ModelLoader 的 _audio_lock_latent，支持 off/anchor/full）
try:
    from .h3_model_loader import _audio_lock_latent
except ImportError:
    try:
        from h3_model_loader import _audio_lock_latent
    except ImportError:
        _audio_lock_latent = None

# H3ClipChain 分段提示词动态输入上限（Autogrow 槽，类似 MiniMax H3 Extender 增加 clip）。
# 与 H3 Prompt Split (micxin) 的 MAX_PROMPTS 保持一致，扩展两端任意一个数字时保持同步。
H3CC_MAX_SEGMENT_PROMPTS = 32


# ────────────────────────────────────────────────────────────
# H3 VAE 时间网格辅助
# ────────────────────────────────────────────────────────────

# H3 视频 VAE 每个 latent step 覆盖的像素帧数（循环）
_FRAME_PER_TOKEN = (1, 4, 4, 4, 4)
# 可用的 Motion Context 窗口（像素帧数，降序）
_CONTEXT_RUN_GRID = (56, 39, 22, 5, 1)
# H3 默认帧率（用于音频裁剪时的采样数换算）
_DEFAULT_FPS = 24


def _pixel_frames(latent_t):
    """latent steps → 像素帧数。"""
    n = int(latent_t)
    total = 0
    i = 0
    while i < n:
        total += _FRAME_PER_TOKEN[i % len(_FRAME_PER_TOKEN)]
        i += 1
    return total


def _steps_for_frames(n):
    """像素帧数 → latent steps（最小覆盖），无法精确对齐返回 None。"""
    target = int(n)
    if target <= 0:
        return None
    steps = 1
    while _pixel_frames(steps) < target:
        steps += 1
        if steps > 4096:
            return None
    return steps


def _step_offsets(latent_t):
    """每个 latent step 的起始像素帧偏移。"""
    offsets = []
    cur = 0
    for i in range(int(latent_t)):
        offsets.append(cur)
        cur += _FRAME_PER_TOKEN[i % len(_FRAME_PER_TOKEN)]
    return offsets


# ────────────────────────────────────────────────────────────
# AV latent 流提取（支持 NestedTensor）
# ────────────────────────────────────────────────────────────

def _video_stream_from_latent(latent):
    """从 AV latent 取视频流 [B, C, T, H, W]。

    支持三种格式：list/tuple、NestedTensor（H3 AV latent）、普通 tensor。
    """
    samples = latent["samples"]
    if isinstance(samples, (list, tuple)):
        return samples[0]
    if getattr(samples, "is_nested", False):
        return samples.unbind()[0]
    return samples


def _audio_stream_from_latent(latent):
    """从 AV latent 取音频流。"""
    samples = latent["samples"]
    if isinstance(samples, (list, tuple)) and len(samples) > 1:
        return samples[1]
    if getattr(samples, "is_nested", False):
        tensors = samples.unbind()
        if len(tensors) > 1:
            return tensors[1]
    return None


# ────────────────────────────────────────────────────────────
# 时长 → H3 帧数（对齐 length % 17 == 5）
# ────────────────────────────────────────────────────────────

def _resolve_length(duration_seconds):
    """秒 → H3 像素帧数，对齐 length % 17 == 5（与 H3 PromptWriter 一致）。"""
    f = max(5, round(float(duration_seconds) * 24))
    f = f + (5 - (f % 17)) % 17
    return int(f)


def _pixel_to_latent_t(pixel_frames):
    """像素帧数 → latent T（H3 VAE 时间压缩比 (1,4,4,4,4) 循环）。"""
    # 反向计算：找到最小的 latent_t 使得 _pixel_frames(latent_t) >= pixel_frames
    for t in range(1, 4096):
        if _pixel_frames(t) >= pixel_frames:
            return t
    return 4096


def _create_segment_latent(base_latent, pixel_frames, head_block=None, head_steps=0,
                           audio_head=None, audio_head_steps=0):
    """基于外部 latent 创建指定帧数的新 AV latent。

    同步处理 video / audio / noise_mask 的 shape，避免 shape 不匹配导致
    CUDA illegal memory access。新视频流默认用随机噪声初始化（和 Empty AV Latent 一致）。

    latent 链延续（head_block 非空）：
      - 头部 head_steps 步填入前一段采样完的真实 latent（同一 latent 链物理延续）
      - 对应 noise_mask 头部置 0（不加噪声、作为初始状态参与采样），新区置 1（重采样）
      - audio_head 同理延续音频 latent 尾部
    音频流默认按比例调整 T 并用 zeros 占位（采样时会被模型覆盖）。
    """
    base_video = _video_stream_from_latent(base_latent)
    base_audio = _audio_stream_from_latent(base_latent)

    B, C, _, H, W = base_video.shape
    latent_t = _pixel_to_latent_t(pixel_frames)

    # 新视频流：随机噪声初始化；latent 链延续时头部填入前段真实 latent
    new_video = torch.randn((B, C, latent_t, H, W),
                            dtype=base_video.dtype, device=base_video.device)
    if head_block is not None:
        hb = head_block.to(dtype=new_video.dtype, device=new_video.device)
        ks = int(hb.shape[2])
        if ks > latent_t:
            ks = latent_t
            hb = hb[:, :, :latent_t]
        new_video[:, :, :ks] = hb

    # 新音频流：按视频 latent T 的比例调整音频 T，zeros 占位；
    # latent 链延续时头部填入前段音频 latent 尾部
    new_audio = None
    if base_audio is not None:
        ratio = base_audio.shape[-1] / max(1, base_video.shape[2])
        audio_t = max(1, int(round(latent_t * ratio)))
        if base_audio.ndim == 4:  # [B, 32, 2, T]
            new_audio = torch.zeros(
                (base_audio.shape[0], base_audio.shape[1], base_audio.shape[2], audio_t),
                dtype=base_audio.dtype, device=base_audio.device)
            if audio_head is not None:
                ah = audio_head.to(dtype=new_audio.dtype, device=new_audio.device)
                aks = int(ah.shape[-1])
                if aks > audio_t:
                    aks = audio_t
                    ah = ah[..., :audio_t]
                new_audio[..., :aks] = ah
        elif base_audio.ndim == 3:  # [2, 32, T]
            new_audio = torch.zeros(
                (base_audio.shape[0], base_audio.shape[1], audio_t),
                dtype=base_audio.dtype, device=base_audio.device)
            if audio_head is not None:
                ah = audio_head.to(dtype=new_audio.dtype, device=new_audio.device)
                aks = int(ah.shape[-1])
                if aks > audio_t:
                    aks = audio_t
                    ah = ah[..., :audio_t]
                new_audio[..., :aks] = ah

    # 构建新的 samples（NestedTensor / list / tensor）
    new_latent = base_latent.copy()
    samples = new_latent["samples"]

    if getattr(samples, "is_nested", False):
        from comfy.nested_tensor import NestedTensor
        if new_audio is not None:
            new_latent["samples"] = NestedTensor((new_video, new_audio))
        else:
            parts = list(samples.unbind())
            parts[0] = new_video
            new_latent["samples"] = NestedTensor(tuple(parts))
    elif isinstance(samples, (list, tuple)):
        parts = list(samples)
        parts[0] = new_video
        if new_audio is not None and len(parts) > 1:
            parts[1] = new_audio
        new_latent["samples"] = parts
    else:
        new_latent["samples"] = new_video

    # 同步处理 noise_mask—— shape 必须和 samples 对齐，
    # 否则采样器内部越界导致 CUDA illegal memory access。
    # latent 链延续（head_block 非空）时必须携带 noise_mask：
    # 头部延续区 0（不加噪声、作为初始状态），新区 1（重采样）。
    if "noise_mask" in new_latent or head_block is not None:
        nm = new_latent.get("noise_mask")
        nm_video = None
        if getattr(nm, "is_nested", False):
            nm_parts = list(nm.unbind())
            nm_video = nm_parts[0]
        elif isinstance(nm, (list, tuple)):
            nm_video = nm[0]
        elif nm is not None:
            nm_video = nm
        new_nm_video = torch.ones(
            (B, C, latent_t, H, W),
            dtype=new_video.dtype, device=new_video.device)
        if head_block is not None and head_steps > 0:
            new_nm_video[:, :, :int(head_steps)] = 0
        new_nm_audio = None
        if new_audio is not None:
            new_nm_audio = torch.ones_like(new_audio)
            if audio_head is not None and audio_head_steps > 0:
                new_nm_audio[..., :int(audio_head_steps)] = 0
        from comfy.nested_tensor import NestedTensor
        if new_nm_audio is not None:
            new_latent["noise_mask"] = NestedTensor((new_nm_video, new_nm_audio))
        else:
            new_latent["noise_mask"] = NestedTensor((new_nm_video,))

    return new_latent


# ────────────────────────────────────────────────────────────
# 分段提示词重新编码（继承外部参考信息）
# ────────────────────────────────────────────────────────────

_STRUCT_MARKS = ("detailed_description", "subject_definitions",
                "integrated_multimodal_description", "retention_analysis")

_DLG_RE = re.compile(r'["\u201c]([^"\u201d\n]{1,300})["\u201d]')


def _tag_dialogue_blocks(text):
    """把中文引号内对话包装为 H3 官方 <d>[Chinese] 原文</d> 格式。

    只处理含中文字符的引号内容（避免误伤英文文本）；已有 <d> 标签的不重复包装。
    """
    def _rep(m):
        s = m.group(1).strip()
        if not s or "<d>" in s or "</d>" in s:
            return m.group(0)
        if not re.search(r'[\u4e00-\u9fff]', s):
            return m.group(0)
        return "<d>[Chinese] %s</d>" % s
    return _DLG_RE.sub(_rep, text)


def _count_refs(base_positive):
    """统计外部 positive 里 minimax_refs 的图/音/视数量（Ref2VA 参考）。"""
    n_image = n_audio = n_video = 0
    if not base_positive or not base_positive[0]:
        return {"n_image": 0, "n_audio": 0, "n_video": 0}
    extra = base_positive[0][1] or {}
    refs = extra.get("minimax_refs") or []
    for r in refs:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("kind", ""))
        if kind == "image":
            n_image += 1
        elif kind == "audio":
            n_audio += 1
        elif kind == "video":
            n_video += 1
    return {"n_image": n_image, "n_audio": n_audio, "n_video": n_video}


def _subject_defs_cc(n_image, n_audio, n_video):
    """subject_definitions + summary + retention_analysis（移植自 H3-Multishot _subject_defs，
    单主角简化版，无角色音色绑定、无链帧）。"""
    d = ["subject_definitions:"]
    if n_image:
        d.append("<Subject 1> is the main subject in <Picture 1>, retaining the "
                 "same appearance, identity and style.")
        for k in range(2, n_image + 1):
            d.append("<Picture %d> is a reference photograph of <Subject 1>." % k)
    else:
        d.append("<Subject 1> is the protagonist speaking in this scene.")
    if n_audio:
        for j in range(1, n_audio + 1):
            d.append("<Audio %d> is a recording of <Subject 1>'s speaking voice." % j)
    if n_video:
        for k in range(1, n_video + 1):
            d.append("<Video %d> is a clip from an earlier moment of this same "
                     "continuous scene, showing <Subject 1> in the same place "
                     "under the same light." % k)

    r = ["retention_analysis:"]
    if n_image:
        r.append("<Subject 1> (appears in [Shot 1]): fully_preserved - <Subject 1> "
                 "retains the same face, skin and hair.")
        for k in range(1, n_image + 1):
            r.append("<Picture %d> ([Shot 1] reference): fully_preserved - the "
                     "subject keeps the appearance of <Picture %d>." % (k, k))
    if n_audio:
        for j in range(1, n_audio + 1):
            r.append("<Audio %d>: reference - the target audio references the "
                     "voice timbre in <Audio %d> so <Subject 1> speaks with the "
                     "same voice." % (j, j))
    if n_video:
        for k in range(1, n_video + 1):
            r.append("<Video %d>: reference - the target video keeps the framing, "
                     "camera distance and colour temperature of <Video %d>." % (k, k))

    s = ["summary:", "The target video is one continuous shot of <Subject 1>."]
    if n_image:
        pics = ", ".join("<Picture %d>" % k for k in range(1, n_image + 1))
        s.append("%s %s the appearance of <Subject 1>." %
                 (pics, "supplies" if n_image == 1 else "supply"))
    if n_audio:
        auds = ", ".join("<Audio %d>" % j for j in range(1, n_audio + 1))
        s.append("%s supplies <Subject 1>'s voice timbre." % auds)
    if n_video:
        vids = ", ".join("<Video %d>" % k for k in range(1, n_video + 1))
        s.append("%s %s the place, framing and light this shot continues from." %
                 (vids, "carries" if n_video == 1 else "carry"))
    return "\n".join(d), " ".join(s), "\n".join(r)


def _auto_h3_compile(prompt, base_positive, auto_h3_compile=True, auto_dialogue=True, first_frame_has_image=False):
    """把非六段式提示词自动包装成 H3 官方结构（移植自 H3-Multishot）。

    - 已含六段式标记 → 原样返回（不重复包装）
    - 有外部参考（Ref2VA）→ subject_definitions + summary + retention_analysis +
      detailed_description + non_diegetic_music: N/A
    - 无参考 → T2VA 官方骨架（instruction line + integrated_multimodal_description +
      overall_soundscape + non_diegetic_music）
    """
    p = (prompt or "").strip()
    if not p:
        return prompt
    if not auto_h3_compile:
        return prompt
    if any(m in p for m in _STRUCT_MARKS):
        return prompt  # 已经是六段式，跳过
    if auto_dialogue:
        p = _tag_dialogue_blocks(p)

    refs = _count_refs(base_positive)
    if refs["n_image"] or refs["n_audio"] or refs["n_video"]:
        defs, summary, retention = _subject_defs_cc(
            refs["n_image"], refs["n_audio"], refs["n_video"])
        return "\n\n".join([defs, summary, retention,
                              "detailed_description:\n" + p.strip(),
                              "non_diegetic_music: N/A"])
    # 该段关键帧锁定了首帧图（AddGuide frame 0）→ I2VA 官方 instruction line
    # （与 H3-Multishot 无参考有链帧时的做法一致：keyframe latent + 文本引导并存）
    if first_frame_has_image:
        return ("For the target video, at 0.00 seconds into the target video, "
                "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
                "integrated_multimodal_description:\n" + p + "\n\n"
                "overall_soundscape: N/A\n"
                "non_diegetic_music: N/A")
    # T2VA 官方骨架（Section B）
    return ("For the target video, there is no image to be referenced.\n\n"
            "integrated_multimodal_description:\n" + p + "\n\n"
            "overall_soundscape: N/A\n"
            "non_diegetic_music: N/A")


def _reencode_prompt_with_ref(clip, prompt, base_positive, segment_idx=0, frames_per_segment=0, first_frame_has_image=False):
    """用新提示词编码 positive，同时继承 base_positive 里的所有额外信息。

    外部 positive（来自 Ref2VA）的 extra 里包含 minimax_keyframes（参考图/视频/
    音频/关键帧）等信息。这里只替换 cond_tensor 和 pooled_output，保留其余全部。

    segment_idx / frames_per_segment：分段采样时，把关键帧的 resolved_frame_index
    相对于当前段起始位置偏移，只保留在当前段范围内的关键帧。

    提示词原样使用（六段式由上游 H3 Prompt Fix / Split+Translate 生成，
    auto_h3_compile/auto_dialogue 接口已删除）。
    """
    # 语义锚（人物一致性）：外部 Ref2VA 的参考图 minimax_ref_items 随 positive
    # 透传（AIO 5a0 注入）；重编码时重新喂给 Qwen 视觉编码。否则分段提示词
    # 丢失"参考图是谁"的语义理解，人脸锚定失效（只剩 ref2va 像素弱约束）。
    extra = {}
    if base_positive and len(base_positive) > 0:
        extra = dict(base_positive[0][1])
    _ref_items = extra.get("minimax_ref_items")
    tokens = (clip.tokenize(prompt, minimax_ref_items=_ref_items)
              if _ref_items else clip.tokenize(prompt))
    # 用官方一致的 encode_from_tokens_scheduled，它内部调用
    # encode_from_tokens(return_dict=True)，返回完整 extra（含 minimax_token_tags、
    # pooled_output 等所有 H3 特有字段）。直接用 encode_from_tokens(return_pooled=True)
    # 会丢失 extra 字典，导致 DiT 无法正确识别文本 token，画面糊。
    new_conds = clip.encode_from_tokens_scheduled(tokens)
    cond, new_extra = new_conds[0]

    # 用新编码的完整 extra 覆盖（pooled_output, minimax_token_tags 等）
    extra.update(new_extra)

    # 关键帧时间戳按段偏移：resolved_frame_index -= segment_idx * frames_per_segment
    # 只保留在当前段范围内 [0, frames_per_segment) 的关键帧
    if frames_per_segment > 0 and segment_idx > 0:
        kfs = extra.get("minimax_keyframes")
        if kfs:
            offset = segment_idx * frames_per_segment
            shifted = []
            for kf in kfs:
                if not isinstance(kf, dict):
                    shifted.append(kf)
                    continue
                rf = kf.get("resolved_frame_index")
                if rf is None:
                    shifted.append(kf)
                    continue
                new_rf = rf - offset
                if 0 <= new_rf < frames_per_segment:
                    kf_copy = dict(kf)
                    kf_copy["resolved_frame_index"] = new_rf
                    shifted.append(kf_copy)
            extra["minimax_keyframes"] = shifted

    return [[cond, extra]]


# ────────────────────────────────────────────────────────────
# Motion Context 注入（严格参考 MiniMax H3 Extender 的 MiniMaxH3MotionContextRAM）
# ────────────────────────────────────────────────────────────

def _pad_motion_context_block_to_target(block, target_video):
    """对齐 context block 的 H/W 到 DiT 2x2 patch grid。

    H3 DiT patch size 是空间 2x2。如果输入分辨率不是 32 的倍数，
    target 会被内部 pad 到偶数，而 unpadded 的 context block 会让
    patchify_video() reshape 失败，甚至触发 CUDA illegal memory access。
    只 pad block，不修改 target latent 本身。
    """
    if block.ndim != 5 or target_video.ndim != 5:
        raise ValueError("Motion Context: expected 5D video latents.")

    target_h = int(target_video.shape[3])
    target_w = int(target_video.shape[4])
    padded_h = ((target_h + 1) // 2) * 2
    padded_w = ((target_w + 1) // 2) * 2

    h = int(block.shape[3])
    w = int(block.shape[4])

    if h > padded_h or w > padded_w:
        raise RuntimeError(
            f"Motion Context: context block {w}x{h} larger than target "
            f"patch grid {padded_w}x{padded_h}.")

    pad_h = padded_h - h
    pad_w = padded_w - w

    if pad_h == 0 and pad_w == 0:
        return block

    # torch.nn.functional.pad for [B,C,T,H,W]: (W_left, W_right, H_top, H_bottom)
    return torch.nn.functional.pad(block, (0, pad_w, 0, pad_h), mode="constant", value=0.0)


def _motion_context_trim(previous_latent, context_length):
    """预计算 Motion Context 将注入/裁掉的帧数（与 _apply_motion_context 内部计算一致）。

    只依赖前一段 latent 的 T 和 context_length（steps → 相位对齐 → covered 像素帧）。
    用于时长补偿：chain 段采样帧数 = 目标净帧 + trim，裁掉重叠后净输出恰好 = 目标帧。
    """
    if previous_latent is None:
        return 0
    source_video = _video_stream_from_latent(previous_latent)
    if source_video is None:
        return 0
    context_frames = int(context_length)
    total_t = int(source_video.shape[2])
    steps = _steps_for_frames(context_frames)
    if steps is None or steps > total_t:
        return 0
    start = total_t - steps
    if start % 5 != 0:
        start = (start // 5) * 5
        steps = total_t - start
    return _pixel_frames(steps)


def _apply_motion_context(positive, latent, previous_latent, context_length,
                           audio_context_length=0, audio_vae=None):
    """把前一个 clip 的最后 N 帧 latent 注入当前 clip 的 conditioning。

    严格参考 MiniMax H3 Extender 的 MiniMaxH3MotionContextRAM 实现：
    - shape 匹配检查（batch/channels/resolution）
    - context block .clone() + pad 到 DiT 2x2 patch grid
    - keyframes 锚定在帧 0..n-1（head 模式）

    返回 (positive, trim_frames)。
    trim_frames = 实际注入的帧数（最终合并时从当前 clip 开头裁掉）。
    """
    if previous_latent is None:
        return positive, 0

    target_video = _video_stream_from_latent(latent)
    source_video = _video_stream_from_latent(previous_latent)

    if target_video is None or source_video is None:
        return positive, 0

    # shape 匹配检查（Extender 原版逻辑）
    if target_video.shape[0] != source_video.shape[0]:
        raise ValueError("Motion Context: batch size differs between previous and next clip.")
    if target_video.shape[1] != source_video.shape[1]:
        raise ValueError("Motion Context: video latent channels differ.")
    if target_video.shape[3:] != source_video.shape[3:]:
        sw = int(source_video.shape[4]) * 16
        sh = int(source_video.shape[3]) * 16
        tw = int(target_video.shape[4]) * 16
        th = int(target_video.shape[3]) * 16
        raise ValueError(
            f"Motion Context: resolution mismatch {sw}x{sh} -> {tw}x{th}. "
            f"Latent motion context cannot resize.")

    context_frames = int(context_length)
    target_frame_count = _pixel_frames(int(target_video.shape[2]))

    if context_frames >= target_frame_count:
        raise ValueError(
            f"Motion Context: context window ({context_frames} frames) must be "
            f"shorter than the next clip ({target_frame_count} frames).")

    # 从 source 尾部提取 blocks（.clone()，Extender 原版用 video[:1, ...]）
    total_t = int(source_video.shape[2])
    steps = _steps_for_frames(context_frames)
    if steps is None or steps > total_t:
        return positive, 0

    start = total_t - steps
    # Extender 原版检查 start % 5 == 0（相位对齐）
    if start % 5 != 0:
        # 相位不对齐时向下取整到最近的对齐点
        start = (start // 5) * 5
        steps = total_t - start

    covered = _pixel_frames(steps)
    n = covered  # trim = 实际覆盖的帧数

    blocks = [source_video[:1, :, start + k:start + k + 1].clone() for k in range(steps)]
    offsets = _step_offsets(steps)

    # 构建 keyframes（每个 block pad 到 target 的 patch grid）
    keyframes = []
    for pixel_index, block in zip(offsets, blocks):
        block = _pad_motion_context_block_to_target(block, target_video)
        keyframes.append({
            "resolved_frame_index": int(pixel_index),
            "latent": block,
        })

    # 音频上下文（可选，暂时保留简单实现）
    if audio_context_length and audio_context_length > 0 and audio_vae is not None:
        audio_stream = _audio_stream_from_latent(previous_latent)
        if audio_stream is not None:
            audio_t = audio_stream.shape[-1] if audio_stream.dim() >= 3 else 0
            if audio_t > 0:
                a_tail = min(int(audio_context_length), audio_t)
                audio_block = audio_stream[..., -a_tail:].clone()
                keyframes.append({
                    "resolved_frame_index": int(n),
                    "audio_latent": audio_block,
                })

    # 注入 conditioning（丢掉 head 重叠区内的已有 keyframe，避免冲突）
    out = []
    head_end = n
    for emb, extra in positive:
        d = dict(extra)
        prior = list(d.get("minimax_keyframes", []))
        kept = [kf for kf in prior if int(kf.get("resolved_frame_index", 0)) >= head_end]
        d["minimax_keyframes"] = kept + keyframes
        out.append([emb, d])

    return out, n


# ────────────────────────────────────────────────────────────
# 采样（H3 rectified flow, cfg=1）
# ────────────────────────────────────────────────────────────

def _sample_h3(model, positive, negative, latent, seed, steps,
               sampler_name="euler", scheduler="simple", denoise=1.0):
    """H3 采样。cfg=1（rectified flow 不需要 negative 引导）。

    参考 nodes.common_ksampler：自己 prepare_noise，然后调 comfy.sample.sample
    （注意不是 comfy.samplers.sample，后者是底层函数，不接受 denoise）。

    """
    latent_image = latent["samples"]
    batch_inds = latent.get("batch_index")
    noise = comfy.sample.prepare_noise(latent_image, seed, batch_inds)
    noise_mask = latent.get("noise_mask")

    samples = comfy.sample.sample(
        model, noise, steps, 1.0,  # cfg=1.0
        sampler_name, scheduler,
        positive, negative, latent_image,
        denoise=denoise,
        noise_mask=noise_mask,
        seed=seed,
    )

    out = latent.copy()
    out["samples"] = samples
    return out


def _encode_negative(clip):
    """空提示词作为 negative（cfg=1 时实际不影响）。"""
    tokens = clip.tokenize("")
    cond, pooled = clip.encode_from_tokens(tokens, return_pooled=True)
    return [[cond, {"pooled_output": pooled}]]


# ────────────────────────────────────────────────────────────
# 解码 + 裁剪 + 合并
# ────────────────────────────────────────────────────────────

def _decode_av_latent(latent, vae, audio_vae):
    """解码 AV latent → (视频帧 IMAGE, 音频 AUDIO)。"""
    video = _video_stream_from_latent(latent)
    decoded = vae.decode(video)
    # H3 video VAE decode 返回 [B, T, H, W, C] float32 [0,1]，
    # 直接 reshape 成 ComfyUI 标准 IMAGE 格式 [B*T, H, W, C]。
    # （与 MiniMaxH3MotionContextDiskFinalDecode 做法一致，不要 permute！）
    if decoded.dim() == 5:
        frames = decoded.reshape(-1, decoded.shape[-3], decoded.shape[-2], decoded.shape[-1])
    else:
        frames = decoded

    audio = None
    audio_stream = _audio_stream_from_latent(latent)
    if audio_stream is not None and audio_vae is not None:
        try:
            decoded = audio_vae.decode(audio_stream)
            # H3 audio VAE decode 返回 [B, L, C]（最后一维是声道），
            # 需要转成 ComfyUI 标准 AUDIO 格式 [B, C, L]。
            # （与 MiniMaxH3MotionContextDiskFinalDecode 的 .movedim(-1,1) 做法一致）
            if isinstance(decoded, torch.Tensor):
                if decoded.dim() == 3 and decoded.shape[-1] in (1, 2):
                    decoded = decoded.movedim(-1, 1)
                sr = int(getattr(audio_vae, "audio_sample_rate_output",
                             getattr(audio_vae, "audio_sample_rate", 32000)))
                audio = {"waveform": decoded, "sample_rate": sr}
            else:
                audio = decoded
        except Exception:
            audio = None

    return frames, audio


def _smart_head_trim(wav, sr, trim, search_s=0.75):
    """从音频头部裁掉 trim 个样本，但裁切点选在开头 search_s 秒内能量最低处。

    盲切会削掉模型放在段首的音节/字头（接缝 'blip'）。在静音窗口内裁掉同样
    数量的样本可保留所有起始音，裁切前音频最多晚 trim/sr 秒，且该区域本身安静。
    移植自 H3-Multishot (one node)。
    """
    import torch
    n = wav.shape[-1]
    if n <= trim:
        return wav[..., :0]
    limit = min(n - trim, int(sr * search_s))
    if limit <= 1:
        return wav[..., trim:]
    mono = wav.float().abs()
    while mono.ndim > 1:
        mono = mono.mean(0)
    sq = mono[:limit + trim] ** 2
    cs = torch.cumsum(torch.cat([torch.zeros(1, device=sq.device), sq]), 0)
    win_energy = cs[trim:limit + trim] - cs[:limit]
    i = int(win_energy.argmin())
    if i > 0:
        print(f"[H3ClipChain] smart weld: seam cut moved {i / sr * 1000:.0f}ms "
              f"into the head (quietest gap), word onsets preserved", flush=True)
    return torch.cat([wav[..., :i], wav[..., i + trim:]], dim=-1)


def _evict_text_encoder(clip, model):
    """采样前把文本编码器卸载到 CPU 并清理显存，给 DiT 腾空间。

    ClipChain 每镜都要重新编码提示词（TE 会在下一镜自动加载），
    所以编码完成后、采样前卸载是安全的。TE 与 DiT 在不同设备时跳过
    （卸载只会强制每镜重载，更慢）。移植自 H3-Multishot (one node)。
    """
    import comfy.model_management as mm
    try:
        te_dev = getattr(clip.patcher, "load_device", None)
        dit_dev = getattr(model, "load_device", None)
        if (te_dev is not None and dit_dev is not None
                and str(te_dev) != str(dit_dev)):
            return  # 不同设备：TE 常驻，卸载只会每镜重载
        clip.patcher.model.to(mm.text_encoder_offload_device())
    except Exception as _e:
        print(f"[H3ClipChain] TE offload skipped: {_e}", flush=True)
    try:
        _dev = mm.get_torch_device()
        mm.free_memory(mm.get_total_memory(_dev) * 0.9, _dev)
        mm.soft_empty_cache()
    except Exception as _e:
        print(f"[H3ClipChain] VRAM purge skipped: {_e}", flush=True)


def _trim_clip(frames, audio, trim_frames, fps=24, smart=True):
    """从 clip 开头裁掉 trim_frames（Motion Context 重叠帧）。"""
    if trim_frames <= 0:
        return frames, audio

    trimmed_frames = frames[trim_frames:] if trim_frames < frames.shape[0] else frames[-1:]

    trimmed_audio = audio
    if audio is not None:
        sr = int(audio.get("sample_rate", 44100))
        trim_samples = int(round(trim_frames / fps * sr))
        waveform = audio.get("waveform")
        if waveform is not None and trim_samples < waveform.shape[-1]:
            trimmed_audio = dict(audio)
            if smart:
                # 模型生成音频：在段首安静窗口内找最静点，保留字头/音节起始音
                trimmed_audio["waveform"] = _smart_head_trim(waveform, sr, trim_samples)
            else:
                # audio_lock 源音频：盲切保持与视频帧精确对位（MV 对拍）
                trimmed_audio["waveform"] = waveform[..., trim_samples:]

    return trimmed_frames, trimmed_audio


def _merge_audio(audio_list, crossfade_ms=250):
    """拼接多个音频段，段间加等功率交叉淡入淡出避免爆音/突兀感。

    crossfade_ms: 交叉淡入淡出时长（毫秒），默认250ms。
    40ms 只够消"咔哒声"，接缝仍听得出；250ms 等功率曲线（cos/sin）
    让接缝处总能量恒定，听感平滑无拼接感。等功率曲线比线性 fade 更平滑。
    """
    valid = [a for a in audio_list if a is not None and a.get("waveform") is not None]
    if not valid:
        return None

    sr = int(valid[0].get("sample_rate", 44100))
    waveforms = []
    for a in valid:
        w = a.get("waveform")
        if w is not None:
            if w.dim() == 2:
                w = w.unsqueeze(0)
            waveforms.append(w)

    if not waveforms:
        return None

    max_ch = max(w.shape[1] for w in waveforms)
    normalized = []
    for w in waveforms:
        if w.shape[1] < max_ch:
            w = w.repeat(1, max_ch, 1)
        normalized.append(w)

    # 交叉淡入淡出拼接
    crossfade_samples = max(1, int(sr * crossfade_ms / 1000))
    merged = normalized[0]
    for i in range(1, len(normalized)):
        cur = normalized[i]
        cf = min(crossfade_samples, merged.shape[-1], cur.shape[-1])
        if cf > 1:
            # 前一段尾部淡出，后一段头部淡入
            t = torch.linspace(0, 1, cf, dtype=merged.dtype, device=merged.device)
            fade_out = torch.cos(t * 3.14159265 / 2)   # equal power
            fade_in = torch.sin(t * 3.14159265 / 2)
            merged_tail = merged[..., -cf:] * fade_out
            cur_head = cur[..., :cf] * fade_in
            merged = torch.cat([
                merged[..., :-cf],
                merged_tail + cur_head,
                cur[..., cf:],
            ], dim=-1)
        else:
            merged = torch.cat([merged, cur], dim=-1)

    return {"waveform": merged, "sample_rate": sr}


# ────────────────────────────────────────────────────────────
# H3 标准提示词字段顺序
# ────────────────────────────────────────────────────────────

# Ref2VA 全参考模式六字段（按此顺序拼接）
_H3_REF_FIELDS = [
    "subject_definitions",
    "summary",
    "retention_analysis",
    "detailed_description",
    "overall_soundscape",
    "non_diegetic_music",
]

# 基础模式三字段
_H3_BASE_FIELDS = [
    "integrated_multimodal_description",
    "overall_soundscape",
    "non_diegetic_music",
]

# 所有已知字段（用于检测对象格式）
_H3_ALL_KNOWN_FIELDS = set(_H3_REF_FIELDS + _H3_BASE_FIELDS)

# Extender / 元数据字段（拼提示词时跳过）
_H3_META_FIELDS = {"_mode", "id", "clip_id", "seed", "duration", "validated",
                   "frame_count", "start_frame", "end_frame"}


def _safe_int(v, default=None):
    """宽容转 int。"""
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v, default=None):
    """宽容转 float。"""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _assemble_h3_prompt(fields_dict):
    """把字段 dict 拼成 H3 标准格式的纯文本提示词。

    优先按 Ref2VA 六字段顺序，然后是基础三字段，最后是其他未知字段。
    每个字段格式为 "field_name: value"，字段之间空一行。
    Extender 元数据字段（id/seed/duration/validated 等）自动跳过。
    """
    ordered = []
    used = set()

    # 先按已知顺序排列（REF/BASE 两表有重叠字段，用 used 去重）
    for field in _H3_REF_FIELDS + _H3_BASE_FIELDS:
        if field in used:
            continue
        if field in fields_dict and str(fields_dict[field]).strip():
            ordered.append((field, str(fields_dict[field]).strip()))
            used.add(field)

    # 再排未知字段
    for field, value in fields_dict.items():
        if field in _H3_META_FIELDS:
            continue
        if field not in used and field != "_mode" and str(value).strip():
            ordered.append((field, str(value).strip()))

    if not ordered:
        return ""

    return "\n\n".join(f"{name}: {value}" for name, value in ordered)


# ────────────────────────────────────────────────────────────
# 分段提示词解析
# ────────────────────────────────────────────────────────────

def _collect_segment_prompts(raw):
    """把 Autogrow 输入（dict {prompt_0: "...", ...}）整理成有序非空提示词列表。

    支持字符串键（prompt_N）与整数键；按索引升序返回，空值跳过。
    """
    if not raw:
        return []
    if not isinstance(raw, dict):
        return []
    items = {}
    for key, val in raw.items():
        if val is None:
            continue
        text = str(val).strip()
        if not text:
            continue
        if isinstance(key, int):
            idx = key
        else:
            m = re.search(r"(\d+)\s*$", str(key))
            idx = int(m.group(1)) if m else len(items)
        items[idx] = text
    return [items[i] for i in sorted(items)]




def _strip_segment_wrap(text):
    """兼容 H3 Prompt Split 的 JSON 包装：["..."] 字符串数组取元素；
    [{"detailed_description":...}] 对象数组取首段拼装；"..." 字符串取本身；
    非 JSON / 解析失败原样返回。"""
    import json as _json
    t = (text or "").strip()
    if not t:
        return t
    if not (t.startswith("[") or (t.startswith("\"") and t.endswith("\""))):
        return t
    try:
        parsed = _json.loads(t)
    except (_json.JSONDecodeError, TypeError, ValueError):
        return t
    if isinstance(parsed, str):
        return parsed.strip()
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            try:
                assembled = _assemble_h3_prompt(first)
                if assembled and assembled.strip():
                    return assembled.strip()
            except Exception:
                pass
    return t


def _build_clips_from_segments(seg_list, seeds_text, base_seed):
    """动态接口（segment_prompts）专用：每路 = 一个完整 clip 段。

    不做行拆分——Writer 普通模式输出的多行六段式整路作为一段；
    避免 _parse_clips 的多行文本按行拆段把六段式拆碎。
    模式：第一路 standalone，后续 chain；seed 优先级同 _parse_clips。
    """
    import json as _json
    seeds = []
    if seeds_text and seeds_text.strip():
        st = seeds_text.strip()
        if st.startswith("["):
            try:
                parsed = _json.loads(st)
                if isinstance(parsed, list):
                    seeds = [int(x) for x in parsed if x is not None]
            except (_json.JSONDecodeError, TypeError, ValueError):
                pass
        if not seeds:
            seeds = [int(s.strip()) for s in st.split(",") if s.strip()]
    clips = []
    for i, text in enumerate(seg_list):
        prompt = _strip_segment_wrap(str(text).strip())
        if not prompt:
            continue
        first_frame = False
        if prompt.startswith("#continue"):
            prompt = prompt[len("#continue"):].strip()
            first_frame = True
        mode = "standalone" if i == 0 else "chain"
        if i == 0:
            first_frame = False  # 首段无前帧可接
        seed = seeds[i] if i < len(seeds) else base_seed + i
        clips.append({"index": i, "mode": mode, "prompt": prompt,
                      "seed": int(seed), "duration": None,
                      "first_frame": first_frame})
    return clips


def _parse_clips(prompts_text, seeds_text, base_seed):
    """解析分段配置 → list of dict。

    prompts 支持三种格式（自动检测）：
      - JSON 对象数组: [{"detailed_description": "...", "overall_soundscape": "..."}, ...]
                       每个对象可包含任意 H3 标准字段，自动拼成标准格式；
                       特殊字段 "_mode": "standalone" 标记独立模式。
      - JSON 字符串数组: ["prompt 1", "prompt 2", ...]（每段当作 detailed_description）
      - 多行文本: 每行一段；行首 "#" 标记独立模式（standalone）；
                  行首 "//" 为注释行，会被跳过；空行跳过。

    每个 clip dict 包含: index, mode, prompt, seed, duration
    （duration 秒；来自 JSON 对象内 "duration" 字段，None=不指定。
      frame_count 由外部 latent 决定，不在此解析）
    """
    import json as _json

    # ---- 解析 prompts ----
    clips_raw = []  # list of (prompt_text, mode_from_json)
    text = (prompts_text or "").strip()

    is_json = False
    if text.startswith("["):
        try:
            parsed = _json.loads(text)
            if isinstance(parsed, list):
                for item in parsed:
                    if item is None:
                        continue
                    if isinstance(item, dict):
                        # JSON 对象：提取 _mode / seed / duration；其余字段拼成 H3 标准提示词
                        mode = str(item.get("_mode", "")).strip().lower()
                        item_aff = False
                        if mode == "continue":
                            mode = "chain"
                            item_aff = True
                        if mode not in ("standalone", "chain"):
                            mode = ""
                        item_seed = _safe_int(item.get("seed"))
                        item_dur = _safe_float(item.get("duration"))
                        # Extender 兼容：{"prompt": "...", "seed": 123, "duration": 10} 直接可用
                        prompt_text = item.get("prompt")
                        if isinstance(prompt_text, str) and prompt_text.strip():
                            prompt = prompt_text.strip()
                        else:
                            prompt = _assemble_h3_prompt(item)
                        if prompt.strip():
                            clips_raw.append((prompt, mode, item_seed, item_dur, item_aff))
                    elif isinstance(item, str) and item.strip():
                        clips_raw.append((item.strip(), "", None, None, False))
                is_json = True
        except (_json.JSONDecodeError, TypeError, ValueError):
            pass

    if not is_json:
        for line in (prompts_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            clips_raw.append((line, "", None, None, False))

    if not clips_raw:
        return []

    # ---- 解析 seeds ----
    seeds = []
    if seeds_text and seeds_text.strip():
        st = seeds_text.strip()
        if st.startswith("["):
            try:
                parsed = _json.loads(st)
                if isinstance(parsed, list):
                    seeds = [int(x) for x in parsed if x is not None]
            except (_json.JSONDecodeError, TypeError, ValueError):
                pass
        if not seeds:
            seeds = [int(s.strip()) for s in st.split(",") if s.strip()]

    # ---- 构建 clips ----
    clips = []
    for i, (prompt, json_mode, item_seed, item_dur, raw_aff) in enumerate(clips_raw):
        # 确定模式
        if json_mode:
            mode = json_mode
        elif is_json:
            # JSON 格式：第一段独立，后续链式
            mode = "standalone" if i == 0 else "chain"
        else:
            # 多行文本：#continue = 链式接续（自动首帧）；# = 独立；无标记 = 链式（不接续）
            first_frame = False
            if prompt.startswith("#continue"):
                mode = "chain"
                prompt = prompt[len("#continue"):].strip()
                first_frame = True
            elif prompt.startswith("#"):
                mode = "standalone"
                prompt = prompt[1:].strip()
            else:
                mode = "chain"
            if i == 0:
                mode = "standalone"
                first_frame = False  # 首段无前帧可接
        # first_frame：多行文本分支已设；JSON 分支用 _mode=continue 标记（首段无前帧可接）
        if is_json:
            first_frame = False if i == 0 else bool(raw_aff)

        # seed 优先级：JSON 对象内 seed > seeds 列表 > base_seed 递增
        if item_seed is not None:
            seed = item_seed
        else:
            seed = seeds[i] if i < len(seeds) else base_seed + i

        clips.append({
            "index": i,
            "mode": mode,
            "prompt": prompt,
            "seed": int(seed),
            "duration": item_dur,  # 秒；None=不指定（Extender 格式兼容）
            "first_frame": first_frame,  # #continue：本段接续上一段尾帧（自动首帧）
        })

    return clips


# ────────────────────────────────────────────────────────────
# 主节点
# ────────────────────────────────────────────────────────────



# 中文引号对话 → <d> 音频块（no_subtitles 用）
_CN_QUOTE_OPEN = r'"\u201c\u300c\u300e'
_CN_QUOTE_CLOSE = r'"\u201d\u300d\u300f'
_CN_QUOTE_BODY = (
    r'[^"\u201c\u201d\u300c\u300d\u300e\u300f\n]{1,120}'
    r'[\u4e00-\u9fff][^"\u201c\u201d\u300c\u300d\u300e\u300f\n]{0,120}'
)
_CN_DIALOG_RE = re.compile(
    '([' + _CN_QUOTE_OPEN + '])(' + _CN_QUOTE_BODY + ')([' + _CN_QUOTE_CLOSE + '])'
)
_DIALOG_BLOCK_RE = re.compile(r'<d>.*?</d>', re.S)


def _cn_quotes_to_dialogue_blocks(text):
    """把引号内含中文的对话（如 她说：「下雨了，真冷。」 / "你凭什么删我东西"）
    包成 <d> 音频块：<d>原文</d>（audio only, no on-screen text）。
    只匹配含 CJK 的引号内容，英文模板引号不会误伤；已 <d> 包裹的对话不动。"""
    if not text:
        return text
    held = []
    def _hold(m):
        held.append(m.group(0))
        return "\x00D%d\x00" % (len(held) - 1)
    text = _DIALOG_BLOCK_RE.sub(_hold, text)
    def _rep(m):
        body = m.group(2).strip()
        if not body:
            return m.group(0)
        return "<d>%s</d> (audio only, no on-screen text)" % body
    text = _CN_DIALOG_RE.sub(_rep, text)
    for i, t in enumerate(held):
        text = text.replace("\x00D%d\x00" % i, t)
    return text


class H3ClipChainAV(io.ComfyNode):
    """H3 纯分段采样器：接收外部 Ref2VA 的 positive+latent，分段换提示词采样。"""

    @classmethod
    def define_schema(cls):
        sampler_names = list(comfy.samplers.SAMPLER_NAMES)
        scheduler_names = list(comfy.samplers.SCHEDULER_NAMES)
        default_sampler = "euler" if "euler" in sampler_names else sampler_names[0]
        default_scheduler = "simple" if "simple" in scheduler_names else scheduler_names[0]

        return io.Schema(
            node_id="H3ClipChainAV",
            display_name="H3 Clip Chain (micxin)",
            category="H3 helper/micxin",
            description=(
                "H3 纯分段采样器：接收外部 Ref2VA（如 H3 R2VA AIO）输出的 positive+latent，"
                "分段换提示词采样，Motion Context 接续，输出合并视频+音频。"
                "宽高长由外部 Ref2VA 节点控制，本节点不重复。\n"
                "闭环用法：H3 PromptWriter → H3 Prompt Split (micxin) → 本节点 segment_prompts "
                "（prompt_0/prompt_1/...），连接一个自动出现下一个空槽，可像 MiniMax H3 Extender "
                "一样继续增加 clip。"
            ),
            inputs=[
                # ---- 必须外部连接 ----
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("vae"),
                io.Vae.Input("audio_vae"),
                io.Conditioning.Input("positive"),
                io.Latent.Input("latent"),
                # ---- 分段提示词（普通 widget，也可外部连接） ----
                io.String.Input(
                    "prompts", multiline=True, optional=True,
                    extra_dict={"hidden": True},
                    default=(
                        '// H3 标准六字段格式（推荐）：每段一个 JSON 对象，字段自动拼成 H3 标准提示词\n'
                        '// 也可以用纯字符串数组或多行文本（每行一段，行首#独立模式）\n'
                        '[\n'
                        '  {\n'
                        '    "detailed_description": "[Shot 1] Live-action, cinematic, ...",\n'
                        '    "overall_soundscape": "Steady rain taps against the window...",\n'
                        '    "non_diegetic_music": "Sparse piano notes at a slow tempo..."\n'
                        '  },\n'
                        '  {\n'
                        '    "detailed_description": "[Shot 2] At 00:05.000, the camera cuts to...",\n'
                        '    "overall_soundscape": "...",\n'
                        '    "non_diegetic_music": "..."\n'
                        '  }\n'
                        ']'
                    ),
                    tooltip=(
                        "分段提示词，支持三种格式（自动检测）：\n"
                        "\n"
                        "【推荐】JSON 对象数组（H3 标准六字段）：\n"
                        '[{"detailed_description": "...", "overall_soundscape": "...", "non_diegetic_music": "..."}, ...]\n'
                        "可用字段（按 H3 标准顺序自动拼接）：\n"
                        "  subject_definitions / summary / retention_analysis（Ref2VA 全局字段）\n"
                        "  detailed_description（每段画面描述，最常用）\n"
                        "  integrated_multimodal_description（基础模式用）\n"
                        "  overall_soundscape / non_diegetic_music（音景和背景音乐）\n"
                        '  "_mode": "standalone" 标记独立模式（不接续前一段）\n'
                        "\n"
                        "JSON 字符串数组：[\"提示词1\", \"提示词2\"]（每段当作 detailed_description）\n"
                        "\n"
                        "多行文本：每行一段；行首 # = 独立模式；行首 // = 注释跳过\n"
                        "\n"
                        "第一段默认独立模式，后续默认链式接续。"
                    ),
                ),
                # ---- 闭环：来自 H3 Prompt Split (micxin) 的每 clip 提示词 ----
                io.Autogrow.Input(
                    "segment_prompts",
                    optional=True,
                    tooltip=(
                        "分段提示词（每 clip 一个）。连接 H3 Prompt Split (micxin) 的 "
                        "prompt_N 输出即送到对应 clip；连接一个自动出现下一个空槽，可继续"
                        "增加 clip（类似 MiniMax H3 Extender 的 clip 扩展）。\n"
                        "行首 # = 独立模式（不接续前一段）；行首 // = 该行跳过。\n"
                        "提供本输入时优先于上方 prompts 文本框。"
                    ),
                    template=io.Autogrow.TemplatePrefix(
                        input=io.String.Input(
                            "prompt",
                            multiline=True,
                            default="",
                            tooltip="单个 clip 的分段提示词。",
                        ),
                        prefix="prompt_",
                        min=0,
                        max=H3CC_MAX_SEGMENT_PROMPTS,
                    ),
                ),
                # ---- 节点内部 widget ----
                io.String.Input("seeds", optional=True, default="",
                                tooltip="每段的随机种子，逗号分隔（如 123,456,789）。留空则自动从 base_seed 递增。"),
                io.Int.Input("base_seed", optional=True, default=0, min=0, max=0xffffffffffffffff,
                             tooltip="基础随机种子。seeds 留空时，每段在此基础上递增（段1=base_seed, 段2=base_seed+1...）。"),
                io.Combo.Input("context_length", optional=True, options=["0", "5", "22", "39", "56"], default="22",
                               tooltip=("链式接续时，从前一段注入多少帧作为 Motion Context（运动上下文）。\n"
                                        "0=不注入 latent 上下文（无拼接颗粒带；画面延续靠 auto_first_frame"
                                        " 首帧强条件，建议两者同开）。\n"
                                        "22≈1秒（推荐），5=短接续，39/56=长接续。\n"
                                        "独立模式（行首#）的分段不使用此参数。")),
                io.Int.Input("audio_context_length", optional=True, default=0, min=0, max=240,
                             tooltip="链式接续时注入的音频上下文帧数。0=不注入音频上下文（推荐），>0=从前一段继承音频。"),
                io.Int.Input("steps", optional=True, default=8, min=1, max=10000,
                             tooltip="每段的采样步数。越高画质越细腻但越慢。\n推荐 8-12（快速预览），16-20（高质量）。"),
                io.Combo.Input("sampler_name", optional=True, options=sampler_names, default=default_sampler,
                               tooltip="采样器。euler 通用，dpmpp_2m 画质更好。"),
                io.Combo.Input("scheduler", optional=True, options=scheduler_names, default=default_scheduler,
                               tooltip="调度器。simple 通用，karras 更稳定。"),
                io.Float.Input("denoise", optional=True, default=1.0, min=0.01, max=1.0, step=0.01,
                               tooltip="去噪强度。1.0=完全重采样（推荐），<1.0=保留更多原始 latent。"),

                io.String.Input("keyframe_paths", multiline=True, optional=True, default="",
                    extra_dict={"hidden": True},
                    tooltip=(
                        "每段关键帧引导（Add Guide），JSON 数组格式，每段一个关键帧路径字符串。\n"
                        "每段内每行一个关键帧：media_path|audio_path|frame_idx|media_start|media_end|audio_start|audio_end\n"
                        "frame_idx 相对于该段（0=段首帧）。\n"
                        "示例：[\n"
                        '  "path/kf1.png||0||||\\npath/kf2.png||73||||",\n'
                        '  "path/kf3.png||0||||\\npath/kf4.png||61||||"\n'
                        "]\n"
                        "留空=不注入关键帧（使用外部 positive 里已有的关键帧）。")),
                io.String.Input("segment_durations", optional=True, default="",
                    tooltip=(
                        "每段时长列表（秒），逗号分隔。如 \"10,5\" = 段1=10秒，段2=5秒。\n"
                        "留空=全部使用外部 latent 的时长（由 H3 PromptWriter 控制）。\n"
                        "帧数自动对齐 H3 约束（length % 17 == 5）。")),
                io.Combo.Input("audio_lock_mode", optional=True,
                    options=["off", "full"], default="off",
                    tooltip=(
                        "音频锁定（需在关键帧里上传音频文件）。\n"
                        "off=不锁定，模型生成音频，关键帧音频作引导（口型跟随）。\n"
                        "full=整轨自定义：解码后把源音频波形写回（非锚定段静音），"
                        "同时保留音频引导让口型跟随源音频。\n"
                        "参考 AIO H3ModelLoader 的同名功能。")),
                io.Int.Input("start_segment", optional=True, default=0, min=0, max=999,
                    extra_dict={"hidden": True},
                    tooltip="起始段索引（0开始）。跳过前面的段，从指定段开始生成。0=从第一段开始。"),
                io.Int.Input("preview_segments", optional=True, default=0, min=0, max=999,
                    extra_dict={"hidden": True},
                    tooltip="只生成前 N 段（预览用）。0=生成全部段。设为1=只跑第一段看效果。"),
                io.Combo.Input("auto_first_frame", optional=True, options=["off", "on"], default="off",
                    tooltip=(
                        "自动接续：把上一段采样出的尾帧作为本段首帧参考图，通过 AddGuide 注入 frame 0"
                        "（H3 I2VA 强条件），段间接缝画面连续不花屏。\n"
                        "off（默认）：不自动接续——切镜（每段不同镜头）时首帧延续会锁死错误画面，"
                        "所以默认关。\n"
                        "on：对 chain 段（第 2 段起）自动生效——把上一段采样出的尾帧注入"
                        "本段 frame 0，无需手写标记；段提示词行首加 #continue 仍兼容"
                        "（显式标记独立段也接续）。\n"
                        "首段无前帧、或本段已配置首帧图（Add Guide）时自动跳过。")),
                                io.Boolean.Input("no_subtitles", default=True,
                    tooltip=(
                        "自动在每段提示词末尾追加字幕禁令：No subtitles, no on-screen "
                        "text, no burned-in captions。H3 模型容易把 <d> 对话/提示词里的"
                        "文字渲染成画面内字幕（且经常出现错字）。\n"
                        "开（默认）：每段自动追加禁令，画面内文字显著减少。\n"
                        "关：完全按原始提示词生成（字幕由模型自行决定）。"),
                ),
            ],
            outputs=[
                io.Image.Output(display_name="images"),
                io.Audio.Output(display_name="audio"),
                io.String.Output(display_name="report"),
                io.Int.Output(display_name="clip_count"),
                io.String.Output(display_name="build"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, vae, audio_vae, positive, latent, prompts,
                segment_prompts=None,
                seeds="", base_seed=0, context_length=22, audio_context_length=0,
                steps=8, sampler_name="euler", scheduler="simple", denoise=1.0,

                keyframe_paths="", segment_durations="", audio_lock_mode="off",
                start_segment=0, preview_segments=0,
                auto_first_frame="off",
                no_subtitles=True):

        # 1. 解析分段：优先使用 H3 Prompt Split 的每 clip 输入（Autogrow）
        split_prompts = _collect_segment_prompts(segment_prompts)
        seg_source = "prompts 文本框"
        if split_prompts:
            # 动态接口每路 = 一个完整段（多行六段式整路作为一段，不做行拆分）
            clips = _build_clips_from_segments(split_prompts, seeds, base_seed)
            seg_source = "H3 Prompt Split 输入 (segment_prompts)"
            print(f"[H3ClipChain] 使用 H3 Prompt Split 输入: {len(clips)} 个 clip "
                  f"(每路=整段, 不按行拆分)", flush=True)
        else:
            if not prompts or not str(prompts).strip():
                raise ValueError(
                    "H3ClipChain: 没有分段提示词。请在 prompts 文本框填写，"
                    "或连接 H3 Prompt Split (micxin) 到 segment_prompts。")
            clips = _parse_clips(prompts, seeds, base_seed)
        if not clips:
            raise ValueError("H3ClipChain: 没有有效的分段提示词。")

        build_str = f"H3ClipChain-micxin-20260904 (max {H3CC_MAX_SEGMENT_PROMPTS} clips)"

        # 1b. 解析每段时长（秒）
        seg_durations = []
        if segment_durations and str(segment_durations).strip():
            for part in str(segment_durations).split(","):
                part = part.strip()
                if part:
                    try:
                        seg_durations.append(float(part))
                    except ValueError:
                        pass

        # 从外部 latent 获取基准帧数
        base_video = _video_stream_from_latent(latent)
        base_frame_count = _pixel_frames(base_video.shape[2])

        # 为每段计算帧数（优先级：segment_durations 文本框 > clip 内 duration > 外部 latent 基准）
        seg_frame_counts = []
        for i, c in enumerate(clips):
            if i < len(seg_durations) and seg_durations[i] > 0:
                fc = _resolve_length(seg_durations[i])
            elif c.get("duration") and c["duration"] > 0:
                fc = _resolve_length(c["duration"])
            else:
                fc = base_frame_count
            seg_frame_counts.append(fc)

        # 1c. 起始段 & 预览段数过滤（hidden widget，面板不显示；可由可视化控制/API 传值）
        actual_clips = []
        actual_indices = []
        for i, c in enumerate(clips):
            if i < start_segment:
                continue
            if preview_segments > 0 and len(actual_clips) >= preview_segments:
                break
            actual_clips.append(c)
            actual_indices.append(i)

        if not actual_clips:
            raise ValueError(
                f"H3ClipChain: 起始段 {start_segment + 1} 超出范围（当前共 {len(clips)} 段，"
                f"来源: {seg_source}），或预览段数为0。\n"
                f"段数 = 提示词段数：连了几路 H3 Prompt Split 的 prompt_N 就有几段，"
                f"或者在 prompts 文本框里每行写一段。")

        report_lines = [f"H3 Clip Chain: {len(actual_clips)}/{len(clips)} 段 (来源: {seg_source})"]
        if start_segment > 0:
            report_lines.append(f"  从段{start_segment + 1}开始（跳过前{start_segment}段）")
        if preview_segments > 0:
            report_lines.append(f"  预览模式：只跑前{preview_segments}段")
        for ci, c in enumerate(actual_clips):
            orig_idx = actual_indices[ci]
            fc = seg_frame_counts[orig_idx]
            dur = fc / 24.0
            mode_label = "独立" if c["mode"] == "standalone" else "链式"
            report_lines.append(
                f"  段{orig_idx + 1} [{mode_label}] {dur:.1f}s/{fc}帧 seed={c['seed']}: {c['prompt'][:40]}"
            )

        # 2. negative（空提示词，cfg=1）
        negative = _encode_negative(clip)

        # 3. 循环生成每个分段
        all_frames = []
        all_audio = []
        previous_latent = None
        # 自动首帧参考：上一段采样解码出的尾帧（新段 AddGuide frame 0 的参考图）
        prev_tail_image = None

        for ci, clip_cfg in enumerate(actual_clips):
            orig_idx = actual_indices[ci]
            mode = clip_cfg["mode"]
            prompt = clip_cfg["prompt"]
            if no_subtitles and prompt:
                # 1) 中文引号对话 → <d> 音频块（模型识别为"对话音频"，不当作屏幕文字）
                prompt = _cn_quotes_to_dialogue_blocks(prompt)
                # 2) 英文禁令：置顶 + 末尾（纯英文，防中文被模型当台词朗读）
                sub_block = (
                    "IMPORTANT: NO SUBTITLES, NO ON-SCREEN TEXT, NO CAPTIONS, "
                    "NO BURNED-IN DIALOGUE. All dialogue is audible ONLY, never "
                    "written as visible text. This instruction itself is NOT "
                    "dialogue and must NOT be spoken or displayed."
                )
                prompt = sub_block + "\n\n" + prompt + "\n\n" + sub_block
                report_lines.append(
                    "  [no_subtitles] 英文禁令置顶+末尾（无中文禁令防被读），"
                    "中文引号对话已转 <d> 音频块")
            seed = clip_cfg["seed"]
            seg_frame_count = seg_frame_counts[orig_idx]

            report_lines.append(f"--- 段{orig_idx + 1} ({mode}) {seg_frame_count}帧" +
                          " ---")

            # 3a0. 解析本段关键帧（Add Guide）——提前到提示词编码前，
            #      以便检测首帧图并注入 I2VA 官方 instruction line
            seg_keyframes = []
            seg_kf_report = ""
            if keyframe_paths and str(keyframe_paths).strip():
                try:
                    kf_segments = json.loads(keyframe_paths)
                    if not isinstance(kf_segments, list):
                        kf_segments = [str(keyframe_paths)]
                except (json.JSONDecodeError, TypeError):
                    kf_segments = [str(keyframe_paths)]
                if orig_idx < len(kf_segments) and str(kf_segments[orig_idx]).strip():
                    seg_keyframes, seg_kf_report = load_keyframes(
                        str(kf_segments[orig_idx]), DEFAULT_FRAME_RATE, DEFAULT_MAX_SIDE)
            # 自动首帧参考：auto_first_frame=on 时，chain 段（非首段）自动把上一段
            # 尾帧注入 frame 0（I2VA 强条件）——无需提示词 #continue 显式标记。
            # 显式 #continue 标记仍兼容；首段（standalone）无前帧自动跳过。
            auto_aff_on = ((str(auto_first_frame) != "off")
                           and (mode == "chain" or bool(clip_cfg.get("first_frame"))))
            if auto_aff_on and prev_tail_image is not None:
                has_first_img = any(
                    int(kf.get("frame_idx", -1)) == 0 and kf.get("image") is not None
                    for kf in seg_keyframes)
                if not has_first_img:
                    seg_keyframes = ([{"frame_idx": 0, "image": prev_tail_image, "audio": None}]
                                     + list(seg_keyframes))
                    report_lines.append("  自动首帧: 注入上一段尾帧图 (I2VA 强条件接续, frame 0)")
            first_frame_has_image = any(
                kf.get("image") is not None and int(kf.get("frame_idx", -1)) == 0
                for kf in seg_keyframes)

            # 3a. 用分段提示词重新编码 positive，继承外部 positive 的参考信息
            #     关键帧 resolved_frame_index 按段偏移，只保留当前段范围内的关键帧
            seg_positive = _reencode_prompt_with_ref(
                clip, prompt, positive,
                segment_idx=orig_idx, frames_per_segment=seg_frame_count,
                first_frame_has_image=first_frame_has_image,
            )

            # 3b. 创建该段时长的 latent（支持自定义每段时长）
            #     时长补偿：chain 段会注入前段尾部 trim 帧做接续（采样后裁掉），
            #     所以采样帧数 = 目标净帧 + trim，裁掉重叠后净输出恰好 = 目标帧，
            #     总时长 = 每段秒数之和（对齐 H3 length % 17 == 5）
            seg_trim_expected = 0
            if mode == "chain" and previous_latent is not None:
                seg_trim_expected = _motion_context_trim(previous_latent, int(context_length))
            fc_total = seg_frame_count + seg_trim_expected

            trim = 0
            if fc_total == base_frame_count:
                seg_latent = latent.copy()
            else:
                seg_latent = _create_segment_latent(latent, fc_total)
                if seg_trim_expected > 0:
                    report_lines.append(f"  时长补偿: 采样 {fc_total} 帧, trim {seg_trim_expected} 后净 {seg_frame_count} 帧")
                else:
                    report_lines.append(f"  自定义时长: 创建 {seg_frame_count}帧 latent (latent_t={_pixel_to_latent_t(seg_frame_count)})")

            # 3c. 段间接续：Motion Context
            if mode == "chain" and previous_latent is not None:
                seg_positive, trim = _apply_motion_context(
                    seg_positive, seg_latent, previous_latent,
                    int(context_length),
                    audio_context_length=audio_context_length,
                    audio_vae=audio_vae,
                )
                report_lines.append(f"  Motion Context: 注入前一段最后{trim}帧")
                # _apply_motion_context 已把需要的 latent block .clone() 进 keyframes，
                # previous_latent 不再被引用，立即释放 GPU 显存，避免多段跑爆显存。
                # （参考 MiniMax H3 Extender：采样后立即 del sampled, positive, latent）
                del previous_latent
                previous_latent = None
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            # 3c2b. 每段关键帧注入（Add Guide）——seg_keyframes 已在 3a0 解析
            if seg_keyframes:
                kf_injected = 0
                kf_img_count = sum(1 for kf in seg_keyframes if kf.get("image") is not None)
                kf_aud_count = sum(1 for kf in seg_keyframes if kf.get("audio") is not None)
                # audio_lock 模式下跳过 AddGuide 音频注入（音频已靠波形替换锁定，
                # 音频 guide 会干扰视频生成导致画面糊——backup 历史实测结论）
                skip_audio_guide = (audio_lock_mode != "off")
                for kf in seg_keyframes:
                    if kf["frame_idx"] >= seg_frame_count:
                        report_lines.append(f"  关键帧跳过: frame_idx={kf['frame_idx']} 超出该段{seg_frame_count}帧范围")
                        continue
                    _img = kf["image"]
                    _aud = None if skip_audio_guide else kf["audio"]
                    # audio_lock 模式下纯音频关键帧（无图）跳过 AddGuide——
                    # 音频已靠 post_sample 锁死，AddGuide 传 image=None+audio=None 会报错
                    if skip_audio_guide and _img is None and kf.get("audio") is not None:
                        report_lines.append(f"  关键帧跳过AddGuide(纯音频,由audio_lock锁定): frame_idx={kf['frame_idx']}")
                        continue
                    if _img is None and _aud is None:
                        report_lines.append(f"  关键帧跳过(无图无音): frame_idx={kf['frame_idx']}")
                        continue
                    seg_positive = MiniMaxH3AddGuide.execute(
                        positive=seg_positive, latent=seg_latent,
                        frame_idx=kf["frame_idx"],
                        vae=vae, audio_vae=audio_vae,
                        image=_img, audio=_aud,
                    )[0]
                    kf_injected += 1
                _guide_note = " (音频guide已跳过,由audio_lock锁定)" if skip_audio_guide and kf_aud_count > 0 else ""
                report_lines.append(f"  关键帧: 注入 {kf_injected}/{len(seg_keyframes)} 帧 (图{kf_img_count}/音{kf_aud_count}){_guide_note}")
                if seg_kf_report and seg_kf_report.strip():
                    report_lines.append(f"    {seg_kf_report.strip()}")

            # 3d. 采样（先卸载文本编码器 + 清理显存，给 DiT 腾空间）
            _evict_text_encoder(clip, model)
            sampled = _sample_h3(
                model, seg_positive, negative, seg_latent, seed, steps,
                sampler_name=sampler_name, scheduler=scheduler, denoise=denoise,
            )

            # 3c2c. 收集音频关键帧（用于解码后波形层面替换；命中缓存也照常）
            audio_kfs_for_post = []
            if audio_lock_mode != "off" and seg_keyframes:
                audio_kfs_for_post = [kf for kf in seg_keyframes if kf.get("audio") is not None]
                if audio_kfs_for_post:
                    report_lines.append(f"  音频锁定({audio_lock_mode}): 收集 {len(audio_kfs_for_post)} 段音频关键帧，解码后波形替换")

            # 3d2. （post_sample latent 替换已移除，改在解码后波形层面替换，避免 zeros latent decode 出噪声）

            # 3e. 解码
            frames, audio = _decode_av_latent(sampled, vae, audio_vae)
            # 自动首帧参考：保留本段尾帧（float32 [1,H,W,C]，与 AddGuide 期待格式一致）
            if frames is not None and frames.shape[0] > 0:
                prev_tail_image = frames[-1:].clone()

            # 3e2. 音频锁定（波形层面）—— full 模式下非锚定段静音，锚定段用源音频波形。
            # 不在 latent 层面用 zeros（zeros latent 直接 decode 会出噪声），而是在波形层面填零（真静音）。
            if audio_lock_mode == "full" and audio_kfs_for_post and audio is not None:
                try:
                    import torchaudio.functional as taF
                    sr = int(audio.get("sample_rate", 32000))
                    waveform = audio["waveform"].clone()  # [B, C, L]
                    total_samples = waveform.shape[-1]
                    # 全静音
                    waveform.zero_()
                    replaced = 0
                    for kf in audio_kfs_for_post:
                        src_audio = kf.get("audio")
                        if src_audio is None:
                            continue
                        src_wave = src_audio["waveform"]  # [B, C, L]
                        src_sr = int(src_audio.get("sample_rate", sr))
                        if src_sr != sr:
                            src_wave = taF.resample(src_wave, src_sr, sr)
                        # 帧→样本（H3 固定 24fps）
                        start_sample = int(round(kf["frame_idx"] / 24.0 * sr))
                        if start_sample < 0:
                            start_sample = total_samples + start_sample
                        if start_sample >= total_samples:
                            continue
                        use = min(src_wave.shape[-1], total_samples - start_sample)
                        if use <= 0:
                            continue
                        # 对齐声道数
                        if src_wave.shape[1] != waveform.shape[1]:
                            if src_wave.shape[1] == 1 and waveform.shape[1] == 2:
                                src_wave = src_wave.repeat(1, 2, 1)
                            elif src_wave.shape[1] == 2 and waveform.shape[1] == 1:
                                src_wave = src_wave[:, :1, :]
                        waveform[..., start_sample:start_sample + use] = src_wave[..., :use]
                        replaced += 1
                    audio = {"waveform": waveform, "sample_rate": sr}
                    report_lines.append(f"  音频锁定(波形): full模式，{replaced}段源音频写入，其余静音，总{total_samples}样本@{sr}Hz")
                except Exception as e:
                    report_lines.append(f"  音频锁定(波形)失败: {e}")

            # 3f. 裁剪重叠帧
            frames, audio = _trim_clip(frames, audio, trim, _DEFAULT_FPS,
                                    smart=(audio_lock_mode == "off"))

            report_lines.append(f"  生成: {list(frames.shape)} 帧, trim={trim}")

            # 3g. 拼接（fp16 存帧省一半内存，合并后由下游转回）
            seg_frames_cpu = frames.detach().cpu().half()

            # 帧级 blend：与上一段尾部做线性混合，
            # 消除拼接点"跳变感"（band = 重叠帧数）
            if trim > 0 and all_frames:
                prev_part = all_frames[-1]
                band = min(trim, int(prev_part.shape[0]), int(seg_frames_cpu.shape[0]))
                if band > 0:
                    _w = torch.linspace(1.0, 0.0, band, dtype=seg_frames_cpu.dtype,
                                        device=seg_frames_cpu.device).view(-1, 1, 1, 1)
                    prev_tail = prev_part[-band:].to(seg_frames_cpu.dtype)
                    new_head = seg_frames_cpu[:band]
                    prev_part[-band:] = _w * prev_tail + (1.0 - _w) * new_head
                    report_lines.append(f"  Blend: 接缝 {band} 帧线性混合")

            all_frames.append(seg_frames_cpu)
            if audio is not None:
                all_audio.append(audio)

            # 3h. 保存 previous_latent（给下一个链式分段用）
            previous_latent = sampled

            # 释放显存
            del seg_positive, seg_latent, sampled, frames
            if audio is not None:
                del audio
            torch.cuda.empty_cache() if torch.cuda.is_available() else None

        # 4. 合并所有帧（单缓冲 copy_，避免 torch.cat 双倍峰值内存）
        final_frames = None
        if all_frames:
            _n = sum(int(_p.shape[0]) for _p in all_frames)
            final_frames = torch.empty(
                (_n,) + tuple(all_frames[0].shape[1:]),
                dtype=all_frames[0].dtype, device=all_frames[0].device)
            _o = 0
            for _i in range(len(all_frames)):
                _p = all_frames[_i]
                final_frames[_o:_o + _p.shape[0]].copy_(_p)
                _o += int(_p.shape[0])
                all_frames[_i] = None
                del _p
        if final_frames is not None:
            report_lines.append(f"=== 合并: {list(final_frames.shape)} 帧 ===")

        # 5. 合并音频
        final_audio = _merge_audio(all_audio) if all_audio else None
        if final_audio is not None:
            report_lines.append(f"音频: {list(final_audio['waveform'].shape)} @ {final_audio['sample_rate']}Hz")
        else:
            report_lines.append("音频: 无")

        report = "\n".join(report_lines)
        return io.NodeOutput(final_frames, final_audio, report, len(clips), build_str)
