/**
 * h3_screenwriter.js (ComfyUI-H3-AutoDirector)
 *
 * v7.2 决定：彻底抛弃 addDOMWidget + widgets.unshift。ComfyUI 1.x 下
 * addDOMWidget 在 ensureEditor 时机不可靠、LiteGraph 会在 widget.value
 * 反序列化后重排数组, unshift 到 widgets[0] 经常失效（用户实测截图证实：
 * h3_concept_editor / h3_model_row 横向容器从未渲染出来）。
 *
 * 改用 LiteGraph 原生 STRING widget (concept_text) — ComfyUI 默认建一个
 * <textarea>。给 widget.computeSize 设大尺寸（≥320px），给 widget.element
 * 加 @ keyup 监听触发 @ 弹窗（替代 contenteditable）。
 *
 * Backend 折叠：直接 hook backend.widget.callback（标准做法）。多个
 * requestAnimationFrame + setTimeout 兜底覆盖 widget 异步初始化。
 *
 * 2026-08-18 v7.2 changes (this rewrite):
 *  - delete addDOMWidget h3_concept_editor / h3_model_row
 *  - concept_text 是原生 STRING widget，widgets[0]，默认大尺寸
 *  - @ 下拉挂到 widget.element (textarea) 的 keyup/input
 *  - backend/gguf_name/mmproj_name 三个原生 widget 竖直排列（不用横向 DOM 容器）
 *  - 删 hideWidget 调用（LiteGraph widget.element 在 attachEditor 时尚未挂载，
 *    element 折叠失效——改用 widget.computeSize 返回 0 实现"视觉折叠"）
 */
import { app } from "../../scripts/app.js";
import { ensureAllTooltipsShown } from "./tooltip_hooker.js";
import { ensureMigrationsApplied } from "./widget_value_migrator.js";

const NODE_CLASS = "H3PromptWriter";
const CONCEPT_WIDGET_NAME = "concept_text";  // INPUT_TYPES.required 第一位 (节点顶部 widgets[0], v7.2.1 起)

// v8: 高级 LLM 设置折叠组 —— advanced_settings 收起时这些 widget 全部 fold。
// 顺序: llm_base_url / model / api_key / temperature / seed /
//       n_gpu_layers / keep_loaded / bypass_llm (mmproj_name 之后的全部)
// v8 删了 auto_save / filename / n_ctx / prompt_override（bypass 改用顶部 concept_text）。
const ADVANCED_GROUP = [
    "llm_base_url", "model", "api_key",
    "temperature", "seed",
    "n_gpu_layers", "keep_loaded",
    "bypass_llm",  // v8: prompt_override 已删，bypass 改用顶部 concept_text 粘贴提示词
];

/* ---- input-types accessor (for schema-version migrations) ---- */
function getInputTypes() {
    try {
        const def = app.getNodeDef && app.getNodeDef(NODE_CLASS);
        if (def && def.input && (def.input.required || def.input.optional)) {
            return { required: def.input.required, optional: def.input.optional };
        }
    } catch (e) {}
    return null;
}

/* ---- widget helpers ---- */
function getWidget(node, name) {
    return node.widgets && node.widgets.find((w) => w && w.name === name);
}

/**
 * Fold (collapse) a widget visually WITHOUT touching w.hidden / w.type —
 * those would break widgets_values indexing across disk reloads. We
 * achieve visual collapse by:
 *   1. computeSize returning [0, 0] (LiteGraph skips layout)
 *   2. draw() returning undefined (LiteGraph skips default draw)
 * Both setters are also scheduled for the next frame in case LiteGraph
 * builds the widget asynchronously after attachEditor.
 */
function foldWidget(w, attempts) {
    if (!w) return;
    try { w.computeSize = () => [0, 0]; } catch (e) {}
    try { w.draw = () => {}; } catch (e) {}
}

function unfoldWidget(w) {
    if (!w) return;
    try {
        delete w.computeSize;
    } catch (e) { w.computeSize = undefined; }
    try {
        delete w.draw;
    } catch (e) { w.draw = undefined; }
}

/* ---- 任务模式 → 概念框示例提示词（v10.3）----
   仅"自定义模式"提供示例；官方示例模式（通用全参考 → 手绘实拍融合）
   不在本表 → 切换时概念框保持空白。切换时仅在概念框为空时填充，
   不覆盖用户已写内容；填充后可全部删除或部分修改。 */
const TASK_MODE_CONCEPT_EXAMPLES = {
    "首帧锚定(I2VA)": "提供一张首帧参考图 <Picture 1>：一位年轻女性站在海边栈道上，阳光明媚。\n请以 <Picture 1> 为视频首帧（0.00s 完全一致），从这张构图延续展开动作：她转身面向镜头微笑，海风吹动头发，随后缓步走向镜头。\n保留 <Picture 1> 的人物、构图和场景，只让动作自然发展。",
    "首帧尾帧": "提供两张参考图：<Picture 1> 是首帧（角色站在门口），<Picture 2> 是尾帧（角色坐在窗边沙发上）。\n请生成一段从 <Picture 1> 构图自然过渡到 <Picture 2> 构图的连贯视频：角色走进房间、穿过空间、最终在窗边沙发坐下，结尾构图精确匹配 <Picture 2>。\n保留两张图的场景与人物特征。",
    "万能动作迁移": "提供动作参考视频 <Video 1>（一段舞蹈/走位动作）和人物参考图 <Picture 1>（目标角色的脸和服装）。\n请把 <Picture 1> 的人物替换进 <Video 1>：保留 <Video 1> 的全部肢体动作、运镜和节奏，完全替换原视频人物的脸（只能出现 <Picture 1> 的脸）。\n背景改为：霓虹灯下的城市天台夜景。保留 <Video 1> 原音频。",
    "固定首帧语言克隆": "提供首帧参考图 <Picture 1>（角色在厨房做饭）和声纹参考音频 <Audio 1>。\n视频以 <Picture 1> 为 0.00s 固定首帧，随后画面动起来；所有台词使用 <Audio 1> 的声纹音色，不要复读 <Audio 1> 的原词。\n台词：角色说：<d>[Chinese] 今晚我给你做一道拿手菜！</d>",
    "参考语言克隆": "提供声纹参考音频 <Audio 1>（一位女性自然说话的录音）。\n生成一段视频，所有说话角色使用 <Audio 1> 克隆的声纹音色，不复读原词：\n场景：深夜书店，年轻女店员整理书架，对镜头说：<d>[Chinese] 欢迎光临，今晚打烊前全场八折。</d>",
    "双人对话": "两位角色（<Picture 1> 男性、<Picture 2> 女性）在咖啡馆进行一场自然对话，对话驱动全片，镜头和动作都服务于台词：\n男（S1）说：<d>[Chinese] 你真的决定辞职去旅行？</d>\n女（S2）说：<d>[Chinese] 攒了三年钱，再不走就走不动了。</d>\n男（S1）说：<d>[Chinese] 那我陪你走第一站。</d>\n场景氛围温馨，结尾两人相视而笑。",
    "高密度未来系统蒙太奇": "参考图 <Picture 1>：某电子产品（或 Logo / App 界面 / 人像 / 材质）。\n请围绕 <Picture 1> 制作 15 秒高密度科技蒙太奇：8-12 个快速剪辑镜头，包含扫描线 / 结构分解 / 粒子环绕 / 爆炸重组 / 定格收尾。\n冷蓝色科技光效 + 纯黑极简背景，禁止改变主体结构与外观，禁止长串可读文字，禁止暖色调。",
    "多参考多轨分镜": "提供多张参考图：<Picture 1> 主角（红色冲锋衣登山者）、<Picture 2> 雪山营地场景。\n请协调多素材生成一段分镜视频，逐镜说明：画面中的主体、环境音、对话、运镜。\n主体外观全程锁定 <Picture 1> / <Picture 2> 设定，各镜之间不漂移。",
    "指令式视频编辑": "参考视频 <Video 1>，我提供了它的三张关键帧图片（首/中/尾），请基于这些关键帧理解视频内容。保留：构图、人物、运镜、时序；修改：XXX。summary 用 [video editing]。",
};

function applyTaskModeConcept(node) {
    const tm = getWidget(node, "task_mode");
    const cw = getWidget(node, CONCEPT_WIDGET_NAME);
    if (!tm || !cw) return;
    const sample = TASK_MODE_CONCEPT_EXAMPLES[tm.value];
    if (!sample) return;  // 官方示例模式 → 不填充，保留空白
    if (cw.value && String(cw.value).trim()) return;  // 已有内容 → 不覆盖
    cw.value = sample;
    try {
        if (cw.element) { cw.element.value = sample; }
    } catch (e) {}
    try { node.setDirtyCanvas && node.setDirtyCanvas(true, false); } catch (e) {}
}

/* ---- dynamic node height (v7.2.2) ----
   折叠 10 个高级 widget 后, 节点应收缩; 展开后伸长。LiteGraph 用
   node.computeSize() 汇总各 widget 高度 (fold 的 widget computeSize=[0,0]
   贡献 0), 直接采纳即可, 不再用固定 1032px 撑高。 */
function recomputeNodeHeight(node) {
    if (!node || !node.computeSize) return;
    try {
        const c = node.computeSize();
        if (c && c[1]) {
            const h = Math.max(220, Math.round(c[1]) + 24);
            node.size[1] = h;
        }
    } catch (e) {}
}

/* ---- backend-aware visibility (3 HTTP-only widgets) ----
   Schema unchanged — backend / llm_base_url / model / api_key are always
   in node.widgets (they own a widgets_values slot). The 3 HTTP ones are
   folded when backend='Local GGUF'. This is purely visual; values stay
   intact on disk, so flipping backend never loses data.

   v7.2.2: 若 advanced_settings 处于折叠(收起)状态, 这 3 个 HTTP widget 已随
   整个高级组被 applyAdvancedVisibility 收起, 这里不再动它们 (避免展开高级组
   之外误展开)。只有高级组展开时才按 backend 折叠/展开 HTTP 三件。 */
function applyBackendVisibility(node) {
    if (!node || !node.widgets) return;
    const adv = getWidget(node, "advanced_settings");
    if (adv && adv.value !== true) return;  // 高级组收起 → HTTP 三件已折叠, 不动
    const backend = getWidget(node, "backend");
    if (!backend) return;
    const wantVisible = (backend.value === "HTTP endpoint");
    const toggles = ["llm_base_url", "model", "api_key"];
    for (const nm of toggles) {
        const w = getWidget(node, nm);
        if (!w) continue;
        if (wantVisible) unfoldWidget(w);
        else foldWidget(w);
    }
    try { if (node.setDirtyCanvas) node.setDirtyCanvas(true, false); } catch (e) {}
}

/* ---- advanced-settings collapse (v7.2.2) ----
   折叠开关 advanced_settings 控制 ADVANCED_GROUP 这 10 个 widget 的视觉显隐。
   默认 False = 折叠 → 节点紧凑。勾选 = 展开 → 再按 backend 折叠 HTTP 三件。
   全部用 foldWidget/unfoldWidget (computeSize=[0,0]+空 draw), 不动 w.hidden,
   不影响 widgets_values 索引, 跨重启状态由真实 BOOLEAN widget 保留。 */
function applyAdvancedVisibility(node) {
    if (!node || !node.widgets) return;
    const adv = getWidget(node, "advanced_settings");
    const expanded = !adv || adv.value === true;
    for (const nm of ADVANCED_GROUP) {
        const w = getWidget(node, nm);
        if (!w) continue;
        if (expanded) unfoldWidget(w);
        else foldWidget(w);
    }
    if (expanded) applyBackendVisibility(node);  // 展开时再按 backend 折叠 HTTP 三件
    recomputeNodeHeight(node);
    try { if (node.setDirtyCanvas) node.setDirtyCanvas(true, false); } catch (e) {}
}

/* ---- concept widget (LiteGraph 原生 STRING textarea) ----
   v7.2 改写：用 LiteGraph 原生 STRING widget (concept_text 在 INPUT_TYPES.required
   第一位 = widgets[0])，强制 computeSize 返回大尺寸。textarea 上挂 @ 监听。
   这里不调任何 addDOMWidget —— LiteGraph 原生 widget 是 ComfyUI 最稳的元素。 */
function enlargeConceptWidget(node) {
    const w = getWidget(node, CONCEPT_WIDGET_NAME);
    if (!w) return;

    // 1. 强制尺寸（160px, ~6-7 行文本可见，原 320px 缩小一半）。
    //    LiteGraph 在 drawWidgets 时调 w.computeSize(width) 决定 widget 高度。
    w.computeSize = (width) => {
        const wd = (width || (node.size ? node.size[0] : 320) - 20);
        // header ~28px + textarea 140px ≈ 168px
        return [wd, 160];
    };

    // 2. textarea 上挂 @ keyup 监听（DOM mount 后 widget.element 才有 element.value / addEventListener）
    //    重试机制：LiteGraph 异步构建 widget, widget.element 可能稍后才挂载。
    const tryWireAtMenu = (tries) => {
        if (tries == null) tries = 0;
        if (!w.element) {
            if (tries < 10) setTimeout(() => tryWireAtMenu(tries + 1), 60 * (tries + 1));
            return;
        }
        if (w.__h3AtHooked) return;
        w.__h3AtHooked = true;

        // textarea 基础样式 + 顶部标题条
        const ta = w.element;
        const wrap = ta.closest(".comfy-multiline-input") || ta;

        // 顶部标题条（插在容器前面，兄弟节点）
        try {
            if (wrap && wrap.parentNode && !wrap.__h3HeaderInjected) {
                const header = document.createElement("div");
                header.style.cssText = [
                    "background: linear-gradient(90deg, #4a6cf7 0%, #7b4dff 100%)",
                    "color:#fff","padding:5px 10px","font-size:12px","font-weight:600",
                    "border-radius:4px 4px 0 0","margin:0","letter-spacing:0.3px",
                ].join(";");
                header.innerHTML = "📝 提示词 (PROMPT) — 输入 @ 选择已上传的图/视/音素材";
                wrap.parentNode.insertBefore(header, wrap);
                wrap.style.borderRadius = "0 0 4px 4px";
                wrap.__h3HeaderInjected = true;
                ta.style.fontSize = "13px";
                ta.style.lineHeight = "1.6";
                ta.style.minHeight = "140px";
                ta.style.maxHeight = "140px";
                ta.style.overflowY = "auto";
                ta.style.resize = "none";
                ta.style.padding = "10px";
                ta.style.background = "#1a1a1a";
                ta.style.color = "#e0e0e0";
                ta.style.border = "1px solid #555";
                ta.style.borderTop = "none";
            }
        } catch (e) {}

        // @ 触发：textarea 的 keyup / input 事件
        const el = w.element;
        const onCaretAt = () => {
            openOrUpdateMenu(node, el, w);
        };
        el.addEventListener("keyup", onCaretAt);
        el.addEventListener("input", onCaretAt);
        el.addEventListener("click", onCaretAt);
        // Escape / blur 关闭弹窗
        el.addEventListener("keydown", (e) => {
            if (menuEl && menuEl.style.display !== "none") {
                if (e.key === "ArrowDown") { e.preventDefault(); menuActiveIndex = Math.min(menuItems.length - 1, menuActiveIndex + 1); renderMenu(); return; }
                if (e.key === "ArrowUp") { e.preventDefault(); menuActiveIndex = Math.max(0, menuActiveIndex - 1); renderMenu(); return; }
                if (e.key === "Enter") { e.preventDefault(); if (menuItems[menuActiveIndex]) insertMention(el, menuItems[menuActiveIndex], w); return; }
                if (e.key === "Escape") { e.preventDefault(); closeMenu(); return; }
            }
        });
        el.addEventListener("blur", () => { setTimeout(closeMenu, 120); });
    };
    tryWireAtMenu(0);
}

/* ---- shrink a multiline widget to a fixed height (extra_instructions 删了, 留作工具) ---- */
function shrinkMultilineWidget(w, targetH) {
    if (!w || w.__h3Shrunk) return;
    w.__h3Shrunk = true;
    const orig = w.computeSize;
    w.computeSize = function (width) {
        let r;
        try {
            r = orig ? orig.call(this, width) : [width || 220, targetH];
        } catch (e) {
            r = [width || 220, targetH];
        }
        return [r[0], targetH];
    };
}

/* ---- @ dropdown (single reused element) ---- */
let menuEl = null;
let menuActiveIndex = 0;
let menuItems = [];

function getMenu() {
    if (menuEl) return menuEl;
    const el = document.createElement("div");
    el.className = "h3-at-menu";
    Object.assign(el.style, {
        position: "fixed", display: "none", zIndex: 99999,
        background: "#1e1e1e", border: "1px solid #444", borderRadius: "6px",
        padding: "4px 0", maxHeight: "320px", overflowY: "auto", minWidth: "230px",
        boxShadow: "0 8px 28px rgba(0,0,0,0.6)", fontFamily: "sans-serif",
        fontSize: "12px", color: "#ddd", boxSizing: "border-box",
    });
    document.body.appendChild(el);
    menuEl = el;
    return el;
}

function closeMenu() {
    if (menuEl) menuEl.style.display = "none";
    menuItems = [];
    menuActiveIndex = 0;
}

function renderMenu(ta, backingWidget) {
    const el = getMenu();
    el.textContent = "";
    if (!menuItems.length) {
        const empty = document.createElement("div");
        empty.textContent = "输入 @ 选择参考素材标签（Picture/Video/Audio/Subject）";
        empty.style.cssText = "padding:10px 12px;color:#999;font-size:11px;text-align:center;";
        el.appendChild(empty);
        return;
    }
    menuItems.forEach((item, index) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex;align-items:center;gap:8px;padding:5px 12px;cursor:pointer;color:#ccc;white-space:nowrap;";
        if (index === menuActiveIndex) row.style.background = "#333";

        const thumb = document.createElement("div");
        Object.assign(thumb.style, {
            width: "32px", height: "32px", flex: "0 0 32px", borderRadius: "4px",
            overflow: "hidden", background: "#2a2a2a", display: "flex",
            alignItems: "center", justifyContent: "center", fontSize: "14px",
            color: "#aaa", flexShrink: "0",
        });
        if (item.previewUrl && item.type === "image") {
            const img = document.createElement("img");
            img.src = item.previewUrl;
            img.style.cssText = "width:100%;height:100%;object-fit:cover;";
            img.onerror = () => { img.style.display = "none"; thumb.textContent = "🖼"; };
            thumb.appendChild(img);
        } else if (item.previewUrl && item.type === "video") {
            const vid = document.createElement("video");
            vid.src = item.previewUrl;
            vid.muted = true; vid.playsInline = true; vid.preload = "metadata";
            vid.style.cssText = "width:100%;height:100%;object-fit:cover;background:#000;display:block;";
            vid.addEventListener("loadeddata", () => { try { vid.currentTime = 0.1; } catch (e) {} });
            thumb.appendChild(vid);
        } else {
            thumb.textContent = item.type === "video" ? "▶" : (item.type === "audio" ? "♪" : "🖼");
        }

        const lbl = document.createElement("span");
        lbl.textContent = item.label;

        row.appendChild(thumb);
        row.appendChild(lbl);
        row.addEventListener("mouseenter", () => {
            menuActiveIndex = index;
            renderMenu(ta, backingWidget);
        });
        row.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            e.stopPropagation();
            insertMention(ta, menuItems[index], backingWidget);
        });
        el.appendChild(row);
    });
}

// 当前正在编辑的 textarea + backing widget（insertMention 用）
let _currentTA = null;
let _currentWidget = null;

function buildOptions(node) {
    const { pics, vids, auds } = collectPreviews();
    const items = [];
    // 动态 @：仅列出已上传的素材（不上传不出现）
    for (let n = 1; n <= pics.length; n++) {
        items.push({
            type: "image",
            label: "Picture " + n,
            tag: "<Picture " + n + ">",
            previewUrl: pics[n - 1],
        });
    }
    for (let n = 1; n <= vids.length; n++) {
        items.push({
            type: "video",
            label: "Video " + n,
            tag: "<Video " + n + ">",
            previewUrl: vids[n - 1],
        });
    }
    for (let n = 1; n <= auds.length; n++) {
        items.push({ type: "audio", label: "Audio " + n, tag: "<Audio " + n + ">", previewUrl: null });
    }
    items.push({ type: "subject", label: "Subject 1", tag: "<Subject 1>", previewUrl: null });
    items.push({ type: "subject", label: "Subject 2", tag: "<Subject 2>", previewUrl: null });
    return items;
}

function collectPreviews() {
    const pics = [];
    const vids = [];
    const auds = [];
    const nodes = (app.graph && app.graph.nodes) ? app.graph.nodes : [];
    for (const n of nodes) {
        if (!n) continue;
        // 1. 独立加载器节点（旧方式，向后兼容）
        if (n._h3_previews && n._h3_previews.length) {
            if (n.comfyClass === "H3MultiImageLoader") {
                for (const p of n._h3_previews) {
                    if (p && p.type === "image" && p.url) pics.push(p.url);
                }
            } else if (n.comfyClass === "H3MultiVideoLoader") {
                for (const p of n._h3_previews) {
                    if (p && p.type === "video" && p.url) vids.push(p.url);
                }
            }
        }
        // 2. H3 R2VA AIO(micxin) 内嵌素材加载器（新方式）—— 从隐藏 path widgets 读
        if (n.comfyClass === "H3ModelLoader" && n.widgets) {
            const imgW = n.widgets.find(w => w && w.name === "image_paths");
            const vidW = n.widgets.find(w => w && w.name === "video_paths");
            const audW = n.widgets.find(w => w && w.name === "audio_paths");
            if (imgW && imgW.value) {
                String(imgW.value).split("\n").map(l => l.trim()).filter(Boolean).forEach(line => {
                    const p = line.split("|")[0];
                    if (p) pics.push(`/api/view?filename=${encodeURIComponent(p)}&type=input`);
                });
            }
            if (vidW && vidW.value) {
                String(vidW.value).split("\n").map(l => l.trim()).filter(Boolean).forEach(line => {
                    const p = line.split("|")[0];
                    if (p) vids.push(`/api/view?filename=${encodeURIComponent(p)}&type=input`);
                });
            }
            if (audW && audW.value) {
                String(audW.value).split("\n").map(l => l.trim()).filter(Boolean).forEach(line => {
                    const p = line.split("|")[0];
                    if (p) auds.push(p);
                });
            }
        }
    }
    return { pics, vids, auds };
}

/* ---- 自动同步 AIO 图片路径到 Screenwriter 的隐藏 _aio_ref_paths widget ----
   扫描画布上的 H3ModelLoader (H3 R2VA AIO) 节点，读取其隐藏 image_paths widget，
   写入 Screenwriter 的 _aio_ref_paths。Python 端在 ref_images 未连接时从这些路径
   加载图片给 LLM，无需 AIO→Screenwriter 连线，从根本上打破循环。 */
function syncAIOImagePaths(node) {
    if (!node || !node.widgets) return;
    const targetW = node.widgets.find(w => w && w.name === "_aio_ref_paths");
    if (!targetW) return;
    const nodes = (app.graph && app.graph.nodes) ? app.graph.nodes : [];
    const allPaths = [];
    for (const n of nodes) {
        if (!n || n.comfyClass !== "H3ModelLoader" || !n.widgets) continue;
        const imgW = n.widgets.find(w => w && w.name === "image_paths");
        if (imgW && imgW.value) {
            String(imgW.value).split("\n").map(l => l.trim()).filter(Boolean).forEach(line => {
                if (line && !allPaths.includes(line)) allPaths.push(line);
            });
        }
    }
    const newVal = allPaths.join("\n");
    if (targetW.value !== newVal) {
        targetW.value = newVal;
        try { if (targetW._state) targetW._state.value = newVal; } catch (e) {}
    }
}

// 全局定时器：每 2 秒同步一次 AIO 图片路径（覆盖所有 Screenwriter 节点）
let _aioSyncTimer = null;
function ensureAIOSyncTimer() {
    if (_aioSyncTimer) return;
    _aioSyncTimer = setInterval(() => {
        try {
            const nodes = (app.graph && app.graph.nodes) ? app.graph.nodes : [];
            for (const n of nodes) {
                if (n && n.comfyClass === "H3Screenwriter") syncAIOImagePaths(n);
            }
        } catch (e) {}
    }, 2000);
}

function openOrUpdateMenu(node, textarea, widget) {
    if (!textarea || !textarea.value) { closeMenu(); return; }
    // 每次交互都同步 AIO 图片路径（确保 Queue Prompt 前路径已更新）
    syncAIOImagePaths(node);
    const pos = textarea.selectionStart;
    if (!pos || pos < 0) { closeMenu(); return; }
    const before = textarea.value.slice(0, pos);
    const mm = before.match(/@([^@\n\s]*)$/);
    if (!mm) { closeMenu(); return; }
    const query = mm[1].toLowerCase();
    const all = buildOptions(node);
    menuItems = all.filter((o) => !query || o.label.toLowerCase().includes(query));
    menuActiveIndex = 0;
    renderMenu(textarea, widget);
    positionMenu(textarea);
}

function positionMenu(textarea) {
    const el = getMenu();
    el.style.visibility = "hidden";
    el.style.display = "block";
    const menuW = el.offsetWidth || 240;
    const menuH = Math.min(320, el.offsetHeight);
    // textarea 屏幕坐标 — 取 bounding rect 的底部
    const rect = textarea.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + menuW > window.innerWidth - 8) left = window.innerWidth - menuW - 8;
    if (top + menuH > window.innerHeight - 8) top = Math.max(8, rect.top - menuH - 4);
    el.style.left = Math.max(8, left) + "px";
    el.style.top = Math.max(8, top) + "px";
    el.style.visibility = "visible";
}

function insertMention(textarea, item, widget) {
    if (!textarea || !item) { closeMenu(); return; }
    const pos = textarea.selectionStart;
    const before = textarea.value.slice(0, pos);
    const after = textarea.value.slice(pos);
    const mm = before.match(/@([^@\n\s]*)$/);
    let prefix;
    if (mm) {
        prefix = before.slice(0, before.length - mm[0].length) + item.tag + " ";
    } else {
        prefix = before + item.tag + " ";
    }
    textarea.value = prefix + after;
    if (widget) {
        widget.value = textarea.value;
        try { if (widget._state) widget._state.value = textarea.value; } catch (e) {}
    }
    const newPos = prefix.length;
    try { textarea.setSelectionRange(newPos, newPos); } catch (e) {}
    closeMenu();
}

/* ---- output hover tooltip ---- */
const H3_OUTPUT_TOOLTIPS = [
    "prompt — H3 官方 full-reference / skill 格式提示词 (六段式: subject_definitions / summary / retention_analysis / detailed_description / overall_soundscape / non_diegetic_music)。"
    + "由 Qwen3-VL 自动生成，接到 MiniMaxH3ReferenceToVideo.prompt 口。"
    + "开启 bypass_llm 后，此口直接输出顶部概念框里粘贴的提示词。",

    "width — 渲染画布宽度 (像素), = √(resolution_mp × 比例), 对齐到 32 倍数。"
    + "喂给 Ref2VA.width (替代 Resolution Selector 节点)。",
    "height — 渲染画布高度 (像素), = √(resolution_mp / 比例), 对齐到 32 倍数。"
    + "喂给 Ref2VA.height。",
    "length — 渲染帧数 (H3 length), = ceil(duration_seconds × 24) 对齐到 5 mod 17。"
    + "喂给 Ref2VA.length。H3 单段 ≤362 帧 (~15s)。",
];
let h3OutputTipInstalled = false;
let h3OutputTipEl = null;

function ensureH3OutputTooltip() {
    if (h3OutputTipInstalled) return;
    h3OutputTipInstalled = true;
    h3OutputTipEl = document.createElement("div");
    h3OutputTipEl.className = "h3-output-tooltip";
    Object.assign(h3OutputTipEl.style, {
        position: "fixed", display: "none", zIndex: "100000",
        background: "#1f1f1f", color: "#e0e0e0",
        border: "1px solid #555", borderRadius: "5px",
        padding: "7px 10px", fontSize: "12px", lineHeight: "1.45",
        fontFamily: "sans-serif", maxWidth: "340px",
        pointerEvents: "none", whiteSpace: "pre-wrap",
        boxShadow: "0 6px 22px rgba(0,0,0,0.65)",
    });
    document.body.appendChild(h3OutputTipEl);
    let lastMoveTime = 0;
    const handleMove = (e) => {
        const now = Date.now();
        if (now - lastMoveTime < 50 && h3OutputTipEl.style.display === "block") return;
        lastMoveTime = now;
        try {
            const canvasInst = (typeof app !== "undefined" && app.canvas
                && app.canvas.canvas) ? app.canvas : null;
            if (!canvasInst) { h3OutputTipEl.style.display = "none"; return; }
            const html = canvasInst.canvas;
            const rect = html.getBoundingClientRect();
            const lx = e.clientX - rect.left;
            const ly = e.clientY - rect.top;
            const slot = canvasInst.getSlotInPosition
                ? canvasInst.getSlotInPosition(lx, ly) : null;
            if (slot && slot.output !== undefined && slot.output !== null
                && slot.node && slot.node.comfyClass === NODE_CLASS) {
                const tips = slot.node.__h3OutputTooltips;
                if (tips && tips[slot.output]) {
                    h3OutputTipEl.textContent = tips[slot.output];
                    h3OutputTipEl.style.left = (e.clientX + 12) + "px";
                    h3OutputTipEl.style.top = (e.clientY + 12) + "px";
                    h3OutputTipEl.style.display = "block";
                    return;
                }
            }
            h3OutputTipEl.style.display = "none";
        } catch (e2) { /* ignore */ }
    };
    const handleLeave = () => { h3OutputTipEl.style.display = "none"; };
    document.addEventListener("mousemove", handleMove, true);
    document.addEventListener("mouseleave", handleLeave, true);
    document.addEventListener("mousedown", handleLeave, true);
}

/* ---- attachEditor (called from nodeCreated / onConfigure) ---- */
function attachEditor(node) {
    if (!node || !node.comfyClass || node.comfyClass !== NODE_CLASS) return;

    // 0. 概念 widget (widgets[0], 原生 STRING, multiline) — 强制大尺寸 + @ 监听
    enlargeConceptWidget(node);

    // 0.5 隐藏 _aio_ref_paths（JS 自动从 AIO 同步，用户不可见）
    //     widget DOM 可能异步构建，用轮询确保 element 出现后也隐藏
    const aioPathsW = getWidget(node, "_aio_ref_paths");
    if (aioPathsW) {
        foldWidget(aioPathsW);
        const hideAioEl = () => {
            if (aioPathsW.element) aioPathsW.element.style.display = "none";
        };
        hideAioEl();
        let hideTries = 0;
        const hideTimer = setInterval(() => {
            hideTries++;
            hideAioEl();
            if (hideTries > 10) clearInterval(hideTimer);
        }, 100);
    }
    // 立即同步一次 AIO 图片路径
    syncAIOImagePaths(node);
    ensureAIOSyncTimer();

    // 1. 友化中文 labels — LiteGraph 默认显示 widget.name (英文)，patch 让用户看得懂
    //    v7.2: extra_instructions 已删；删对应 label
    const labelPatch = {
        task_mode:             "📋 任务模式 (task_mode)",
        duration_seconds:      "⏱ 渲染总秒数 (duration_seconds)",
        aspect_ratio:          "📐 画幅 (aspect_ratio)",
        resolution_mp:         "🖥 渲染分辨率 (MP 档 0.2~2.0)",
        context_size:          "🧠 上下文窗口 (context_size, 2048~65536)",
        backend:               "⚙ 后端 (backend)",
        gguf_name:             "📦 GGUF 模型 (Local GGUF 模式)",
        mmproj_name:           "🖼 多模态投影 (Local GGUF 模式)",
        llm_base_url:          "🌐 LLM 接口 URL (HTTP 模式)",
        model:                 "🤖 模型名 (HTTP 模式)",
        api_key:               "🔑 API Key (高级组展开且 HTTP 模式才出现)",
        advanced_settings:     "🧰 高级 LLM 设置 (默认折叠, 点击展开)",
        temperature:           "🌡 温度 (temperature, 0.2-0.4 最稳)",
        seed:                  "🎲 种子 (0=随机)",
        n_gpu_layers:          "🧊 GPU 层数 (-1=全部)",
        keep_loaded:           "🔁 写完不卸 (勾选=每次写都重载)",
        bypass_llm:            "⏭ 绕过 VL 4B (用顶部概念框粘贴提示词)",
    };
    for (const [key, lbl] of Object.entries(labelPatch)) {
        const w = getWidget(node, key);
        if (!w) continue;
        try { w.label = lbl; } catch (e) {}
    }

    // 2. Hook backend.combo — backend 切换时立刻折叠/展开 3 个 HTTP widget。
    //    关键：LiteGraph COMBO widget 的 callback 在 onChange 时触发，直接挂上就行。
    const backend = getWidget(node, "backend");
    if (backend && !backend.__h3BackendHooked) {
        backend.__h3BackendHooked = true;
        const _origCb = backend.callback;
        backend.callback = function () {
            try { if (_origCb) _origCb.apply(this, arguments); } catch (e) {}
            applyBackendVisibility(node);
        };
    }

    // 3. Hook task_mode — 切换任务模式时，若概念框为空则自动填充该模式的
    //    示例提示词（官方示例模式无示例 → 保留空白；填充后可删改）。
    const taskMode = getWidget(node, "task_mode");
    if (taskMode && !taskMode.__h3TaskModeHooked) {
        taskMode.__h3TaskModeHooked = true;
        const _origCb = taskMode.callback;
        taskMode.callback = function () {
            try { if (_origCb) _origCb.apply(this, arguments); } catch (e) {}
            applyTaskModeConcept(node);
        };
    }

    // 2.5 Hook advanced_settings — 折叠开关切换时收起/展开 10 个高级 widget
    //     并动态收节点高度。默认 False(折叠) → 节点紧凑。
    const adv = getWidget(node, "advanced_settings");
    if (adv && !adv.__h3AdvHooked) {
        adv.__h3AdvHooked = true;
        const _origAdv = adv.callback;
        adv.callback = function () {
            try { if (_origAdv) _origAdv.apply(this, arguments); } catch (e) {}
            applyAdvancedVisibility(node);
        };
    }

    // 3. 立即 + 4 重兜底跑 applyAdvancedVisibility (内部已含 backend 折叠)
    //    v7.2 原因: widgets[] 在 onConfigure 反序列化时, LiteGraph 异步构建
    //    widget 数组, attachEditor 同步调一次可能撞到空数组。多重兜底覆盖。
    applyAdvancedVisibility(node);
    requestAnimationFrame(() => applyAdvancedVisibility(node));
    setTimeout(() => applyAdvancedVisibility(node), 50);
    setTimeout(() => applyAdvancedVisibility(node), 250);
    setTimeout(() => applyAdvancedVisibility(node), 800);

    // 4. output tooltip
    try {
        node.__h3OutputTooltips = H3_OUTPUT_TOOLTIPS;
        ensureH3OutputTooltip();
    } catch (e) {}

    // 5. 节点宽自适应：按 widget label + value 最长字符串算出最小可视宽
    try {
        const candidates = [];
        candidates.push("http://127.0.0.1:8080/v1/chat/completions");
        candidates.push("Qwen3-VL-8B-Instruct-abliterated-v2.0.Q4_K_M");
        candidates.push("Local GGUF", "HTTP endpoint");
        const ggufW = node.widgets.find((w) => w.name === "gguf_name");
        if (ggufW && ggufW.options && Array.isArray(ggufW.options.values)) {
            for (const v of ggufW.options.values) candidates.push(String(v));
        }
        const mmprojW = node.widgets.find((w) => w.name === "mmproj_name");
        if (mmprojW && mmprojW.options && Array.isArray(mmprojW.options.values)) {
            for (const v of mmprojW.options.values) candidates.push(String(v));
        }
        const longest = candidates.reduce((a, b) => Math.max(a, String(b || "").length), 0);
        const charW = 7.2;
        const padding = 110;
        const minW = Math.max(380, longest * charW + padding);

        const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
        const scale = (app && app.canvas && app.canvas.ds && app.canvas.ds.scale)
            ? app.canvas.ds.scale : 1;
        const maxW = Math.floor((vw * 0.85) / scale);
        const targetW = Math.max(380, Math.min(minW, maxW));
        node.size[0] = targetW;
        // v7.2.2: 高度按当前可见 widget 动态计算 (折叠 10 个高级 widget 后自动收缩),
        // 不再用固定 1032px 撑高。recomputeNodeHeight 采纳 LiteGraph computeSize。
        recomputeNodeHeight(node);
    } catch (e) {}
}

/* ---- extension registration ---- */
app.registerExtension({
    name: "ComfyUI-H3-AutoDirector.H3Screenwriter",
    async beforeRegisterNodeDef(nodeType, comfyClass) {
        if (comfyClass !== NODE_CLASS) return;
        const _origOnConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const r = _origOnConfigure ? _origOnConfigure.apply(this, arguments) : undefined;
            // 磁盘 reload 后立刻按 advanced_settings + backend 折叠
            try { if (this.widgets) applyAdvancedVisibility(this); } catch (e) {}
            // schema migration (默认值同步)
            try {
                const its = getInputTypes();
                if (its && info) ensureMigrationsApplied(this, its, info.widgets_values);
            } catch (e) {}
            return r;
        };
    },
    nodeCreated(node) {
        attachEditor(node);
        try {
            node.__h3OutputTooltips = H3_OUTPUT_TOOLTIPS;
            ensureH3OutputTooltip();
        } catch (e) {}
        ensureAllTooltipsShown(node);
        // schema migration
        try {
            const its = getInputTypes();
            if (its) ensureMigrationsApplied(node, its);
        } catch (e) {}
    },
});

document.addEventListener("mousedown", (e) => {
    if (menuEl && menuEl.style.display !== "none" && !menuEl.contains(e.target)) {
        closeMenu();
    }
});