# ComfyUI-H3-AutoDirector

MiniMax H3 全自动剧本创作与提示词生成节点集（by micxin2025，集成 micxin2025 h3-prompt-writing 资产）。

## 节点列表

| 节点 | 说明 |
|---|---|
| **H3 Prompt Writer (micxin)** | 概念 → H3 官方六段式提示词。支持 16 种任务模式、剧本模式、资产库联动、Local GGUF / HTTP endpoint 双后端 |
| **H3 Story Setup (micxin)** | 一键短剧创作引擎。一句话创意 → 剧本 + 分镜表 + 图片提示词 + H3六段式视频提示词 |
| **H3 Asset Library (micxin)** | 角色/场景/道具资产管理面板（前端 UI） |
| **H3 Skill Manager (micxin)** | 自定义 skill 模板安装/删除 |
| **H3 Reference Builder (micxin)** | 参考图/视频/音频路径构建 |
| **H3 Prompt Split (micxin)** | JSON 数组 prompt 拆分成最多 8 个独立端口 |
| **H3 Shot Selector (micxin)** | 按索引从 JSON 数组 prompt 列表中选一段 |
| **H3 Shot Queue (micxin)** | 逐镜自动队列，每次 Queue 自动下一镜，尾帧自动接续 |
| **H3 Video Source (micxin)** | 从已保存文件读取视频，供段级重跑取料 |

## 安装

把整个 `ComfyUI-H3-AutoDirector/` 文件夹复制到 ComfyUI 的 `custom_nodes/` 目录，重启 ComfyUI 后端，浏览器 `Ctrl+Shift+R` 刷新。

依赖：`llama-cpp-python`（Local GGUF 模式，可选）、`Pillow`（视觉反推，可选）。

## H3 Prompt Writer 核心特性

- **16 种任务模式**：通用全参考、I2VA、FL2VA、动作迁移、语言克隆、双人对话、3D 动画、产品广告、纸艺定格、品牌宣传、音乐 MV、游戏开场、纸拼贴、手绘实拍融合、高密度蒙太奇等
- **剧本模式**：输出结构化分镜 JSON（角色档案 + 场景档案 + 每镜 prompt/时长/角色/场景），可逐镜渲染
- **资产库联动**：外接角色/场景/道具资产库 JSON，自动引用资产并同步参考图
- **双 LLM 后端**：Local GGUF（ComfyUI 内直接加载，带显存自动降级）/ HTTP endpoint（OpenAI 兼容，支持 Ollama / SiliconFlow / DeepSeek 等）
- **代码级对话打标**：concept 中写 `说话者："台词"` 自动打成 `<d>[Language] 台词</d>`，粤语/普通话/日/韩自动识别
- **bypass_llm**：跳过 LLM，直接复用顶部概念框粘贴的提示词

## H3 提示词铁律（已写死在节点 system prompt）

- 叙事用英文；对白/歌词/画面文字保留原语言，包在 `<d>[Language] … </d>` 里
- 每个镜头自包含：场景/人物/风格在每一镜都原样复述
- 六段式结构：subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music
- 单镜头 2–15s；参考素材：图≤9 / 视频≤3 / 音频≤3，总文件≤12

## 文件结构

```
ComfyUI-H3-AutoDirector/
├── __init__.py                  # 节点注册 + WEB_DIRECTORY="./js"
├── h3_screenwriter.py           # H3PromptWriter — 概念→H3提示词(调用 LLM)
├── h3_story_setup_node.py       # H3StorySetup — 一键短剧创作引擎
├── h3_asset_library.py          # H3AssetLibrary — 资产管理
├── h3_asset_library_api.py      # 资产库 API
├── h3_micxin_assets.py          # 并入的 micxin2025 资产：16 任务模板 + _tag_dialogue
├── h3_reference_builder.py      # 参考路径构建
├── h3_skill_manager.py          # 自定义 skill 管理
├── js/                          # 前端 UI（@引用编辑器、资产面板、tooltip 等）
└── skills/                      # 自定义 skill 模板目录
```