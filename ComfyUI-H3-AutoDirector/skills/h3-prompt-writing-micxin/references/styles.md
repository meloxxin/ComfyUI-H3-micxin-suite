# H3 风格引擎参考（styles.md）

> 从 ComfyUI 节点 `h3-prompt-writing (micxin)` 移植的 **16 风格附录 + 独立模式提示词**。
> 由 `SKILL.md` 按风格名检索使用。所有叙事一律英文；对话/歌词在 `<d>[Language]…</d>` 内保留原语言（粤语原汉字，绝不翻译/罗马化）。
> 本文件不重复官方 base/ref 的字段规则，只放「风格专属」与「节点实战」内容；字段结构仍以 `base-en.txt` / `ref-en.txt` 为准。

---

## 0. 通用全参考引擎附注（默认风格 `H3 通用全参考模版` 用）

以下规则叠加在 `ref-en.txt` 六 section 之上，用于**防止弱本地模型幻觉**（来自节点的 REVERSE_INFERENCE_BASE，精炼）。

### 0.1 参考素材接地规则（最高优先级）
1. 只能依据用户**文字描述**推断参考素材；绝不能凭空捏造 `<Picture N>`/`<Video N>`/`<Audio N>` 或 `[video editing]`/`[audio reuse]` 等任务类型。
2. 用户没提视频/音频 → **绝对禁止**创建 `<Video N>`/`<Audio N>`，summary 只用 `[reference generation]`（指定首帧锚点时加 `[keyframe completion]`）。
3. 角色外观以用户文字为权威；永不套用经典角色先验（如把"女性化 Ryu"写成男性）。用户未指定的外貌细节（发色/体型/脸型）不臆造，锚定到"the exact appearance shown in <Picture N>"。
4. 每张参考图严格一一映射到清晰的主体；不把同一张图指向两个角色，也不把图误读成无关物体。
5. 不添加用户未提及的参考素材、标签、任务类型或镜头动作。动作/场景必须来自用户文本；参考图只提供"外观/风格"指引。

### 0.2 模式检测优先级（写正式提示词前先判定，首行声明 `[MODE: …]`）
- **FL2VA**：用户明确说两张图分别为"首帧"与"尾帧" → 首帧锚 `<Picture 1>` + 尾帧锚 `<Picture 2>`，summary `[reference generation + keyframe completion]`。
- **L2VA**：1 张图被描述为"尾帧/目标帧" → `<Picture 1>` 对齐结尾，summary `[reference generation + keyframe completion]`。
- **I2VA**：≥1 张图且至少一张是"首帧"，或用户说"image to video/I2VA" → 首行 `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`，summary `[reference generation + keyframe completion]`，[Shot 1] 必须以 "the shot begins from <Picture 1>" 起手。
- **Ref2VA**：≥2 张图但无首/尾锚定，或仅外观参考 → 标准六 section，summary 只用 `[reference generation]`。
- **T2VA**：无任何参考图，仅文本 → 仍用六 section 但无引用标签，subject_definitions 可省略，retention_analysis 写 "N/A — no reference materials provided"。

### 0.3 对话处理规则
- 用户输入的 `说话者："台词"` 已由代码级解析器打成 `<d>[Language] 原文</d>` 格式；**逐字**搬进对应 shot，不重写/不翻译/不概括。
- 稳定说话人 ID `(S1)`/`(S2)`；说到某主体时写 `<Subject N> (Sx) says: <d>[Language] …</d>`。
- 遇到未被预处理的 `说话者："台词"`，自行按同样格式补标签。
- 绝不要把对话改写成"角色开口说话"之类的叙述——那会让 H3 丢失真实台词而自行乱讲。

---

## 1. 风格附录（叠加在 Ref2VA 六段之上）

### 1.1 3D Animation Short (3D CG)
1. **视觉风格**：`[Shot 1]` 用 "3D CG" 主前缀；说明渲染方式：PBR / toon-shaded / cel-shaded / stylized NPR。
2. **角色设计**：3D 角色比例一致、拓扑友好、可 rig 姿势；描述材质（皮肤次表面、布料模拟、金属/橡胶/玻璃高光）。
3. **灯光**：三点布光；主光方向、轮廓光分离剪影、AO 接触阴影；可用 HDRI 环境光。
4. **摄像机**：利用 3D 自由——弧线运动、环绕、荷兰角、无缝景深 racks；必要时镜头内变焦。
5. **环境**：模块化资产搭建；几何密度、材质变化、粒子系统（尘埃/余烬/体积雾）。
6. **运动**：动画十二原则；预备→主动作→跟随→重叠；发丝/布料次级运动；情绪点适度夸张。
7. **色彩脚本**：逐镜可控配色；说明主色调、饱和度、对比方案（互补/类比/三分）。
8. **比例**：默认 16:9 宽银幕；竖屏(9:16) 短内容注明。

### 1.2 Minimalist Product Ad
1. **视觉风格**："minimalist product cinematic" / "clean studio product showcase"；极净构图、大量负空间、无视觉杂乱。
2. **产品为主角**：规则三分或居中对称；如实描述材质（哑光/光泽/金属/半透）与光的交互。
3. **背景**：纯中性色（白/浅灰/柔渐变/无缝弧背），或深暗单色环境；无图案无干扰。
4. **灯光**：影棚级；柔光箱主光缓衰减；轻微轮廓/分离光；产品表面高光传达材质；零硬阴影。
5. **摄像机**：缓慢克制；轻微 push-in 露细节；慢小幅度 pedestal/tracking；静态产品镜保标签可读。
6. **色彩**：单色或近单色；产品强调色是中性场里唯一亮点；高调曝光。
7. **节奏**：4–15s，至多 2–3 个产品角度；每镜服务单一沟通目标（形态/材质/功能/生活方式）。
8. **音频**：极简；轻微 room tone 或 faint ambient hum；non_diegetic 稀疏现代不抢（solo piano / soft electronic pulse）；无旁白除非指定。
9. **文字/叠加**：若出现文字，中文双引号逐字保留。

### 1.3 Papercraft Stop-motion
1. **视觉风格**："Papercraft stop-motion" 主前缀；场景一切看起来由剪/折/层纸构成。
2. **材料语言**：纸艺术语描述所有表面——边缘卡纸厚度、纸纹、折痕、层叠剪纸景深、层间微妙投影；角色有可动纸关节四肢。
3. **动画**：逐帧定格质感；帧间略不完美（手作微抖）；即便 24fps 内部按 12fps 想，运动有重量与触感。
4. **灯光**：顶光或四分之三棚光，强调纸边阴影与层深；暖、亲密调性；轻微暗角聚焦。
5. **布景**：纸艺透视 diorama；层叠剪纸背景带视差；道具为折/弯卡纸；地面为纹理纸面。
6. **摄像机**：基本静止或极慢 push/pan（如锁在定格 rig 上）；偶发 macro push-in 强调细节。
7. **色彩**：略去饱和、手作纸自然色，点缀少量高饱和强调色；白/米白纸基。
8. **讲解结构**：教学/科普内容每镜引一个概念；清晰视觉层级；主体进→演示→出；箭头/标签道具（亦纸艺）引导注意。
9. **音频**：轻触觉 foley（纸沙沙、轻敲、轻揉）；non_diegetic  acoustic guitar / ukulele / glockenspiel——暖、教学、童趣节奏。

### 1.4 Brand Promo
1. **视觉风格**："cinematic brand film" / "commercial showcase"；高端制作质感；全程一致品牌色板与视觉识别。
2. **品牌融合**：品牌/产品在 aspirational 生活方式语境中自然出现（非强行贴广告）；放置有机；品牌标只出现在叙事契合处。
3. **灯光**：电影感调色；暖黄金时刻或冷专业棚光；镜头光晕 sparingly 显高级；高制作抛光。
4. **摄像机**：广告级语汇；rack focus、speed ramps、动态无人机揭示、产品↔生活无缝转场；能量高点 fast pans；关键点 slow-motion beauty shots。
5. **节奏**：6–30s；前 1.5s 钩住眼；结构 setup→conflict→resolution→brand moment；尾帧强化品牌识别。
6. **调性**：aspirational 但真实；留一个人类不完美或 candid 微瞬间，避免过抛光冷感。
7. **色彩**：品牌主导色板；在暖（生活/情绪）与冷（产品/精准）间切换；与品牌指南一致。
8. **音频架构**：音乐驱动情绪弧——低起、推到峰、干净收；音设点出视觉节拍（whoosh/impact/环境切）；旁白若存在用 calm confident 调。
9. **尾帧**：必须含清晰品牌收尾——品牌 logo lockup、产品 hero 帧或难忘品牌符号。

### 1.5 MV Lyric Subtitle
1. **视觉风格**："Music video" 主前缀；视觉节奏锁歌曲节拍结构；每个 cut/运动/视觉事件对齐鼓点、主副歌转场或歌词时刻。
2. **歌词显示**：屏上歌词/字幕作为 3D 场景空间内**可见文字元素**（非叠加 UI）——霓虹、投影文字、漂浮排版、手机屏、墙涂鸦、衣字；歌词中文双引号逐字保留。
3. **音乐结构意识**：视觉段映射 song structure——intro/verse1/pre-chorus/chorus/verse2/bridge/final chorus/outro；每段不同视觉处理；chorus=最高视觉能量与制作值。
4. **表演**：艺人/表演者为中央主体；捕捉表演能量——肢体节奏、表达手势、眼神连接；跨段多造型/多出现。
5. **叙事线（可选）**：表演外有故事则交叉剪表演镜与叙事 B-roll；表演驱动情绪传达，叙事提供视觉变化。
6. **灯光**：演唱会/舞台启发；彩色 gel、节拍同步频闪、背光剪影、实用灯作场景装饰；随音乐变。
7. **摄像机**：MV 语法——节拍上 fast cuts、长音 slow motion、能量峰 fast pans、展规模 aerial/drone、情绪特写 extreme close-up。
8. **色彩**：高度风格化，常互补对比色板；verse（冷/去饱和）与 chorus（饱和/鲜明）切换；允许黑白段作对比。
9. **音频**：音乐本身是主音频；overall_soundscape 描述额外加的生产环境；non_diegetic_music: N/A（授权音乐即配乐）；用户提供 `<Audio N>` 参考音轨即该音乐曲。

### 1.6 Co-op Game Intro
1. **视觉风格**："Game cinematic" / "in-engine cutscene" 主前缀；实时渲染质感（非预渲 CGI）；允许少量引擎痕迹——受控多边形密度、清晰纹理分辨率、bloom/HDR/景深后处理。
2. **双主角结构**：恰好两个主角（co-op 玩家 avatar）；subject_definitions 建立极高辨识剪影（不同体型/配色/武器装备/物种）；任意构图瞬认。
3. **动态默契**：视觉叙事展合作——背靠背、同步动作、互补能力秀、共享眼神、fist-bump 或等效羁绊手势。
4. **世界引入**：[Shot 1]–[Shot 3] 立游戏世界调性与赌注；传达类型（sci-fi 设施/fantasy 界/post-apoc 废土/cyberpunk 城）；一个"敬畏"级环境揭示。
5. **威胁暗示**：阴影身影、逼近结构、军队剪影或环境危害暗示对立势力/核心冲突而不全揭；让玩家觉得"我们要一起打那个"。
6. **玩法暗示**：1–2 个视觉时刻暗示 co-op 机制——合技效果、协同进入、互补工具使用或环境谜题。
7. **摄像机**：游戏过场语汇——epic wide 建置、戏剧角色揭示（穿前景障碍 slow push）、split-screen 同显双主角、动作节拍 rapid zoom。
8. **节奏**：15–45s；快开钩→世界建→角色介→威胁暗示→英雄节拍→logo/title-card 时刻。
9. **音频**：管弦/电子混合配乐；渐强强度曲线；引入难忘音乐 motif/theme；title card 强冲击重音。

### 1.7 Paper Collage
1. **视觉风格**："Paper collage art" 主前缀；整场景像由撕/剪/层纸屑拼成——照片、杂志剪报、纹理纸、布样、拾得物。
2. **拼贴纹理语言**：边缘描述撕（deckle/纤维）、剪（干净剪刀边）或撕破；注意元素间层叠阴影深；混纹理：新闻半调、牛皮纸纹、光泽照片纸、薄半透纸、瓦楞纸板。
3. **构图**：拼贴审美——略非对称、刻意手作组装感；元素重叠露可见接缝线；尺度玩（小细节旁超大物体）；混照片元素与平色块。
4. **动画**：拼贴元素带手作 DIY 质；碎屑滑入位、翻、从他层后 zoom；转场用拼贴隐喻——撕开露、掀层显下、散再组。
5. **色彩**：折中但和谐；混照片写实（剪报元素）与图形平色（色纸）；一条统一色彩线串起 disparate 元素。
6. **讲解逻辑**：每个视觉元素服务信息传达；人物替身（组装人形）演示概念；道具符号（亦拼贴）代数据点；箭头连线（纸条或手绘线）引导眼。
7. **排版（若有）**：文字作剪字、旧纸打字机、杂志 ransom-note 风或手绘墨于纸纹；文字内容逐字保留。
8. **摄像机**：基本静止"画布视图"，如看拼贴艺术品；轻 zoom 聚焦区域；偶层间视差移深。
9. **音频**：翻页沙、纸皱、轻剪 snap、胶带拉声；non_diegetic  acoustic folk / lo-fi hip-hop / soft indie——创意、手作房氛围。

### 1.8 Handdrawn-live Fusion
1. **视觉风格**："Handdrawn-live fusion" 主前缀；场景含实拍元素与手绘动画元素，无缝合成。
2. **融合方法**：每镜明确哪些元素实拍、哪些手绘；常见模式：实拍环境+手绘角色 / 实拍演员+手绘特效道具符号 / 手绘思考泡涂鸦注解浮现于实拍帧上 / 实拍基层+手绘变形叠加（物体 morph、色渗入线稿）。
3. **手绘审美**：速写线质（铅笔/墨/炭/粉彩）；可变线宽；略不完美有机线（非矢量完美）；色可为水彩洗、marker 填或平 cel-shade。
4. **融合点**：手绘元素必须真"存在"于实拍空间——投正确阴影、被实物遮挡、落真实表面、匹配透视；融合点决定幻觉是否成立。
5. **交互**：实拍角色应注意到/反应手绘元素（瞥、触、被手绘变形影响）；这卖合成。
6. **灯光匹配**：手绘元素采用实拍场景光向、色温、阴影软度；手绘高光对齐真实光源。
7. **摄像机**：可全实拍机位（手绘元素 track 进素材），或混合运动；手绘机位可比实拍基层更松/更具表达。
8. **转场时刻**：关键故事节拍可实拍↔手绘变换——实拍手空中画、画显形；手绘门开向实拍空间；墨渗过实拍帧变场景。
9. **音频**：实拍 diegetic 声（真房间 tone、脚步、物件处理）+ 手绘 foley（铅笔刮、纸声、墨 whoosh）用于手绘元素交互；配乐桥两界——现实用 acoustic、手绘魔法用 melodic/hopeful。

### 1.9 Speculative System Montage（高密度未来系统蒙太奇）
1. **主体验证（最高优先级）**：用户上传参考图是**唯一锚定主体**；绝不改/变形/重塑主体结构或外观。单主体直接锁；多主体须请用户指定核心主体。五类：Logo / Hardware product / App UI / Portrait / Material。
2. **创意收敛（按类）**：Logo→线框描边、全息扫描、粒子拆+组、数据流轨道；Hardware→外壳扫描、内部透视、组件 macro、结构分解蒙太奇；App UI→控制粒子解构、数据流连通、全息触控、浮参面板；Portrait→全身光束扫描、人体 wireframe、生物 HUD 参、神经数据流（严禁改脸/肖像）；Material→纹理扫描、微结构可视、材质粒子流、macro 特写。
3. **镜头规格**：15s 时长、8–12 个高密度快切短镜、AE tech-mashup 节奏；须含 scan/disassembly/orbit/macro/reassembly/freeze-frame 节拍。
4. **视觉统一**：纯黑极简背景上冷蓝 sci-fi 光；premium AE 级运动设计；硬核 tech ad 快切节奏；仅精细不可读参数 UI、流动粒子、蓝 HUD 扫描线、全息网格、爆/重组动画；绝不拼出长可读文字。
5. **全局禁止**：无主体改动；无多余/散落物体；无长可读屏文；无暖色调；无杂乱背景；无慢镜；无叙事/生活 footage。
6. **音效**：冷 tech 环境——subtle servo/数据 hum、精确 UI blips、低电子 drone；non_diegetic_music: driving electronic / glitch-tech 配乐，紧节奏脉冲同步快切，向 reassembly 节拍推；仅当请求静音时 N/A。

---

## 2. 独立模式完整指引

### 2.1 First-Frame Anchor (I2VA 首帧锚定)
你是 H3 "full-reference mode" **首帧锚定型** prompt 引擎：把用户简概念转六 section 格式，首帧锚定 `<Picture 1>`、动作自首帧向前发展。

- 首行必须：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`
- subject_definitions 须把 `<Picture 1>` 定义为首帧锚：`<Picture 1> is the first frame of [Shot 1], showing...`
- detailed_description 的 `[Shot 1]` 必须从 `<Picture 1>` 的构图/人物/场景续写：`the shot begins from <Picture 1>, showing <Subject 1>...` 再描述后续动作。
- 不脱离首帧构图；后续镜可推/拉/切，但初始态必须锚 `<Picture 1>`。
- 输出结构：首行锚 + 六 section（英文）。summary 以 `[reference generation + keyframe completion]` 起。
- 硬规则：叙事英文，对话 `<d>[Language] 原文</d>` 锁原语言；引用标签全篇一致；retention_analysis 每标签一行；稳定 ID (S1)/(S2)。
- 禁止：对话重复（每镜推进或换）、自创歌唱/音乐（除非用户要求）、自写对话（只写用户给的）。

### 2.2 FL2VA（首帧尾帧）
用户提供恰好两张参考图：目标视频首帧与尾帧。写连续连贯视觉叙事，自然从首帧过渡到尾帧。

- 首行对齐：`How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns to 0.00s of the target video; Picture 2 (from Shot N) aligns to S.SSs of the target video.`
- 空行后：`integrated_multimodal_description: [Shot 1] <风格前缀>, <精确匹配 Picture 1 的构图>, <动作起始>... [Shot N] At MM:SS.mmm, <向 Picture 2 过渡>, <精确匹配 Picture 2 的收尾构图>.`
- `overall_soundscape`（1–4 句英文）、`non_diegetic_music`（1–3 句；无则 N/A）。
- 规则：[Shot 1] 须描述 Picture 1 精确视觉作开场；尾镜落于匹配 Picture 2 的构图；过渡须连贯因果驱动（非随机跳）；风格前缀 Cinematic/live-action/2D-animated/3D CG/claymation/watercolor/vintage film；摄像机运动三维；对话 `<d>[Language]…</d>` 逐字；屏文中文双引号；时长通常 4–15s，短时长 2–4 镜。
- 禁止：输出 markdown 围栏/标题/元注释；捏造对话；自创歌唱/配乐（除非要求）。

### 2.3 Action Transfer（万能动作迁移 / 换脸）
用户要把参考视频的**动作、剪辑、摄像机运动、时序**迁移到参考图的新主体上。核心原则：**逐帧复刻动作，只换人**。

- 输出 Ref2VA 六 section（叙事英文）。summary **必须以 `[video editing + audio reuse]` 起**。
- 关键规则：
  1. 摄像机与剪辑**只来自参考视频**：不设计新镜/剪/运镜；从 `<Video 1>` 抽精确切点、镜型、运镜、节奏、时序，逐字复刻进 detailed_description。
  2. 换脸（首要）：每镜原脸出现处显式声明替换——用 `[TARGET]` 选择器圈定（如 `[TARGET] man in red jacket`）；写三遍强调 "the face must come exactly from <Subject 1>, fully replacing the original face, leaving no trace of the original face."；多人用性别/衣着/位置/动作圈定目标。
  3. 其余全保留：灯光/背景/地面/构图/比例/编舞/动作时序/机位标 fully_preserved 或 partially_preserved。
  4. 音频默认 fully_copy 原音轨（除非用户要求换）。
  5. 比例与时长默认匹配参考视频；取片段则在 `<Video 1>`/`<Audio 1>` 描述注范围。
  6. 动作描述不自写运动，让 `<Video 1>` 驱动；文本只描述"谁在做"（换后角色），不描述"什么动作"。
  7. 风格前缀 `[Shot 1]`：Cinematic/live-action/2D-animated/3D CG/claymation/watercolor/vintage film。
  8. 对话 `<d>[Language]…</d>` 逐字，稳定 ID (S1)(S2)。
- 禁止：切到 "reference generation" 模式（削弱动作保真、丢第二人）；重设计镜/新转场；输出 markdown/标题/元注释；捏造对话。

### 2.4 Fixed First-Frame Voice Clone（固定首帧语言克隆）
用户提供：1 张参考图（固定首帧，t=0.00s 逐字出现）+ 1 段参考音频（提供克隆音色/音质）。生成视频首帧精确匹配参考图，且视频中任何语音/声音用从参考音频克隆的音色。

- 输出 I2VA 格式（叙事英文）。首行：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.` 空行后三核心字段。
- `integrated_multimodal_description`：[Shot 1] 精确描述 Picture 1 作冻结/开场帧（每细节须匹配），然后场景活起来…[Shot N] At MM:SS.mmm, …
- `overall_soundscape`：环境 1–4 句；视频中任何语音须用从 `<Audio 1>` 克隆的音色，描述克隆嗓特征（音高/音质/口音）。
- `non_diegetic_music`：背景配乐 1–3 句；无则 N/A。
- 关键：首帧锁——[Shot 1] 须全细节描述 Picture 1，作锚点 t=0.00s 视觉一致；克隆音色——在 overall_soundscape 注明 "All speech uses the voice timbre cloned from <Audio 1> — [简短嗓特征]"。
- 禁止：偏离/改 Picture 1；输出 markdown/标题/元注释；捏造对话。

### 2.5 Reference Voice Clone（参考语言克隆）
用户提供一段参考音频，其音色（音高/音质/口音/节奏/发声质感）应被克隆并应用到生成视频的语音/音频。

- 仅音频（无图）→ 用 T2VA 格式（叙事英文）：`integrated_multimodal_description` 中任何说话者用从 `<Audio 1>` 克隆的音色。
- 同时有图 → 按 I2VA/FL2VA/L2VA/Ref2VA 适配，并加语音克隆注。
- 语音克隆规则：
  1. 分析 `<Audio 1>`：性别年龄、音域（高/中/低）、口音方言、语速、发声质感（气声/共鸣/平滑/粗粝）、情绪调。
  2. 一致应用：一旦从 `<Audio 1>` 建立嗓_profile，生成视频每个说话者都用此克隆音（除非多段参考音对应多个 distinct 声）。
  3. Ref2VA 在 retention_analysis 记：`<Audio 1> (voice timbre): fully_copy — the voice timbre cloned from this reference is applied to all speech in the target video.`
  4. 在 soundscape 显式写："The speech in this video uses the voice timbre cloned from <Audio 1> — [音高/音质/口音/语速/发声质感描述]."
  5. 表达（调/节奏/情绪）写在 `<d>` 外英文；spoken 原词在 `<d>` 内原语言；中文方言标 [Cantonese]/[Mandarin]/[Shanghainese]/[Hokkien]/[Sichuanese] 原汉字。
- 通用：风格前缀 `[Shot 1]`；摄像机三维；对话 `<d>[Language]…</d>` 逐字。
- 禁止：输出 markdown/标题/元注释；捏造对话；自创歌唱/配乐（除非匹配参考音风格）。

### 2.6 Dual Dialogue（双人对话）
你是 H3 双人对话视频 prompt 引擎；两角色自然、有情绪共鸣的对话/对白驱动场景，对话本身是创意主驱动——摄像机/场景/动作都服务对话。

- 核心：每镜都为传达/支撑/回应对话而存在；不是偶尔抖机灵的动作戏，是被可视化为视频的对话。
- 说话人：恰好两个 (S1)(S2)；subject_definitions 建立极高辨识身份（外貌/年龄/性别/嗓特征/性格暗示）；来自参考图则各给 `<Subject N>`；跨镜 ID 一致；除非指定不引入第三说话人。
- 输出 Ref2VA 六 section（叙事英文），summary 以 `[reference generation]` 起。
- 对话规则：
  1. 对话驱动一切：从对话钩子起 detailed_description；每个运镜/表情变/动作关联谁在说、如何反应、对话暗示。
  2. 自然流：S1/S2 交替（除非剧作需要，不多于 2 连独白）；每线推进对话——同意/反对/问/揭示/回避/情绪回应；适当加插话/打断/重叠（方向注出）；静默与停顿是有效戏剧工具（显式注出）。
  3. 情绪弧：开场立语境/张力/问 → 中升/复杂/深 → 收 payoff/理解/冲突/悬疑。
  4. 非言语：微表情、肢体语（前倾/抱臂/焦躁/避眼）、空间动态（距变/站坐/进出）。
  5. 潜台词：说与意可不同；在 retention_analysis 或动作描述注出；非每线字面。
  6. 格式：`<Subject N> (Sx) says: <d>[Language] …</d>` 内 `<d>` 是用户口播原文逐字原语言；中文方言标 [Cantonese]/[Mandarin]/[Shanghainese]/[Hokkien]/[Sichuanese] 原汉字；括号舞台指示表传递：(pauses, looks away)(leaning in)(barely above whisper)。
  7. 环境作情绪容器：紧张对话用紧/限空间；揭示/和解用开/阔空间；环境（光/天/时）随情绪拍变。
- 视觉/音频：风格前缀 `[Shot 1]`；摄像机多中近与双人中景覆盖对话，切反应镜，仅慢动机位；overall_soundscape 对话时 room tone/呼吸/衣动/物件处理（全静才 N/A）；non_diegetic_music 重对话段极简/无，情绪峰 swell（无则 N/A）。
- 禁止：写像解说/信息倾倒的对话；跨镜重复对话；输出 markdown 围栏/标题/元注释；捏造对话（用户给上下文/主题可生成合适对话）。

---

## 3. 对话保留铁律（最高优先级，覆盖一切其他指令）

- user_prompt 中任何已包在 `<d>[Language] … </d>` 内的对话/歌词/屏文，是用户**原口播词逐字**；必须**原样**搬进正式提示词对应 section（detailed_description 或 integrated_multimodal_description）——标签、口语文本、语言标记**不得**更改/删除/移出 `<d>`。
- 若 user_prompt 仍含未标记 `"说话者："line""` 形式对话，也标为 `<Subject N> (Sx) says: <d>[Language] 原线</d>` 并逐字保留；用稳定 ID (S1)/(S2) 与方言标（[Cantonese]/[Mandarin]/[Shanghainese]/[Hokkien]/[Sichuanese]）原汉字，绝不翻译或罗马化。
- **绝对禁止**把 `<d>` 内真实台词改写成"角色开口说话"或"小明说话"之类的叙述——那会让视频丢失真实对话、模型自由发挥，是本系统严重错误。
