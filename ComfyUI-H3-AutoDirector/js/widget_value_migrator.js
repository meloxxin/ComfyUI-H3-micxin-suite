/**
 * widget_value_migrator.js (ComfyUI-H3-AutoDirector)
 */

const SCHEMA_MIGRATIONS = {
    "n_ctx": { from: [4096, 2048], to: 8192 },
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

function realignByDiskWidgets(node, inputTypes, diskWidgetsValues) {
    if (!node || !node.widgets || !node.widgets.length) return { changed: false };
    if (!inputTypes) return { changed: false };
    const ordered = getOrderedKeys(inputTypes);
    if (!ordered.length) return { changed: false };
    const diskArr = Array.isArray(diskWidgetsValues) ? diskWidgetsValues
        : (Array.isArray(node.widgets_values) ? node.widgets_values : []);
    if (!diskArr.length) return { changed: false };
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
        try {
            rebuildWidgetsValues(node, inputTypes);
        } catch (e) {}
    }
}
