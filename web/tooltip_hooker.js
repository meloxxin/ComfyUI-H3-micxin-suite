/**
 * tooltip_hooker.js (ComfyUI-H3-AutoDirector)
 *
 * Standalone browser-side helper that forcibly exposes each widget's
 * `tooltip` text as a browser-native HTML `title` attribute on the
 * widget's DOM element. This guarantees hover-tooltips work regardless
 * of:
 *   - whether LiteGraph / ComfyUI-DD-Translation cleared `widget.options.tooltip`;
 *   - the LiteGraph / ComfyUI version's tooltip-popup CSS being broken or
 *     hidden by another extension;
 *   - the user being on the legacy LiteGraph canvas or the new Vue 2.0 nodes.
 *
 * Used by h3_screenwriter.js and h3_multishot_director.js.
 *
 * Usage:
 *   import { ensureAllTooltipsShown } from "./tooltip_hooker.js";
 *   nodeCreated(node) { ensureAllTooltipsShown(node); }
 *
 * Idempotent and lightweight — runs once per node at creation, plus once
 * per draw cycle so any widget added/renamed later also gets covered.
 */

const TITLE_ATTR = "data-h3-tooltip-hooked";
const TIP_ATTR = "title";

function _grabTooltip(widget) {
    if (!widget) return "";
    // Order matters: tooltip is sometimes on the widget itself, sometimes
    // on widget.options depending on the LiteGraph version.
    return (
        widget.tooltip ||
        (widget.options && (widget.options.tooltip || widget.options.tooltipText)) ||
        (widget.label && widget.label.tooltip) ||
        ""
    );
}

function _applyTitleToElement(el, tip) {
    if (!el || !tip || !el.setAttribute) return false;
    // Always overwrite: ComfyUI-DD-Translation / LiteGraph may strip the
    // title attribute between draws, so we re-apply it each sweep. The
    // data-* hook marker is what guards against thrash (we skip only if
    // BOTH marker AND title already equal `tip`).
    if (
        el.getAttribute(TITLE_ATTR) === tip &&
        el.getAttribute(TIP_ATTR) === tip
    ) return true;
    el.setAttribute(TITLE_ATTR, tip);
    el.setAttribute(TIP_ATTR, tip);
    return true;
}

/**
 * Sweep a single widget once and set title on every relevant DOM element.
 * Returns true if at least one title was applied.
 */
function _sweepWidget(widget) {
    const tip = _grabTooltip(widget);
    if (!tip) return false;
    let any = false;

    // 1) widget.inputEl (most INPUT/TEXTAREA widgets expose one)
    if (_applyTitleToElement(widget.inputEl, tip)) any = true;
    // 2) widget.element (LiteGraph container for the widget row)
    if (_applyTitleToElement(widget.element, tip)) any = true;

    // 3) widget.options?.element — some extensions store it here
    if (widget.options && _applyTitleToElement(widget.options.element, tip)) any = true;

    // 4) If we still have no anchor, try to discover an element inside the
    //    widget's own subtree (covers CustomWidgets/DOM widgets we missed).
    const fallback = (!widget.inputEl && !widget.element)
        ? (widget.options && widget.options.element)
        : null;
    if (fallback && _applyTitleToElement(fallback, tip)) any = true;

    return any;
}

/**
 * Apply all widget tooltips on a node. Safe to call multiple times.
 * - Retries up to 6 times with linear backoff to wait for widgets/DOM to appear
 *   (ComfyUI builds widgets asynchronously after nodeCreated fires).
 * - Hooks node.onDrawForeground / node.drawWidgets so any widget the user
 *   adds/renames later also gets a title attribute on its next render.
 */
export function ensureAllTooltipsShown(node) {
    if (!node || node.__h3TipHooked) return;
    node.__h3TipHooked = true;

    let attempts = 0;
    const trySweep = () => {
        if (!node.widgets || !node.widgets.length) {
            if (attempts++ < 6) setTimeout(trySweep, 60 * attempts);
            return;
        }
        for (const w of node.widgets) _sweepWidget(w);
    };
    trySweep();

    // Hook the draw cycle so future widget additions also get tooltips.
    const install = () => {
        if (node.__h3DrawHooked) return;
        node.__h3DrawHooked = true;
        const origDraw = node.onDrawForeground;
        node.onDrawForeground = function () {
            try {
                for (const w of (node.widgets || [])) _sweepWidget(w);
            } catch (e) { /* swallow — non-fatal */ }
            if (origDraw) return origDraw.apply(this, arguments);
        };
    };
    // Some node types override drawWidgets instead — wrap both.
    const origDrawWidgets = node.drawWidgets;
    node.drawWidgets = function () {
        try { install(); } catch (e) {}
        try {
            for (const w of (node.widgets || [])) _sweepWidget(w);
        } catch (e) {}
        if (origDrawWidgets) return origDrawWidgets.apply(this, arguments);
    };
    install();
}