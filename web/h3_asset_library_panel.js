// h3_asset_library_panel.js — H3 Asset Library v3.0 前端浮动面板
// 参考 ComfyUI_GJJ_Nodes 的资产管理设计：
//   - 给 H3AssetLibrary 节点添加"📂 打开资产管理面板"按钮
//   - 点击弹出浮动面板，可视化管理角色/场景/道具
//   - 通过 /h3/asset_library/* API 与后端交互
//   - 数据持久化到 ComfyUI/user/default/h3_assets/library.json

import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

(function () {
    "use strict";

    const EXTENSION_NAME = "Comfy.H3.AssetLibraryPanel";
    const PANEL_ID = "h3-asset-library-panel";
    const STYLE_ID = "h3-asset-library-style";

    // 当前选中的 Tab
    let currentTab = "characters";
    // 当前选中的资产 ID
    let selectedId = "";
    // 面板状态
    let panelVisible = false;
    let panelEl = null;
    // 拖拽状态
    let dragState = null;

    // -----------------------------------------------------------------------
    // API 封装
    // -----------------------------------------------------------------------
    async function apiFetch(path, options = {}) {
        const url = api?.apiURL ? api.apiURL(path) : path;
        const response = await fetch(url, options);
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.error || `请求失败: ${response.status}`);
        }
        return data;
    }

    async function loadLibrary() {
        const data = await apiFetch("/h3/asset_library");
        return data.data || { characters: [], scenes: [], props: [] };
    }

    async function createItem(category, item) {
        const data = await apiFetch(`/h3/asset_library/${category}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(item),
        });
        return data.data;
    }

    async function updateItem(category, id, item) {
        const data = await apiFetch(`/h3/asset_library/${category}/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(item),
        });
        return data.data;
    }

    async function deleteItem(category, id) {
        await apiFetch(`/h3/asset_library/${category}/${id}`, {
            method: "DELETE",
        });
    }

    async function scanFolder(folder, category = null) {
        const data = await apiFetch("/h3/asset_library/scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder, category }),
        });
        return data.data;
    }

    async function importFolder(folder, category = null) {
        const data = await apiFetch("/h3/asset_library/import", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ folder, category }),
        });
        return data.data;
    }

    async function describeImage(image) {
        const data = await apiFetch("/h3/asset_library/describe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image }),
        });
        return data.data.description;
    }

    async function describeAll(items) {
        const data = await apiFetch("/h3/asset_library/describe_all", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items }),
        });
        return data.data.results;
    }

    // -----------------------------------------------------------------------
    // 样式注入
    // -----------------------------------------------------------------------
    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent = `
            #${PANEL_ID} {
                position: fixed;
                z-index: 10000;
                width: 680px;
                max-height: 560px;
                display: flex;
                flex-direction: column;
                background: #1a1a2e;
                border: 1px solid #4a4a6a;
                border-radius: 10px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.6);
                font-family: system-ui, "Microsoft YaHei", sans-serif;
                color: #e0e0e0;
                overflow: hidden;
            }
            #${PANEL_ID} .h3-panel-header {
                display: flex;
                align-items: center;
                padding: 10px 14px;
                background: linear-gradient(135deg, #2d2d44, #1a1a2e);
                border-bottom: 1px solid #4a4a6a;
                cursor: move;
                user-select: none;
            }
            #${PANEL_ID} .h3-panel-title {
                font-size: 15px;
                font-weight: 700;
                flex: 1;
                color: #fff;
            }
            #${PANEL_ID} .h3-panel-close {
                width: 28px; height: 28px;
                border: none; border-radius: 6px;
                background: #3a3a5a; color: #ccc;
                cursor: pointer; font-size: 16px;
                display: flex; align-items: center; justify-content: center;
            }
            #${PANEL_ID} .h3-panel-close:hover { background: #e74c3c; color: #fff; }
            #${PANEL_ID} .h3-panel-tabs {
                display: flex;
                gap: 2px;
                padding: 6px 10px 0;
                background: #22223a;
                border-bottom: 1px solid #4a4a6a;
            }
            #${PANEL_ID} .h3-tab {
                padding: 8px 18px;
                border: none;
                background: transparent;
                color: #888;
                cursor: pointer;
                font-size: 13px;
                font-weight: 600;
                border-radius: 6px 6px 0 0;
                transition: all 0.15s;
            }
            #${PANEL_ID} .h3-tab:hover { color: #ccc; background: #2a2a44; }
            #${PANEL_ID} .h3-tab.active {
                color: #fff;
                background: #1a1a2e;
                border-bottom: 2px solid #6c5ce7;
            }
            #${PANEL_ID} .h3-tab .h3-count {
                display: inline-block;
                margin-left: 6px;
                padding: 1px 7px;
                background: #3a3a5a;
                border-radius: 10px;
                font-size: 11px;
                color: #aaa;
            }
            #${PANEL_ID} .h3-tab.active .h3-count { background: #6c5ce7; color: #fff; }
            #${PANEL_ID} .h3-panel-body {
                flex: 1;
                display: flex;
                overflow: hidden;
                min-height: 380px;
            }
            #${PANEL_ID} .h3-list-pane {
                width: 260px;
                border-right: 1px solid #3a3a5a;
                display: flex;
                flex-direction: column;
                background: #1e1e36;
            }
            #${PANEL_ID} .h3-list-toolbar {
                display: flex;
                gap: 6px;
                padding: 8px;
                border-bottom: 1px solid #3a3a5a;
            }
            #${PANEL_ID} .h3-btn {
                padding: 6px 12px;
                border: 1px solid #4a4a6a;
                border-radius: 6px;
                background: #2d2d44;
                color: #ddd;
                cursor: pointer;
                font-size: 12px;
                font-weight: 600;
                transition: all 0.15s;
            }
            #${PANEL_ID} .h3-btn:hover { background: #3d3d5c; border-color: #6c5ce7; }
            #${PANEL_ID} .h3-btn.primary { background: #6c5ce7; border-color: #6c5ce7; color: #fff; }
            #${PANEL_ID} .h3-btn.primary:hover { background: #7d6ef0; }
            #${PANEL_ID} .h3-btn.danger { background: #c0392b; border-color: #c0392b; color: #fff; }
            #${PANEL_ID} .h3-btn.danger:hover { background: #e74c3c; }
            #${PANEL_ID} .h3-btn.small { padding: 4px 8px; font-size: 11px; }
            #${PANEL_ID} .h3-list {
                flex: 1;
                overflow-y: auto;
                padding: 6px;
            }
            #${PANEL_ID} .h3-list-item {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 8px 10px;
                margin-bottom: 4px;
                border-radius: 6px;
                cursor: pointer;
                transition: background 0.1s;
            }
            #${PANEL_ID} .h3-list-item:hover { background: #2a2a44; }
            #${PANEL_ID} .h3-list-item.selected { background: #3d3d6a; border-left: 3px solid #6c5ce7; }
            #${PANEL_ID} .h3-item-thumb {
                width: 36px; height: 36px;
                border-radius: 4px;
                background: #2a2a44;
                object-fit: cover;
                flex-shrink: 0;
                border: 1px solid #4a4a6a;
            }
            #${PANEL_ID} .h3-item-thumb.placeholder {
                display: flex; align-items: center; justify-content: center;
                font-size: 16px; color: #666;
            }
            #${PANEL_ID} .h3-item-info { flex: 1; min-width: 0; }
            #${PANEL_ID} .h3-item-name {
                font-size: 13px; font-weight: 600; color: #eee;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            }
            #${PANEL_ID} .h3-item-id { font-size: 10px; color: #888; }
            #${PANEL_ID} .h3-edit-pane {
                flex: 1;
                padding: 14px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 12px;
            }
            #${PANEL_ID} .h3-edit-empty {
                flex: 1;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #666;
                font-size: 14px;
            }
            #${PANEL_ID} .h3-field { display: flex; flex-direction: column; gap: 4px; }
            #${PANEL_ID} .h3-field label {
                font-size: 12px; font-weight: 600; color: #aaa;
            }
            #${PANEL_ID} .h3-field input,
            #${PANEL_ID} .h3-field textarea {
                padding: 8px 10px;
                border: 1px solid #4a4a6a;
                border-radius: 6px;
                background: #1a1a2e;
                color: #eee;
                font-size: 13px;
                font-family: inherit;
                outline: none;
                transition: border-color 0.15s;
            }
            #${PANEL_ID} .h3-field input:focus,
            #${PANEL_ID} .h3-field textarea:focus { border-color: #6c5ce7; }
            #${PANEL_ID} .h3-field textarea { min-height: 80px; resize: vertical; }
            #${PANEL_ID} .h3-edit-actions {
                display: flex;
                gap: 8px;
                margin-top: auto;
                padding-top: 10px;
                border-top: 1px solid #3a3a5a;
            }
            #${PANEL_ID} .h3-folder-bar {
                display: flex;
                gap: 6px;
                padding: 8px 10px;
                background: #22223a;
                border-top: 1px solid #3a3a5a;
            }
            #${PANEL_ID} .h3-folder-bar input {
                flex: 1;
                padding: 6px 10px;
                border: 1px solid #4a4a6a;
                border-radius: 6px;
                background: #1a1a2e;
                color: #eee;
                font-size: 12px;
                outline: none;
            }
            #${PANEL_ID} .h3-status {
                padding: 6px 10px;
                font-size: 11px;
                color: #888;
                background: #1a1a2e;
                border-top: 1px solid #3a3a5a;
                min-height: 16px;
            }
            #${PANEL_ID} .h3-status.error { color: #e74c3c; }
            #${PANEL_ID} .h3-status.success { color: #2ecc71; }
        `;
        document.head.appendChild(style);
    }

    // -----------------------------------------------------------------------
    // 面板创建
    // -----------------------------------------------------------------------
    function createPanel() {
        if (panelEl) return panelEl;

        panelEl = document.createElement("div");
        panelEl.id = PANEL_ID;
        panelEl.style.display = "none";
        panelEl.innerHTML = `
            <div class="h3-panel-header">
                <span class="h3-panel-title">📂 H3 资产管理库</span>
                <button class="h3-panel-close" title="关闭">×</button>
            </div>
            <div class="h3-panel-tabs">
                <button class="h3-tab active" data-tab="characters">👤 角色<span class="h3-count">0</span></button>
                <button class="h3-tab" data-tab="scenes">🏠 场景<span class="h3-count">0</span></button>
                <button class="h3-tab" data-tab="props">📦 道具<span class="h3-count">0</span></button>
            </div>
            <div class="h3-panel-body">
                <div class="h3-list-pane">
                    <div class="h3-list-toolbar">
                        <button class="h3-btn primary" id="h3-add-btn">➕ 添加</button>
                        <button class="h3-btn" id="h3-batch-desc-btn" title="用 LLM 为当前 Tab 下所有有图但无描述的资产自动生成英文描述">⚡ 批量打标</button>
                    </div>
                    <div class="h3-list" id="h3-list"></div>
                </div>
                <div class="h3-edit-pane" id="h3-edit-pane">
                    <div class="h3-edit-empty">选择左侧资产进行编辑，或点击「➕ 添加」创建新资产</div>
                </div>
            </div>
            <div class="h3-folder-bar">
                <input type="text" id="h3-folder-input" placeholder="文件夹路径，如 C:/assets/  （支持 characters/scenes/props 子目录自动归类）" />
                <button class="h3-btn small" id="h3-scan-btn">🔍 扫描</button>
                <button class="h3-btn small primary" id="h3-import-btn">📥 导入</button>
            </div>
            <div class="h3-status" id="h3-status">就绪</div>
        `;

        document.body.appendChild(panelEl);

        // 事件绑定
        panelEl.querySelector(".h3-panel-close").onclick = hidePanel;
        panelEl.querySelectorAll(".h3-tab").forEach(tab => {
            tab.onclick = () => switchTab(tab.dataset.tab);
        });
        panelEl.querySelector("#h3-add-btn").onclick = addNewItem;
        panelEl.querySelector("#h3-batch-desc-btn").onclick = onBatchDescribe;
        panelEl.querySelector("#h3-scan-btn").onclick = onScanFolder;
        panelEl.querySelector("#h3-import-btn").onclick = onImportFolder;

        // 拖拽
        const header = panelEl.querySelector(".h3-panel-header");
        header.addEventListener("mousedown", startDrag);

        return panelEl;
    }

    function showPanel() {
        injectStyles();
        createPanel();
        panelEl.style.display = "flex";
        // 居中显示
        const rect = panelEl.getBoundingClientRect();
        panelEl.style.left = `${Math.max(20, (window.innerWidth - rect.width) / 2)}px`;
        panelEl.style.top = `${Math.max(20, (window.innerHeight - rect.height) / 2)}px`;
        panelVisible = true;
        refreshAll();
    }

    function hidePanel() {
        if (panelEl) panelEl.style.display = "none";
        panelVisible = false;
    }

    function togglePanel() {
        if (panelVisible) hidePanel();
        else showPanel();
    }

    // -----------------------------------------------------------------------
    // 拖拽
    // -----------------------------------------------------------------------
    function startDrag(e) {
        if (e.target.classList.contains("h3-panel-close")) return;
        const rect = panelEl.getBoundingClientRect();
        dragState = {
            offsetX: e.clientX - rect.left,
            offsetY: e.clientY - rect.top,
        };
        document.addEventListener("mousemove", onDrag);
        document.addEventListener("mouseup", stopDrag);
    }

    function onDrag(e) {
        if (!dragState) return;
        panelEl.style.left = `${e.clientX - dragState.offsetX}px`;
        panelEl.style.top = `${e.clientY - dragState.offsetY}px`;
    }

    function stopDrag() {
        dragState = null;
        document.removeEventListener("mousemove", onDrag);
        document.removeEventListener("mouseup", stopDrag);
    }

    // -----------------------------------------------------------------------
    // 数据与渲染
    // -----------------------------------------------------------------------
    let libraryData = { characters: [], scenes: [], props: [] };

    async function refreshAll() {
        try {
            libraryData = await loadLibrary();
            updateTabCounts();
            renderList();
            renderEditPane();
            setStatus("就绪", "");
        } catch (e) {
            setStatus(`加载失败: ${e.message}`, "error");
        }
    }

    function updateTabCounts() {
        const tabs = panelEl.querySelectorAll(".h3-tab");
        tabs.forEach(tab => {
            const cat = tab.dataset.tab;
            const count = libraryData[cat]?.length || 0;
            tab.querySelector(".h3-count").textContent = count;
        });
    }

    function switchTab(tab) {
        currentTab = tab;
        selectedId = "";
        panelEl.querySelectorAll(".h3-tab").forEach(t => {
            t.classList.toggle("active", t.dataset.tab === tab);
        });
        renderList();
        renderEditPane();
    }

    function renderList() {
        const listEl = panelEl.querySelector("#h3-list");
        const items = libraryData[currentTab] || [];

        if (items.length === 0) {
            listEl.innerHTML = `<div style="padding:20px;text-align:center;color:#666;font-size:12px;">暂无资产，点击「➕ 添加」或从文件夹导入</div>`;
            return;
        }

        listEl.innerHTML = items.map(item => {
            const thumb = item.image
                ? `<img class="h3-item-thumb" src="${imageUrl(item.image)}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'" /><div class="h3-item-thumb placeholder" style="display:none;">🖼️</div>`
                : `<div class="h3-item-thumb placeholder">🖼️</div>`;
            return `
                <div class="h3-list-item ${item.id === selectedId ? "selected" : ""}" data-id="${item.id}">
                    ${thumb}
                    <div class="h3-item-info">
                        <div class="h3-item-name">${escapeHtml(item.name || "(未命名)")}</div>
                        <div class="h3-item-id">${item.id}</div>
                    </div>
                </div>
            `;
        }).join("");

        listEl.querySelectorAll(".h3-list-item").forEach(el => {
            el.onclick = () => {
                selectedId = el.dataset.id;
                renderList();
                renderEditPane();
            };
        });
    }

    function renderEditPane() {
        const editEl = panelEl.querySelector("#h3-edit-pane");
        const items = libraryData[currentTab] || [];
        const item = items.find(i => i.id === selectedId);

        if (!item) {
            editEl.innerHTML = `<div class="h3-edit-empty">选择左侧资产进行编辑，或点击「➕ 添加」创建新资产</div>`;
            return;
        }

        const categoryLabel = { characters: "角色", scenes: "场景", props: "道具" }[currentTab];

        editEl.innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
                <span style="font-size:18px;">${categoryLabel === "角色" ? "👤" : categoryLabel === "场景" ? "🏠" : "📦"}</span>
                <span style="font-size:14px;font-weight:700;">${escapeHtml(item.name || "(未命名)")}</span>
                <span style="font-size:11px;color:#888;">${item.id}</span>
            </div>
            <div class="h3-field">
                <label>名称</label>
                <input type="text" id="h3-edit-name" value="${escapeAttr(item.name || "")}" placeholder="如：女主、客厅、手机" />
            </div>
            <div class="h3-field">
                <label style="display:flex;align-items:center;gap:8px;">描述（建议英文，LLM 生成提示词时使用）
                    <button class="h3-btn small" id="h3-desc-btn" title="用 LLM 看图自动生成英文描述，不满意可手动修改">✨ 自动描述</button>
                </label>
                <textarea id="h3-edit-desc" placeholder="如：20-year-old East Asian woman, long black hair, white dress">${escapeHtml(item.description || "")}</textarea>
            </div>
            <div class="h3-field">
                <label>图片路径（完整路径或文件名）</label>
                <input type="text" id="h3-edit-image" value="${escapeAttr(item.image || "")}" placeholder="C:/assets/char1.png 或 char1.png" />
            </div>
            ${item.image ? `<div style="display:flex;justify-content:center;"><img src="${imageUrl(item.image)}" style="max-width:200px;max-height:150px;border-radius:6px;border:1px solid #4a4a6a;" onerror="this.style.display='none'" /></div>` : ""}
            <div class="h3-edit-actions">
                <button class="h3-btn primary" id="h3-save-btn">💾 保存</button>
                <button class="h3-btn danger" id="h3-delete-btn">🗑️ 删除</button>
                <button class="h3-btn" id="h3-cancel-btn" style="margin-left:auto;">取消</button>
            </div>
        `;

        editEl.querySelector("#h3-save-btn").onclick = saveCurrentItem;
        editEl.querySelector("#h3-delete-btn").onclick = deleteCurrentItem;
        editEl.querySelector("#h3-desc-btn").onclick = describeCurrentItem;
        editEl.querySelector("#h3-cancel-btn").onclick = () => { selectedId = ""; renderList(); renderEditPane(); };
    }

    async function describeCurrentItem() {
        const items = libraryData[currentTab] || [];
        const item = items.find(i => i.id === selectedId);
        if (!item) { return; }
        if (!item.image) { setStatus("该资产没有图片，无法自动打标", "error"); return; }
        try {
            setStatus(`正在用 LLM 识别 ${item.name || item.id} 的图片...`, "");
            const desc = await describeImage(item.image);
            // 填入描述框
            const descEl = panelEl.querySelector("#h3-edit-desc");
            if (descEl) descEl.value = desc;
            setStatus(`已生成描述，点「💾 保存」确认（不满意可手动改）`, "success");
        } catch (e) {
            setStatus(`自动打标失败: ${e.message}`, "error");
        }
    }

    async function onBatchDescribe() {
        const items = libraryData[currentTab] || [];
        // 只打标有图且描述为空的资产
        const targets = items.filter(i => i.image && !(i.description || "").trim());
        if (targets.length === 0) {
            setStatus("当前 Tab 下没有「有图且无描述」的资产", "error");
            return;
        }
        if (!confirm(`将为 ${targets.length} 个资产批量自动打标（用 LLM 看图生成英文描述）。确定继续？`)) return;

        const payload = targets.map(i => ({
            category: currentTab,
            id: i.id,
            image: i.image,
        }));

        setStatus(`正在批量打标 ${payload.length} 个资产（每个约 3-8 秒）...`, "");
        // 禁用批量按钮，防止重复点击
        const batchBtn = panelEl.querySelector("#h3-batch-desc-btn");
        if (batchBtn) batchBtn.disabled = true;
        try {
            const results = await describeAll(payload);
            // 先刷新数据（会触发 renderList/renderEditPane），再单独设置状态，避免被 refreshAll 的"就绪"覆盖
            libraryData = await loadLibrary();
            updateTabCounts();
            renderList();
            renderEditPane();
            const okCount = results.filter(r => r.ok).length;
            const failMsgs = results.filter(r => !r.ok).map(r => `${r.id}:${r.error}`).slice(0, 3);
            const statusMsg = okCount === results.length
                ? `批量打标完成: ${okCount}/${results.length} 成功`
                : `批量打标: ${okCount}/${results.length} 成功${failMsgs.length ? "，失败: " + failMsgs.join("；") : ""}`;
            setStatus(statusMsg, okCount === results.length ? "success" : "error");
        } catch (e) {
            setStatus(`批量打标失败: ${e.message}`, "error");
        } finally {
            if (batchBtn) batchBtn.disabled = false;
        }
    }

    async function addNewItem() {
        try {
            const prefix = { characters: "S", scenes: "E", props: "P" }[currentTab];
            const existing = libraryData[currentTab].map(i => i.id);
            let n = 1;
            while (existing.includes(`${prefix}${n}`)) n++;
            const newId = `${prefix}${n}`;

            const item = await createItem(currentTab, {
                id: newId,
                name: `新${currentTab === "characters" ? "角色" : currentTab === "scenes" ? "场景" : "道具"}`,
                description: "",
                image: "",
            });
            selectedId = item.id;
            setStatus(`已创建 ${item.id}`, "success");
            await refreshAll();
        } catch (e) {
            setStatus(`创建失败: ${e.message}`, "error");
        }
    }

    async function saveCurrentItem() {
        const name = panelEl.querySelector("#h3-edit-name").value.trim();
        const description = panelEl.querySelector("#h3-edit-desc").value.trim();
        const image = panelEl.querySelector("#h3-edit-image").value.trim();

        try {
            await updateItem(currentTab, selectedId, { name, description, image });
            setStatus(`已保存 ${selectedId}`, "success");
            await refreshAll();
        } catch (e) {
            setStatus(`保存失败: ${e.message}`, "error");
        }
    }

    async function deleteCurrentItem() {
        if (!confirm(`确定删除 ${selectedId} 吗？`)) return;
        try {
            await deleteItem(currentTab, selectedId);
            setStatus(`已删除 ${selectedId}`, "success");
            selectedId = "";
            await refreshAll();
        } catch (e) {
            setStatus(`删除失败: ${e.message}`, "error");
        }
    }

    // -----------------------------------------------------------------------
    // 文件夹扫描 / 导入
    // -----------------------------------------------------------------------
    async function onScanFolder() {
        const folder = panelEl.querySelector("#h3-folder-input").value.trim();
        if (!folder) { setStatus("请输入文件夹路径", "error"); return; }
        try {
            setStatus("扫描中...", "");
            const result = await scanFolder(folder);
            const all = result.all || [];
            const chars = result.characters || [];
            const scenes = result.scenes || [];
            const props = result.props || [];
            setStatus(`扫描到 ${all.length} 张图片（角色${chars.length}/场景${scenes.length}/道具${props.length}），点击「📥 导入」添加到库`, "success");
        } catch (e) {
            setStatus(`扫描失败: ${e.message}`, "error");
        }
    }

    async function onImportFolder() {
        const folder = panelEl.querySelector("#h3-folder-input").value.trim();
        if (!folder) { setStatus("请输入文件夹路径", "error"); return; }
        try {
            setStatus("导入中...", "");
            const result = await importFolder(folder);
            const total = result.total || 0;
            setStatus(`已导入 ${total} 个资产`, "success");
            await refreshAll();
        } catch (e) {
            setStatus(`导入失败: ${e.message}`, "error");
        }
    }

    // -----------------------------------------------------------------------
    // 工具函数
    // -----------------------------------------------------------------------
    function setStatus(msg, type = "") {
        const el = panelEl?.querySelector("#h3-status");
        if (!el) return;
        el.textContent = msg;
        el.className = "h3-status" + (type ? " " + type : "");
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return escapeHtml(str).replace(/"/g, "&quot;");
    }

    // 把资产图片路径转成可访问的 API URL（浏览器不能直接访问本地绝对路径）
    function imageUrl(path) {
        if (!path) return "";
        if (/^(data:|https?:|file:)/i.test(path)) return path;
        return api?.apiURL
            ? api.apiURL(`/h3/asset_library/file?path=${encodeURIComponent(path)}`)
            : `/h3/asset_library/file?path=${encodeURIComponent(path)}`;
    }

    // -----------------------------------------------------------------------
    // 节点按钮添加
    // -----------------------------------------------------------------------
    function isH3AssetLibraryNode(node) {
        if (!node || !node.widgets) return false;
        // v3.0 节点没有任何 widget，通过 type 检测
        return node.type === "H3AssetLibrary" || node.comfyClass === "H3AssetLibrary";
    }

    function addPanelButton(node) {
        if (!isH3AssetLibraryNode(node)) return;
        if (node._h3_panel_button_added) return;
        node._h3_panel_button_added = true;

        // 添加一个按钮 widget
        try {
            const btn = node.addWidget("button", "📂 打开资产管理面板", null, () => {
                togglePanel();
            });
            if (btn) {
                btn.computeSize = () => [0, 32];
            }
        } catch (e) {
            // fallback：通过 onDrawForeground 添加按钮区域
            console.warn("[H3 AssetLibrary] addWidget 失败，使用 fallback:", e);
        }
    }

    // -----------------------------------------------------------------------
    // 注册扩展
    // -----------------------------------------------------------------------
    app.registerExtension({
        name: EXTENSION_NAME,
        nodeCreated: (node) => {
            // 延迟添加按钮，确保节点完全初始化
            setTimeout(() => addPanelButton(node), 100);
        },
        nodeConfigured: (node) => {
            setTimeout(() => addPanelButton(node), 100);
        },
    });

    // 兜底：轮询检测节点
    setInterval(() => {
        try {
            const graph = app.graph;
            if (!graph || !graph._nodes) return;
            for (const node of graph._nodes) {
                addPanelButton(node);
            }
        } catch (e) {}
    }, 1000);

})();