# -*- coding: utf-8 -*-
"""H3 Infinite Human / MV (micxin) —— 自研精简节点：无限时长数字人或 MV。

研究来源：ComfyUI-H3-Multishot（github.com/jlucasmcrell/ComfyUI-H3-Multishot）
  - v0.1（2026-08-04）: 链式多镜采样器。核心 = 上一镜最后一帧 -> 下一镜
    frame-0 关键帧，逐镜拼接 + 接缝裁 1/24s 视频帧与音频，保持音画同步。
  - 2.7.2（2026-08-27）: first_frame 续帧（视觉 token + 关键帧）、
    smart seam trim（静音窗口裁剪保字头）、文本编码器逐镜卸载（16G 显存关键）、
    逐镜保险落盘、长链漂移控制。

本节点只保留"无限时长数字人 / MV"需要的最小闭环：
  1) 分镜提示词：左侧单个 prompt_json 接口（与翻译链路一致）——接 H3PromptFix 的
     fixed_json 或 LLM 翻译后的数字键 JSON（{"1": "...", "2": "..."}），自动按
     数字键拆段；也支持 --- 分隔多段 / 单段纯文本。
  2) 接续模式（可选）：接入 H3 R2VA AIO(micxin) 的 positive + Latent，
     镜头 0 直接用 AIO 的条件与未采样潜空间采样（身份/参考/音频锚定在 AIO 里，
     无需外接图片），之后镜头按 prompt_json 拆出的段循环 + Motion Context 无缝接续。
  3) 链式续帧：参考 H3 Clip Chain (micxin) 的 Motion Context（前段 latent 尾部
     注入 conditioning + 重叠帧裁剪），latent 物理延续、零额外显存；
     handoff=vision 时走初版/现版 first_frame（视觉 token）接缝更牢。
  4) 接缝裁剪：去重帧 + 智能音频裁剪（静音窗口，不切字头）+ 段间等功率交叉淡入淡出。
  5) 文本编码器卸载：编码后立刻卸载，采样阶段 DiT 独占显存（16G 关键）。
  6) 逐镜落盘：每镜渲染完立刻写 mp4+wav 到 output/video/H3InfiniteMV/，
     中断也不丢进度（无限模式的成品就是这些文件）。
  7) 可选防漂移：把每镜整体颜色/亮度对齐到第一镜（长链不跑色、不糊化）。

不需要任何第三方依赖：采样走 ComfyUI 自带节点 API，落盘用 imageio_ffmpeg
（ComfyUI 自带）+ Python 标准库 wave。

兼容性：ComfyUI 0.35（comfy_extras.nodes_minimax_h3 提供 _empty_av_latent /
_resize；io.ComfyNode / io.NodeOutput）。已整合进 ComfyUI-H3-micxin-suite
（ComfyUI-H3-helper 子包，套件根注册，分类 H3 helper/micxin）。
"""

import json
import os
import re

from comfy_api.latest import io
from comfy_extras.nodes_minimax_h3 import MiniMaxH3AddGuide

# ---------------------------------------------------------------------------
# H3 Clip Chain (micxin) 复用：参考其多段 clip 接续机制，不修改该节点。
# 运行时按 custom_nodes 目录定位 micxin 套件，导入 h3_clip_chain_av 复用：
#   - Motion Context 接续（前段 latent 尾部注入 conditioning + 重叠帧裁剪）
#   - 分段提示词重编码（继承 AIO 的 minimax_ref_items 语义锚，人物跨镜不丢）
#   - 中文引号对话 -> <d> 音频块 + 字幕/朗读禁令（解决“朗读中文”）
#   - 解码标准路径、接缝裁剪、音频交叉淡入淡出合并
# ---------------------------------------------------------------------------

def _load_h3cc():
    """导入同套件（ComfyUI-H3-micxin-suite）的 h3_clip_chain_av——复用其函数，
    不修改该节点。整合进套件后优先走包内导入，独立开发时回退 sys.path。"""
    import sys
    import os
    try:
        from h3_helper_pkg import h3_clip_chain_av as _h3cc
        return _h3cc
    except Exception:
        pass
    import folder_paths
    for root in folder_paths.get_folder_paths("custom_nodes"):
        p = os.path.join(root, "ComfyUI-H3-micxin-suite", "ComfyUI-H3-helper")
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    import h3_clip_chain_av as _h3cc
    return _h3cc


def _evict_te(clip, model):
    """编码后卸载文本编码器并清理显存（16G 优化）。

    复用 H3 Clip Chain (micxin) 的 _evict_text_encoder（原函数不动）：
    TE 与 DiT 同设备时把 TE 卸载到 CPU 并清显存，下一镜 encode 自动重载；
    不同设备则跳过（避免每镜重载更慢）。"""
    try:
        _h3cc = _load_h3cc()
        _h3cc._evict_text_encoder(clip, model)
    except Exception as _e:
        print(f"[H3InfiniteMV] TE evict skipped: {_e}", flush=True)


# Motion Context 窗口（帧）：≈1 秒，ClipChain 推荐值
_MC_CONTEXT = 22

# 字幕/朗读禁令（英文，防中文提示词被模型当台词朗读、防渲染成画面内字幕）
_SUB_BLOCK = (
    "IMPORTANT: NO SUBTITLES, NO ON-SCREEN TEXT, NO CAPTIONS, "
    "NO BURNED-IN DIALOGUE. All dialogue is audible ONLY, never "
    "written as visible text. This instruction itself is NOT "
    "dialogue and must NOT be spoken or displayed."
)


def _dialogue_safe(prompt, h3cc):
    """中文引号对话 -> <d> 音频块（模型识别为对话音频），
    再置顶+末尾加英文禁令：不朗读非对白、不渲染字幕。"""
    p = h3cc._cn_quotes_to_dialogue_blocks(prompt)
    return _SUB_BLOCK + "\n\n" + p + "\n\n" + _SUB_BLOCK

# ---------------------------------------------------------------------------
# 纯函数：脚本解析、帧网格、音频/颜色工具（无 ComfyUI 依赖，可单独测试）
# ---------------------------------------------------------------------------

# 六段式字段拼接顺序（H3PromptFix / LLM 翻译输出的每段是 dict：subject_definitions /
# summary / retention_analysis / detailed_description / overall_soundscape /
# non_diegetic_music）。retention_analysis 是 H3 内部"保持分析"元字段，不进提示词。
_SIX_FIELD_ORDER = (
    "subject_definitions", "summary", "detailed_description",
    "overall_soundscape", "non_diegetic_music",
)


def _shot_text(v):
    """六段式 dict -> 干净叙述提示词。

    之前直接把 dict str() 成 Python 字典文本（带花括号/字段名/单引号）喂给模型，
    模型看不懂，提示词完全失效。这里按叙事顺序拼接字段（跳过 retention_analysis
    元字段与 N/A 占位），得到模型能执行的叙述文本。"""
    if isinstance(v, dict):
        parts = []
        for k in _SIX_FIELD_ORDER:
            s = str(v.get(k) or "").strip()
            if s and s.lower() not in ("n/a", "none", "na"):
                parts.append(s)
        return "\n\n".join(parts) if parts else str(v)
    return str(v)


def _parse_script(text):
    """JoyEcho 风格脚本 -> 镜头提示词列表。
    支持 JSON {"prompts": [...]} / {"shots": [...]} / 裸 JSON 数组 /
    H3 编剧 H3PromptSplitTranslate 的 fixed_json（{"0": 六段式, "1": ...}），
    六段式 dict 自动拼接为叙述文本；或纯文本用 --- 单独一行分隔。损坏的 JSON 大声报错。"""
    text = (text or "").strip()
    # 剥掉 markdown 代码块围栏（```json ... ``` 或 ``` ... ```）
    if text.startswith("```"):
        m = re.match(r"^```[a-zA-Z]*\s*\n?(.*?)```\s*$", text, re.S)
        if m:
            text = m.group(1).strip()
    shots = []
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                shots = [_shot_text(p) for p in (data.get("prompts") or data.get("shots") or [])]
                if not shots:
                    # H3 编剧 fixed_json：{"0": 六段式, "1": ...}，按数字键排序
                    keys = sorted((k for k in data if str(k).isdigit()), key=int)
                    shots = [_shot_text(data[k]) for k in keys]
            elif isinstance(data, list):
                shots = [_shot_text(p) for p in data]
        except json.JSONDecodeError as e:
            preview = text[:60].replace("\n", "\\n")
            raise ValueError(
                f"[H3InfiniteMV] 脚本像 JSON 但解析失败 ({e})。"
                f"输入开头: {preview}\n"
                f"常见原因：复制时少了结尾、只贴了一个 '['、缺逗号/引号、"
                f"或把 LLM 示例.json 的整份工作流贴了进来（应该只贴提示词数组）。\n"
                f"要么修 JSON，要么用 --- 分隔的纯文本提示词。")
    if not shots:
        shots = [b.strip().replace('\\"', '"')
                 for b in re.split(r"(?m)^---\s*$", text) if b.strip()]
    return shots


def _strip_ref_anchor(base_positive):
    """跳切模式：剥掉 AIO 条件的参考锚（minimax_ref_items / minimax_keyframes）。

    H3 R2VA 是参考生视频模型：ref_items（参考图/参考视频采样帧）喂给 Qwen 后，
    生成的首帧必然是该参考图的变种，提示词只能微调、无法跳切场景/机位。
    剥掉后镜头 1+ 由提示词主导（可跳切），人物/画面延续靠 Motion Context 的
    latent 物理接续；镜头 0 不受影响（AIO 条件原样使用，参考锚在首镜建立身份）。
    """
    if not base_positive:
        return base_positive
    out = []
    for cond, extra in base_positive:
        ne = dict(extra)
        ne.pop("minimax_ref_items", None)
        ne.pop("minimax_keyframes", None)
        out.append([cond, ne])
    return out


def _grid_down(frames):
    """H3 帧网格：合法长度满足 n % 17 == 5。向下就近取合法值。"""
    n = int(frames)
    if n < 5:
        return 5
    while n % 17 != 5 and n > 5:
        n -= 1
    return max(5, n)


def _smart_head_trim(wav, sr, trim, search_s=0.75):
    """从链式镜头音频的头部去掉 trim 个采样，但切在“最安静窗口”而非 0 采样处，
    保住任何放在镜头开头的字/音头。剪切量相同，音画同步不受影响。"""
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
        return torch.cat([wav[..., :i], wav[..., i + trim:]], dim=-1)
    return wav[..., trim:]


def _ch_stats(imgs):
    """单镜头逐通道全局均值/标准差（防漂移基准）。imgs: [T,H,W,C] float。"""
    import torch
    im = imgs.float()
    return (im.mean(dim=(0, 1, 2)), im.std(dim=(0, 1, 2)))


def _match_stats(imgs, stats):
    """把镜头整体颜色/亮度对齐到基准 stats（逐通道 mean/std 重映射）。"""
    import torch
    rm, rs = stats
    im = imgs.float()
    m = im.mean(dim=(0, 1, 2), keepdim=True)
    s = im.std(dim=(0, 1, 2), keepdim=True)
    out = (im - m) / (s + 1e-6) * (rs + 1e-6) + rm
    return out.clamp(0, 1)


def _write_wav(path, wav, sr):
    """torch 波形 -> 16bit WAV（标准库 wave）。wav: [1,C,T] 或 [C,T]。"""
    import torch
    import wave
    w = wav.detach().cpu()
    if w.ndim == 3:
        w = w[0]
    if w.ndim == 1:
        w = w.unsqueeze(0)
    nch = w.shape[0]
    if nch > 2:
        w = w[:1]
        nch = 1
    data = (w.clamp(-1, 1) * 32767).to(torch.int16).t().contiguous().numpy().tobytes()
    with wave.open(path, "wb") as f:
        f.setnchannels(nch)
        f.setsampwidth(2)
        f.setframerate(int(sr))
        f.writeframes(data)


def _write_shot(dirpath, idx, frames, wav, sr, width, height, fps=24.0):
    """逐镜保险落盘：mp4（libx264 + aac）+ 以 <shot>_XXXX 命名。
    失败只警告，不影响采样主流程。"""
    import torch
    import subprocess
    import imageio_ffmpeg
    tag = f"shot_{int(idx):04d}"
    mp4 = os.path.join(dirpath, tag + ".mp4")
    tmp_wav = os.path.join(dirpath, tag + ".tmp.wav")
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:
        print(f"[H3InfiniteMV] imageio_ffmpeg 不可用，跳过落盘（{e}）", flush=True)
        return False
    try:
        cmd = [exe, "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{int(width)}x{int(height)}",
               "-r", f"{fps:.6f}",
               "-i", "-"]
        if wav is not None:
            _write_wav(tmp_wav, wav, sr)
            cmd += ["-i", tmp_wav,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest",
                    "-movflags", "+faststart",
                    mp4]
        else:
            # 后期配音场景（无音轨）：只写视频
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-an",
                    "-movflags", "+faststart",
                    mp4]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        u8 = (frames.clamp(0, 1) * 255.0).round().to(torch.uint8)
        p.stdin.write(u8.numpy().tobytes())
        p.stdin.close()
        p.wait(timeout=600)
        return os.path.exists(mp4)
    except Exception as e:
        print(f"[H3InfiniteMV] 落盘 shot_{idx:04d} 失败（不影响采样）：{e}", flush=True)
        return False
    finally:
        if os.path.exists(tmp_wav):
            try:
                os.remove(tmp_wav)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 节点：H3 无限数字人 / MV (micxin)
# ---------------------------------------------------------------------------

class H3InfiniteHumanMV(io.ComfyNode):
    """无限时长数字人 / MV 链式采样器（io.ComfyNode，左侧单个 prompt_json 接口）。

    - shot_count=0：无限渲染（循环使用 prompt_json 拆出的段），按 Esc 取消；
      每镜渲染完立刻落盘，取消时已渲染的镜头全部保留。
    - shot_count=N：固定渲染 N 镜，返回完整 master_frames + master_audio。
    - 尺寸/帧数：无参数——接续模式由 H3 R2VA AIO(micxin) 的 latent 自动决定；
      独立模式（未接 initial_latent）固定 768×1344×243。
    - prompt_json（与翻译节点一致）：接 H3PromptFix 的 fixed_json 或 LLM 翻译后的
      数字键 JSON（{"1": "...", "2": "..."}，自动按数字键拆段）；
      也支持 --- 分隔多段 / 单段纯文本。留空 + 接 initial_cond = 纯续链。
    - handoff：latent = Motion Context（前段 latent 注入，零额外显存，16G 首选）；
      vision = 上一镜末帧作 frame-0 关键帧 + 视觉 token 重编码（接缝更牢，
      但每镜需重新编码、显存和耗时更高）。
    """

    @classmethod
    def define_schema(cls):
        import comfy.samplers
        sampler_names = list(getattr(comfy.samplers, "SAMPLER_NAMES", ["res_multistep", "euler"]))
        scheduler_names = list(getattr(comfy.samplers, "SCHEDULER_NAMES", ["simple", "normal"]))
        return io.Schema(
            node_id="H3InfiniteHumanMV",
            display_name="H3 InfiniteSampler (micxin)",
            category="H3 helper/micxin",
            description=(
                "无限采样（数字人 / MV）：左侧 prompt_json 接翻译链路的数字键 JSON"
                "（H3PromptFix.fixed_json / LLM 翻译结果，自动拆段）逐镜循环，"
                "Motion Context 无缝接续，逐镜落盘。shot_count=0 无限渲染直到取消。\n"
                "接 H3 R2VA AIO(micxin) 的 positive+latent 作镜头 0 锚；"
                "prompt_json 留空则全程沿用 AIO 条件，纯续链。"
            ),
            inputs=[
                # ---- 必须外部连接 ----
                io.Model.Input("model"),
                io.Clip.Input("clip"),
                io.Vae.Input("video_vae"),
                io.Vae.Input(
                    "audio_vae", optional=True,
                    tooltip="后期配音时可留空：跳过音频解码/合并/落盘音轨，"
                            "只出视频（嘴型为模型自由生成，配音后需 Wav2Lip 类"
                            "工具对齐；要模型原生口型同步请接 audio_vae）"),
                # ---- AIO 接续（可选）----
                io.Conditioning.Input(
                    "initial_cond", optional=True,
                    tooltip="接续模式：接 H3 R2VA AIO(micxin) 的 positive 输出。"
                            "镜头 0 直接用该条件采样（角色参考/音频锚定都在 AIO 里，"
                            "无需外接图片）；之后镜头用 prompt_json 拆出的段 + "
                            "Motion Context 续链。prompt_json 留空则全程沿用该条件。"),
                io.Latent.Input(
                    "initial_latent", optional=True,
                    tooltip="接续模式：接 H3 R2VA AIO(micxin) 的 Latent 输出"
                            "（未采样 AV 潜空间）。镜头 0 直接采样它，不重复生成。"
                            "宽高长自动由该 latent 决定（由编剧/AIO 控制）。"),
                # ---- 分镜提示词 JSON（左侧接口，与翻译链路一致：接 JSON）----
                # 参照 AIO.prompt 同款：optional + force_input -> 前端显示为左侧
                # 接口区 socket（可连线），不带参数区文本框；留空(不接)=纯续链。
                io.String.Input(
                    "prompt_json",
                    display_name="prompt_json",
                    optional=True,
                    force_input=True,
                    default="",
                    tooltip=(
                        "分镜提示词 JSON（与翻译节点输出一致）：接 LLM 翻译后的数字键 "
                        "JSON（{\"1\": \"...\", \"2\": \"...\"}），自动按数字键拆段；"
                        "也支持 {\"prompts\": [...]} / --- 分隔多段 / 单段纯文本。\n"
                        "留空(不接) + 接了 initial_cond = 全程沿用 AIO 条件，纯续链。"
                    ),
                ),
                # ---- 节点内部 widget ----
                io.Int.Input(
                    "shot_count", default=0, min=0, max=10000,
                    tooltip="0 = 无限时长，直到按取消（推荐，配合逐镜落盘）。"
                            ">0 = 固定渲染 N 镜并返回完整成品。"),
                io.Int.Input(
                    "max_shots", default=1000, min=1, max=100000,
                    tooltip="shot_count=0 时的安全上限，防止无限循环失控。"),
                io.Int.Input(
                    "seed", default=0, min=0, max=0xffffffffffffffff,
                    control_after_generate=True),
                io.Int.Input("steps", default=20, min=1, max=100,
                             tooltip="每镜采样步数。8-12 快速预览，16-20 高质量。"),
                io.Combo.Input("sampler_name", options=sampler_names,
                               default="euler"),
                io.Combo.Input("scheduler", options=scheduler_names, default="simple"),
                # ---- 分段音频锁定（动态接口，跨段使用；不走 AIO）----
                # AIO 的 keyframe_paths 只覆盖它自己的单段 latent（≤15s），多段会
                # 超界报错；本节点按段接音频，每段自己的音频，天然跨段。
                io.Autogrow.Input(
                    "shot_audios",
                    template=io.Autogrow.TemplatePrefix(
                        io.Audio.Input("audio"),
                        prefix="audio_", min=0, max=100),
                    optional=True,
                    tooltip=("分段音频（动态接口：节点上点 + 可加任意数量，"
                             "每段一个）。接 VHS_LoadAudioUpload（拖拽上传）即可，"
                             "不用写路径。第 1 个接口=第 1 镜音频，第 2 个=第 2 镜……"
                             "audio_lock_mode=full 时锁定整段，off 时作口型引导。")),
                io.Combo.Input(
                    "audio_lock_mode", optional=True, options=["off", "full"],
                    default="off",
                    tooltip=("音频锁定（shot_audios 里传音频）。\n"
                             "off=不锁定：模型生成音频，关键帧音频作引导（口型跟随）。\n"
                             "full=整轨自定义：解码后把源音频波形写回（非锚定段静音），"
                             "同时保留音频引导让口型跟随源音频。")),
                io.Combo.Input(
                    "handoff", options=["latent", "vision"], default="latent",
                    tooltip="latent：Motion Context 接续（前段 latent 注入，零额外显存，"
                            "16G 首选）。vision：上一镜末帧作 frame-0 关键帧 + 视觉 token"
                            "重编码（接缝更牢，但每镜重新编码，显存/耗时更高）。"),
                io.Combo.Input(
                    "ref_anchor", options=["first_shot", "always"], default="first_shot",
                    tooltip="参考锚定：H3 R2VA 是参考生视频模型，参考锚会让首帧成为"
                            "参考图变种。first_shot=参考锚只在镜头 0（AIO 条件），"
                            "镜头 1+ 纯提示词 + Motion Context，可跳切场景/机位/动作"
                            "（推荐，无限多镜叙事）。always=每镜重编码都带参考锚，"
                            "人物一致最强但首帧始终是参考图变种，不能跳切。"
                            "first_shot 时 drift_guard 自动忽略（跳切场景颜色对齐会冲突）。"),
                io.Boolean.Input(
                    "smart_trim", default=True,
                    tooltip="接缝裁剪时找最安静窗口切，不切掉字头/音头。"),
                io.Boolean.Input(
                    "drift_guard", default=False,
                    tooltip="把每镜整体颜色/亮度对齐到第一镜，压住长链跑色/糊化。"
                            "若剧情需要光照变化请关闭。"),
                io.Boolean.Input(
                    "save_every_shot", default=True,
                    tooltip="每镜渲染完立即写 mp4+wav 到 output/video/H3InfiniteMV/"
                            "（固定目录，与 VHS 同前缀）。无限模式请保持开启——"
                            "中断后这就是成品。"),
            ],
            outputs=[
                io.Image.Output(display_name="master_frames"),
                io.Audio.Output(display_name="master_audio"),
                io.Int.Output(display_name="shots_rendered"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, video_vae, audio_vae,
                initial_cond=None, initial_latent=None, prompt_json=None,
                shot_count=0, max_shots=1000, seed=0, steps=20,
                sampler_name="euler", scheduler="simple",
                handoff="latent", ref_anchor="first_shot", smart_trim=True,
                drift_guard=False, save_every_shot=True,
                shot_audios=None, audio_lock_mode="off"):
        import torch
        import node_helpers
        import comfy.model_management as mm
        from comfy_extras import nodes_custom_sampler as ncs
        from comfy_extras import nodes_minimax_h3 as mmh3
        import folder_paths

        try:
            from comfy.model_management import throw_exception_if_processing_interrupted
        except Exception:
            throw_exception_if_processing_interrupted = None

        # ---- 分镜提示词：prompt_json（翻译链路数字键 JSON / --- 多段 / 单段文本）----
        if prompt_json and str(prompt_json).strip():
            prompts = _parse_script(str(prompt_json))
            # 防呆：H3 R2VA（Qwen-VL）会把提示词里的中文当台词朗读。
            # 若发现分镜含中文，打醒目警告，帮用户快速定位"只说话不动作"。
            _cjk = re.compile(r"[\u4e00-\u9fff]")
            for _i, _p in enumerate(prompts):
                _m = _cjk.search(str(_p))
                if _m:
                    _snippet = str(_p)[max(0, _m.start() - 6): _m.start() + 14]
                    print(f"[H3InfiniteMV] 警告：分镜 {_i + 1} 含中文（…{_snippet}…），"
                          f"H3 R2VA 会把中文当台词朗读，人物会只说话不动作。"
                          f"请接翻译后的英文提示词（<d> 中文对白），或让编剧/外部 LLM "
                          f"翻译后再进 prompt_json。", flush=True)
                    break
        else:
            prompts = []
        chained = (initial_latent is not None) or (initial_cond is not None)
        if not prompts and initial_cond is None:
            raise ValueError("[H3InfiniteMV] prompt_json 为空且未接 initial_cond："
                             "请在左侧 prompt_json 接翻译链路的数字键 JSON（H3PromptFix "
                             "fixed_json / LLM 翻译结果），或从 H3 R2VA AIO(micxin) 接入 "
                             "initial_cond。")

        infinite = (int(shot_count) <= 0)
        # 无锚定镜：所有镜都是正片，全部吃 prompt_json 提示词、全部输出。
        # AIO 参考图经 minimax_refs（Ref2VA 身份锚）作用于每镜的条件，
        # 不需要额外种子镜；写 N 段提示词 = 出 N 段视频。
        total = max_shots if infinite else int(shot_count)
        n_prompts = len(prompts)
        if chained:
            print("[H3InfiniteMV] 接续模式：镜头 0 用 AIO 的 latent+条件，"
                  "之后镜头链式续帧", flush=True)
        if not infinite and total > 5000:
            print(f"[H3InfiniteMV] shot_count={total} 会占用大量内存并渲染很久；"
                  f"长内容建议用 shot_count=0 + 逐镜落盘", flush=True)

        # ---- 采样器与调度（H3 专用采样在 h3cc._sample_h3 内处理）----

        # ---- 空 AV 潜空间（接续模式下以 AIO latent 为模板，第 2 镜起用同形空模板）----
        if initial_latent is not None:
            from comfy import nested_tensor
            _v, _a = initial_latent["samples"].unbind()
            width = int(_v.shape[4]) * 16
            height = int(_v.shape[3]) * 16
            frames_per_shot = ((int(_v.shape[2]) - 2) // 5) * 17 + 5
            latent_base = None
            frame_count = frames_per_shot
            print(f"[H3InfiniteMV] 接续模式：由 AIO latent 决定 {width}x{height} "
                  f"每镜 {frame_count} 帧", flush=True)
        else:
            # 独立模式：固定 768x1344x243（≈10s），无参数可配
            width, height, frames_per_shot = 768, 1344, 243
            latent_base, frame_count = mmh3._empty_av_latent(width, height, frames_per_shot)
        print(f"[H3InfiniteMV] 每镜 {frame_count} 帧（{frame_count / 24.0:.1f}s）@ "
              f"{width}x{height}；{'无限' if infinite else str(total)} 镜，"
              f"{n_prompts} 组提示词循环", flush=True)

        # ---- 复用 H3 Clip Chain (micxin) 的多段 clip 接续函数（不修改该节点）----
        h3cc = _load_h3cc()

        # ---- latent 模式：非接续模式下一次性预编码所有提示词，然后卸载 TE ----
        # 接续模式不预编码：每镜要 _reencode_prompt_with_ref 继承 AIO 语义锚
        base_conds = None
        if not chained and handoff != "vision" and n_prompts:
            print("[H3InfiniteMV] 独立模式 handoff=latent：预编码提示词，TE 只加载一次",
                  flush=True)
            base_conds = []
            for p in prompts:
                base_conds.append(clip.encode_from_tokens_scheduled(
                    clip.tokenize(_dialogue_safe(p, h3cc))))
            _evict_te(clip, model)

        # ---- 循环 ----
        # 镜头 0 起于 AIO latent（角色/场景锚定全在 AIO 条件里，无需 start_image）；
        # 第 2 镜起（接续模式）走 Motion Context：前段 latent 尾部注入 conditioning，
        # 采样后裁掉重叠帧——latent 物理延续，接缝比首帧图更牢、零额外显存。
        prev_last = None        # 上一镜尾帧图（vision 首帧 / 独立模式续链用）
        prev_latent = None      # 上一镜完整 AV latent（Motion Context 源）
        frames_parts, audio_parts = [], []
        sr = None
        ref_stats = None
        out_dir = None
        if save_every_shot:
            out_dir = os.path.join(folder_paths.get_output_directory(),
                                   "video", "H3InfiniteMV")
            os.makedirs(out_dir, exist_ok=True)
            print(f"[H3InfiniteMV] 逐镜落盘目录：{out_dir}", flush=True)

        try:
            for i in range(total):
                if throw_exception_if_processing_interrupted is not None:
                    throw_exception_if_processing_interrupted()

                # --- latent：每镜都从 AIO latent 模板随机创建 ---
                # AIO latent 是 Ref2VA 参考图编码，直接用会把首帧锁成参考图变种
                # （提示词无法跳切）；随机化 + minimax_refs 身份锚 = 参考生视频
                # 正确用法：画面由提示词主导、人物身份由参考锚保持。
                if initial_latent is not None:
                    trim_expected = (h3cc._motion_context_trim(prev_latent, _MC_CONTEXT)
                                     if prev_latent is not None else 0)
                    latent = h3cc._create_segment_latent(
                        initial_latent, frame_count + trim_expected)
                else:
                    latent = latent_base

                # --- 条件：每镜都吃提示词（重编码继承 AIO 参考锚）---
                if n_prompts == 0:
                    cond = initial_cond          # 词组留空：全程沿用 AIO 条件
                else:
                    prompt = prompts[i % n_prompts]
                    if chained:
                        # 中文引号对话 -> <d> 音频块 + 字幕/朗读禁令。
                        # 参考锚：first_shot 时第一镜保留 minimax_refs（身份锚，
                        # 首帧不再锁参考图），后续镜剥掉（靠 Motion Context 延续
                        # 身份，画面跳切自由）；always 时每镜保留（人物一致最强）。
                        anchor_base = initial_cond
                        if ref_anchor != "always" and i > 0:
                            anchor_base = _strip_ref_anchor(initial_cond)
                        cond = h3cc._reencode_prompt_with_ref(
                            clip, _dialogue_safe(prompt, h3cc), anchor_base,
                            segment_idx=i, frames_per_segment=frame_count,
                            first_frame_has_image=(handoff == "vision"
                                                   and prev_last is not None))
                        _evict_te(clip, model)
                    elif handoff == "vision":
                        kf_img = None
                        if prev_last is not None:
                            kf_img = mmh3._resize(prev_last[:1], width, height, "disabled")
                        if kf_img is not None:
                            prompt = ("For the target video, at 0.00 seconds into the "
                                      "target video, <Picture 1> (from [Shot 1]) is "
                                      "fully referenced.\n\n") + prompt
                        tokens = clip.tokenize(
                            prompt, images=[kf_img] if kf_img is not None else [])
                        cond = clip.encode_from_tokens_scheduled(tokens)
                        _evict_te(clip, model)
                    else:
                        cond = base_conds[i % n_prompts]

                # --- Motion Context 注入（接续 + latent 模式 + 第 2 镜起）---
                trim = 0
                if chained and handoff == "latent" and prev_latent is not None:
                    cond, trim = h3cc._apply_motion_context(
                        cond, latent, prev_latent, _MC_CONTEXT)
                    del prev_latent
                    prev_latent = None
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                # --- 本段音频（动态接口 shot_audios，Add Guide + 音频锁定源）---
                # shot_audios 是 Autogrow 动态输入 dict：键 "audio_0","audio_1"...，
                # 值为 AUDIO dict（接 VHS_LoadAudioUpload 拖拽上传）。第 i 镜取第 i 个。
                seg_keyframes = []
                _shot_audio = None
                if shot_audios and isinstance(shot_audios, dict):
                    _shot_audio = shot_audios.get(f"audio_{i}")
                    if _shot_audio is None:
                        # 按键名数字排序兜底（防止命名/顺序不一致）
                        def _kidx(k):
                            try:
                                return int(str(k).rsplit("_", 1)[-1])
                            except (ValueError, IndexError):
                                return 999999
                        _vals = [v for _, v in
                                 sorted(shot_audios.items(), key=lambda kv: _kidx(kv[0]))
                                 if v is not None]
                        if i < len(_vals):
                            _shot_audio = _vals[i]
                if _shot_audio is not None:
                    seg_keyframes = [
                        {"frame_idx": 0, "image": None, "audio": _shot_audio}]
                # AddGuide 注入（audio_lock=full 或未接 audio_vae 时跳过音频 guide——
                # 音频靠解码后波形替换锁定；音频 guide 会干扰视频生成导致画面糊）
                _skip_audio_guide = (audio_lock_mode != "off") or (audio_vae is None)
                for kf in seg_keyframes:
                    if int(kf.get("frame_idx", -1)) >= frame_count:
                        continue
                    _img = kf.get("image")
                    _aud = None if _skip_audio_guide else kf.get("audio")
                    if _img is None and _aud is None:
                        continue
                    cond = MiniMaxH3AddGuide.execute(
                        positive=cond, latent=latent,
                        frame_idx=int(kf["frame_idx"]),
                        vae=video_vae, audio_vae=audio_vae,
                        image=_img, audio=_aud)[0]

                # --- 独立/vision 模式：上一镜末帧 -> frame-0 关键帧 ---
                kf_img = None
                if prev_last is not None and (handoff == "vision" or not chained):
                    kf_img = mmh3._resize(prev_last[:1], width, height, "disabled")
                if kf_img is not None:
                    kf = {"resolved_frame_index": 0, "image": kf_img}
                    kf["latent"] = video_vae.encode(kf.pop("image"))
                    cond = node_helpers.conditioning_set_values(cond, {
                        "minimax_keyframes": [kf],
                        "minimax_frame_count": frame_count,
                    })

                tag = (i % n_prompts + 1 if n_prompts else "AIO")
                print(f"[H3InfiniteMV] 镜 {i + 1}/{total or '∞'} "
                      f"({frame_count}f, 条件 {tag})", flush=True)

                # --- 采样（H3 专用：comfy.sample.sample + prepare_noise，
                #    正确对 AV NestedTensor latent 的视频/音频两部分加噪，
                #    修复通用 BasicGuider+RandomNoise 音频放飞问题；cfg=1.0）---
                neg = h3cc._encode_negative(clip)
                out = h3cc._sample_h3(
                    model, cond, neg, latent,
                    (seed + i) & 0xffffffffffffffff, steps,
                    sampler_name=sampler_name, scheduler=scheduler,
                    denoise=1.0)

                # --- 解码（ClipChain 标准路径：video->IMAGE，audio->AUDIO dict）---
                frames, audio = h3cc._decode_av_latent(out, video_vae, audio_vae)
                audio_ok = audio is not None
                sr = int(audio["sample_rate"]) if audio_ok else 0
                wav = audio["waveform"] if audio_ok else None

                prev_last = frames[-1:].clone()      # 下一镜续帧源
                if chained:
                    prev_latent = out                # 供下一镜 Motion Context

                # --- 音频锁定（波形层面，full 模式）---
                # 不在 latent 层用 zeros（zeros latent 直接 decode 出噪声），
                # 解码后波形层面填零（真静音）+ 写入源音频波形。
                audio_kfs_for_post = [kf for kf in seg_keyframes
                                      if kf.get("audio") is not None]
                if audio_lock_mode == "full" and audio_kfs_for_post \
                        and audio is not None:
                    try:
                        import torchaudio.functional as taF
                        sr = int(audio.get("sample_rate", 32000))
                        waveform = audio["waveform"].clone()   # [B, C, L]
                        total_samples = waveform.shape[-1]
                        waveform.zero_()   # 非锚定段静音
                        replaced = 0
                        for kf in audio_kfs_for_post:
                            src_audio = kf.get("audio")
                            if src_audio is None:
                                continue
                            src_wave = src_audio["waveform"]   # [B, C, L]
                            src_sr = int(src_audio.get("sample_rate", sr))
                            if src_sr != sr:
                                src_wave = taF.resample(src_wave, src_sr, sr)
                            start_sample = int(round(kf["frame_idx"] / 24.0 * sr))
                            if start_sample < 0:
                                start_sample = total_samples + start_sample
                            if start_sample >= total_samples:
                                continue
                            use = min(src_wave.shape[-1],
                                      total_samples - start_sample)
                            if use <= 0:
                                continue
                            if src_wave.shape[1] != waveform.shape[1]:
                                if src_wave.shape[1] == 1 and waveform.shape[1] == 2:
                                    src_wave = src_wave.repeat(1, 2, 1)
                                elif src_wave.shape[1] == 2 and waveform.shape[1] == 1:
                                    src_wave = src_wave[:, :1, :]
                            waveform[..., start_sample:start_sample + use] = \
                                src_wave[..., :use]
                            replaced += 1
                        audio = {"waveform": waveform, "sample_rate": sr}
                        wav = waveform
                        print(f"[H3InfiniteMV] 音频锁定(波形): full，{replaced} 段源音频"
                              f"写入，其余静音，总 {total_samples} 样本 @ {sr}Hz",
                              flush=True)
                    except Exception as e:
                        print(f"[H3InfiniteMV] 音频锁定(波形)失败: {e}", flush=True)

                # --- 接缝 ---
                if chained:
                    if trim > 0:
                        frames, audio = h3cc._trim_clip(
                            frames, audio, trim, 24,
                            smart=(smart_trim and audio_lock_mode == "off"))
                        if audio is not None:
                            wav = audio["waveform"]
                elif i > 0:
                    # 独立/vision 模式：去复制帧 + 智能音频裁剪（静音窗口不切字头）
                    frames = frames[1:]
                    trim_s = max(1, int(round(sr / 24.0)))
                    if wav is not None:
                        wav = (_smart_head_trim(wav, sr, trim_s) if smart_trim
                               else wav[..., trim_s:])

                # --- 防漂移：对齐第一镜整体颜色/亮度（first_shot 跳切模式下忽略——
                # 跳切场景后颜色对齐会把新场景拉回第一镜色调）---
                if drift_guard:
                    if ref_anchor == "first_shot" and i > 0:
                        if i == 1:
                            print("[H3InfiniteMV] ref_anchor=first_shot："
                                  "drift_guard 已忽略（跳切模式）", flush=True)
                    else:
                        if ref_stats is None:
                            ref_stats = _ch_stats(frames)
                        else:
                            frames = _match_stats(frames, ref_stats)

                # --- 全部正片：收集 + 逐镜保险落盘 ---
                frames_parts.append(frames.cpu().half())
                audio_parts.append(audio)

                # --- 逐镜保险落盘 ---
                if save_every_shot:
                    _write_shot(out_dir, i, frames.cpu().float(), wav, sr,
                                width, height)

                # --- 无限模式：只保留最近 4 镜在内存（其余已在盘上）---
                if infinite and len(frames_parts) > 4:
                    frames_parts.pop(0)
                    audio_parts.pop(0)

                print(f"[H3InfiniteMV] 镜 {i + 1} "
                      f"完成：{frames.shape[0]} 帧，"
                      f"{frames.shape[0] / 24.0:.1f}s，累计正片 "
                      f"~{len(frames_parts) * frames.shape[0] / 24.0:.0f}s",
                      flush=True)
        except Exception as e:
            print(f"[H3InfiniteMV] 中断/出错于第 {i + 1} 镜：{e}",
                  flush=True)
            if save_every_shot:
                print(f"[H3InfiniteMV] 已完成镜头已保存在 {out_dir}", flush=True)
            raise

        master = torch.cat(frames_parts, dim=0).float()
        n_shot = len(frames_parts)
        # 音频：段间交叉淡入淡出合并（ClipChain 等功率曲线，接缝平滑）
        valid_audio = [a for a in audio_parts
                       if a is not None and a.get("waveform") is not None]
        if valid_audio:
            final_audio = h3cc._merge_audio(valid_audio)
            if final_audio is None:
                wavs = [a["waveform"].cpu() for a in valid_audio]
                sr = int(valid_audio[0]["sample_rate"])
                final_audio = {"waveform": torch.cat(wavs, dim=-1),
                               "sample_rate": sr}
        else:
            # 后期配音场景（audio_vae 空接）：输出与视频等长的静音占位，
            # 保证 VHS 正常合成；配音时直接替换音轨
            silence = torch.zeros(
                1, 1, max(1, int(master.shape[0] / 24.0 * 32000)))
            final_audio = {"waveform": silence, "sample_rate": 32000}
        print(f"[H3InfiniteMV] 完成：{n_shot} 镜，{master.shape[0]} 帧 "
              f"（~{master.shape[0] / 24.0:.1f}s）", flush=True)
        return io.NodeOutput(master, final_audio, n_shot)
