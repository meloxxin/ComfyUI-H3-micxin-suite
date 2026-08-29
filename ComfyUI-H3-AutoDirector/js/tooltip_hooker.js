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
    return (
        widget.tooltip ||
        (widget.options && (widget.options.tooltip || widget.options.tooltipText)) ||
        (widget.label && widget.label.tooltip) ||
        ""
    );
}

function _applyTitleToElement(el, tip) {
    if (!el || !tip || !el.setAttribute) return false;
    if (
        el.getAttribute(TITLE_ATTR) === tip &&
        el.getAttribute(TIP_ATTR) === tip
    ) return true;
    el.setAttribute(TITLE_ATTR, tip);
    el.setAttribute(TIP_ATTR, tip);
    return true;
}

function _sweepWidget(widget) {
    const tip = _grabTooltip(widget);
    if (!tip) return false;
    let any = false;
    if (_applyTitleToElement(widget.inputEl, tip)) any = true;
    if (_applyTitleToElement(widget.element, tip)) any = true;
    if (widget.options && _applyTitleToElement(widget.options.element, tip)) any = true;
    const fallback = (!widget.inputEl && !widget.element)
        ? (widget.options && widget.options.element)
        : null;
    if (fallback && _applyTitleToElement(fallback, tip)) any = true;
    return any;
}

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
    const install = () => {
        if (node.__h3DrawHooked) return;
        node.__h3DrawHooked = true;
        const origDraw = node.onDrawForeground;
        node.onDrawForeground = function () {
            try {
                for (const w of (node.widgets || [])) _sweepWidget(w);
            } catch (e) {}
            if (origDraw) return origDraw.apply(this, arguments);
        };
    };
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
