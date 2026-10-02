/* ==============================================================================
   h3_segments_grid.js (by micxin2025)
   H3 Segments Unpack (micxin) 内嵌 3×3 九宫格分镜编辑/预览：
     - 9 格可直接点击输入/粘贴提示词（内容序列化为 JSON 写入隐藏的 manual_shots）
     - segments_json 接线时，执行后自动用上游内容填格
============================================================================== */
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_CLASS = "H3SegmentsUnpack";

function isOurNode(node) {
    return node && (node.comfyClass === NODE_CLASS || node.type === NODE_CLASS);
}
function esc(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

app.registerExtension({
    name: "micxin.H3SegmentsGrid",
    async nodeCreated(node) {
        if (!isOurNode(node)) return;
        if (typeof document === "undefined" || typeof node.addDOMWidget !== "function") return;

        // 隐藏 manual_shots 文本框（照套件图片网格的成熟写法）
        const ms = node.widgets?.find(x => x.name === "manual_shots");
        if (ms) {
            Object.defineProperty(ms, "hidden", { get: () => true, set: () => {} });
            Object.defineProperty(ms, "type", { get: () => "hidden", set: () => {} });
            ms.computeSize = function () { return [0, 0]; };
            const iv = setInterval(() => {
                if (ms.element) ms.element.style.display = "none";
            }, 50);
            setTimeout(() => clearInterval(iv), 1000);
        }

        const wrap = document.createElement("div");
        wrap.style.cssText =
            "display:grid;grid-template-columns:1fr 1fr 1fr;gap:3px;" +
            "padding:3px;background:#1b1b1b;border:1px solid #444;border-radius:4px;";

        // 初始化：从 manual_shots 读已有内容
        let initShots = [];
        if (ms && ms.value) {
            try { initShots = JSON.parse(ms.value); } catch (e) { initShots = []; }
        }

        const cells = [];
        for (let i = 0; i < 9; i++) {
            const c = document.createElement("div");
            c.contentEditable = "true";
            c.style.cssText =
                "background:#262626;border:1px solid #555;border-radius:3px;" +
                "padding:3px;color:#bbb;font-size:9px;line-height:1.3;" +
                "height:96px;overflow-y:auto;overflow-x:hidden;word-break:break-word;" +
                "scrollbar-width:thin;outline:none;";
            const init = initShots[i] || "";
            c.innerHTML = init ? `<b style="color:#7fd4ff">镜${i + 1}</b><br>${esc(init)}` : `镜${i + 1}<br>(点击输入)`;
            c.addEventListener("input", () => {
                const raw = c.innerText.replace(/^镜\d+\n?/, "");
                const arr = cells.map(x => x.innerText.replace(/^镜\d+\n?/, ""));
                if (ms) ms.value = JSON.stringify(arr);
            });
            wrap.appendChild(c);
            cells.push(c);
        }
        node._h3GridCells = cells;

        const w = node.addDOMWidget("grid_view", "DIV", wrap, { serialize: false });
        if (w && typeof w.computeSize === "function") {
            w.computeSize = function () { return [300, 300]; };
        }
        node.setSize([node.size[0] || 320, 360]);
    }
});

api.addEventListener("executed", ({ detail }) => {
    try {
        const nid = (detail?.node && typeof detail.node === "object") ? detail.node.id : detail?.node;
        const node = app.graph.getNodeById(nid);
        if (!isOurNode(node)) return;
        if (!node._h3GridCells) return;

        const out = detail?.output;
        let prompts = [];
        if (Array.isArray(out)) {
            prompts = out.filter(o => typeof o === "string").slice(0, 9);
        } else if (out && Array.isArray(out.text)) {
            prompts = out.text.map(String).slice(1, 10);
        }
        const ms = node.widgets?.find(x => x.name === "manual_shots");
        prompts.forEach((p, i) => {
            const c = node._h3GridCells[i];
            if (!c) return;
            c.title = p;
            c.innerHTML = `<b style="color:#7fd4ff">镜${i + 1}</b><br>${esc(p)}`;
        });
        // 同步回 manual_shots（保存工作流不丢）
        if (ms) ms.value = JSON.stringify(prompts);
    } catch (e) {
        console.error("[H3-GRID]", e);
    }
});
