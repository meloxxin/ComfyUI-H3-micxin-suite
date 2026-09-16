# -*- coding: utf-8 -*-
"""H3 Prompt Translate (micxin) — 分段提示词翻译节点。

把 H3 Prompt Split 拆出的每段提示词里的**非对话中文**翻译成英文，
**<d> 对话原文保留**（中文/方言不翻译）。N 路进 N 路出，
直接对接 H3 Clip Chain (micxin) 的 segment_prompts。

动机：H3InfiniteStoryWriter 一次只能吃一段概念，本地弱 GGUF 翻译质量也不稳；
本节点用本地 GGUF 做"只翻译描述、保留对话"的窄任务，多段并行，
输出格式与输入完全一致，clipchain 可直接消费。
"""
import re

from comfy_api.latest import io
from .h3_screenwriter import (
    _load_local_llm,
    _call_local_llm,
    _unload_local,
    _list_llm_files,
)

MAX_SEGMENTS = 32

_TRANSLATE_SYS = """You are a professional translator for H3 video generation prompts (Chinese to English).
Translate the Chinese description text into English. STRICT RULES:
1. <d>...</d> dialogue blocks: KEEP the original text EXACTLY as-is. Never translate them, never change punctuation.
2. Everything else (subject definitions, actions, camera moves, scene, emotions, soundscape, music descriptions): translate into English.
3. Keep all field names unchanged: subject_definitions:, summary:, retention_analysis:, detailed_description:, overall_soundscape:, non_diegetic_music:.
4. Keep the overall structure, field order and line breaks. Do not add or remove segments or fields.
5. Output ONLY the translated prompt text. No explanations, no notes, no code fences."""


def _collect_segment_inputs(raw):
    """把 Autogrow 输入（dict {prompt_0: "...", ...}）整理成有序非空提示词列表。"""
    if not raw or not isinstance(raw, dict):
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


def _translate_segment(seg_text, llm, temperature, seed, max_tokens=4096):
    """单段翻译：非 <d> 中文 → 英文；<d> 对话保留原文。"""
    messages = [
        {"role": "system", "content": _TRANSLATE_SYS},
        {"role": "user", "content": seg_text},
    ]
    try:
        out = _call_local_llm(llm, messages, temperature, seed, max_tokens)
        out = (out or "").strip()
        if out:
            return out
    except Exception as e:
        print(f"[H3PromptTranslate] 翻译失败({type(e).__name__}: {e})，保留原文", flush=True)
    return seg_text


class H3PromptTranslate(io.ComfyNode):
    """多段提示词翻译：非对话中文→英文，<d> 对话保留，N 进 N 出接 ClipChain。"""

    @classmethod
    def define_schema(cls):
        ggufs = _list_llm_files(include_mmproj=False) or [""]
        mmprojs = _list_llm_files(mmproj_only=True) or ["", "None"]
        return io.Schema(
            node_id="H3PromptTranslate",
            display_name="H3 Prompt Translate (micxin)",
            category="H3 helper/micxin",
            description=(
                "把 H3 Prompt Split 拆出的每段提示词：非对话中文翻译成英文，"
                "<d> 对话原文保留（不翻译）。N 路进 N 路出，直接对接 "
                "H3 Clip Chain (micxin) 的 segment_prompts（prompt_0/1/2...）。\n"
                "替代 H3InfiniteStoryWriter 的多段瓶颈：不用重新生成剧本，"
                "只做'描述英文化 + 对话留中文'的窄翻译。"
            ),
            inputs=[
                io.Autogrow.Input(
                    "segment_prompts",
                    optional=True,
                    tooltip=(
                        "每段提示词输入（来自 H3 Prompt Split 的 prompt_N 输出，"
                        "或任意多段文本）。连接一个自动出现下一个空槽。"
                    ),
                    template=io.Autogrow.TemplatePrefix(
                        input=io.String.Input(
                            "prompt",
                            multiline=True,
                            default="",
                            tooltip="一段提示词（六段式文本 / JSON 片段）。",
                        ),
                        prefix="prompt_",
                        min=0,
                        max=MAX_SEGMENTS,
                    ),
                ),
                io.Combo.Input(
                    "backend",
                    options=["Local GGUF", "HTTP"],
                    default="Local GGUF",
                    tooltip="只用本地 GGUF（默认）。HTTP 仅当你自己起了 llama.cpp server / vLLM 时才切。",
                ),
                io.Combo.Input(
                    "gguf_name",
                    options=ggufs,
                    default=ggufs[0] if ggufs and ggufs[0] else "",
                    tooltip="本地 GGUF 模型（ComfyUI/models/LLM 下）。留空无法运行 Local GGUF。",
                ),
                io.Combo.Input(
                    "mmproj_name",
                    options=mmprojs,
                    default="",
                    tooltip="VLM 投影层（看图用）。纯文本翻译可留空。",
                ),
                io.Int.Input("context_size", default=8192, min=512, max=131072,
                             tooltip="上下文窗口。翻译任务 4096 足够。"),
                io.Int.Input("n_gpu_layers", default=-1, min=-1, max=200,
                             tooltip="卸载多少层到 GPU。-1=全部。"),
                io.Float.Input("temperature", default=0.4, min=0.0, max=2.0, step=0.05,
                               tooltip="采样温度。翻译任务建议 0.3-0.5（偏低更忠实）。"),
                io.Int.Input("seed", default=0, min=0, max=0xFFFFFFFF,
                             tooltip="随机种子。0=不固定。"),
                io.Boolean.Input("keep_loaded", default=True,
                                 tooltip="ON=模型常驻内存，多段连续翻译快；OFF=跑完卸载释放显存。"),
                io.String.Input("llm_base_url", default="http://127.0.0.1:8080/v1/chat/completions",
                                tooltip="backend=HTTP 时使用。Local GGUF 自动忽略。"),
                io.String.Input("model", default="",
                                tooltip="backend=HTTP 时的模型名。Local GGUF 自动忽略。"),
            ],
            outputs=[
                io.String.Output(id=f"prompt_{i}", display_name=f"prompt_{i}")
                for i in range(MAX_SEGMENTS)
            ] + [io.String.Output(id="report", display_name="report")],
        )

    @classmethod
    def execute(cls, **kwargs):
        segs = _collect_segment_inputs(kwargs.get("segment_prompts"))
        backend = kwargs.get("backend", "Local GGUF") or "Local GGUF"
        gguf_name = kwargs.get("gguf_name", "") or ""
        mmproj_name = kwargs.get("mmproj_name", "") or ""
        context_size = int(kwargs.get("context_size", 8192) or 8192)
        n_gpu_layers = int(kwargs.get("n_gpu_layers", -1) or -1)
        temperature = float(kwargs.get("temperature", 0.4) or 0.4)
        seed = int(kwargs.get("seed", 0) or 0)
        keep_loaded = bool(kwargs.get("keep_loaded", True))

        if not segs:
            raise ValueError(
                "H3PromptTranslate: 没有输入段。请连接 H3 Prompt Split (micxin) "
                "的 prompt_0/1/2... 输出到 segment_prompts。")

        llm = None
        if backend == "Local GGUF":
            llm = _load_local_llm(gguf_name, mmproj_name, n_gpu_layers, context_size)

        outputs = {}
        report = []
        for i, s in enumerate(segs):
            out_text = _translate_segment(s, llm, temperature, seed)
            outputs[f"prompt_{i}"] = out_text
            changed = "翻译" if out_text != s else "原样(无中文或失败)"
            report.append(f"seg {i}: {changed}")
            print(f"[H3PromptTranslate] seg {i}: {changed}", flush=True)

        if backend == "Local GGUF" and not keep_loaded:
            _unload_local()

        result = [outputs.get(f"prompt_{i}", "") for i in range(MAX_SEGMENTS)]
        result.append("\n".join(report))
        print(f"[H3PromptTranslate] 完成 {len(segs)} 段翻译", flush=True)
        return io.NodeOutput(*result)


NODE_CLASS_MAPPINGS = {"H3PromptTranslate": H3PromptTranslate}
NODE_DISPLAY_NAME_MAPPINGS = {"H3PromptTranslate": "H3 Prompt Translate (micxin)"}
