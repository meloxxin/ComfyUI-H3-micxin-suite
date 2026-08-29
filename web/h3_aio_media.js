import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

/* ==============================================================================
   h3_aio_media.js (by micxin2025)
   H3 R2VA AIO(micxin) 节点内嵌素材上传 UI —— 三标签页（图片/视频/音频），
   拖拽上传即连接，无需外部连线。

   功能：
     - 顶部 [图片] [视频] [音频] 标签页切换，每页显示素材数量
     - 拖拽文件到节点 / 点击上传 / 粘贴 加入素材
     - 每槽 ☰ 手柄拖动排序、↻ 更换、↑ ↓ 上移/下移、删除
     - 视频/音频每槽带起止秒裁剪
     - 素材路径写入节点隐藏 widget（image_paths / video_paths / audio_paths）
     - 任意改动自动 +1 update widget 触发下游重算
     - 底部状态栏显示各类素材数量
============================================================================== */

// ==========================================================================
// 全局样式（仅注入一次）
// ==========================================================================
function injectStyles() {
    if (document.getElementById("h3-aio-media-style")) return;
    const st = document.createElement("style");
    st.id = "h3-aio-media-style";
    st.textContent = `
        .h3-aio-media{width:100%;background:#222;border:1px solid #353545;border-radius:4px;
            margin-top:5px;padding:8px;box-sizing:border-box;display:flex;flex-direction:column;
            gap:6px;pointer-events:auto;overflow:visible;}
        .h3-aio-tabbar{display:flex;align-items:flex-end;width:100%;border-bottom:1px solid #3a3a44;
            margin-bottom:-1px;gap:2px;flex-shrink:0;}
        .h3-aio-tab{background:#2a2a32;color:#9aa;border:1px solid #3a3a44;border-bottom:none;
            padding:3px 12px;border-radius:4px 4px 0 0;cursor:pointer;font-size:11px;user-select:none;}
        .h3-aio-tab.active{background:#3a3f4b;color:#fff;border-color:#5a5f6b;}
        .h3-aio-tab:hover:not(.active){background:#33333c;color:#ccc;}
        .h3-aio-tab .cnt{color:#7fdca0;font-size:9px;margin-left:3px;}
        .h3-aio-panel{display:none;flex-direction:column;gap:6px;width:100%;flex:1 1 auto;min-height:0;}
        .h3-aio-panel.active{display:flex;}
        .h3-aio-topbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;flex-shrink:0;}
        .h3-aio-btn{background:#3a3f4b;color:#fff;border:1px solid #5a5f6b;padding:3px 8px;
            border-radius:3px;cursor:pointer;font-size:10px;}
        .h3-aio-btn.danger{background:#cc2222;border-color:#aa1111;}
        .h3-aio-btn.danger:hover{background:#ff3333;}
        .h3-aio-gridwrap{position:relative;width:100%;min-height:120px;max-height:280px;overflow-y:auto;
            overflow-x:hidden;scrollbar-width:thin;scrollbar-color:#5a5f6b #1a1a1a;flex:1 1 auto;}
        .h3-aio-gridwrap::-webkit-scrollbar{width:8px;}
        .h3-aio-gridwrap::-webkit-scrollbar-track{background:#1a1a1a;border-radius:4px;}
        .h3-aio-gridwrap::-webkit-scrollbar-thumb{background:#5a5f6b;border-radius:4px;}
        .h3-aio-gridwrap::-webkit-scrollbar-thumb:hover{background:#7a7f8b;}
        .h3-aio-grid{display:grid;gap:6px;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));
            grid-auto-rows:max-content;align-content:start;width:100%;padding:2px;}
        .h3-aio-slot{position:relative;background:#000;border:1px solid #444;border-radius:4px;
            overflow:hidden;display:flex;flex-direction:column;gap:2px;padding:2px;}
        .h3-aio-slot-header{display:flex;align-items:center;gap:3px;padding:1px 3px;
            background:linear-gradient(#1c1c22,#16161a);border-bottom:1px solid #2c2c34;flex-shrink:0;}
        .h3-aio-grip{cursor:grab;color:#9aa;font-size:12px;line-height:1;width:14px;text-align:center;
            user-select:none;flex:0 0 auto;}
        .h3-aio-rep{cursor:pointer;color:#cfe;font-size:12px;line-height:1;width:16px;text-align:center;flex:0 0 auto;}
        .h3-aio-rep:hover{color:#6cf;}
        .h3-aio-move{cursor:pointer;color:#cfe;font-size:13px;line-height:1;width:14px;text-align:center;flex:0 0 auto;}
        .h3-aio-move:hover{color:#6cf;}
        .h3-aio-move.disabled{color:#555;cursor:default;}
        .h3-aio-spacer{flex:1 1 auto;}
        .h3-aio-del{cursor:pointer;background:#cc2222;color:#fff;width:16px;height:16px;
            display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:bold;
            border-radius:3px;flex:0 0 auto;}
        .h3-aio-del:hover{background:#ff3333;}
        .h3-aio-badge{position:absolute;bottom:22px;left:0;background:rgba(0,0,0,.75);color:#fff;
            padding:1px 5px;font-size:10px;font-family:sans-serif;font-weight:bold;
            border-top-right-radius:4px;pointer-events:none;z-index:5;}
        .h3-aio-trim{display:flex;gap:2px;align-items:center;justify-content:center;padding:1px 2px;background:#1a1a1a;flex-shrink:0;}
        .h3-aio-trim span{color:#9aa;font-size:8px;}
        .h3-aio-trim input{width:36px;font-size:9px;background:#2a2a2a;color:#fff;border:1px solid #555;border-radius:2px;}
        .h3-aio-trimhint{color:#7fdca0;font-size:7px;text-align:center;padding:0 2px;flex-shrink:0;}
        .h3-aio-status{width:100%;background:#1a1a1a;border:1px solid #2f2f3a;border-radius:4px;
            color:#c8c8c8;font-family:monospace;font-size:10px;padding:4px 7px;white-space:pre-wrap;
            word-break:break-all;box-sizing:border-box;flex-shrink:0;line-height:1.4;min-height:20px;}
        .h3-dragging{opacity:.4;}
        .h3-dragover{outline:2px dashed #4CAF50;outline-offset:-2px;box-shadow:0 0 6px #4CAF50 inset;}
        /* ---- 关键帧标签页 ---- */
        .h3-aio-kfempty{border:1px dashed #555;border-radius:3px;color:#788;font-size:9px;
            padding:6px 2px;text-align:center;cursor:pointer;background:#141418;flex-shrink:0;}
        .h3-aio-kfempty:hover{border-color:#7fdca0;color:#7fdca0;}
        .h3-aio-kfrow{display:flex;align-items:center;gap:3px;justify-content:center;
            padding:2px;background:#1a1a22;border-radius:3px;flex-shrink:0;}
        .h3-aio-kfrow span{color:#9aa;font-size:8px;flex:0 0 auto;}
        .h3-aio-kfrow input{width:44px;font-size:9px;background:#2a2a2a;color:#fff;border:1px solid #555;border-radius:2px;}
        .h3-aio-kfrow .sec{width:56px;}
        .h3-aio-kfrow .eq{color:#7fdca0;}
        .h3-aio-kfsub{display:flex;flex-direction:column;gap:2px;flex-shrink:0;}
        .h3-aio-kfaud{display:flex;align-items:center;gap:3px;background:#12121a;border-radius:3px;padding:1px 3px;}
        .h3-aio-kfaud audio{width:100%;height:22px;flex:1 1 auto;min-width:0;}
        .h3-aio-kfdel{cursor:pointer;color:#f77;font-size:11px;line-height:1;flex:0 0 auto;padding:0 2px;}
        .h3-aio-kfdel:hover{color:#ff4444;}
        .h3-aio-kfmedia video,.h3-aio-kfmedia img{width:100%;max-height:90px;object-fit:contain;background:#000;display:block;}
        .h3-aio-kfmedia video{max-height:90px;}
        .h3-aio-kfhint{color:#7fdca0;font-size:7px;text-align:center;padding:0 2px;flex-shrink:0;}
    `;
    document.head.appendChild(st);
}

// ==========================================================================
// 单文件上传
// ==========================================================================
async function uploadOneFile(file) {
    const body = new FormData();
    body.append("image", file);
    try {
        const resp = await api.fetchApi("/upload/image", { method: "POST", body });
        if (resp.status === 200) {
            const data = await resp.json();
            let name = data.name;
            if (data.subfolder) name = data.subfolder + "/" + name;
            return name;
        }
    } catch (e) { console.error("H3 AIO upload error", e); }
    return null;
}

// ==========================================================================
// 构建单个媒体类型的网格面板
// cfg: { node, pathsWidget, updateWidget, mediaType, maxItems, accept, bumpUpdate, onChanged }
// ==========================================================================
function buildMediaGrid(cfg) {
    const { node, pathsWidget, updateWidget, mediaType, maxItems, accept, bumpUpdate, onChanged } = cfg;
    const typePrefix = mediaType === "image" ? "image/"
        : (mediaType === "video" ? "video/" : "audio/");
    let dragSrcIndex = null;

    const panel = document.createElement("div");
    panel.className = "h3-aio-panel";

    // 顶部操作条
    const topbar = document.createElement("div");
    topbar.className = "h3-aio-topbar";
    const uploadBtn = document.createElement("button");
    uploadBtn.className = "h3-aio-btn";
    uploadBtn.textContent = mediaType === "image" ? "上传图片" : (mediaType === "video" ? "上传视频" : "上传音频");
    const clearBtn = document.createElement("button");
    clearBtn.className = "h3-aio-btn danger";
    clearBtn.textContent = "清空";
    topbar.appendChild(uploadBtn);
    topbar.appendChild(clearBtn);
    panel.appendChild(topbar);

    // 网格
    const gridwrap = document.createElement("div");
    gridwrap.className = "h3-aio-gridwrap";
    const grid = document.createElement("div");
    grid.className = "h3-aio-grid";
    gridwrap.appendChild(grid);
    panel.appendChild(gridwrap);

    // 隐藏文件输入
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.multiple = true;
    fileInput.accept = accept;
    fileInput.style.display = "none";
    panel.appendChild(fileInput);

    const replaceInput = document.createElement("input");
    replaceInput.type = "file";
    replaceInput.accept = accept;
    replaceInput.style.display = "none";
    panel.appendChild(replaceInput);

    let replaceIdx = -1;
    replaceInput.onchange = async (e) => {
        const f = e.target.files && e.target.files[0];
        if (f && replaceIdx >= 0) {
            const name = await uploadOneFile(f);
            if (name) {
                const items = getItems();
                if (replaceIdx >= 0 && replaceIdx < items.length) {
                    items[replaceIdx] = { path: name, start: 0, end: 0 };
                    setItems(items);
                }
            }
        }
        replaceIdx = -1;
        replaceInput.value = "";
    };

    uploadBtn.onclick = () => fileInput.click();
    fileInput.onchange = (e) => handleFiles(e.target.files);
    clearBtn.onclick = () => setItems([]);

    // 数据读写
    const oldCb = pathsWidget?.callback;
    function getItems() {
        return (pathsWidget?.value || "")
            .split("\n").map(l => l.trim()).filter(Boolean)
            .map(line => {
                const p = line.split("|");
                return { path: p[0] || "", start: parseFloat(p[1]) || 0, end: parseFloat(p[2]) || 0 };
            });
    }
    function setItems(newItems) {
        if (!pathsWidget) return;
        const val = newItems.map(it => `${it.path}|${it.start}|${it.end}`).join("\n");
        const tmp = pathsWidget.callback;
        pathsWidget.callback = null;
        pathsWidget.value = val;
        if (oldCb) oldCb.apply(pathsWidget, [val]);
        pathsWidget.callback = tmp;
        bumpUpdate();
        refresh();
        if (onChanged) onChanged();
    }

    async function handleFiles(files) {
        const current = getItems();
        const uploaded = [];
        for (const file of files) {
            const name = await uploadOneFile(file);
            if (name) uploaded.push({ path: name, start: 0, end: 0 });
        }
        if (uploaded.length) {
            setItems(current.concat(uploaded).slice(0, maxItems));
        }
    }

    // 排序
    function onDropReorder(e, index, slot) {
        e.preventDefault(); e.stopPropagation();
        slot.classList.remove("h3-dragover");
        const src = dragSrcIndex;
        dragSrcIndex = null;
        if (src === null || src === index) return;
        const items = getItems();
        if (src < 0 || src >= items.length) return;
        const [moved] = items.splice(src, 1);
        if (!moved) return;
        let target = index;
        if (src < index) target = index - 1;
        target = Math.max(0, Math.min(target, items.length));
        items.splice(target, 0, moved);
        setItems(items);
    }
    function moveItem(index, dir) {
        const items = getItems();
        const target = index + dir;
        if (target < 0 || target >= items.length) return;
        const [moved] = items.splice(index, 1);
        items.splice(target, 0, moved);
        setItems(items);
    }

    // 渲染
    function refresh() {
        grid.innerHTML = "";
        const items = getItems();
        items.forEach((it, index) => {
            const slot = document.createElement("div");
            slot.className = "h3-aio-slot";

            // 预览
            const attachTrim = (el) => {
                if (it.end > 0 || it.start > 0) {
                    el.addEventListener("loadedmetadata", () => { if (it.start > 0) el.currentTime = it.start; });
                    el.addEventListener("play", () => { if (it.start > 0) el.currentTime = it.start; });
                    el.addEventListener("timeupdate", () => { if (it.end > 0 && el.currentTime >= it.end) el.pause(); });
                }
            };
            if (mediaType === "video") {
                const vid = document.createElement("video");
                vid.src = `/api/view?filename=${encodeURIComponent(it.path)}&type=input`;
                vid.muted = false; vid.preload = "metadata"; vid.playsInline = true;
                vid.style.cssText = "width:100%;height:80px;object-fit:contain;background:#000;display:block;";
                attachTrim(vid);
                vid.addEventListener("mouseenter", () => {
                    try {
                        if (it.start > 0) vid.currentTime = it.start;
                        // Try with sound first; browsers may block autoplay-with-audio,
                        // fall back to muted playback if the promise rejects.
                        vid.muted = false;
                        const p = vid.play();
                        if (p && typeof p.catch === "function") {
                            p.catch(() => {
                                try { vid.muted = true; vid.play().catch(()=>{}); } catch(e){}
                            });
                        }
                    } catch(e){}
                });
                vid.addEventListener("mouseleave", () => {
                    try { vid.pause(); if (it.start > 0) vid.currentTime = it.start; } catch(e){}
                });
                slot.appendChild(vid);
            } else if (mediaType === "audio") {
                const aud = document.createElement("audio");
                aud.src = `/api/view?filename=${encodeURIComponent(it.path)}&type=input`;
                aud.controls = true;
                aud.style.cssText = "width:100%;height:28px;";
                attachTrim(aud);
                const fn = document.createElement("div");
                fn.textContent = it.path.split("/").pop();
                fn.style.cssText = "color:#ccc;font-size:8px;padding:0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
                slot.appendChild(aud);
                slot.appendChild(fn);
            } else {
                const img = document.createElement("img");
                img.src = `/api/view?filename=${encodeURIComponent(it.path)}&type=input`;
                img.style.cssText = "width:100%;height:80px;object-fit:contain;background:#000;display:block;cursor:pointer;";
                img.title = "点击更换此图片";
                img.onclick = (e) => { e.stopPropagation(); replaceIdx = index; replaceInput.click(); };
                slot.appendChild(img);
            }

            // 裁剪（视频/音频）
            if (mediaType !== "image") {
                const trim = document.createElement("div");
                trim.className = "h3-aio-trim";
                const lab = t => { const s = document.createElement("span"); s.textContent = t; return s; };
                const numInp = (val, which) => {
                    const inp = document.createElement("input");
                    inp.type = "number"; inp.step = "0.1"; inp.min = "0"; inp.value = String(val);
                    inp.onchange = () => {
                        const items2 = getItems();
                        items2[index][which] = parseFloat(inp.value) || 0;
                        setItems(items2);
                    };
                    return inp;
                };
                trim.appendChild(lab("起"));
                trim.appendChild(numInp(it.start, "start"));
                trim.appendChild(lab("止"));
                trim.appendChild(numInp(it.end, "end"));
                slot.appendChild(trim);
                if (it.end > 0 || it.start > 0) {
                    const hint = document.createElement("div");
                    hint.className = "h3-aio-trimhint";
                    hint.textContent = `裁剪 ${it.start > 0 ? it.start : 0}-${it.end > 0 ? it.end : "尾"}s`;
                    slot.appendChild(hint);
                }
            }

            // 顶部工具条
            const header = document.createElement("div");
            header.className = "h3-aio-slot-header";

            const grip = document.createElement("div");
            grip.className = "h3-aio-grip";
            grip.textContent = "☰";
            grip.title = "拖动排序";
            grip.draggable = true;
            grip.addEventListener("dragstart", (e) => {
                dragSrcIndex = index;
                e.dataTransfer.effectAllowed = "move";
                try { e.dataTransfer.setData("text/plain", String(index)); } catch(err) {}
                slot.classList.add("h3-dragging");
                e.stopPropagation();
            });
            grip.addEventListener("dragend", () => {
                dragSrcIndex = null;
                slot.classList.remove("h3-dragging");
                grid.querySelectorAll(".h3-dragover").forEach(s => s.classList.remove("h3-dragover"));
            });

            const rep = document.createElement("div");
            rep.className = "h3-aio-rep";
            rep.textContent = "↻";
            rep.title = "更换素材";
            rep.onclick = (e) => { e.stopPropagation(); replaceIdx = index; replaceInput.click(); };

            const mkMove = (arrow, dir, disabled) => {
                const b = document.createElement("div");
                b.className = "h3-aio-move" + (disabled ? " disabled" : "");
                b.textContent = arrow;
                b.title = dir < 0 ? "上移" : "下移";
                if (!disabled) b.onclick = (e) => { e.stopPropagation(); moveItem(index, dir); };
                return b;
            };

            const spacer = document.createElement("div");
            spacer.className = "h3-aio-spacer";

            const del = document.createElement("div");
            del.className = "h3-aio-del";
            del.innerHTML = `<svg width="9" height="9" viewBox="0 0 10 10" fill="none"><path d="M1 1L9 9M9 1L1 9" stroke="white" stroke-width="2" stroke-linecap="round"/></svg>`;
            del.title = "删除";
            del.onclick = (e) => { e.stopPropagation(); setItems(getItems().filter((_, i) => i !== index)); };

            header.appendChild(grip);
            header.appendChild(rep);
            header.appendChild(mkMove("↑", -1, index === 0));
            header.appendChild(mkMove("↓", 1, index === items.length - 1));
            header.appendChild(spacer);
            header.appendChild(del);
            slot.insertBefore(header, slot.firstChild);

            // 序号
            if (mediaType === "image" || mediaType === "video") {
                const badge = document.createElement("div");
                badge.className = "h3-aio-badge";
                badge.textContent = (index + 1).toString();
                slot.appendChild(badge);
            }

            // 拖放目标
            slot.addEventListener("dragover", (e) => {
                if (dragSrcIndex === null || dragSrcIndex === index) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                slot.classList.add("h3-dragover");
            });
            slot.addEventListener("dragleave", () => slot.classList.remove("h3-dragover"));
            slot.addEventListener("drop", (e) => onDropReorder(e, index, slot));
            slot.addEventListener("contextmenu", (e) => e.stopPropagation());

            grid.appendChild(slot);
        });
    }

    // 面板拖放
    panel.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); };
    panel.ondrop = (e) => {
        e.preventDefault(); e.stopPropagation();
        if (e.dataTransfer.files.length) {
            const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith(typePrefix));
            if (files.length) handleFiles(files);
        }
    };

    if (pathsWidget) {
        pathsWidget.callback = (v) => {
            if (oldCb) oldCb.apply(pathsWidget, [v]);
            refresh();
            if (onChanged) onChanged();
        };
    }

    return { panel, refresh, getItems, setItems, handleFiles, typePrefix, grid };
}

// ==========================================================================
// 构建"关键帧"网格面板（Add Guide for MiniMax H3）
// 每个槽 = { media(图片或视频) + audio(可选) + frame_idx }，动态增删、任意多个。
// 行序列化: media_path|audio_path|frame_idx|media_start|media_end|audio_start|audio_end
// 帧↔秒换算沿用 H3 数学表达式: 帧→秒 = frame/24 ; 秒→帧 = Math.round(s*24)
// ==========================================================================
const H3_FPS = 24;
const H3_KF_MEDIA_EXTS = ["jpg","jpeg","png","webp","bmp","gif","tiff"];
const H3_KF_VIDEO_EXTS = ["mp4","webm","mkv","avi","mov","m4v","flv","wmv"];

function isImagePath(p) {
    const ext = (p || "").split(".").pop().toLowerCase();
    return H3_KF_MEDIA_EXTS.includes(ext);
}
function isVideoPath(p) {
    const ext = (p || "").split(".").pop().toLowerCase();
    return H3_KF_VIDEO_EXTS.includes(ext);
}

function buildKeyframeGrid(cfg) {
    const { node, pathsWidget, updateWidget, maxItems, bumpUpdate, onChanged } = cfg;
    let dragSrcIndex = null;

    const panel = document.createElement("div");
    panel.className = "h3-aio-panel";

    // 顶部操作条
    const topbar = document.createElement("div");
    topbar.className = "h3-aio-topbar";
    const addBtn = document.createElement("button");
    addBtn.className = "h3-aio-btn";
    addBtn.textContent = "＋ 添加关键帧";
    const clearBtn = document.createElement("button");
    clearBtn.className = "h3-aio-btn danger";
    clearBtn.textContent = "清空";
    topbar.appendChild(addBtn);
    topbar.appendChild(clearBtn);
    panel.appendChild(topbar);

    // 网格
    const gridwrap = document.createElement("div");
    gridwrap.className = "h3-aio-gridwrap";
    const grid = document.createElement("div");
    grid.className = "h3-aio-grid";
    gridwrap.appendChild(grid);
    panel.appendChild(gridwrap);

    // 隐藏文件输入：图片/视频（媒体）
    const mediaInput = document.createElement("input");
    mediaInput.type = "file";
    mediaInput.accept = "image/*,video/*";
    mediaInput.style.display = "none";
    panel.appendChild(mediaInput);
    // 隐藏文件输入：音频
    const audioInput = document.createElement("input");
    audioInput.type = "file";
    audioInput.accept = "audio/*";
    audioInput.style.display = "none";
    panel.appendChild(audioInput);

    // 数据读写（行格式 media|audio|frame|ms|me|as|ae）
    const oldCb = pathsWidget?.callback;
    function getItems() {
        return (pathsWidget?.value || "")
            .split("\n").map(l => l.trim()).filter(Boolean)
            .map(line => {
                const p = line.split("|");
                const media = p[0] ? { path: p[0], start: parseFloat(p[3]) || 0, end: parseFloat(p[4]) || 0 } : null;
                const audio = p[1] ? { path: p[1], start: parseFloat(p[5]) || 0, end: parseFloat(p[6]) || 0 } : null;
                return { media, audio, frame_idx: parseInt(p[2], 10) || 0 };
            });
    }
    function setItems(newItems) {
        if (!pathsWidget) return;
        const val = newItems.map(it => {
            const m = it.media || {};
            const a = it.audio || {};
            return `${m.path || ""}|${a.path || ""}|${it.frame_idx || 0}|${m.start || 0}|${m.end || 0}|${a.start || 0}|${a.end || 0}`;
        }).join("\n");
        const tmp = pathsWidget.callback;
        pathsWidget.callback = null;
        pathsWidget.value = val;
        if (oldCb) oldCb.apply(pathsWidget, [val]);
        pathsWidget.callback = tmp;
        bumpUpdate();
        refresh();
        if (onChanged) onChanged();
    }

    // 面板级拖放/批量：图片/视频→media，音频→audio（优先填空槽，否则新建槽）
    async function handleFiles(files) {
        const items = getItems();
        let changed = false;
        for (const file of files) {
            const name = await uploadOneFile(file);
            if (!name) continue;
            changed = true;
            if (file.type.startsWith("audio/")) {
                const slot = items.find(it => !it.audio);
                if (slot) slot.audio = { path: name, start: 0, end: 0 };
                else items.push({ media: null, audio: { path: name, start: 0, end: 0 }, frame_idx: 0 });
            } else {
                const slot = items.find(it => !it.media);
                if (slot) slot.media = { path: name, start: 0, end: 0 };
                else items.push({ media: { path: name, start: 0, end: 0 }, audio: null, frame_idx: 0 });
            }
        }
        if (changed) setItems(items.slice(0, maxItems));
    }

    // 排序（与单类型网格一致）
    function onDropReorder(e, index, slot) {
        e.preventDefault(); e.stopPropagation();
        slot.classList.remove("h3-dragover");
        const src = dragSrcIndex;
        dragSrcIndex = null;
        if (src === null || src === index) return;
        const items = getItems();
        if (src < 0 || src >= items.length) return;
        const [moved] = items.splice(src, 1);
        if (!moved) return;
        let target = index;
        if (src < index) target = index - 1;
        target = Math.max(0, Math.min(target, items.length));
        items.splice(target, 0, moved);
        setItems(items);
    }
    function moveItem(index, dir) {
        const items = getItems();
        const target = index + dir;
        if (target < 0 || target >= items.length) return;
        const [moved] = items.splice(index, 1);
        items.splice(target, 0, moved);
        setItems(items);
    }

    // 裁剪输入（回调直接收 input 元素，避免 this 绑定问题）
    function makeTrimInput(val, onchange) {
        const inp = document.createElement("input");
        inp.type = "number"; inp.step = "0.1"; inp.min = "0"; inp.value = String(val);
        inp.onchange = () => onchange(inp);
        return inp;
    }

    // 槽渲染
    function refresh() {
        grid.innerHTML = "";
        const items = getItems();
        items.forEach((it, index) => {
            const slot = document.createElement("div");
            slot.className = "h3-aio-slot";

            // ---- 媒体区（图片 / 视频）----
            const mediaZone = document.createElement("div");
            mediaZone.className = "h3-aio-kfmedia";
            if (it.media) {
                if (isImagePath(it.media.path)) {
                    const img = document.createElement("img");
                    img.src = `/api/view?filename=${encodeURIComponent(it.media.path)}&type=input`;
                    img.title = "点击更换图片";
                    img.onclick = (e) => { e.stopPropagation(); mediaInput._slot = index; mediaInput._which = "media"; mediaInput.click(); };
                    mediaZone.appendChild(img);
                } else {
                    const vid = document.createElement("video");
                    vid.src = `/api/view?filename=${encodeURIComponent(it.media.path)}&type=input`;
                    vid.muted = false; vid.preload = "metadata"; vid.playsInline = true;
                    if (it.media.end > 0 || it.media.start > 0) {
                        vid.addEventListener("loadedmetadata", () => { if (it.media.start > 0) vid.currentTime = it.media.start; });
                        vid.addEventListener("timeupdate", () => { if (it.media.end > 0 && vid.currentTime >= it.media.end) vid.pause(); });
                    }
                    vid.addEventListener("mouseenter", () => {
                        try {
                            if (it.media.start > 0) vid.currentTime = it.media.start;
                            const p = vid.play();
                            if (p && typeof p.catch === "function") p.catch(() => { try { vid.muted = true; vid.play().catch(()=>{}); } catch(e){} });
                        } catch(e){}
                    });
                    vid.addEventListener("mouseleave", () => { try { vid.pause(); } catch(e){} });
                    vid.title = "点击更换视频";
                    vid.style.cursor = "pointer";
                    vid.onclick = (e) => { e.stopPropagation(); mediaInput._slot = index; mediaInput._which = "media"; mediaInput.click(); };
                    mediaZone.appendChild(vid);
                }
                // 视频片段裁剪
                if (isVideoPath(it.media.path)) {
                    const trim = document.createElement("div");
                    trim.className = "h3-aio-trim";
                    const lab = t => { const s = document.createElement("span"); s.textContent = t; return s; };
                    trim.appendChild(lab("起"));
                    trim.appendChild(makeTrimInput(it.media.start, (inp) => {
                        const items2 = getItems();
                        items2[index].media.start = parseFloat(inp.value) || 0;
                        setItems(items2);
                    }));
                    trim.appendChild(lab("止"));
                    trim.appendChild(makeTrimInput(it.media.end, (inp) => {
                        const items2 = getItems();
                        items2[index].media.end = parseFloat(inp.value) || 0;
                        setItems(items2);
                    }));
                    mediaZone.appendChild(trim);
                }
            } else {
                const empty = document.createElement("div");
                empty.className = "h3-aio-kfempty";
                empty.textContent = "＋ 拖入 / 点击上传图片或视频";
                empty.onclick = (e) => { e.stopPropagation(); mediaInput._slot = index; mediaInput._which = "media"; mediaInput.click(); };
                mediaZone.appendChild(empty);
            }
            // 媒体区自身作为拖放目标（图片/视频）
            mediaZone.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); mediaZone.classList.add("h3-dragover"); };
            mediaZone.ondragleave = () => mediaZone.classList.remove("h3-dragover");
            mediaZone.ondrop = (e) => {
                e.preventDefault(); e.stopPropagation();
                mediaZone.classList.remove("h3-dragover");
                const files = Array.from(e.dataTransfer.files).filter(f => !f.type.startsWith("audio/"));
                if (files.length) {
                    const items2 = getItems();
                    items2[index].media = null;
                    setItems(items2);
                    assignFilesToSlot(files, index, "media");
                }
            };
            slot.appendChild(mediaZone);

            // ---- 音频区 ----
            const audZone = document.createElement("div");
            audZone.className = "h3-aio-sub";
            if (it.audio) {
                const wrap = document.createElement("div");
                wrap.className = "h3-aio-kfaud";
                const aud = document.createElement("audio");
                aud.src = `/api/view?filename=${encodeURIComponent(it.audio.path)}&type=input`;
                aud.controls = true; aud.preload = "metadata";
                if (it.audio.end > 0 || it.audio.start > 0) {
                    aud.addEventListener("loadedmetadata", () => { if (it.audio.start > 0) aud.currentTime = it.audio.start; });
                    aud.addEventListener("timeupdate", () => { if (it.audio.end > 0 && aud.currentTime >= it.audio.end) aud.pause(); });
                }
                wrap.appendChild(aud);
                const del = document.createElement("div");
                del.className = "h3-aio-kfdel";
                del.textContent = "✕";
                del.title = "移除音频";
                del.onclick = (e) => {
                    e.stopPropagation();
                    const items2 = getItems();
                    items2[index].audio = null;
                    setItems(items2);
                };
                wrap.appendChild(del);
                audZone.appendChild(wrap);
                const trim = document.createElement("div");
                trim.className = "h3-aio-trim";
                const lab = t => { const s = document.createElement("span"); s.textContent = t; return s; };
                trim.appendChild(lab("起"));
                trim.appendChild(makeTrimInput(it.audio.start, (inp) => {
                    const items2 = getItems();
                    items2[index].audio.start = parseFloat(inp.value) || 0;
                    setItems(items2);
                }));
                trim.appendChild(lab("止"));
                trim.appendChild(makeTrimInput(it.audio.end, (inp) => {
                    const items2 = getItems();
                    items2[index].audio.end = parseFloat(inp.value) || 0;
                    setItems(items2);
                }));
                audZone.appendChild(trim);
            } else {
                const empty = document.createElement("div");
                empty.className = "h3-aio-kfempty";
                empty.textContent = "＋ 拖入 / 点击上传音频（可裁切）";
                empty.onclick = (e) => { e.stopPropagation(); audioInput._slot = index; audioInput._which = "audio"; audioInput.click(); };
                audZone.appendChild(empty);
            }
            audZone.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); audZone.classList.add("h3-dragover"); };
            audZone.ondragleave = () => audZone.classList.remove("h3-dragover");
            audZone.ondrop = (e) => {
                e.preventDefault(); e.stopPropagation();
                audZone.classList.remove("h3-dragover");
                const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("audio/"));
                if (files.length) {
                    const items2 = getItems();
                    items2[index].audio = null;
                    setItems(items2);
                    assignFilesToSlot(files, index, "audio");
                }
            };
            slot.appendChild(audZone);

            // ---- 帧号 / 秒 换算行（沿用 H3 数学表达式）----
            const row = document.createElement("div");
            row.className = "h3-aio-kfrow";
            const lab = t => { const s = document.createElement("span"); s.textContent = t; return s; };
            const fInp = document.createElement("input");
            fInp.type = "number"; fInp.step = "1"; fInp.value = String(it.frame_idx);
            fInp.title = "锚定帧号（0 起始；负数=从结尾倒数）";
            const eq = document.createElement("span");
            eq.className = "eq";
            eq.textContent = "=";
            const sInp = document.createElement("input");
            sInp.type = "number"; sInp.step = "0.1"; sInp.className = "sec";
            sInp.value = (it.frame_idx / H3_FPS).toFixed(2);
            sInp.title = "对应秒数（帧 ÷ 24）；修改秒数会反算帧号 round(秒×24)";
            fInp.onchange = () => {
                const items2 = getItems();
                items2[index].frame_idx = parseInt(fInp.value, 10) || 0;
                sInp.value = (items2[index].frame_idx / H3_FPS).toFixed(2);
                setItems(items2);
            };
            sInp.onchange = () => {
                const items2 = getItems();
                const f = Math.round((parseFloat(sInp.value) || 0) * H3_FPS);
                items2[index].frame_idx = f;
                fInp.value = String(f);
                setItems(items2);
            };
            row.appendChild(lab("帧"));
            row.appendChild(fInp);
            row.appendChild(eq);
            row.appendChild(sInp);
            row.appendChild(lab("秒"));
            slot.appendChild(row);
            if (it.media) {
                const hint = document.createElement("div");
                hint.className = "h3-aio-kfhint";
                hint.textContent = `锚定 ${it.frame_idx} 帧 ≈ ${(it.frame_idx / H3_FPS).toFixed(2)}s` + (it.frame_idx < 0 ? "（从结尾倒数）" : "");
                slot.appendChild(hint);
            }

            // ---- 顶部工具条 ----
            const header = document.createElement("div");
            header.className = "h3-aio-slot-header";
            const grip = document.createElement("div");
            grip.className = "h3-aio-grip";
            grip.textContent = "☰";
            grip.title = "拖动排序";
            grip.draggable = true;
            grip.addEventListener("dragstart", (e) => {
                dragSrcIndex = index;
                e.dataTransfer.effectAllowed = "move";
                try { e.dataTransfer.setData("text/plain", String(index)); } catch(err) {}
                slot.classList.add("h3-dragging");
                e.stopPropagation();
            });
            grip.addEventListener("dragend", () => {
                dragSrcIndex = null;
                slot.classList.remove("h3-dragging");
                grid.querySelectorAll(".h3-dragover").forEach(s => s.classList.remove("h3-dragover"));
            });
            const rep = document.createElement("div");
            rep.className = "h3-aio-rep";
            rep.textContent = "↻";
            rep.title = "更换主素材";
            rep.onclick = (e) => { e.stopPropagation(); mediaInput._slot = index; mediaInput._which = "media"; mediaInput.click(); };
            const mkMove = (arrow, dir, disabled) => {
                const b = document.createElement("div");
                b.className = "h3-aio-move" + (disabled ? " disabled" : "");
                b.textContent = arrow;
                b.title = dir < 0 ? "上移" : "下移";
                if (!disabled) b.onclick = (e) => { e.stopPropagation(); moveItem(index, dir); };
                return b;
            };
            const spacer = document.createElement("div");
            spacer.className = "h3-aio-spacer";
            const del = document.createElement("div");
            del.className = "h3-aio-del";
            del.innerHTML = `<svg width="9" height="9" viewBox="0 0 10 10" fill="none"><path d="M1 1L9 9M9 1L1 9" stroke="white" stroke-width="2" stroke-linecap="round"/></svg>`;
            del.title = "删除该关键帧";
            del.onclick = (e) => { e.stopPropagation(); setItems(getItems().filter((_, i) => i !== index)); };
            header.appendChild(grip);
            header.appendChild(rep);
            header.appendChild(mkMove("↑", -1, index === 0));
            header.appendChild(mkMove("↓", 1, index === items.length - 1));
            header.appendChild(spacer);
            header.appendChild(del);
            slot.insertBefore(header, slot.firstChild);

            // 序号
            const badge = document.createElement("div");
            badge.className = "h3-aio-badge";
            badge.textContent = (index + 1).toString();
            slot.appendChild(badge);

            // 拖放目标（排序）
            slot.addEventListener("dragover", (e) => {
                if (dragSrcIndex === null || dragSrcIndex === index) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                slot.classList.add("h3-dragover");
            });
            slot.addEventListener("dragleave", () => slot.classList.remove("h3-dragover"));
            slot.addEventListener("drop", (e) => onDropReorder(e, index, slot));
            slot.addEventListener("contextmenu", (e) => e.stopPropagation());

            grid.appendChild(slot);
        });
    }

    // 把 files 上传并逐个落到某槽的 media/audio（先清空该位）
    async function assignFilesToSlot(files, slotIndex, which) {
        const items = getItems();
        const slot = items[slotIndex];
        if (!slot) return;
        const name = await uploadOneFile(files[0]);
        if (name) {
            if (which === "media") slot.media = { path: name, start: 0, end: 0 };
            else slot.audio = { path: name, start: 0, end: 0 };
            setItems(items);
        }
    }

    // 文件输入事件
    mediaInput.onchange = () => {
        const f = mediaInput.files && mediaInput.files[0];
        if (f) assignFilesToSlot([f], mediaInput._slot || 0, mediaInput._which || "media");
        mediaInput.value = "";
    };
    audioInput.onchange = () => {
        const f = audioInput.files && audioInput.files[0];
        if (f) assignFilesToSlot([f], audioInput._slot || 0, audioInput._which || "audio");
        audioInput.value = "";
    };

    addBtn.onclick = () => {
        setItems(getItems().concat([{ media: null, audio: null, frame_idx: 0 }]).slice(0, maxItems));
    };
    clearBtn.onclick = () => setItems([]);

    // 面板级拖放（按类型路由）
    panel.ondragover = (e) => { e.preventDefault(); e.stopPropagation(); };
    panel.ondrop = (e) => {
        e.preventDefault(); e.stopPropagation();
        if (e.dataTransfer.files.length) handleFiles(Array.from(e.dataTransfer.files));
    };

    if (pathsWidget) {
        pathsWidget.callback = (v) => {
            if (oldCb) oldCb.apply(pathsWidget, [v]);
            refresh();
            if (onChanged) onChanged();
        };
    }

    return { panel, refresh, getItems, setItems, handleFiles, grid };
}

// ==========================================================================
// 主函数：给 H3ModelLoader 节点添加内嵌素材上传 UI
// ==========================================================================
function buildAIOMediaLoader(node) {
    injectStyles();

    const imagePathsW = node.widgets.find(w => w.name === "image_paths");
    const videoPathsW = node.widgets.find(w => w.name === "video_paths");
    const audioPathsW = node.widgets.find(w => w.name === "audio_paths");
    const keyframePathsW = node.widgets.find(w => w.name === "keyframe_paths");
    const updateW = node.widgets.find(w => w.name === "update");

    // 确保隐藏 widget 不显示（V3 extra_dict hidden 应该已处理，这里做双保险）
    [imagePathsW, videoPathsW, audioPathsW, keyframePathsW, updateW].forEach(w => {
        if (!w) return;
        w.computeSize = function () { return [0, 0]; };
        w.draw = function () {};
        const iv = setInterval(() => { if (w.element) w.element.style.display = "none"; }, 50);
        setTimeout(() => clearInterval(iv), 1000);
    });

    function bumpUpdate() {
        if (!updateW) return;
        const cur = parseInt(updateW.value, 10);
        updateW.value = (isNaN(cur) ? 0 : cur) + 1;
        if (typeof updateW.callback === "function") updateW.callback(updateW.value);
    }

    // 主容器
    const container = document.createElement("div");
    container.className = "h3-aio-media";

    // 标签页栏
    const tabbar = document.createElement("div");
    tabbar.className = "h3-aio-tabbar";
    const tabs = [
        { key: "image", label: "图片", widget: imagePathsW, accept: "image/*", mediaType: "image", maxItems: 9 },
        { key: "video", label: "视频", widget: videoPathsW, accept: "video/*", mediaType: "video", maxItems: 3 },
        { key: "audio", label: "音频", widget: audioPathsW, accept: "audio/*", mediaType: "audio", maxItems: 3 },
        { key: "kf", label: "关键帧", widget: keyframePathsW, accept: null, mediaType: null, maxItems: 32 },
    ];
    const tabBtns = {};
    const grids = {};
    let activeTab = "image";

    tabs.forEach(cfg => {
        const btn = document.createElement("div");
        btn.className = "h3-aio-tab";
        btn.innerHTML = `${cfg.label}<span class="cnt" id="h3-aio-cnt-${cfg.key}">0</span>`;
        btn.onclick = () => switchTab(cfg.key);
        tabbar.appendChild(btn);
        tabBtns[cfg.key] = btn;
    });
    container.appendChild(tabbar);

    // 标签页内容
    const content = document.createElement("div");
    content.style.cssText = "width:100%;position:relative;";

    function updateCounts() {
        tabs.forEach(cfg => {
            const el = document.getElementById(`h3-aio-cnt-${cfg.key}`);
            if (el) el.textContent = String(grids[cfg.key].getItems().length);
        });
        updateStatus();
        requestNodeResize();
    }

    tabs.forEach(cfg => {
        const g = (cfg.key === "kf")
            ? buildKeyframeGrid({
                node, pathsWidget: cfg.widget, updateWidget: updateW,
                maxItems: cfg.maxItems, bumpUpdate, onChanged: updateCounts,
            })
            : buildMediaGrid({
                node, pathsWidget: cfg.widget, updateWidget: updateW,
                mediaType: cfg.mediaType, maxItems: cfg.maxItems, accept: cfg.accept,
                bumpUpdate, onChanged: updateCounts,
            });
        grids[cfg.key] = g;
        content.appendChild(g.panel);
    });
    container.appendChild(content);

    function switchTab(key) {
        activeTab = key;
        tabs.forEach(cfg => {
            const active = cfg.key === key;
            tabBtns[cfg.key].classList.toggle("active", active);
            grids[cfg.key].panel.classList.toggle("active", active);
        });
        setTimeout(() => { grids[key].refresh(); requestNodeResize(); }, 10);
    }

    // 状态栏
    const status = document.createElement("div");
    status.className = "h3-aio-status";
    status.textContent = "拖拽或上传素材到对应标签页，即自动连接";
    container.appendChild(status);

    function updateStatus() {
        const ic = grids.image.getItems().length;
        const vc = grids.video.getItems().length;
        const ac = grids.audio.getItems().length;
        const kc = grids.kf.getItems().length;
        const total = ic + vc + ac;
        let txt = `图片: ${ic}/9  |  视频: ${vc}/3  |  音频: ${ac}/3  |  合计: ${total}/12  |  关键帧: ${kc}/32`;
        if (ac > 0 && ic === 0 && vc === 0) txt += "\n⚠ H3 不允许纯音频输入，需搭配图片或视频";
        if (total > 12) txt += "\n⚠ 合计超过 12 个文件上限";
        if (kc > 0) txt += "\n◆ 关键帧将按列表顺序逐帧锚定（接法同原生 Add Guide 节点）";
        status.textContent = txt;
    }

    // 添加为 DOM widget
    const domWidget = node.addDOMWidget("Gallery", "html_gallery", container, { serialize: false });

    // 强制节点重新计算布局（支持扩展和收缩，消除底部灰色留白）
    function requestNodeResize() {
        try {
            if (node.computeSize) {
                const sz = node.computeSize();
                if (sz && sz[1]) {
                    // 计算高度 + 少量 padding，允许收缩（消除 prompt 框移除后的底部留白）
                    const targetH = Math.max(220, Math.round(sz[1]) + 16);
                    const targetW = Math.max(220, node.size?.[0] || sz[0] || 220);
                    node.setSize([targetW, targetH]);
                }
            }
            app.graph.setDirtyCanvas(true, true);
        } catch (e) {}
    }

    domWidget.computeSize = function (width) {
        const nodeWidth = node.size?.[0] || width || 220;
        const tabbarH = tabbar.offsetHeight || 28;
        const activePanel = grids[activeTab]?.panel;
        const topbarH = activePanel?.querySelector(".h3-aio-topbar")?.offsetHeight || 28;
        const gridwrap = activePanel?.querySelector(".h3-aio-gridwrap");
        const gridContentH = gridwrap ? Math.min(gridwrap.scrollHeight || 120, 280) : 120;
        const statusH = status.offsetHeight || 24;
        // tabbar + panel(topbar+grid) + status + padding/gaps
        const contentH = tabbarH + topbarH + gridContentH + statusH + 32;
        const h = Math.max(contentH, 220);
        return [Math.max(10, nodeWidth - 30), h];
    };

    // 节点级拖放（按当前标签页过滤；关键帧页不按类型过滤，交给内部路由）
    const origOnDragDrop = node.onDragDrop;
    node.onDragDrop = function (e) {
        let handled = false;
        if (e.dataTransfer && e.dataTransfer.files) {
            const g = grids[activeTab];
            let files;
            if (g.typePrefix) {
                files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith(g.typePrefix));
            } else {
                files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/") || f.type.startsWith("video/") || f.type.startsWith("audio/"));
            }
            if (files.length) { e.preventDefault(); g.handleFiles(files); handled = true; }
        }
        if (!handled && origOnDragDrop) return origOnDragDrop.apply(this, arguments);
        return handled;
    };
    const origOnDragOver = node.onDragOver;
    node.onDragOver = function (e) {
        if (e.dataTransfer && e.dataTransfer.items) {
            const g = grids[activeTab];
            const has = Array.from(e.dataTransfer.items).some(f => {
                if (f.kind !== "file") return false;
                return g.typePrefix ? f.type.startsWith(g.typePrefix)
                    : (f.type.startsWith("image/") || f.type.startsWith("video/") || f.type.startsWith("audio/"));
            });
            if (has) { e.preventDefault(); return true; }
        }
        if (origOnDragOver) return origOnDragOver.apply(this, arguments);
        return false;
    };

    // 粘贴
    const pasteHandler = (e) => {
        if (app.canvas.selected_nodes && app.canvas.selected_nodes[node.id]) {
            const items = e.clipboardData?.items;
            if (!items) return;
            const g = grids[activeTab];
            const files = [];
            for (let i = 0; i < items.length; i++) {
                if (items[i].kind !== "file") continue;
                const ok = g.typePrefix ? items[i].type.startsWith(g.typePrefix)
                    : (items[i].type.startsWith("image/") || items[i].type.startsWith("video/") || items[i].type.startsWith("audio/"));
                if (ok) files.push(items[i].getAsFile());
            }
            if (files.length) { e.preventDefault(); e.stopImmediatePropagation(); g.handleFiles(files); }
        }
    };
    document.addEventListener("paste", pasteHandler, { capture: true });

    const origOnRemoved = node.onRemoved;
    node.onRemoved = function () {
        document.removeEventListener("paste", pasteHandler, { capture: true });
        if (origOnRemoved) origOnRemoved.apply(this, arguments);
    };

    // 初始化
    switchTab("image");
    tabs.forEach(cfg => grids[cfg.key].refresh());
    updateCounts();
    requestAnimationFrame(() => requestNodeResize());
    setTimeout(() => { tabs.forEach(cfg => grids[cfg.key].refresh()); updateCounts(); requestNodeResize(); }, 150);
    setTimeout(() => requestNodeResize(), 500);
    setTimeout(() => requestNodeResize(), 1200);
}

// ==========================================================================
// 注册扩展
// ==========================================================================
app.registerExtension({
    name: "Comfy.H3AIOMediaLoader",
    async nodeCreated(node) {
        if (node.comfyClass === "H3ModelLoader") {
            buildAIOMediaLoader(node);
        }
    },
});