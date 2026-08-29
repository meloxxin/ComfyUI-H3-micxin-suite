# ComfyUI H3 micxin Suite

MiniMax H3 全流程自定义节点套装，包含**提示词写作**和**全资源模型加载**两大插件，专为短剧/剧情视频生成优化。

## 包含的插件

### 1. ComfyUI-H3-AutoDirector — 剧本与提示词写作

| 节点 | 功能 |
|---|---|
| `H3PromptWriter` (micxin) | 六段式 H3 提示词生成器，内置 micxin2025 16 种任务模板，支持 bypass_llm 手写剧本 |
| `H3Screenwriter` | 多镜头剧本自动写作，输出 JSON 到 `input/rift_prompts/` |
| `H3StorySetup` | 短剧故事设定（风格、类型、镜头数、角色/场景资产库） |
| `H3AssetLibrary` | 角色/场景资产管理库，支持参考图绑定 |
| `H3SkillManager` | 提示词写作技能管理 |
| `H3ReferenceBuilder` | 参考图构建器 |

### 2. ComfyUI-H3-helper (micxin) — 全资源模型加载

| 节点 | 功能 |
|---|---|
| `H3ModelLoader` (R2VA AIO) | 全资源输入中心：图片/视频/音频/关键帧统一上传，自带播放器与裁切，集成 MiniMaxH3AddGuide 原生音频驱动 |
| `H3SeparateAVLatent` | 分离 H3 联合音视频 latent 为视频+音频 |
| `H3CombineAVLatent` | 合并视频+音频 latent 为联合 AV latent |

## 安装

### 要求

- **ComfyUI v0.31+**（需要 `comfy_api.latest` 和内置 H3 节点支持）
- Python 3.10+
- NVIDIA GPU（建议 16GB+ 显存）

### 步骤

1. 下载本仓库
2. 将 `ComfyUI-H3-AutoDirector` 和 `ComfyUI-H3-helper` **两个文件夹**都复制到 ComfyUI 的 `custom_nodes/` 目录
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

### 音频驱动口型

在 `H3ModelLoader` 的**音频**标签页上传对白音频，自带播放器和起止裁切。H3 原生 `MiniMaxH3AddGuide` 会驱动人物口型对齐音频，无需外接音频驱动节点。

**提示词必须写口型指令**，例如：
```
her lips naturally opening and closing in precise sync with the audio,
lip shapes matching the spoken words
```

## 关于 micxin2025 模板

`h3_micxin_assets.py` 中的六段式提示词模板基于 [micxin2025](https://github.com/micxin2025) 的 H3 提示词写作工作流改编，在此致谢。原模板保留了英文叙事 + `<d>[Language]...</d>` 对白标签的 H3 标准格式。

## 常见问题

### Q: xueluo 微调模型报错怎么办？
A: xueluo 等社区微调模型可能和当前 ComfyUI/H3 节点版本不兼容。建议使用官方原版模型 + 4步/8步加速 LoRA，最稳定。

### Q: 16GB 显存闪退？
A: 
- 降低帧数：先试 124 帧（~5秒），稳定后再加
- 关闭 TAE 预览：`ModelPreviewOverrideKJ` 的 `preview_frames` 设为 1 或 bypass
- 分辨率降一档：0.4MP → 0.3MP
- 启动加 `--lowvram` 参数

### Q: 人物不开口，音频像背景音？
A: 提示词中每个有对白的镜头必须明确写 `lips opening and closing in sync with the audio`，不要只写 `she says`。同时确保镜头中人物面部/嘴唇清晰可见。

### Q: 两个插件可以只装一个吗？
A: 可以。AutoDirector 负责提示词写作，H3-helper 负责模型加载。但典型工作流需要两者配合使用。

## 示例工作流

`workflows/` 目录包含开箱即用的示例工作流：

| 文件 | 说明 |
|---|---|
| `H3_R2VA_AIO_micxin_example.json` | H3 R2VA AIO 完整示例：咖啡杯特写 + 关键帧音频驱动。使用官方 int8 UNet + 4步加速 LoRA + MiniMax 8B LLM + 普通 CLIP 32B。**仅依赖本套件节点，可直接运行。** |
| `H3_Extender_one_click_drama_example.json` | 一键短剧示例（基于 Extender 长视频链式生成）。展示 H3StorySetup → H3AssetLibrary → H3PromptSplit → MiniMaxH3Extender 的完整短剧流水线。**需要额外安装 Extender 节点（非本套件），详见下方说明。** |
| `assets/example_keyframe_coffee.jpg` | 示例关键帧图片（咖啡杯特写） |
| `assets/example_audio_jazz.wav` | 示例音频驱动（爵士乐片段） |

**使用方法**：将 `assets/` 下的示例文件复制到 `ComfyUI/input/` 目录，然后加载工作流即可直接运行。

### 关于一键短剧示例工作流的说明

- **角色名均为虚构**：工作流中的"霞姐""德叔""阿Yan"等角色名均为虚构示例，不指代任何真实人物，可随意替换为你自己的角色设定。
- **参考图路径已清空**：所有角色/场景参考图的本地路径已清空，使用时需在 `H3AssetLibrary` 或对应节点中自行上传参考图。
- **依赖额外节点**：该工作流使用 `MiniMaxH3Extender`、`MiniMaxH3MotionContextDiskFinalDecode`、`MiniMaxH3ReferencePackBridge`、`MiniMaxH3PromptPackBridge` 等节点，**这些节点不属于本套件**，需自行安装对应的 Extender 节点包（基于 ComfyUI-H3-Motion-Context 生态，部分版本可能为付费或私有分发）。
- **已知缺陷**：一键 Extender 方案存在角色一致性差、场景连贯性弱、口型不稳定、显存占用极高、黑盒难调试、依赖链脆弱等问题（详见下方"一键短剧的已知缺陷"）。建议进阶用户使用，新手推荐从 `H3_R2VA_AIO_micxin_example.json` 单镜头工作流入手。

## 关于 MiniMax H3 Extender（非本套件节点）

> **重要声明**：`MiniMaxH3Extender`、`MiniMaxH3MotionContextDiskFinalDecode`、`MiniMaxH3ReferencePackBridge`、`MiniMaxH3PromptPackBridge` 等节点**不属于本套件**，本仓库不包含这些节点的代码，也不提供其工作流文件。

### 节点来源与依赖

这些 Extender 系列节点基于 **[ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context)** 生态开发，用于长视频分段链式生成。部分衍生版本（如 MultiRef fork）增加了参考图打包、提示词打包等桥接节点。

如需使用 Extender 节点，请自行搜索并安装对应的自定义节点包（部分版本可能为付费或私有分发）。本套件不对 Extender 节点的功能、稳定性或兼容性负责。

### "一键短剧"工作流的已知缺陷

基于 Extender 的一键短剧工作流虽然方便，但存在以下结构性问题：

1. **角色一致性难以保证**：多镜头分段生成时，角色外貌、发型、服装在不同镜头间容易漂移，尤其超过 3 个镜头后
2. **场景连贯性差**：镜头切换时环境光照、物体位置、时间线容易断裂，Motion Context 只能缓解不能消除
3. **音频口型不稳定**：长视频分段后，每段音频独立驱动，口型对齐在拼接处容易错位
4. **显存占用极高**：Extender 使用磁盘缓存 + 最终解码，虽然降低了峰值显存，但生成速度慢，16GB 显存跑 5 段以上容易 OOM
5. **工作流黑盒化**：节点参数多、内部逻辑复杂，出问题时难以定位和调试，新手基本无法调整
6. **依赖链脆弱**：Extender 依赖 Motion Context、Bridge 节点、特定版本的 H3 模型，任何一个环节更新都可能导致整个工作流失效

**建议**：对于短剧创作，优先使用本套件的 `H3PromptWriter` + `H3ModelLoader` 单镜头工作流，逐镜生成后手动剪辑拼接，可控性和稳定性远高于一键 Extender 方案。

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。
