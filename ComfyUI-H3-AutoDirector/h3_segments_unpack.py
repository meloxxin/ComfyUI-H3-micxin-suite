# -*- coding: utf-8 -*-
"""H3 Segments Unpack (micxin) — segments_json 拆成 9 个分镜口。

H3PromptSplitTranslate.segments_json（一根线，JSON 字符串数组）→ 本节点
→ prompt_0..8（镜1..镜9）九个固定口，按九宫格从左到右、从上到下顺序，
直接接 H3 Clip Chain (micxin) 的 segment_prompts（Autogrow）。

不足 9 镜时多余口输出空串，方便满接 9 路；ClipChain 按 prompt_0..8 顺序
逐段感知分镜（每镜一口）。
"""
from comfy_api.latest import io

try:
    from .h3_prompt_split_translate import _split_to_prompt_list
except ImportError:  # 独立脚本/裸加载兜底
    from h3_prompt_split_translate import _split_to_prompt_list

MAX_UNPACK = 9


class H3SegmentsUnpack(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3SegmentsUnpack",
            display_name="H3 Segments Unpack (micxin)",
            category="H3 helper/micxin",
            is_output_node=True,  # 声明为终点节点：即使下游 ClipChain 未接保存节点，本节点每次 Queue 都会执行，九宫格照常填
            description=(
                "segments_json（JSON 字符串数组，接 H3 Prompt Split+Translate 的 "
                "segments_json 输出）拆成 9 个分镜口 prompt_0..8（镜1..镜9），"
                "按九宫格从左到右、从上到下顺序，接 H3 Clip Chain 的 segment_prompts。\n"
                "不足 9 镜时多余口留空；ClipChain 逐口感知分镜（每镜一口）。"
            ),
            inputs=[
                io.String.Input(
                    "segments_json",
                    optional=True,
                    force_input=True,
                    default="",
                    tooltip=(
                        "接 H3 Prompt Split+Translate 的 segments_json 输出，或外接多行字符串节点粘贴 JSON 数组。\n"
                        "留空时则改用九宫格 9 格内手动输入的内容。"
                    ),
                ),
                io.String.Input(
                    "manual_shots",
                    optional=True,
                    multiline=True,
                    default="",
                    tooltip="内部用：九宫格 9 格手动输入内容的 JSON 数组（JS 自动写入，勿手改）。",
                ),
            ],
            outputs=[
                io.String.Output(id=f"prompt_{i}", display_name=f"镜 {i + 1}")
                for i in range(MAX_UNPACK)
            ] + [
                io.String.Output(id="report", display_name="report"),
            ],
        )

    @classmethod
    def execute(cls, **kwargs):
        segments_json = kwargs.get("segments_json", "") or ""
        if segments_json.strip():
            items = _split_to_prompt_list(segments_json) or []
            items = items[:MAX_UNPACK]
            padded = [items[i] if i < len(items) else "" for i in range(MAX_UNPACK)]
            src = "segments_json"
        else:
            # 手动模式：从 manual_shots（JS 写入的 JSON 数组）读 9 格内容
            import json as _json
            raw = kwargs.get("manual_shots", "") or ""
            padded = [""] * MAX_UNPACK
            if raw.strip():
                try:
                    arr = _json.loads(raw)
                    if isinstance(arr, list):
                        for i, v in enumerate(arr[:MAX_UNPACK]):
                            padded[i] = str(v)
                except Exception:
                    pass
            src = "manual"
        items = [p for p in padded if p.strip()]
        report_lines = [f"解包: {len(items)} 镜（{src}）"]
        for i, t in enumerate(padded):
            if t.strip():
                head = " ".join(t.split())[:60]
                report_lines.append(f"  镜{i + 1}: {head}")
        report = "\n".join(report_lines)
        print(f"[H3SegmentsUnpack] {len(items)} 镜 ({src})", flush=True)
        return io.NodeOutput(*padded, report, ui={"text": (report, *padded)})


NODE_CLASS_MAPPINGS = {"H3SegmentsUnpack": H3SegmentsUnpack}
NODE_DISPLAY_NAME_MAPPINGS = {"H3SegmentsUnpack": "H3 Segments Unpack (micxin)"}
