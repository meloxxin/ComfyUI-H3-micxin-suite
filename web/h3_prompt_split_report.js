/* ==============================================================================
   h3_prompt_split_report.js (by micxin2025)
   H3 Prompt Split+Translate (micxin) report 内嵌显示：
     - 执行完成后（api "executed" 事件）把 report 摘要写入 report_inline
       multiline 文本框（节点内直接可见，无需外接 report 输出）。
     - 兼容 V3 executed 事件：detail.node 可能是 {id} 对象；output 可能是数组 / {text}。
     - 与 Python 侧 io.NodeOutput(..., ui={"text": (report,)}) 互为双保险。
============================================================================== */
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

api.addEventListener("executed", ({ detail }) => {
    try {
        const nid = (detail?.node && typeof detail?.node === "object") ? detail.node.id : detail?.node;
        const node = app.graph.getNodeById(nid);
        if (!node || node.type !== "H3PromptSplitTranslate") return;

        let text = "";
        const out = detail?.output;
        if (Array.isArray(out)) {
            const strs = out.filter(o => typeof o === "string");
            text = strs[strs.length - 1] ?? "";
            if (!text) {
                for (const o of out) {
                    if (o && typeof o === "object") {
                        if (typeof o.report === "string") { text = o.report; break; }
                        if (Array.isArray(o.text) && o.text.length) { text = String(o.text[o.text.length - 1]); break; }
                    }
                }
            }
        } else if (typeof out === "string") {
            text = out;
        } else if (out && typeof out === "object") {
            if (typeof out.report === "string") text = out.report;
            else if (Array.isArray(out.text) && out.text.length) text = String(out.text[out.text.length - 1]);
            else if (typeof out.text === "string") text = out.text;
        }
        if (typeof text !== "string") text = String(text ?? "");
        if (!text) return;

        const slot = node.widgets?.find(w => w.name === "report_inline");
        if (slot) {
            slot.value = text;
            if (typeof slot.callback === "function") slot.callback(text);
            app.graph.setDirtyCanvas(true, true);
        }
    } catch (e) {
        console.error("[H3-PS-REPORT]", e);
    }
});
