// h3_story_setup.js — H3StorySetup (micxin) 前端增强
// backend 切换时自动折叠/展开 Local GGUF / HTTP endpoint 对应 widget

(function () {
    "use strict";

    const LOCAL_WIDGETS = ["gguf_name", "mmproj_name", "n_gpu_layers", "keep_loaded"];
    const HTTP_WIDGETS = ["llm_base_url", "model", "api_key"];
    const _processedNodes = new WeakSet();
    const _lastBackend = new WeakMap();

    const ORDERED_WIDGETS = [
        "concept", "style", "drama_genre", "num_shots", "ref2va_mode",
        "backend", "gguf_name", "mmproj_name", "n_gpu_layers",
        "llm_base_url", "model", "api_key", "temperature", "seed", "keep_loaded",
        "dialogue_mode",
        "asset_library",
    ];

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

    function isH3StorySetup(node) {
        if (!node || !node.widgets) return false;
        const names = new Set(node.widgets.map(w => w && w.name));
        return names.has("backend") && names.has("gguf_name") && names.has("llm_base_url");
    }

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

    const LABEL_PATCH = {
        concept: "💡 创意 (concept)",
        mode: "🎬 运行模式 (mode)",
        style: "🎨 视觉风格 (style)",
        drama_genre: "📺 短剧类型 (genre)",
        num_shots: "🎞 镜头数 (num_shots)",
        duration_per_shot: "⏱ 每镜秒数 (duration/shot)",
        aspect_ratio: "📐 画幅 (aspect_ratio)",
        resolution_mp: "🖥 分辨率 (MP)",
        ref2va_mode: "🖼 参考图模式 (Ref2VA/纯文本)",
        backend: "⚙ LLM 后端 (backend)",
        gguf_name: "📦 GGUF 模型 (Local GGUF)",
        mmproj_name: "🖼 多模态投影 (mmproj, None=纯文本)",
        n_gpu_layers: "🧊 GPU 层数 (-1=全部, 0=CPU)",
        llm_base_url: "🌐 LLM 接口 URL (HTTP endpoint)",
        model: "🤖 模型名 (HTTP endpoint)",
        api_key: "🔑 API Key (HTTP endpoint, 本地留空)",
        temperature: "🌡 温度 (0.7 适中, 0.3 最稳)",
        seed: "🎲 种子 (0=随机)",
        keep_loaded: "🔁 跑完保留模型",
    };

    function applyLabels(node) {
        for (const [key, lbl] of Object.entries(LABEL_PATCH)) {
            const w = getWidget(node, key);
            if (!w) continue;
            try { w.label = lbl; } catch (e) {}
        }
    }

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

    function initNode(node, diskValues) {
        if (!isH3StorySetup(node)) return;
        if (_processedNodes.has(node)) return;
        _processedNodes.add(node);
        try { reorderWidgets(node); } catch (e) {}
        try { realignWidgetValues(node, diskValues); } catch (e) {}
        try { rebuildWidgetValues(node); } catch (e) {}
        applyLabels(node);
        applyBackendVisibility(node);
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
        const bw = getWidget(node, "backend");
        if (bw) _lastBackend.set(node, bw.value);
        requestAnimationFrame(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); });
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 50);
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 250);
        setTimeout(() => { try { reorderWidgets(node); realignWidgetValues(node, diskValues); rebuildWidgetValues(node); } catch (e) {} applyBackendVisibility(node); }, 800);
    }

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

    app.registerExtension({
        name: "ComfyUI-H3-AutoDirector.H3StorySetup",
        async beforeRegisterNodeDef(nodeType, comfyClass) {
            const _origOnConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (info) {
                const r = _origOnConfigure ? _origOnConfigure.apply(this, arguments) : undefined;
                try { initNode(this, info && info.widgets_values); } catch (e) {}
                return r;
            };
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
