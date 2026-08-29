/**
 * widget_value_migrator.js (ComfyUI-H3-AutoDirector)
 *
 * 三层修复（按调用顺序）：
 *
 * (1) widget-name 重排（realignByDiskWidgets）：
 *     磁盘 widgets_values 是按 INPUT_TYPES ordered 存的（前后端 schema 一致
 *     时的约定）。但浏览器 widget 数组顺序可能因为 schema 升级 / LiteGraph
 *     反序列化顺序 / 第三方扩展（cg-use-everywhere / DD-Translation /
 *     crystools）插入 / unshift domWidget 等而与 INPUT_TYPES 顺序不一致——
 *     LiteGraph 默认按 widget[i].value = widgets_values[i] 复制，导致整组
 *     错位（用户报告：磁盘 [20]=8192 错位到 n_gpu_layers（max=200 截成
 *     200），n_ctx 显示 widgets_values[19]=-1，keep_loaded 显示
 *     widgets_values[21]=false 渲染成 0）。
 *
 *     修复：把磁盘原始 widgets_values 视为 INPUT_TYPES.ordered[i] → value
 *     映射，按 widget.name 写回 widget.value。这样无论 widget 数组顺序如
 *     何，每个 widget 都能拿到"name 对应的磁盘值"。
 *
 * (2) 单字段 schema 升级（migrateFieldDefaults）：
 *     已知的历史 default 升级（如 n_ctx 4096→8192）。
 *
 * (3) widgets_values 数组写回（rebuildWidgetsValues）：
 *     把 node.widgets_values 按 widget.name 顺序重排成 INPUT_TYPES
 *     ordered，下次保存磁盘能落盘到正确位置。
 */

const SCHEMA_MIGRATIONS = {
    // 历史上节点的 n_ctx 默认为 4096；2026-08-17 后改为 8192。旧实例残留
    // 4096 → 自动升 8192。
    "n_ctx": { from: [4096, 2048], to: 8192 },
    // 以后再升级 default 时，往这里追加 { from: [old_defaults], to: new_default }。
};

function widgetName(w) {
    if (!w) return null;
    return w.options?.name || w.name || null;
}

function getOrderedKeys(inputTypes) {
    if (!inputTypes) return [];
    return []
        .concat(inputTypes.required ? Object.keys(inputTypes.required) : [])
        .concat(inputTypes.optional ? Object.keys(inputTypes.optional) : []);
}

/** (1) 把磁盘 widgets_values 按 INPUT_TYPES ordered 视为 ordered→value
 *      映射，按 widget.name 写回每个 widget.value。
 *      必须在 LiteGraph 复制 widget.value 之后调用（widget 数组已就绪）。
 *      @returns { changed: bool }
 */
function realignByDiskWidgets(node, inputTypes, diskWidgetsValues) {
    if (!node || !node.widgets || !node.widgets.length) return { changed: false };
    if (!inputTypes) return { changed: false };
    const ordered = getOrderedKeys(inputTypes);
    if (!ordered.length) return { changed: false };
    const diskArr = Array.isArray(diskWidgetsValues) ? diskWidgetsValues
        : (Array.isArray(node.widgets_values) ? node.widgets_values : []);
    if (!diskArr.length) return { changed: false };

    // disk widgets_values[i] 视为 ordered[i] 对应的值
    const diskByName = {};
    ordered.forEach((n, i) => {
        if (i < diskArr.length) diskByName[n] = diskArr[i];
    });

    let changed = 0;
    node.widgets.forEach((w) => {
        const n = widgetName(w);
        if (!n) return;
        const v = diskByName[n];
        if (v === undefined) return;
        // 跳过 domWidget 这种"额外" widget（不在 INPUT_TYPES 里）
        if (ordered.indexOf(n) < 0) return;
        if (JSON.stringify(w.value) !== JSON.stringify(v)) {
            try {
                w.value = v;
                if (w._state) w._state.value = v;
                changed++;
            } catch (e) {}
        }
    });

    return { changed: changed > 0, count: changed };
}

/** (2) 单字段 schema 升级：widget.value 命中 SCHEMA_MIGRATIONS.from 时强制改写。
 *      @returns 改写的字段数 */
function migrateFieldDefaults(node, inputTypes) {
    if (!node || !node.widgets || !node.widgets.length) return 0;
    const ordered = getOrderedKeys(inputTypes);
    if (!ordered.length) return 0;

    let changed = 0;
    node.widgets.forEach((w) => {
        if (!w) return;
        const n = widgetName(w);
        if (!n) return;
        const rule = SCHEMA_MIGRATIONS[n];
        if (rule && rule.from && rule.from.includes(w.value)) {
            try {
                w.value = rule.to;
                if (w._state) w._state.value = rule.to;
                changed++;
            } catch (e) {}
            return;
        }
        // 兜底：widget.value 是 undefined / null 且 INPUT_TYPES 有 default
        if ((w.value === undefined || w.value === null || w.value === "")
            && w.options && "default" in w.options && w.type !== "STRING") {
            try {
                w.value = w.options.default;
                if (w._state) w._state.value = w.options.default;
                changed++;
            } catch (e) {}
        }
    });
    return changed;
}

/** (3) 按 widget.name + INPUT_TYPES ordered 重写 node.widgets_values。
 *      下次保存磁盘会写到正确位置（按 INPUT_TYPES ordered）。*/
function rebuildWidgetsValues(node, inputTypes) {
    if (!node || !node.widgets || !node.widgets.length) return false;
    const ordered = getOrderedKeys(inputTypes);
    if (!ordered.length) return false;

    const byName = {};
    node.widgets.forEach((w) => {
        const n = widgetName(w);
        if (!n) return;
        if (!(n in byName)) byName[n] = w.value;
    });

    const newValues = ordered.map((n) => (n in byName ? byName[n] : null));
    try {
        node.widgets_values = newValues;
    } catch (e) {}
    return true;
}

/** 把节点的 widget values 与 INPUT_TYPES 当前 default 对齐，按 widget.name
 *  把磁盘 widgets_values 回填到 widget.value。
 *
 *  @param {object} node - LiteGraph node instance
 *  @param {object} inputTypes - the class's INPUT_TYPES dict (required + optional)
 *  @param {Array} [diskWidgetsValues] - 磁盘原始 widgets_values（从 onConfigure
 *      的 info.widgets_values 捕获）。如果不传，则用 node.widgets_values。
 *      **强烈建议传** —— LiteGraph 默认按 index 复制过 widget.value，再用
 *      node.widgets_values 收集会拿到错位的值；用磁盘原始数据才稳。
 *  @returns {object} { realigned: bool, migrated: number, count: number }
 */
export function migrateWidgetValues(node, inputTypes, diskWidgetsValues) {
    if (!node || !node.widgets || !node.widgets.length) {
        return { realigned: false, migrated: 0, count: 0 };
    }
    const realignRes = realignByDiskWidgets(node, inputTypes, diskWidgetsValues);
    const migrated = migrateFieldDefaults(node, inputTypes);
    return {
        realigned: !!realignRes.changed,
        migrated,
        count: realignRes.count,
    };
}

export function ensureMigrationsApplied(node, inputTypes, diskWidgetsValues) {
    if (!node) return;
    if (node.__h3Migrated) return;
    node.__h3Migrated = true;
    const res = migrateWidgetValues(node, inputTypes, diskWidgetsValues);
    if (res.realigned || res.migrated > 0) {
        try {
            if (node.setDirtyCanvas) node.setDirtyCanvas(true, false);
            if (node.onPropertyChanged) node.onPropertyChanged();
        } catch (e) {}
        // 重建 node.widgets_values 让磁盘下次保存到正确位置
        try {
            rebuildWidgetsValues(node, inputTypes);
        } catch (e) {}
    }
}