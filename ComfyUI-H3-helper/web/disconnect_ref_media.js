// H3Helper.DisconnectRefMedia
// ---------------------------------------------------------------------------
// 给「H3 R2VA AIO(micxin)」节点提供「一键断开所有参考媒体输入」的能力：
//   ref_image_* / ref_video_* / ref_video_audio_* / ref_audio_*
// （例如接了 3 张图 + 1 个视频，一次全部断开，方便清空重做）
// 保留 prompt / width / height / length / ref_image_size 以及所有模型/VAE 设置不动。
//
// 实现方式：快捷键（V3 节点 io.ComfyNode 不走 node.getExtraMenuOptions，右键菜单无效）。
//   选中 AIO 节点 → 按 Ctrl+Alt+D → 断开该节点上所有已连接的参考媒体。
// 本脚本不引入任何重命名功能（「重命名接口」是 ComfyUI 槽自带核心菜单项）。
// ---------------------------------------------------------------------------
import { app } from "/scripts/app.js";

const NODE_ID = "H3ModelLoader";
const MEDIA_PREFIXES = ["ref_image_", "ref_video_", "ref_audio_"];
const EXCLUDE = new Set(["ref_image_size"]);

function isMediaInput(name) {
  if (!name || EXCLUDE.has(name)) return false;
  return MEDIA_PREFIXES.some((p) => name.startsWith(p));
}

// 目标节点识别：优先按类型名，V3 节点类型名万一不一致时用输入槽名特征兜底。
function isTargetNode(node) {
  if (!node) return false;
  const t = node.type || "";
  const c = node.comfyClass || "";
  if (t === NODE_ID || c === NODE_ID) return true;
  if (t && t.includes(NODE_ID)) return true;
  if (node.inputs && node.inputs.some((i) => {
        const n = i && i.name;
        return n && (n.startsWith("ref_image_") || n.startsWith("ref_video_") ||
                     n.startsWith("ref_audio_"));
      })) {
    if (t.includes("H3") || t.includes("Ref2VA") ||
        c.includes("H3") || c.includes("Ref2VA")) {
      return true;
    }
  }
  return false;
}

// 一键断开所有已连接的参考媒体输入，返回断开数量。
function disconnectAllMedia(node) {
  if (!node.inputs) return 0;
  let removed = 0;
  for (let i = node.inputs.length - 1; i >= 0; i--) {
    const inp = node.inputs[i];
    const name = inp && inp.name;
    if (!isMediaInput(name)) continue;
    if (inp.link != null) {
      try {
        const link = app.graph.links[inp.link];
        if (link && typeof link.disconnect === "function") link.disconnect();
      } catch (e) { /* noop */ }
      inp.link = null;
    }
    try { node.disconnectInput(i); } catch (e) { /* noop */ }
    removed++;
  }
  return removed;
}

// 取得当前画布中选中的、且是目标类型的节点（支持多选中批量断开）。
function getSelectedTargetNodes() {
  const canvas = app.canvas;
  const out = [];
  if (!canvas) return out;
  const sel = canvas.selected_nodes;
  if (sel && typeof sel === "object") {
    for (const k in sel) {
      const n = sel[k];
      if (n && isTargetNode(n)) out.push(n);
    }
  }
  const items = canvas.selectedItems;
  if (items && typeof items === "object" && !Array.isArray(items)) {
    for (const k in items) {
      const n = items[k];
      if (n && isTargetNode(n) && out.indexOf(n) === -1) out.push(n);
    }
  }
  return out;
}

// 快捷键 Ctrl+Alt+D：断开选中 AIO 节点的所有参考媒体输入。
document.addEventListener("keydown", (e) => {
  // 在文本框/输入框里输入时不触发，避免误触。
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
    return;
  }
  if (e.ctrlKey && e.altKey && e.code === "KeyD") {
    e.preventDefault();
    const nodes = getSelectedTargetNodes();
    if (!nodes.length) {
      console.log(
        "[H3 AIO] 快捷键未生效：请先选中「H3 R2VA AIO(micxin)」节点，再按 Ctrl+Alt+D。"
      );
      return;
    }
    let total = 0;
    nodes.forEach((n) => { total += disconnectAllMedia(n); });
    if (total > 0) app.graph.setDirtyCanvas(true, true);
    const msg = `[H3 AIO] 已断开 ${total} 个参考媒体输入（prompt/宽高长/设置保留）。`;
    console.log(msg);
    try { if (app.ui && app.ui.toast) app.ui.toast(msg); } catch (_) { /* noop */ }
  }
});

// 同时尝试挂到节点/槽右键菜单（部分 ComfyUI 版本有效，V3 节点可能无效，留作兼容）。
app.registerExtension({
  name: "H3Helper.DisconnectRefMedia",
  nodeCreated(node) {
    if (!isTargetNode(node)) return;

    const origNodeMenu =
      node.getExtraMenuOptions && node.getExtraMenuOptions.bind(node);
    if (origNodeMenu) {
      node.getExtraMenuOptions = function (canvas, options) {
        const opts = origNodeMenu(canvas, options) || [];
        opts.push({
          content: "断开所有参考媒体输入（图/视频/音频） [Ctrl+Alt+D]",
          callback: () => {
            const removed = disconnectAllMedia(node);
            if (removed > 0) app.graph.setDirtyCanvas(true, true);
          },
        });
        return opts;
      };
    }

    const origSlotMenu =
      node.getSlotMenuOptions && node.getSlotMenuOptions.bind(node);
    if (origSlotMenu) {
      node.getSlotMenuOptions = function (slot, options) {
        const opts = origSlotMenu(slot, options) || [];
        const slotName = (slot && (slot.name || (slot.input && slot.input.name))) || "";
        const isInput = slot && (slot.input != null || slot.type === LiteGraph.INPUT);
        if (isInput && isMediaInput(slotName)) {
          if (opts.length > 0) opts.push(null);
          opts.push({
            content: "断开所有参考媒体输入（图/视频/音频） [Ctrl+Alt+D]",
            callback: () => {
              const removed = disconnectAllMedia(node);
              if (removed > 0) app.graph.setDirtyCanvas(true, true);
            },
          });
        }
        return opts;
      };
    }
  },
});
