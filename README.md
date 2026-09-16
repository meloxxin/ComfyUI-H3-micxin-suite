# ComfyUI H3 micxin Suite

> **v2.0** — 新增多镜提示词拆分/忠实翻译链路、ClipChain 多段接续、无限时长数字人/MV 采样器；示例工作流随仓库发布。

MiniMax H3 全流程自定义节点套装，包含**提示词写作**和**全资源模型加载**两大插件，专为短剧/剧情视频生成优化。

## 包含的插件

### 1. ComfyUI-H3-AutoDirector — 剧本与提示词写作

| 节点 | 功能 |
|---|---|
| `H3PromptWriter` (micxin) | 六段式 H3 提示词生成器，内置 19 种任务模式（16 内置风格 + 官方/增强版提示词写作技能），默认 micxin 增强版；支持 bypass_llm 手写剧本、`embeddings` 提示词嵌入与 `prompt_enhance` Context IR 增强 |
| `H3PromptFix` (LLM→JSON) | 把 LLM 输出整理成合法 JSON（fixed_json），供拆分 / 翻译 / 无限采样链路直接消费 |
| `H3 Prompt → Segment Prompts` | 把 JSON 提示词拆成逐镜 conditioning 段 |
| `H3PromptSplitTranslate` (micxin) | 六段式拆分 + 忠实翻译：非对话中文翻译为英文，`<d>` 对话原文保留（JSON 结构不变） |
| `H3PromptTranslate` (micxin) | 分段翻译节点：只翻描述、保留对话，N 路进 N 路出，对接 ClipChain segment_prompts |
| `H3Screenwriter` | 多镜头剧本自动写作，输出 JSON 到 `input/rift_prompts/` |
| `H3AssetLibrary` | 角色/场景资产管理库，支持参考图绑定 |
| `H3ReferenceBuilder` | 参考图构建器（从上一段视频抽帧，供接续段使用） |

### 2. ComfyUI-H3-helper (micxin) — 全资源模型加载

| 节点 | 功能 |
|---|---|
| `H3ModelLoader` (R2VA AIO) | 全资源输入中心：图片/视频/音频/关键帧统一上传，自带播放器与裁切，集成 MiniMaxH3AddGuide 原生音频驱动 |
| `H3SeparateAVLatent` | 分离 H3 联合音视频 latent 为视频+音频 |
| `H3CombineAVLatent` | 合并视频+音频 latent 为联合 AV latent |
| `H3NoiseMask` | 为 H3 联合 AV latent 构建逐 token 噪声蒙版（局部重绘 / 物体移除 / latent 空间无缝续写） |
| `H3StitchSegments` | 多段渲染结果去重叠帧拼接为整片（保留原生音频） |
| `H3ClipChainAV` | 多段 clip 接续：Motion Context 无缝续帧（latent 物理延续），逐镜落盘 |
| `H3InfiniteHumanMV` | 无限时长数字人 / MV：单节点链式续帧采样器，prompt_json 多镜拆分，直接接 AIO（无需外接图片） |

## 安装

### 要求

- **ComfyUI v0.31+**（需要 `comfy_api.latest` 和内置 H3 节点支持）
- Python 3.10+
- NVIDIA GPU（建议 16GB+ 显存）

### 步骤

1. 下载本仓库
2. 将整个 `ComfyUI-H3-micxin-suite` 文件夹复制到 ComfyUI 的 `custom_nodes/` 目录
3. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
4. 重启 ComfyUI

### H3 模型文件

需要自行下载 MiniMax H3 模型文件，放到对应目录：

```
ComfyUI/models/
├── diffusion_models/
│   └── minimax_h3_ref2va_*.safetensors
├── text_encoders/
│   └── qwen3vl_*_minimax_h3_*.safetensors
├── vae/
│   ├── minimax_h3_video_vae_*.safetensors
│   └── minimax_h3_audio_vae_*.safetensors
└── loras/
    └── MiniMax-H3-Ref2VA-Acc-*.safetensors  (可选，加速用)
```

## 示例工作流（workflows/）

| 文件 | 用途 |
|---|---|
| `H3分镜长视频.json` | 分镜长视频：Prompt Split+Translate → 分镜提示词 → 逐镜渲染 |
| `H3无限时长数字人.json` | 无限时长数字人：AIO + InfiniteSampler 链式续帧 |
| `Minimax H3提示词增强.json` | 提示词增强 + 分镜写作链路 |

> 示例中的模型 widget 默认留空，请按自己的模型文件选择（官方模型见上文目录）。
> LLM 端点默认 `http://127.0.0.1:8080/v1/chat/completions`（本地 llama.cpp / Ollama 兼容）。

## 典型工作流接法

```
H3PromptWriter (micxin)
  ├─ prompt ──────────────┐
  ├─ width ───────────────┤
  ├─ height ──────────────┼→ H3ModelLoader (R2VA AIO)
  └─ length ──────────────┤    ├─ 图片标签页：人物参考图
                           │    ├─ 关键帧标签页：关键帧图（可设出现秒数）
                           │    ├─ 音频标签页：对白音频（自带裁切，原生驱动口型）
                           │    └─ MODEL ─→ LoRA ─→ SigmaShift ─→ Sampler
                           │                              ↓
                           └──────────────────────────  VAEDecode → VHS_VideoCombine
```

### 关键帧用法

在 `H3ModelLoader` 的**关键帧**标签页上传图片，每张可设置出现的秒数/位置，H3 会在关键帧之间插值生成。

### 视频用途（第 6 点：editable_ref / fixed_guide / boundary）

`H3ModelLoader` **视频**标签页的每个视频可选择「用途」，决定它在 H3 管线里的角色
（也可在 `video_paths` 行直接写第 4 段，格式 `path|start|end|use`）：

| 用途 | 行为 | 典型场景 |
|---|---|---|
| `editable_ref`（可编辑参考，默认） | 作为 R2V 参考视频，模型可重新演绎 | 换装 / 换动作参考 |
| `fixed_guide`（固定 Guide） | 整段作为 `MiniMaxH3AddGuide` 锚在帧 0，原样保留 | 视频延长 / 多段连续接续 |
| `boundary`（首尾边界固定） | 首帧锚在帧 0、尾帧锚在末帧，模型在边界间插值 | 角色替换 / 动作迁移 / 中间缺段补全 |

用途节点会把视频连同其原生音频一起锚定，`fixed_guide` / `boundary` 的视频不会再进 R2V 参考通道。

### 音频驱动口型

在 `H3ModelLoader` 的**音频**标签页上传对白音频，自带播放器和起止裁切。H3 原生 `MiniMaxH3AddGuide` 会驱动人物口型对齐音频，无需外接音频驱动节点。

**提示词必须写口型指令**，例如：
```
her lips naturally opening and closing in precise sync with the audio,
lip shapes matching the spoken words
```

## H3 提示词嵌入（v11，ComfyUI v0.34+）

`H3PromptWriter` 新增 `embeddings` 输入：逗号分隔嵌入名，自动在提示词开头注入
`embedding:xxx` 标记（ComfyUI #15697 官方支持）。官方仓库内置 10 个社区风格嵌入：

| 名称 | 效果 |
|---|---|
| `bullet_time` | 子弹时间 |
| `truman_show` | 《楚门的世界》风格 |
| `four_seasons` | 四季更替 |
| `storm_magic` | 风暴魔法 |
| `art_is_explosion` | 爆炸艺术 |
| `kiss_camera` | 接吻镜头 |
| `fire_breath` | 火焰吐息 |
| `blooming_flowers` | 花朵绽放 |
| `dark_magic` | 黑暗魔法 |
| `spiral_ascent` | 螺旋上升 |

嵌入文件放在 `ComfyUI/models/embeddings/`。模板管结构、嵌入管风格，可叠加使用。

## Context IR 提示词增强（v11）

`H3PromptWriter` 新增 `prompt_enhance` 开关（默认关）：开启后生成完初稿，再把提示词
过一遍 LLM 做 Context IR 式增强（提升细节密度 / 镜头语言 / 声音设计），需多一次推理。
仅普通模式生效，剧本模式自动忽略。

## 长视频 / 无缝接续（导演台方案）

> **偏色说明**：本套件**不做**「末帧解码 → 再喂下一段 first_frame」的像素往返接续——
> VAE 解码/编码往返会造成结构性偏色，调参救不回来。改用 **latent 空间接续**，无偏色。

推荐的长视频接续链路：

1. **逐段渲染**：`H3ModelLoader` 每段按 5-10 秒（124~294 帧）分块渲染，显存恒定。
2. **尾帧接续（AddGuide 断帧引导）**：在下一段的**关键帧**标签页把上一段渲染出的
   尾部画面作为关键帧锚在帧 0（已集成官方 `MiniMaxH3AddGuide`），模型从该画面无缝继续。
3. **目标过渡帧**：需要向某个故事板画面过渡时，把目标图作为关键帧锚在接近末尾的帧号，
   模型会自动向该画面 morph。
4. **音频驱动**：配合 longcat / 对白音频标签页，每段自带原生音频。
5. **收尾拼接**：全部段渲染后，用 `H3StitchSegments` 按顺序拼接（去重叠帧 + 合并音频）。

### H3NoiseMask：latent 空间局部重绘 / 续写

`H3NoiseMask` 生成逐 token 噪声蒙版（ComfyUI #15375）：输出接到
`SamplerCustomAdvanced` 的 `denoise_mask` 输入，`0` = 保留原 latent 区域、`1` = 重新生成。
可用于：

- **视频局部重绘 / 物体移除**：`spatial_mask` 传空间蒙版图限制重画区域
- **latent 空间无缝续写**：把上一段尾帧 latent 区域置 `0`（保留）、新帧置 `1`（重画），
  即可在 latent 空间继续生成，完全绕开「解码→再喂」的偏色问题（配合二采工作流使用）

## 本地测试

```bash
pip install pytest
# 设 COMFYUI_ROOT 指向 ComfyUI 源码根目录（含 comfy/ 与 folder_paths.py）
# 找不到时会自动跳过依赖 ComfyUI 的用例
$env:COMFYUI_ROOT = "你的ComfyUI路径"
python -m pytest tests -q
```

覆盖：提示词嵌入注入、AV latent 拆分合并往返、噪声蒙版构建（时间/空间/翻转）、
视频段拼接去重、素材行解析与视频用途分流。

## 关于 micxin2025 模板

`h3_micxin_assets.py` 中的六段式提示词模板基于 [micxin2025](https://github.com/micxin2025) 的 H3 提示词写作工作流改编，在此致谢。原模板保留了英文叙事 + `<d>[Language]...</d>` 对白标签的 H3 标准格式。

## 常见问题

### Q: xueluo 微调模型报错怎么办？
A: xueluo 等社区微调模型可能和当前 ComfyUI/H3 节点版本不兼容。建议使用官方原版模型 + 4步/8步加速 LoRA，最稳定。

### Q: 16GB 显存闪退？
A: 
- 降低帧数：先试 124 帧（~5秒），稳定后再加
- 关闭 TAE 预览：`ModelPreviewOverrideKJ` 的 `preview_frames` 设为 1 或 bypass；或用官方轻量预览器 **TAESD H3**（v0.34+）进一步省显存
- 分辨率降一档：0.4MP → 0.3MP
- 启动加 `--lowvram` 参数

### Q: 人物不开口，音频像背景音？
A: 提示词中每个有对白的镜头必须明确写 `lips opening and closing in sync with the audio`，不要只写 `she says`。同时确保镜头中人物面部/嘴唇清晰可见。

### Q: 提示词嵌入不生效？
A: 确认 ComfyUI 版本 ≥ v0.34；嵌入文件必须放在 `ComfyUI/models/embeddings/`（从 Comfy-Org 仓库
`minimax-h3/embeddings/` 目录复制），文件名即嵌入名。

### Q: 两个插件可以只装一个吗？
A: 可以。AutoDirector 负责提示词写作，H3-helper 负责模型加载。但典型工作流需要两者配合使用。

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。
