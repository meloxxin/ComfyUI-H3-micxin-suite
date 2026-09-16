import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ==============================================================================
   h3_clipchain_keyframes.js (by micxin2025, enhanced)
   H3 Clip Chain (micxin) 节点内嵌分段关键帧可视化 UI。

   功能：
     - 顶部段选择器（段1/段2/...），自动根据 prompts / 已连接的 H3 Prompt Split
       prompt_N 输入 / keyframe_paths 数量生成
     - "＋ 添加段 / － 删末段"：像 MiniMax H3 Extender 一样动态增删 clip
     - 每段独立的关键帧列表：点击/拖拽上传图片、设置出现秒数、删除
     - 每个关键帧可附带音频（可选，写入 audio_path 字段）
     - 秒数自动×24转帧数，写入 keyframe_paths widget（JSON 数组格式）
     - 底部状态栏显示段数（clip 数）、关键帧数量和来源

   keyframe_paths widget 格式（每行 7 段，| 分隔）：
     media_path|audio_path|frame_idx|media_start|media_end|audio_start|audio_end
     例：
     ["seg1_kf1.png||0||||\nseg1_kf2.png||73||||",
      "seg2_kf1.png|seg2_bgm.mp3|0||||"]
============================================================================== */

function injectStyles() {
    if (document.getElementById("h3-cc-kf-style")) return;
    const st = document.createElement("style");
    st.id = "h3-cc-kf-style";
    st.textContent = `
        .h3-cc-kf{width:100%;background:#1e1e28;border:1px solid #353545;border-radius:4px;
            margin:4px 0;padding:8px;box-sizing:border-box;display:flex;flex-direction:column;
            gap:6px;}
        .h3-cc-kf-title{color:#c8c8c8;font-size:11px;font-weight:bold;margin-bottom:2px;
            display:flex;align-items:center;gap:6px;}
        .h3-cc-kf-title .dot{width:8px;height:8px;border-radius:50%;background:#7fdca0;
            display:inline-block;}
        .h3-cc-kf-segbar{display:flex;flex-wrap:wrap;gap:3px;align-items:center;width:100%;
            padding-bottom:5px;border-bottom:1px solid #2a2a35;}
        .h3-cc-kf-seglabel{color:#888;font-size:10px;margin-right:4px;}
        .h3-cc-kf-segbtn{background:#2a2a32;color:#9aa;border:1px solid #3a3a44;
            padding:2px 10px;border-radius:3px;cursor:pointer;font-size:10px;user-select:none;
            transition:all .15s;}
        .h3-cc-kf-segbtn.active{background:#2d5a3d;color:#fff;border-color:#5a9f7b;}
        .h3-cc-kf-segbtn:hover:not(.active){background:#33333c;color:#ccc;}
        .h3-cc-kf-segbtn.add{background:#1f3a2a;color:#7fdca0;border-color:#2d5a3d;}
        .h3-cc-kf-segbtn.add:hover{background:#2d5a3d;color:#fff;}
        .h3-cc-kf-segbtn.del{background:#3a2020;color:#e88;border-color:#5a2d2d;}
        .h3-cc-kf-segbtn.del:hover{background:#5a2d2d;color:#fff;}
        .h3-cc-kf-segbtn.run{background:#1f3a55;color:#8ab4f8;border-color:#2d5a88;
            padding:2px 6px;font-size:9px;min-width:20px;}
        .h3-cc-kf-segbtn.run:hover:not(.running){background:#2d5a88;color:#fff;}
        .h3-cc-kf-segbtn.run.running{background:#8a6a1a;color:#ffd75e;border-color:#c8a030;}
        .h3-cc-kf-segbtn.runall{background:#33333c;color:#ccc;border-color:#4a4a55;}
        .h3-cc-kf-segbtn.runall:hover{background:#4a4a55;color:#fff;}
        .h3-cc-kf-topbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;}
        .h3-cc-kf-btn{background:#3a3f4b;color:#fff;border:1px solid #5a5f6b;padding:3px 10px;
            border-radius:3px;cursor:pointer;font-size:10px;transition:all .15s;}
        .h3-cc-kf-btn:hover{background:#4a4f5b;}
        .h3-cc-kf-dur{width:70px;background:#23232e;color:#d8d8d8;border:1px solid #3a3a44;
            border-radius:3px;padding:2px 6px;font-size:10px;}
        .h3-cc-kf-dur:focus{outline:none;border-color:#5a9f7b;}
        .h3-cc-kf-btn.danger{background:#5a2a2a;border-color:#8a3a3a;}
        .h3-cc-kf-btn.danger:hover{background:#7a3a3a;}
        .h3-cc-kf-list{display:flex;flex-direction:column;gap:4px;width:100%;
            max-height:280px;overflow-y:auto;scrollbar-width:thin;
            scrollbar-color:#5a5f6b #1a1a1a;min-height:32px;}
        .h3-cc-kf-list::-webkit-scrollbar{width:6px;}
        .h3-cc-kf-list::-webkit-scrollbar-track{background:#1a1a1a;}
        .h3-cc-kf-list::-webkit-scrollbar-thumb{background:#5a5f6b;border-radius:3px;}
        .h3-cc-kf-item{display:flex;align-items:center;gap:6px;background:#252530;
            border:1px solid #333;border-radius:4px;padding:4px 6px;}
        .h3-cc-kf-thumb{width:52px;height:38px;object-fit:cover;background:#000;
            border-radius:3px;flex-shrink:0;cursor:pointer;border:1px solid #444;}
        .h3-cc-kf-noimg{width:52px;height:38px;flex-shrink:0;display:flex;align-items:center;
            justify-content:center;background:#181820;border-radius:3px;cursor:pointer;
            border:1px solid #444;color:#7fdcb0;font-size:16px;user-select:none;}
        .h3-cc-kf-info{display:flex;flex-direction:column;gap:2px;flex:1 1 auto;min-width:0;}
        .h3-cc-kf-name{color:#b8b8c8;font-size:9px;white-space:nowrap;overflow:hidden;
            text-overflow:ellipsis;font-family:monospace;}
        .h3-cc-kf-time{display:flex;align-items:center;gap:4px;}
        .h3-cc-kf-time label{color:#7fdca0;font-size:9px;flex-shrink:0;}
        .h3-cc-kf-time input{width:44px;font-size:10px;background:#1a1a22;color:#fff;
            border:1px solid #444;border-radius:2px;text-align:center;padding:1px 2px;}
        .h3-cc-kf-frame{color:#666;font-size:9px;flex-shrink:0;font-family:monospace;}
        .h3-cc-kf-audio{display:flex;align-items:center;gap:4px;width:100%;}
        .h3-cc-kf-audio label{color:#8ab4f8;font-size:9px;flex-shrink:0;}
        .h3-cc-kf-audio span{color:#9aa;font-size:9px;white-space:nowrap;overflow:hidden;
            text-overflow:ellipsis;font-family:monospace;flex:1 1 auto;min-width:0;}
        .h3-cc-kf-audio .add{background:#2a3a55;color:#8ab4f8;border:1px solid #3a5a88;
            border-radius:3px;cursor:pointer;font-size:9px;padding:1px 8px;flex-shrink:0;}
        .h3-cc-kf-audio .add:hover{background:#3a5a88;color:#fff;}
        .h3-cc-kf-audio .rm{color:#e77;cursor:pointer;font-size:12px;line-height:1;
            flex-shrink:0;padding:0 4px;font-weight:bold;border-radius:2px;}
        .h3-cc-kf-audio .rm:hover{color:#ff4444;background:#3a1a1a;}
        .h3-cc-kf-del{cursor:pointer;color:#e77;font-size:14px;line-height:1;flex-shrink:0;
            padding:0 4px;font-weight:bold;border-radius:2px;}
        .h3-cc-kf-del:hover{color:#ff4444;background:#3a1a1a;}
        .h3-cc-kf-empty{border:1px dashed #444;border-radius:4px;color:#666;font-size:10px;
            padding:12px 8px;text-align:center;cursor:pointer;background:#181820;
            transition:all .15s;}
        .h3-cc-kf-empty:hover{border-color:#7fdca0;color:#7fdca0;background:#1a2a20;}
        .h3-cc-kf-status{width:100%;background:#15151c;border:1px solid #2a2a35;border-radius:3px;
            color:#666;font-family:monospace;font-size:9px;padding:4px 6px;white-space:pre-wrap;
            word-break:break-all;line-height:1.4;max-height:60px;overflow-y:auto;}
    `;
    document.head.appendChild(st);
}

// 上传文件到 ComfyUI input 目录（图片/视频/音频通用）
async function uploadFile(file) {
    try {
        const formData = new FormData();
        formData.append("image", file);
        const resp = await api.fetchApi("/upload/image", { method: "POST", body: formData });
        if (resp.status === 200) {
            const data = await resp.json();
            return data.name;
        }
    } catch (e) {
        console.error("[H3-CC-KF] upload error:", e);
    }
    return null;
}

const IMG_RE = /\.(jpe?g|png|webp|bmp|gif|tiff?)$/i;
const VID_RE = /\.(mp4|webm|mkv|avi|mov|m4v|flv|wmv)$/i;

// 解析 keyframe_paths widget → 每段的 items 数组（保留 7 字段）
function parseWidget(value) {
    if (!value || !String(value).trim()) return [];
    try {
        const arr = JSON.parse(value);
        if (!Array.isArray(arr)) return [];
        return arr.map(segStr => {
            if (!segStr || !String(segStr).trim()) return [];
            return String(segStr).split("\n").map(l => l.trim()).filter(Boolean).map(line => {
                const p = line.split("|");
                return {
                    media: (p[0] || "").trim(),
                    audio: (p[1] || "").trim(),
                    frame_idx: parseInt(p[2], 10) || 0,
                    media_start: parseFloat(p[3]) || 0,
                    media_end: parseFloat(p[4]) || 0,
                    audio_start: parseFloat(p[5]) || 0,
                    audio_end: parseFloat(p[6]) || 0,
                };
            });
        });
    } catch (e) {
        return [];
    }
}

// 序列化 → keyframe_paths widget（7 字段格式）
function serializeWidget(segments) {
    return JSON.stringify(segments.map(items =>
        items.map(it => {
            const ms = it.media_start || 0;
            const me = it.media_end || 0;
            const as = it.audio_start || 0;
            const ae = it.audio_end || 0;
            return `${it.media || ""}|${it.audio || ""}|${it.frame_idx}|${ms}|${me}|${as}|${ae}`;
        }).join("\n")
    ));
}

app.registerExtension({
    name: "h3_clipchain_keyframes",

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name !== "H3ClipChain" && nodeData.name !== "H3ClipChainAV") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onNodeCreated?.apply(this, arguments);
            injectStyles();

            const node = this;
            let currentSeg = 0;

            // 找到 keyframe_paths widget 和 prompts widget
            const kfWidget = node.widgets?.find(w => w.name === "keyframe_paths");
            const promptsWidget = node.widgets?.find(w => w.name === "prompts");
            const segDurWidget = node.widgets?.find(w => w.name === "segment_durations");

            if (!kfWidget) {
                console.warn("[H3-CC-KF] keyframe_paths widget not found");
                return;
            }

            // 隐藏原始文本 widget（三管齐下：type + options.hidden + computeSize 收缩高度）
            kfWidget.type = "hidden";
            if (kfWidget.options) kfWidget.options.hidden = true;
            kfWidget.computeSize = () => [0, -4];

            // segment_durations 保留原始输入框（不隐藏），用户可在节点参数里直接填，
            // 图形界面里不再重复显示，避免挤压关键帧空间。

            // ---- 构建 UI 容器 ----
            const container = document.createElement("div");
            container.className = "h3-cc-kf";

            // 标题
            const title = document.createElement("div");
            title.className = "h3-cc-kf-title";
            title.innerHTML = '<span class="dot"></span>分段关键帧（可视化编辑）';
            container.appendChild(title);

            // 段选择器
            const segBar = document.createElement("div");
            segBar.className = "h3-cc-kf-segbar";
            const segLabel = document.createElement("span");
            segLabel.className = "h3-cc-kf-seglabel";
            segLabel.textContent = "选择段：";
            segBar.appendChild(segLabel);

            // Extender 式：添加 / 删除段（= 增删 clip）
            const segAddBtn = document.createElement("button");
            segAddBtn.className = "h3-cc-kf-segbtn add";
            segAddBtn.textContent = "＋ 添加段";
            segAddBtn.title = "新增一个 clip（类似 MiniMax H3 Extender 添加 clip）";
            const segDelBtn = document.createElement("button");
            segDelBtn.className = "h3-cc-kf-segbtn del";
            segDelBtn.textContent = "－ 删末段";
            segDelBtn.title = "删除最后一个 clip 的关键帧";
            segBar.appendChild(segAddBtn);
            segBar.appendChild(segDelBtn);
            // "全跑"：恢复 start_segment=0 / preview_segments=0
            const runAllBtn = document.createElement("button");
            runAllBtn.className = "h3-cc-kf-segbtn runall";
            runAllBtn.textContent = "全跑";
            runAllBtn.title = "恢复全部段：start_segment=0, preview_segments=0";
            runAllBtn.onclick = () => setRunRange(0, 0);
            segBar.appendChild(runAllBtn);
            container.appendChild(segBar);

            // 顶部操作条
            const topbar = document.createElement("div");
            topbar.className = "h3-cc-kf-topbar";
            const addBtn = document.createElement("button");
            addBtn.className = "h3-cc-kf-btn";
            addBtn.textContent = "＋ 上传关键帧";
            addBtn.title = "上传图片/视频/音频作为关键帧（自动判断类型）";
            const clearBtn = document.createElement("button");
            clearBtn.className = "h3-cc-kf-btn danger";
            clearBtn.textContent = "清空本段";
            topbar.appendChild(addBtn);
            topbar.appendChild(clearBtn);
            container.appendChild(topbar);

            // 关键帧列表
            const list = document.createElement("div");
            list.className = "h3-cc-kf-list";
            container.appendChild(list);

            // 状态栏
            const status = document.createElement("div");
            status.className = "h3-cc-kf-status";
            container.appendChild(status);

            // 隐藏文件输入（图片 + 视频 + 音频）
            const fileInput = document.createElement("input");
            fileInput.type = "file";
            fileInput.accept = "image/*,video/*,audio/*";
            fileInput.multiple = true;
            fileInput.style.display = "none";
            container.appendChild(fileInput);

            // ---- 功能函数 ----

            // 统计已连接的 H3 Prompt Split prompt_N 输入（Autogrow 动态槽）
            function getConnectedSegCount() {
                let n = 0;
                const listIn = Array.isArray(node.inputs) ? node.inputs : Object.values(node.inputs || {});
                for (const inp of listIn) {
                    if (!inp || typeof inp.name !== "string") continue;
                    const m = /(?:^|\.)prompt_(\d+)$/.exec(inp.name);
                    if (m && inp.link != null) n = Math.max(n, parseInt(m[1], 10) + 1);
                }
                return n;
            }

            function connSrcTxt() {
                const conn = getConnectedSegCount();
                if (conn > 0) return "H3 Prompt Split 输入";
                if (promptsWidget?.value?.trim()) return "prompts 文本框";
                return "关键帧(无提示词，需接入 Split 或填 prompts)";
            }

            function getSegCount() {
                // 1) 优先：已连接的 H3 Prompt Split 输入数量 = clip 数量
                const conn = getConnectedSegCount();
                if (conn > 0) return conn;
                // 2) prompts widget
                const pv = promptsWidget?.value || "";
                if (pv) {
                    try {
                        const arr = JSON.parse(pv);
                        if (Array.isArray(arr)) return Math.max(1, arr.length);
                    } catch (e) {}
                    const lines = String(pv).split("\n").filter(l => {
                        const t = l.trim();
                        return t && !t.startsWith("//");
                    });
                    if (lines.length > 1) return Math.max(1, lines.length);
                }
                // 3) keyframe_paths
                const segs = parseWidget(kfWidget?.value);
                if (segs.length > 0) return Math.max(1, segs.length);
                return 1;
            }

            // 设置 widget 值（保留原有 callback）
            function setWidgetValue(name, val) {
                const w = node.widgets?.find(w => w.name === name);
                if (!w) return;
                const oldCb = w.callback;
                w.callback = null;
                w.value = val;
                w.callback = oldCb;
                if (oldCb) oldCb.apply(w, [val]);
            }

            // 读取当前跑段范围 {start(0基), count}（来自 start_segment / preview_segments）
            function getRunRange() {
                const sw = node.widgets?.find(w => w.name === "start_segment");
                const pw = node.widgets?.find(w => w.name === "preview_segments");
                const start = sw ? (parseInt(sw.value, 10) || 0) : 0;
                const prev = pw ? (parseInt(pw.value, 10) || 0) : 0;
                return { start, count: prev };
            }

            // 设置跑段范围：startIdx 0 基；count=0 表示从该段跑到结尾
            function setRunRange(startIdx, count) {
                setWidgetValue("start_segment", startIdx);
                setWidgetValue("preview_segments", count);
                rebuildSegButtons();
                refresh();
                requestNodeResize();
            }

            // 跑段范围的人类可读描述
            function runRangeText() {
                const rr = getRunRange();
                if (rr.start === 0 && rr.count === 0) return "全部段";
                if (rr.start > 0 && rr.count === 0) return `从段${rr.start + 1}到结尾`;
                if (rr.start === 0 && rr.count > 0) return `前${rr.count}段`;
                if (rr.count === 1) return `只跑段${rr.start + 1}`;
                return `段${rr.start + 1}~${rr.start + rr.count}`;
            }

            function rebuildSegButtons() {
                segBar.querySelectorAll(".h3-cc-kf-segbtn:not(.add):not(.del):not(.runall)").forEach(b => b.remove());
                const count = getSegCount();
                const rr = getRunRange();
                for (let i = 0; i < count; i++) {
                    const inRun = rr.count > 0 ? (i >= rr.start && i < rr.start + rr.count)
                                               : (rr.start > 0 && i >= rr.start);
                    const btn = document.createElement("button");
                    btn.className = "h3-cc-kf-segbtn" + (i === currentSeg ? " active" : "");
                    btn.textContent = `段${i + 1}`;
                    btn.onclick = () => {
                        currentSeg = i;
                        rebuildSegButtons();
                        refresh();
                    };
                    // 插入到添加/删除按钮之前
                    segBar.insertBefore(btn, segAddBtn);
                    // ▶ 只跑这段（start_segment=i, preview_segments=1）
                    const runBtn = document.createElement("button");
                    runBtn.className = "h3-cc-kf-segbtn run" + (inRun ? " running" : "");
                    runBtn.textContent = "▶";
                    runBtn.title = `只跑这段（段${i + 1}）：start_segment=${i}, preview_segments=1`;
                    runBtn.onclick = () => {
                        const clipN = getSegCount();
                        if (i >= clipN) {
                            status.style.color = "#e88";
                            status.textContent = `⚠ 段${i + 1} 没有提示词（当前共 ${clipN} 段 clip，来源: ${connSrcTxt()}）。\n请把上方 H3 Prompt Split 的 prompt_${i} 接到本节点的下一个空槽（或在 prompts 文本框加第 ${i + 1} 行），再点 ▶。`;
                            return;
                        }
                        setRunRange(i, 1);
                    };
                    segBar.insertBefore(runBtn, segAddBtn);
                }
                if (currentSeg >= count) currentSeg = 0;
            }

            function writeWidget(segments) {
                const val = serializeWidget(segments);
                const oldCb = kfWidget.callback;
                kfWidget.callback = null;
                kfWidget.value = val;
                kfWidget.callback = oldCb;
                // 同步 widgets_values：hidden widget 执行/保存时读的是 widgets_values，
                // 只改 value 会“刷新页面才生效”
                try {
                    const wi = node.widgets ? node.widgets.indexOf(kfWidget) : -1;
                    if (wi >= 0 && Array.isArray(node.widgets_values) && node.widgets_values[wi] !== val) {
                        node.widgets_values[wi] = val;
                    }
                } catch (e) {}
                if (oldCb) oldCb.apply(kfWidget, [val]);
                requestNodeResize();
            }

            function requestNodeResize() {
                try {
                    if (node.computeSize) {
                        const sz = node.computeSize();
                        if (sz && sz[1]) {
                            node.setSize([Math.max(240, node.size?.[0] || sz[0]), Math.max(200, sz[1] + 10)]);
                        }
                    }
                    app.graph.setDirtyCanvas(true, true);
                } catch (e) {}
            }

            function refresh() {
                rebuildSegButtons();
                const segments = parseWidget(kfWidget.value);
                const items = segments[currentSeg] || [];
                list.innerHTML = "";

                if (items.length === 0) {
                    const empty = document.createElement("div");
                    empty.className = "h3-cc-kf-empty";
                    empty.textContent = "＋ 点击或拖拽图片/视频/音频到此处添加关键帧";
                    empty.onclick = () => fileInput.click();
                    empty.ondragover = (e) => { e.preventDefault(); empty.style.borderColor = "#7fdca0"; };
                    empty.ondragleave = () => { empty.style.borderColor = ""; };
                    empty.ondrop = async (e) => {
                        e.preventDefault();
                        empty.style.borderColor = "";
                        const files = Array.from(e.dataTransfer.files || []);
                        if (files.length) await handleFiles(files);
                    };
                    list.appendChild(empty);
                } else {
                    items.forEach((it, idx) => {
                        const item = document.createElement("div");
                        item.className = "h3-cc-kf-item";

                        // 缩略图 / 占位（视频或纯音频关键帧）
                        const isImg = it.media && IMG_RE.test(it.media);
                        const isVid = it.media && VID_RE.test(it.media);
                        if (isImg) {
                            const thumb = document.createElement("img");
                            thumb.className = "h3-cc-kf-thumb";
                            thumb.src = `/view?filename=${encodeURIComponent(it.media)}&type=input`;
                            thumb.onerror = () => { thumb.style.display = "none"; };
                            thumb.onclick = () => fileInput.click();
                            thumb.title = it.media;
                            item.appendChild(thumb);
                        } else {
                            const noimg = document.createElement("div");
                            noimg.className = "h3-cc-kf-noimg";
                            noimg.textContent = isVid ? "🎬" : "♪";
                            noimg.title = it.media || (it.audio ? "纯音频关键帧" : "无媒体");
                            noimg.onclick = () => fileInput.click();
                            item.appendChild(noimg);
                        }

                        // 信息区
                        const info = document.createElement("div");
                        info.className = "h3-cc-kf-info";
                        const name = document.createElement("div");
                        name.className = "h3-cc-kf-name";
                        name.textContent = it.media || (it.audio ? `音频: ${it.audio}` : "(无媒体)");
                        name.title = it.media || it.audio || "";
                        info.appendChild(name);

                        const timeRow = document.createElement("div");
                        timeRow.className = "h3-cc-kf-time";
                        const secLabel = document.createElement("label");
                        secLabel.textContent = "秒:";
                        const secInput = document.createElement("input");
                        secInput.type = "number";
                        secInput.min = "0";
                        secInput.step = "0.5";
                        secInput.value = (it.frame_idx / 24).toFixed(1);
                        const frameLabel = document.createElement("span");
                        frameLabel.className = "h3-cc-kf-frame";
                        frameLabel.textContent = `帧:${it.frame_idx}`;
                        secInput.onchange = () => {
                            const sec = parseFloat(secInput.value) || 0;
                            it.frame_idx = Math.round(sec * 24);
                            const segments = parseWidget(kfWidget.value);
                            while (segments.length <= currentSeg) segments.push([]);
                            segments[currentSeg][idx] = it;
                            writeWidget(segments);
                            frameLabel.textContent = `帧:${it.frame_idx}`;
                        };
                        timeRow.appendChild(secLabel);
                        timeRow.appendChild(secInput);
                        timeRow.appendChild(frameLabel);
                        info.appendChild(timeRow);

                        // 音频行（仅当有关键帧音频时显示：播放按钮 + 文件名 + 裁切起/止 + 移除）
                        if (it.audio) {
                            const audioRow = document.createElement("div");
                            audioRow.className = "h3-cc-kf-audio";
                            // 播放按钮
                            const playBtn = document.createElement("span");
                            playBtn.style.cssText = "cursor:pointer;color:#7fdca0;font-size:11px;flex-shrink:0;padding:0 2px;user-select:none;";
                            playBtn.textContent = "▶";
                            playBtn.title = "播放/暂停音频";
                            let audioEl = null;
                            playBtn.onclick = (e) => {
                                e.stopPropagation();
                                if (!audioEl) {
                                    audioEl = new Audio(`/view?filename=${encodeURIComponent(it.audio)}&type=input`);
                                    audioEl.onended = () => { playBtn.textContent = "▶"; };
                                    // 应用裁切起/止：从 audio_start 开始，到 audio_end 暂停
                                    const aStart = it.audio_start || 0;
                                    const aEnd = it.audio_end || 0;
                                    audioEl.addEventListener("loadedmetadata", () => {
                                        if (aStart > 0) audioEl.currentTime = aStart;
                                    });
                                    if (aEnd > aStart) {
                                        audioEl.addEventListener("timeupdate", () => {
                                            if (audioEl.currentTime >= aEnd) {
                                                audioEl.pause();
                                                playBtn.textContent = "▶";
                                            }
                                        });
                                    }
                                }
                                if (audioEl.paused) {
                                    // 每次播放从裁切起点开始
                                    const aStart = it.audio_start || 0;
                                    if (aStart > 0) audioEl.currentTime = aStart;
                                    audioEl.play();
                                    playBtn.textContent = "⏸";
                                } else {
                                    audioEl.pause();
                                    playBtn.textContent = "▶";
                                }
                            };
                            audioRow.appendChild(playBtn);
                            const aLabel = document.createElement("label");
                            aLabel.textContent = "音:";
                            audioRow.appendChild(aLabel);
                            const aName = document.createElement("span");
                            aName.textContent = it.audio;
                            aName.title = it.audio;
                            audioRow.appendChild(aName);
                            // 音频裁切起止
                            const aRange = document.createElement("span");
                            aRange.style.cssText = "display:flex;align-items:center;gap:2px;flex-shrink:0;";
                            const aStartLabel = document.createElement("label");
                            aStartLabel.style.cssText = "color:#8ab4f8;font-size:9px;";
                            aStartLabel.textContent = "起:";
                            const aStartInput = document.createElement("input");
                            aStartInput.type = "number";
                            aStartInput.min = "0";
                            aStartInput.step = "0.5";
                            aStartInput.style.cssText = "width:36px;font-size:9px;background:#1a1a22;color:#fff;border:1px solid #444;border-radius:2px;text-align:center;padding:1px 2px;";
                            aStartInput.value = (it.audio_start || 0).toFixed(1);
                            aStartInput.title = "音频裁切起始秒数";
                            const aEndLabel = document.createElement("label");
                            aEndLabel.style.cssText = "color:#8ab4f8;font-size:9px;";
                            aEndLabel.textContent = "止:";
                            const aEndInput = document.createElement("input");
                            aEndInput.type = "number";
                            aEndInput.min = "0";
                            aEndInput.step = "0.5";
                            aEndInput.style.cssText = "width:36px;font-size:9px;background:#1a1a22;color:#fff;border:1px solid #444;border-radius:2px;text-align:center;padding:1px 2px;";
                            aEndInput.value = (it.audio_end || 0).toFixed(1);
                            aEndInput.title = "音频裁切结束秒数（0=不裁切，用完整音频）";
                            aStartInput.onchange = () => {
                                it.audio_start = parseFloat(aStartInput.value) || 0;
                                const segments = parseWidget(kfWidget.value);
                                while (segments.length <= currentSeg) segments.push([]);
                                segments[currentSeg][idx] = it;
                                writeWidget(segments);
                                refresh();
                            };
                            aEndInput.onchange = () => {
                                it.audio_end = parseFloat(aEndInput.value) || 0;
                                const segments = parseWidget(kfWidget.value);
                                while (segments.length <= currentSeg) segments.push([]);
                                segments[currentSeg][idx] = it;
                                writeWidget(segments);
                                refresh();
                            };
                            aRange.appendChild(aStartLabel);
                            aRange.appendChild(aStartInput);
                            aRange.appendChild(aEndLabel);
                            aRange.appendChild(aEndInput);
                            audioRow.appendChild(aRange);
                            const aRm = document.createElement("span");
                            aRm.className = "rm";
                            aRm.textContent = "✕";
                            aRm.title = "移除该关键帧的音频";
                            aRm.onclick = () => {
                                const segments = parseWidget(kfWidget.value);
                                while (segments.length <= currentSeg) segments.push([]);
                                segments[currentSeg][idx].audio = "";
                                segments[currentSeg][idx].audio_start = 0;
                                segments[currentSeg][idx].audio_end = 0;
                                writeWidget(segments);
                                refresh();
                            };
                            audioRow.appendChild(aRm);
                            info.appendChild(audioRow);
                        }
                        item.appendChild(info);

                        // 删除按钮
                        const del = document.createElement("span");
                        del.className = "h3-cc-kf-del";
                        del.textContent = "✕";
                        del.title = "删除此关键帧";
                        del.onclick = () => {
                            const segments = parseWidget(kfWidget.value);
                            while (segments.length <= currentSeg) segments.push([]);
                            segments[currentSeg].splice(idx, 1);
                            writeWidget(segments);
                            refresh();
                        };
                        item.appendChild(del);

                        list.appendChild(item);
                    });
                }

                // 状态栏
                status.style.color = "";
                const totalKf = segments.reduce((s, seg) => s + seg.length, 0);
                const clipN = getSegCount();
                const srcTxt = connSrcTxt();
                const warn = (segments.length > clipN)
                    ? `\n⚠ 提示词只有 ${clipN} 段，但关键帧有 ${segments.length} 段——超出的段跑不到。把 Split 的 prompt_${clipN} 接到下一个空槽才会成为新 clip。`
                    : "";
                status.textContent = `共 ${clipN} 段(clip) / 关键帧${segments.length}段·${totalKf}帧 | 跑段: ${runRangeText()} | 来源: ${srcTxt} | 当前段${currentSeg + 1}: ${items.length} 帧` +
                    warn +
                    (items.length > 0 ? "\n" + items.map((it, i) => `  ${i + 1}. ${it.media || "(仅音频)"} @ ${(it.frame_idx / 24).toFixed(1)}s (帧${it.frame_idx})${it.audio ? " +音频" : ""}`).join("\n") : "\n（本段无关键帧，点击上方按钮添加）");
            }

            async function handleFiles(files) {
                const segments = parseWidget(kfWidget.value);
                while (segments.length <= currentSeg) segments.push([]);
                for (const file of files) {
                    const name = await uploadFile(file);
                    if (name) {
                        const isAudio = file.type.startsWith("audio/") || /\.(mp3|wav|ogg|flac|m4a|aac|wma)$/i.test(file.name);
                        const nextFrame = segments[currentSeg].length > 0
                            ? segments[currentSeg][segments[currentSeg].length - 1].frame_idx + 24
                            : 0;
                        if (isAudio) {
                            // 纯音频关键帧（media 为空，只有 audio）
                            segments[currentSeg].push({ media: "", audio: name, frame_idx: nextFrame });
                        } else {
                            segments[currentSeg].push({ media: name, audio: "", frame_idx: nextFrame });
                        }
                    }
                }
                writeWidget(segments);
                refresh();
            }

            // ---- 事件绑定 ----
            addBtn.onclick = () => fileInput.click();
            clearBtn.onclick = () => {
                const segments = parseWidget(kfWidget.value);
                while (segments.length <= currentSeg) segments.push([]);
                segments[currentSeg] = [];
                writeWidget(segments);
                refresh();
            };
            segAddBtn.onclick = () => {
                const segments = parseWidget(kfWidget.value);
                segments.push([]);
                writeWidget(segments);
                currentSeg = segments.length - 1;
                rebuildSegButtons();
                refresh();
            };
            segDelBtn.onclick = () => {
                const segments = parseWidget(kfWidget.value);
                if (segments.length <= 1) return;
                segments.pop();
                if (currentSeg >= segments.length) currentSeg = segments.length - 1;
                writeWidget(segments);
                rebuildSegButtons();
                refresh();
            };
            fileInput.onchange = async (e) => {
                const files = Array.from(e.target.files || []);
                if (files.length) await handleFiles(files);
                fileInput.value = "";
            };

            // 节点级拖拽
            const origOnDragDrop = node.onDragDrop;
            node.onDragDrop = function (e) {
                if (e.dataTransfer && e.dataTransfer.files) {
                    const files = Array.from(e.dataTransfer.files).filter(f =>
                        f.type.startsWith("image/") || f.type.startsWith("video/") || f.type.startsWith("audio/"));
                    if (files.length) {
                        e.preventDefault();
                        handleFiles(files);
                        return true;
                    }
                }
                if (origOnDragDrop) return origOnDragDrop.apply(this, arguments);
            };

            // 监听 prompts 变化，重建段按钮
            if (promptsWidget) {
                const oldCb = promptsWidget.callback;
                promptsWidget.callback = function () {
                    if (oldCb) oldCb.apply(this, arguments);
                    rebuildSegButtons();
                    refresh();
                };
            }

            // 监听连接变化（H3 Prompt Split 输入接入/断开时刷新段数）——原型级一次性包装
            if (!nodeType.prototype.__h3ccKfWrapped) {
                nodeType.prototype.__h3ccKfWrapped = true;
                const origConn = nodeType.prototype.onConnectionsChange;
                nodeType.prototype.onConnectionsChange = function (type, tNode, slot, link, linkInfo) {
                    const r = origConn ? origConn.apply(this, arguments) : undefined;
                    if (tNode === this && this.__h3ccKfRefresh) {
                        setTimeout(() => { try { this.__h3ccKfRefresh(); } catch (e) {} }, 60);
                    }
                    return r;
                };
            }
            // 本节点把刷新函数挂到实例，连接变化时调用
            node.__h3ccKfRefresh = () => {
                rebuildSegButtons();
                refresh();
                requestNodeResize();
            };

            // ---- 用 addDOMWidget 挂载 ----
            const domWidget = node.addDOMWidget("分段关键帧", "h3_cc_kf", container, { serialize: false });
            domWidget.computeSize = function (width) {
                const w = Math.max(200, (node.size?.[0] || width || 240) - 24);
                const segCount = getSegCount();
                const items = (parseWidget(kfWidget.value)[currentSeg] || []).length;
                const listH = items > 0 ? Math.min(48 + items * 56, 280) : 56;
                const h = 24 + 36 + 32 + listH + 40 + 16;
                return [w, h];
            };

            // 延迟刷新（等节点完全挂载）
            setTimeout(() => { refresh(); requestNodeResize(); }, 200);
            setTimeout(() => { refresh(); requestNodeResize(); }, 800);
        };
    }
});
