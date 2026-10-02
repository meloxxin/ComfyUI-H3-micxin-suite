/* ==============================================================================
   h3_clip_chain_av.js (by micxin2025)
   H3 Clip Chain (micxin) 旧工作流槽位迁移 —— 修复 schema 演化导致的 widgets_values 错位。

   背景：2026-10-02 在 segment_prompts (Autogrow) 之后、seeds 之前插入了
   segments_json 输入槽。V3 前端所有输入槽（含 force_input）都会占一个
   widgets_values 位置，旧工作流保存的数组没有这一位 → 重新打开后从 seeds
   起全部 widget 值错位（steps=NaN / sampler_name=simple / scheduler=1 等），
   节点校验报错、输出被忽略。

   修复：磁盘保存的 widgets_values_named 是按 widget 名存的名值对，不受顺序
   影响。加载时用 named 按名写回每个 widget 值（只认 schema 字段名，跳过
   Autogrow 动态槽与 JS 注入字段），随后重建 node.widgets_values，让下次
   保存落到正确顺序。
============================================================================== */
import { app } from "../../scripts/app.js";

const NODE_CLASS = "H3ClipChainAV";

function isOurNode(node) {
    return node && (node.comfyClass === NODE_CLASS || node.type === NODE_CLASS);
}

// Autogrow 动态槽名（prompt_0..N）与 JS 注入的非 schema 字段，不参与按名迁移
function isDynamicSlotName(n) {
    return /^prompt_\d+$/.test(n) || n === "分段关键帧" || n === "grid_view";
}

function realignByNamed(node, named) {
    if (!named || typeof named !== "object" || !node.widgets) return false;
    let changed = 0;
    for (const w of node.widgets) {
        const n = w && w.name;
        if (!n || isDynamicSlotName(n)) continue;
        if (!(n in named)) continue;
        const v = named[n];
        if (JSON.stringify(w.value) !== JSON.stringify(v)) {
            try {
                w.value = v;
                if (w._state) w._state.value = v;
                changed++;
            } catch (e) { console.error("[H3ClipChainAV]", n, e); }
        }
    }
    return changed > 0;
}

// 按当前 node.widgets 顺序重建 widgets_values（下次保存不丢、顺序正确）
function rebuildWidgetsValues(node) {
    if (!node.widgets) return;
    try {
        const vals = [];
        for (const w of node.widgets) {
            if (!w || isDynamicSlotName(w.name)) continue;
            vals.push(w.value);
        }
        node.widgets_values = vals;
    } catch (e) { console.error("[H3ClipChainAV] rebuild", e); }
}

app.registerExtension({
    name: "micxin.H3ClipChainAV.Realign",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (!nodeData || nodeData.name !== NODE_CLASS) return;
        const _orig = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const r = _orig ? _orig.apply(this, arguments) : undefined;
            try {
                if (info && info.widgets_values_named &&
                    Object.keys(info.widgets_values_named).length) {
                    const changed = realignByNamed(this, info.widgets_values_named);
                    if (changed) {
                        rebuildWidgetsValues(this);
                        if (this.setDirtyCanvas) this.setDirtyCanvas(true, false);
                        if (this.onPropertyChanged) this.onPropertyChanged();
                    }
                }
            } catch (e) { console.error("[H3ClipChainAV] migrate", e); }
            return r;
        };
    },
    async nodeCreated(node) {
        if (!isOurNode(node)) return;
        // 磁盘加载兜底：onConfigure 没拿到 named 时，尝试 node.widgets_values_named
        const tryMigrate = () => {
            try {
                const named = node.widgets_values_named;
                if (named && Object.keys(named).length) {
                    const changed = realignByNamed(node, named);
                    if (changed) {
                        rebuildWidgetsValues(node);
                        if (node.setDirtyCanvas) node.setDirtyCanvas(true, false);
                    }
                }
            } catch (e) { console.error("[H3ClipChainAV] migrate2", e); }
        };
        tryMigrate();
        setTimeout(tryMigrate, 50);
        setTimeout(tryMigrate, 250);
    },
});
