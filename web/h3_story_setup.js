// h3_story_setup.js — H3StorySetup (micxin) 前端增强
// backend 切换时自动折叠/展开 Local GGUF / HTTP endpoint 对应 widget
//
// 健壮性设计：
//   - 不依赖 node.comfyClass，用 widget 特征检测（同时有 backend + gguf_name）
//   - 用 setInterval 轮询 backend 值变化，不依赖 combo.callback（新API可能不同）
//   - 多重初始化时机（nodeCreated + onConfigure + 延迟轮询）
//   - foldWidget 用 computeSize=[0,0]+空draw，不动 w.hidden，不影响 widgets_values 索引

(function () {
    "use strict";

    // Local GGUF 专属 widget（选 Local GGUF 时显示，HTTP 时折叠）
    const LOCAL_WIDGETS = ["gguf_name", "mmproj_name", "n_gpu_layers", "keep_loaded"];
    // HTTP endpoint 专属 widget（选 HTTP 时显示，Local 时折叠）
    const HTTP_WIDGETS = ["llm_base_url", "model", "api_key"];
    // 已处理的节点集合，避免重复初始化
    const _processedNodes = new WeakSet();
    // 节点 -> 上一次 backend 值 的映射
    const _lastBackend = new WeakMap();

    /* ---- widget-name 按序表（与普通节点 INPUT_TYPES 的 required+optional 顺序一致）----
       普通节点 widget 顺序由 Python 端 INPUT_TYPES 定义，这里仅用于 JS 端兜底重排。
       16个required在前，1个optional(asset_library)在后。 */
    const ORDERED_WIDGETS = [
        "concept", "style", "drama_genre", "num_shots", "ref2va_mode",
        "backend", "gguf_name", "mmproj_name", "n_gpu_layers",
        "llm_base_url", "model", "api_key", "temperature", "seed", "keep_loaded",
        "dialogue_mode",
        "asset_library",
    ];

    /* ---- 从磁盘 widgets_values（按 ORDERED_WIDGETS 顺序）按 name 回填 widget.value ---- */
    function realignWidgetValues(node, diskValues) {
        if (!node || !node.widgets || !node.widgets.length) return 0;
        if (!Array.isArray(diskValues) || !diskValues.length) return 0;
        const byName = {};
        ORDERED_WIDGETS.forEach((n, i) => {
            if (i < diskValues.length) byName[n] = diskValues[i];
        });
        let changed = 0;
        node.widgets.forEach((w) => {
            if (!w) return;
            const n = w.name || (w.options && w.options.name);
            if (!n || !(n in byName)) return;
            if (JSON.stringify(w.value) !== JSON.stringify(byName[n])) {
                try {
                    w.value = byName[n];
                    if (w._state) w._state.value = byName[n];
                    changed++;
                } catch (e) {}
            }
        });
        return changed;
    }

    /* ---- 按 ORDERED_WIDGETS 顺序重建 node.widgets_values（保证下次保存磁盘顺序正确）---- */
    function rebuildWidgetValues(node) {
        if (!node || !node.widgets) return false;
        const byName = {};
        node.widgets.forEach((w) => {
            if (!w) return;
            const n = w.name || (w.options && w.options.name);
            if (n && !(n in byName)) byName[n] = w.value;
        });
        const newValues = ORDERED_WIDGETS.map((n) => (n in byName ? byName[n] : null));
        try { node.widgets_values = newValues; return true; } catch (e) { return false; }
    }

    /* ---- widget helpers ---- */
    function getWidget(node, name) {
        return node.widgets && node.widgets.find((w) => w && w.name === name);
    }

    function foldWidget(w) {
        if (!w) return;
        try { w.computeSize = () => [0, 0]; } catch (e) {}
        try { w.draw = () => {}; } catch (e) {}
    }

    function unfoldWidget(w) {
        if (!w) return;
        try { delete w.computeSize; } catch (e) { w.computeSize = undefined; }
        try { delete w.draw; } catch (e) { w.draw = undefined; }
    }

    function recomputeNodeHeight(node) {
        if (!node || !node.computeSize) return;
        try {
            const c = node.computeSize();
            if (c && c[1]) {
                const h = Math.max(200, Math.round(c[1]) + 24);
                node.size[1] = h;
            }
        } catch (e) {}
    }

    /* ---- 检测是否是 H3StorySetup 节点 ----
       新API节点的 comfyClass 可能不同，用 widget 特征检测：
       同时存在 backend + gguf_name + llm_base_url 三个 widget 的就是本节点。 */
    function isH3StorySetup(node) {
        if (!node || !node.widgets) return false;
        const names = new Set(node.widgets.map(w => w && w.name));
        return names.has("backend") && names.has("gguf_name") && names.has("llm_base_url");
    }

    /* ---- backend-aware visibility ---- */
    function applyBackendVisibility(node) {
        if (!isH3StorySetup(node)) return;
        const backend = getWidget(node, "backend");
        if (!backend) return;
        const isLocal = (backend.value === "Local GGUF");

        for (const nm of LOCAL_WIDGETS) {
            const w = getWidget(node, nm);
            if (!w) continue;
            if (isLocal) unfoldWidget(w);
            else foldWidget(w);
        }
        for (const nm of HTTP_WIDGETS) {
            const w = getWidget(node, nm);
            if (!w) continue;
            if (isLocal) foldWidget(w);
            else unfoldWidget(w);
        }

        recomputeNodeHeight(node);
        try { if (node.setDirtyCanvas) node.setDirtyCanvas(true, false); } catch (e) {}
    }

    /* ---- 中文 label 美化 ---- */
    const LABEL_PATCH = {
        concept:            "💡 创意 (concept)",
        mode:               "🎬 运行模式 (mode)",
        style:              "🎨 视觉风格 (style)",
        drama_genre:        "📺 短剧类型 (genre)",
        num_shots:          "🎞 镜头数 (num_shots)",
        duration_per_shot:  "⏱ 每镜秒数 (duration/shot)",
        aspect_ratio:       "📐 画幅 (aspect_ratio)",
        resolution_mp:      "🖥 分辨率 (MP)",
        ref2va_mode:        "🖼 参考图模式 (Ref2VA/纯文本)",
        backend:            "⚙ LLM 后端 (backend)",
        gguf_name:          "📦 GGUF 模型 (Local GGUF)",
        mmproj_name:        "🖼 多模态投影 (mmproj, None=纯文本)",
        n_gpu_layers:       "🧊 GPU 层数 (-1=全部, 0=CPU)",
        llm_base_url:       "🌐 LLM 接口 URL (HTTP endpoint)",
        model:              "🤖 模型名 (HTTP endpoint)",
        api_key:            "🔑 API Key (HTTP endpoint, 本地留空)",
        temperature:        "🌡 温度 (0.7 适中, 0.3 最稳)",
        seed:               "🎲 种子 (0=随机)",
        keep_loaded:        "🔁 跑完保留模型",
    };

    function applyLabels(node) {
        for (const [key, lbl] of Object.entries(LABEL_PATCH)) {
            const w = getWidget(node, key);
            if (!w) continue;
            try { w.label = lbl; } catch (e) {}
        }
    }

    /* ---- 按 ORDERED_WIDGETS 重排 node.widgets 数组（核心修复：确保 widget 顺序与保存顺序一致）---- */
    function reorderWidgets(node) {
        if (!node || !node.widgets || !node.widgets.length) return false;
        const byName = {};
        const extras = [];
        node.widgets.forEach((w) => {
            if (!w) { extras.push(w); return; }
            const n = w.name || (w.options && w.options.name);
            if (n && ORDERED_WIDGETS.includes(n)) {
                if (!(n in byName)) byName[n] = w;
            } else {
                extras.push(w);
            }
        });
        const reordered = ORDERED_WIDGETS.map((n) => byName[n]).filter(Boolean);
        if (reordered.length !== node.widgets.length - extras.length) return false;
        node.widgets = reordered.concat(extras);
        return true;
    }

    /* ---- 初始化单个节点 ---- */
    function initNode(node, diskValues) {
        if (!isH3StorySetup(node)) return;
        if (_processedNodes.has(node)) return;
        _processedNodes.add(node);

        // ★ 核心修复1：按 ORDERED_WIDGETS 重排 widget 数组，从根源杜绝顺序错位
        try { reorderWidgets(node); } catch (e) {}

        // 按 name 从磁盘 widgets_values 回填，修复第三方扩展导致的 index 错位
        try { realignWidgetValues(node, diskValues); } catch (e) {}
        try { rebuildWidgetValues(node); } catch (e) {}

        applyLabels(node);
        applyBackendVisibility(node);

        // ★ 核心修复2：每个 widget 值变化后立即 rebuild widgets_values，防止用户改值后错位
        try {
            node.widgets.forEach((w) => {
                if (!w) return;
                const origCb = w.callback;
                w.callback = function () {
                    try { rebuildWidgetValues(node); } catch (e) {}
                    if (typeof origCb === "function") {
                        try { return origCb.apply(this, arguments); } catch (e) {}
                    }
                };
            });
        } catch (e) {}

        // 记录初始 backend 值
        const bw = getWidget(node, "backend");
        if (bw) _lastBackend.set(node, bw.value);

        // 多重延迟兜底（LiteGraph 异步构建 widget）
        requestAnimationFrame(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); });
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 50);
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 250);
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 800);
    }

    /* ---- 全局轮询：检测 backend 值变化 ----
       不依赖 combo.callback，每 300ms 扫描画布上所有 H3StorySetup 节点，
       发现 backend 值变化就触发折叠。这是最健壮的方式，兼容新旧 API。 */
    setInterval(() => {
        try {
            const graph = app && app.graph;
            if (!graph || !graph._nodes) return;
            for (const node of graph._nodes) {
                if (!isH3StorySetup(node)) continue;
                const bw = getWidget(node, "backend");
                if (!bw) continue;
                const last = _lastBackend.get(node);
                if (last !== bw.value) {
                    _lastBackend.set(node, bw.value);
                    applyBackendVisibility(node);
                }
            }
        } catch (e) {}
    }, 300);

    /* ---- extension registration ---- */
    app.registerExtension({
        name: "ComfyUI-H3-AutoDirector.H3StorySetup",
        async beforeRegisterNodeDef(nodeType, comfyClass) {
            // 不管 comfyClass 是什么，都 patch onConfigure
            const _origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                const r = _origOnConfigure ? _origOnConfigure.apply(this, arguments) : undefined;
                try { initNode(this, info && info.widgets_values); } catch (e) {}
                return r;
            };
            // ★ 核心修复3：patch serialize（标准序列化方法，保存时一定调用），强制重排+重建
            const _origSerialize = nodeType.prototype.serialize;
            nodeType.prototype.serialize = function () {
                try {
                    if (isH3StorySetup(this)) {
                        reorderWidgets(this);
                        rebuildWidgetValues(this);
                    }
                } catch (e) {}
                return _origSerialize ? _origSerialize.apply(this, arguments) : undefined;
            };
        },
        nodeCreated(node) {
            try { initNode(node, node.widgets_values); } catch (e) {}
        },
    });

})();