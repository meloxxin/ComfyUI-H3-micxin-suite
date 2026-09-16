---
name: h3-prompt-writing-micxin
display_name: H3 提示词写作（micxin 增强）
description: 把用户的自由输入（文本 + 可选参考图/视频/音频 + 风格选择 + 视频设置）转换为严格符合 MiniMax-H3 官方结构的视频生成提示词，并附渲染尺寸与帧数。当用户要"写 H3 提示词 / 生成不同风格的 H3 视频 / 给 MiniMax-H3 写 prompt / 用 H3 做视频"、或提供文本+图/视频并希望产出 H3 可用提示词时触发。内置 16 种风格引擎（3D 动画 / 极简产品广告 / 纸艺定格 / 品牌宣传 / MV 歌词贴字 / 双人游戏开场 / 纸拼贴 / 手绘实拍融合 / 未来系统蒙太奇 / 首帧锚定 / 首尾帧 / 万能动作迁移 / 固定首帧语音克隆 / 参考语音克隆 / 双人对话）、视频设置换算（aspect_ratio × MP → width/height，duration → length 帧）与 bypass 直出模式（粘贴成品提示词只校验重整）。
agent_created: true
---

# H3 Prompt Writing（micxin 增强版）

把用户的自由输入转换为**严格符合 MiniMax-H3 官方结构**的视频生成提示词，并一并发出**渲染尺寸与帧数**，
便于直接粘进 ComfyUI 的 `MiniMax H3 Reference to Video` 节点的 `prompt` / `width` / `height` / `length`。

本 skill = 官方 `h3-prompt-writing` 的结构模板 + ComfyUI 节点 `h3-prompt-writing (micxin)` 的 **16 风格引擎**
+ `H3 Screenwriter (micxin)` 的**视频设置换算**，合并为一个 WorkBuddy skill。
效果比单一节点好：风格由引擎替你想好（不用动脑），尺寸/时长自动算好（不用手填），还能 bypass 直出。

## 何时触发

- 用户说"写 H3 提示词 / 用 H3 做视频 / 给 MiniMax-H3 写 prompt"，或提供文本 + 图/视频/音频。
- 用户想"换一种风格"（动画/广告/MV/游戏/纸艺/手绘…）而不想自己拼结构。
- 用户已有一段提示词，想校验/重整成官方格式（bypass 模式）。

若用户只是"问 H3 怎么写提示词"而非"帮我写一条"，用对话回答即可，不必硬套模板。

## Workflow

### Step 1 · 判定模式（决定输出 schema，最高优先级）

与官方 `h3-prompt-writing` 一致，先按素材判定五种模式：

- **Ref2VA（全参考）**：图≥2、或 图+视频、图+音频、视频+音频（上限：图≤9、视频≤3、音频≤3，总≤12；音频不能单独提交，须配图像或视频）。输出**六 section**。
- **FL2VA（首尾帧）**：2 张图（首+尾）。**I2VA（首帧）**：1 张首帧。**L2VA（尾帧）**：1 张尾帧。三者均输出**三核心字段** + 首行对齐指令。
- **T2VA（文生视频）**：纯文本。输出**三核心字段**。

素材不足默认 **I2VA 首帧**，在判定行注明"默认首帧"，不要反问阻塞。纯文本按 T2VA。

输出开头强制标注一行：`【风格：<名称>】【模式：<模式>】`。

### Step 2 · 选风格（节点 16 风格引擎）

从下方「风格目录」选一种（或问用户要哪种；用户没说则默认 `H3 通用全参考模版`）。
风格只决定**创意调性**，**不改变** Step 1 的字段结构：
- Ref2VA 类风格 → 读 `references/ref-en.txt`（六 section 权威） + `references/styles.md` 对应附录。
- 基础模式类风格（I2VA/FL2VA/…）→ 读 `references/base-en.txt`（三核心权威） + `references/styles.md` 对应说明。
- 独立模式（动作迁移/对话/语音克隆等）→ 直接读 `references/styles.md` 对应完整指引。

### Step 3 · 视频设置换算（H3 Screenwriter 数学，必做）

向用户确认或沿用其指定的三项，算出渲染参数，随提示词一起输出：

- `aspect_ratio`：16:9 / 9:16 / 1:1 / 21:9 / 4:3（默认 16:9）
- `resolution_mp`：0.2–2.0（步进 0.1，默认 1.0；>1.03MP 超出训练舒适区，偏软偏慢但真实生效）
- `duration_seconds`：2–15（默认 5；H3 单段上限 15s）

换算公式（与节点 `_resolve_resolution` / `_resolve_length` 逐字一致）：

```
a = ASPECT_FACTORS[aspect_ratio]      # 16:9→16/9, 9:16→9/16, 1:1→1.0, 21:9→21/9, 4:3→4/3
mp_px = resolution_mp * 1_000_000
width  = max(32, round(sqrt(mp_px * a) / 32) * 32)   # 对齐到 32 的倍数（H3 canvas multiple）
height = max(32, round(sqrt(mp_px / a) / 32) * 32)
frames = max(5, round(duration_seconds * 24))
length = frames + (5 - (frames % 17)) % 17           # H3 约束：length % 17 == 5
```

输出一个「视频设置」块（用户直接抄进 ComfyUI 的 Ref2VA 节点）：

```
【视频设置】 aspect_ratio=16:9  resolution_mp=1.0  duration=5s
→ width=1344  height=768  length=124
```

常用预设速查（均按公式在 1.0 MP 实算；因对齐 32 倍数，面积略低于档位值 ±几个 %）：
- 16:9 → 1344×736 ｜ 9:16 → 736×1344
- 1:1 → 992×992 ｜ 21:9 → 1536×640 ｜ 4:3 → 1152×864
- 想逼近训练舒适区上限（短边 768、≈1.03MP，即官方 MAX_PIXELS 768×1344 形态）：把 `resolution_mp` 调到约 1.05 即得 16:9 → 1344×768 / 9:16 → 768×1344。
时长→length：5s→124、10s→243、15s→362（H3 约束 `length % 17 == 5`）。

### Step 4 · 套模板 + 写字段（硬规则）

- 叙事用英文；对话/歌词/画面文字在 `<d>[Language]…</d>` 内**逐字保留原语言**（粤语 `[Cantonese]`、普通话 `[Chinese]`、日文 `[Japanese]`、韩文 `[Korean]`、其他用 ISO/英文名，粤语等用原汉字，绝不翻译或罗马化）。
- 风格前缀写在 `[Shot 1]` 开头：`Cinematic / live-action / 2D-animated / 3D CG / claymation / watercolor / vintage film`，或风格专属前缀。
- 镜头时间戳：`[Shot 1]` 无时间戳；后续 `[Shot N] At MM:SS.mmm,` 递增且落在时长内。
- 摄像机运动三维：类型 + 幅度(`with small/large amplitude`) + 速度(`at slow/normal/fast speed`)，写成自然英文动作。
- 说话人稳定 ID `(S1)`/`(S2)`；对话 `<Subject N> (Sx) says: <d>[Language] …</d>`；画外音加 `lips remain completely closed`；屏上文字双引号原样保留。
- `overall_soundscape` 1–4 句英文、不重复对话与歌声；`non_diegetic_music` 1–3 句、只写乐器/速度/节奏/动态、无抽象情绪词；无则 N/A。
- Ref2VA 引用标签 `<Subject/Picture/Video/Audio N>` 先定义、全程复用；`retention_analysis` 用固定标记（可见 `fully_preserved / partially_preserved / attribute_transfer / weak_reference`，音频 `fully_copy / partially_copy / reference / weak_reference`）。
- 4–15s 只放 2–3 个主要动作；保持跨镜头一致性；留白，不逐秒排满；视觉描述在前、音频在后。

### Step 5 · 输出

1. 标注行：`【风格：…】【模式：…】`
2. 结构化提示词块（英文，可直接喂 H3 / Ref2VA.prompt）。
3. 视频设置块（width/height/length）。
4. 简短中文说明（补了什么、镜头/声音怎么排，便于二次修改）。

## 风格目录（16 种，节点引擎）

| 风格 | 输出 schema | 读哪段 |
|---|---|---|
| H3 通用全参考模版（默认） | Ref2VA 六段 | ref-en.txt |
| 3D 动画短片 | Ref2VA 六段 + 3D CG 附录 | styles.md › 3D Animation |
| 极简产品广告 | Ref2VA 六段 + 极简产品附录 | styles.md › Minimalist Product Ad |
| 纸艺定格科普 | Ref2VA 六段 + 纸艺附录 | styles.md › Papercraft Stop-motion |
| 品牌宣传短片 | Ref2VA 六段 + 品牌附录 | styles.md › Brand Promo |
| 音乐 MV 歌词贴字 | Ref2VA 六段 + MV 附录 | styles.md › MV Lyric Subtitle |
| 双人游戏开场 | Ref2VA 六段 + 游戏附录 | styles.md › Co-op Game Intro |
| 纸拼贴讲解 | Ref2VA 六段 + 拼贴附录 | styles.md › Paper Collage |
| 手绘实拍融合 | Ref2VA 六段 + 手绘附录 | styles.md › Handdrawn-live |
| 高密度未来系统蒙太奇 | Ref2VA 六段 + 蒙太奇附录 | styles.md › Speculative System Montage |
| 首帧锚定(I2VA) | I2VA 三核心 | base-en.txt + styles.md › First-Frame Anchor |
| 首帧尾帧(FL2VA) | FL2VA 三核心 | base-en.txt + styles.md › FL2VA |
| 万能动作迁移 | Ref2VA 六段 `[video editing + audio reuse]` | styles.md › Action Transfer |
| 固定首帧语言克隆 | I2VA 三核心 + 语音克隆 | base-en.txt + styles.md › Fixed First-Frame Voice Clone |
| 参考语言克隆 | 依输入选 schema + 语音克隆 | styles.md › Reference Voice Clone |
| 双人对话 | Ref2VA 六段 `[reference generation]` | styles.md › Dual Dialogue |

## Bypass 模式（可选 LLM 开关）

当用户**已有一段现成/半成品提示词**，只想校验与重整：

- 判定行标注 `【模式：bypass】`。
- **不创造性生成**新内容；只做：① 字段名/顺序校准到官方结构；② 对话补 `<d>[Language]…</d>` 标签；③ 摄像机运动补三维；④ 引用标签一致性检查；⑤ 顺手把视频设置块算出来。
- 若用户贴的已是合法官方结构，则原样返回 + 视频设置块即可。

## 角色替换 / 动作迁移（来自节点实战，优先级高于通用模板）

- **只换人、不换分镜**：镜头切换/机位/编舞时间轴沿用参考视频；音频默认 `fully_copy` 原音轨（除非用户要求换）。
- **换脸失效**时用 `[TARGET]` 选择器 3× 强化（"the face must come exactly from <Subject 1>, fully replace the original face, no trace of the original face remains"），**勿切到 `reference generation`**（那会让动作只"相似"而非逐帧复刻，且会丢原视频里第二个人）。
- **多人物**用性别/衣着/位置/动作圈定目标（如 `the leftmost woman in the blue dress`），图片仅提供新脸；其余人物标 `fully_preserved`。
- **完全复刻动作**：`detailed_description` 不让 `<Video 1>` 之外的文本自写运动，机位/切点严格沿用原视频。

## 参考资源

- `references/base-en.txt` — 官方 T2VA/I2VA/FL2VA/L2VA 基础指南（字段规则 + 完整示例），逐字收录。
- `references/ref-en.txt` — 官方 Ref2VA 全参考指南（六 section + 完整示例），逐字收录。
- `references/styles.md` — 节点 16 风格引擎的完整风格附录/独立模式指引（从 ComfyUI 节点 `h3-prompt-writing (micxin)` 移植），按风格名检索。
- `../h3-prompt-writing/references/base-en.txt`、`../h3-prompt-writing/references/ref-en.txt` — 同源官方备份，交叉核对用。
