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

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。
