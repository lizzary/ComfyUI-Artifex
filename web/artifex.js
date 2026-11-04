import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const states = new Map();
const words = {
  en: {
    gallery: "Open gallery", group: "Group", color: "Color group", tag: "Auto-tag",
    unavailable: "Temporarily unavailable", direct: "None", default: "Default group",
    openGroup: "Open current group ↗", staging: "Open staging folder ↗",
    delete: "Delete this image from Artifex", refresh: "Refresh Artifex groups",
    connected: "Uses the connected input; this selection is the fallback.",
  },
  zh: {
    gallery: "打开图库", group: "分组", color: "颜色组", tag: "自动标签",
    unavailable: "暂不可用", direct: "无", default: "默认分组",
    openGroup: "打开当前分组 ↗", staging: "打开暂存目录 ↗",
    delete: "从 Artifex 删除此图像", refresh: "刷新 Artifex 分组",
    connected: "优先使用连接的输入；此选项作为回退值。",
  },
  "zh-TW": {
    gallery: "開啟圖庫", group: "分組", color: "顏色組", tag: "自動標籤",
    unavailable: "暫不可用", direct: "無", default: "預設分組",
    openGroup: "開啟目前分組 ↗", staging: "開啟暫存目錄 ↗",
    delete: "從 Artifex 刪除此圖像", refresh: "重新整理 Artifex 分組",
    connected: "優先使用連接的輸入；此選項作為回退值。",
  },
};

function t(key) {
  const locale = app.extensionManager.setting.get("Comfy.Locale") || "en";
  return words[locale === "zh-TW" ? locale : String(locale).startsWith("zh") ? "zh" : "en"][key];
}

function report(error) {
  console.error("[Artifex]", error);
  app.extensionManager.toast.add({ severity: "error", summary: "Artifex", detail: error.message, life: 8000 });
}

async function request(path, { method = "GET", body } = {}) {
  const response = await api.fetchApi(`/artifex${path}`, {
    method,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  const result = response.status === 204 ? {} : await response.json();
  if (!response.ok) throw new Error(result.error || result.message || `Artifex: HTTP ${response.status}`);
  return result;
}

function widget(node, name) {
  return node.widgets?.find((item) => item.name === name);
}

function identifyPack(node) {
  node.properties ??= {};
  node.properties.cnr_id ||= "comfyui-artifex";
  node.properties.aux_id ||= "lizzary/ComfyUI-Artifex";
}

function removeAutoTagSocket(node) {
  // Some frontend builds omit socketless from BOOLEAN widget options, and old
  // workflows can restore the former socket. Keep this control widget-only.
  const tag = widget(node, "auto_tag");
  if (tag) tag.options.socketless = true;
  const index = node.inputs?.findIndex((input) => input.name === "auto_tag") ?? -1;
  if (index >= 0) node.removeInput(index);
}

function linked(node, name) {
  const index = node.inputs?.findIndex((input) => input.name === name) ?? -1;
  return index >= 0 && node.isInputConnected(index);
}

// Primitive STRING nodes can be resolved before execution. Other nodes are resolved
// by the backend during execution; opening the group then uses its last result.
function literalInput(node, name) {
  const index = node.inputs?.findIndex((item) => item.name === name) ?? -1;
  if (index < 0 || !node.isInputConnected(index)) return undefined;
  const source = node.getInputNode(index);
  if (!source || !["PrimitiveNode", "PrimitiveString", "PrimitiveStringMultiline"].includes(source.comfyClass || source.type)) return undefined;
  if (source.inputs?.some((_, index) => source.isInputConnected(index))) return undefined;
  const values = source.widgets?.filter((item) => typeof item.value === "string");
  return values?.length === 1 ? values[0].value : undefined;
}

function target(node) {
  const groupName = literalInput(node, "group_name");
  const saved = states.get(node)?.group;
  return {
    group: linked(node, "group_name") && groupName === undefined && saved
      ? saved : widget(node, "group")?.value || "default",
    ...(groupName === undefined ? {} : { group_name: groupName }),
  };
}

async function open(targetName, node) {
  try {
    await request("/open", { method: "POST", body: { target: targetName, ...(node ? target(node) : {}) } });
  } catch (error) {
    report(error);
  }
}

function alignNameInputs(node) {
  const pairs = [["group_name", "group"], ["color_group_name", "color_group"]];
  const arrange = node.arrange;
  node.arrange = function (...args) {
    const halfSlot = LiteGraph.NODE_SLOT_HEIGHT / 2;
    for (const [name] of pairs) {
      const input = this.inputs.find((item) => item.name === name);
      if (!input) continue;
      input.pos ??= [halfSlot, 0];
      input.label = " ";
    }
    const imageIndex = this.inputs.findIndex((input) => input.name === "images");
    this.widgets_start_y = this.getInputPos(imageIndex)[1] - this.pos[1] + halfSlot + 2;
    const result = arrange.apply(this, args);
    let moved = false;
    for (const [name, controlName] of pairs) {
      const input = this.inputs.find((item) => item.name === name);
      const control = widget(this, controlName);
      if (!input || !control) continue;
      const y = control.y + halfSlot;
      if (input.pos[0] !== halfSlot || input.pos[1] !== y) {
        input.pos = [halfSlot, y];
        moved = true;
      }
    }
    // slot.pos drives native link endpoints; arranging again refreshes its hit box.
    // Keep STRING inputs separate from widgets so Primitive nodes cannot overwrite
    // the saved dropdown fallback or inherit its COMBO type.
    if (moved) arrange.apply(this, args);
    return result;
  };
}

function setOptions(control, options, fallback, specialLabel) {
  if (!control) return;
  const labels = new Map(options.map(({ value, label }) => [value, label]));
  if (!labels.has(fallback)) labels.set(fallback, specialLabel);
  control.options.values = [...labels.keys()];
  control.options.getOptionLabel = (value) => value == null || value === fallback ? specialLabel : labels.get(value) || String(value);
  if (control.value == null) control.value = fallback;
}

function localize(node) {
  const state = states.get(node);
  if (!state) return;
  const group = widget(node, "group");
  const color = widget(node, "color_group");
  const tag = widget(node, "auto_tag");
  if (group) {
    group.label = t("group");
    group.disabled = linked(node, "group_name");
    group.tooltip = group.disabled ? t("connected") : "";
  }
  if (color) {
    color.label = t("color");
    color.disabled = linked(node, "color_group_name");
    color.tooltip = color.disabled ? t("connected") : "";
  }
  if (tag) {
    tag.disabled = !state.autoTagAvailable;
    tag.label = state.autoTagAvailable ? t("tag") : `${t("tag")} · ${t("unavailable")}`;
  }
  state.groupButton.textContent = t("openGroup");
  state.stagingButton.textContent = t("staging");
  if (state.options) {
    setOptions(group, state.options.groups, "default", t("default"));
    setOptions(color, state.options.color_groups, "", t("direct"));
  }
  node.setDirtyCanvas(true, true);
}

async function refresh(node, notify = false) {
  const state = states.get(node);
  if (!state) return;
  const sequence = ++state.sequence;
  try {
    const options = await request(`/options?${new URLSearchParams(target(node))}`);
    if (states.get(node) !== state || sequence !== state.sequence) return;
    state.options = options;
    state.autoTagAvailable = options.auto_tag_available !== false;
    localize(node);
  } catch (error) {
    if (notify) report(error);
    else console.warn("[Artifex] Cannot refresh group choices:", error.message);
  }
}

function outputFor(node) {
  return states.get(node)?.output || app.nodeOutputs[String(node.id)];
}

async function deleteImage(node, index) {
  const state = states.get(node);
  const output = outputFor(node);
  const record = output?.artifex_images?.[index];
  if (!record || state.deleting.has(record.id)) return;
  state.deleting.add(record.id);
  try {
    await request(`/images/${encodeURIComponent(record.id)}`, { method: "DELETE" });
    // Replace output objects so Comfy's native preview store observes the update.
    const remove = (value) => {
      const indices = value.artifex_images.map((item, i) => item.id === record.id ? i : -1).filter((i) => i >= 0);
      return {
        ...value,
        images: value.images.filter((_, i) => !indices.includes(i)),
        artifex_images: value.artifex_images.filter((item) => item.id !== record.id),
      };
    };
    for (const [key, value] of Object.entries(app.nodeOutputs)) {
      if (value?.artifex_images?.some((item) => item.id === record.id)) app.nodeOutputs[key] = remove(value);
    }
    state.output = remove(output);
    node.imgs?.splice(index, 1);
    node.imageIndex = null;
    node.overIndex = null;
    node.setDirtyCanvas(true, true);
  } catch (error) {
    report(error);
  } finally {
    state.deleting.delete(record.id);
  }
}

function addNodeUI(node) {
  if (states.has(node)) return;
  identifyPack(node);
  removeAutoTagSocket(node);
  alignNameInputs(node);
  const row = document.createElement("div");
  row.className = "artifex-actions";
  const groupButton = document.createElement("button");
  const stagingButton = document.createElement("button");
  for (const [button, destination] of [[groupButton, "group"], [stagingButton, "staging"]]) {
    button.type = "button";
    button.addEventListener("pointerdown", (event) => event.stopPropagation());
    button.addEventListener("click", (event) => { event.stopPropagation(); void open(destination, node); });
    row.append(button);
  }
  const actions = node.addDOMWidget("artifex_actions", "artifex-actions", row, {
    // The parameter panel's legacy renderer overwrites the shared widget's
    // width when the node is selected. These actions belong on the node only.
    serialize: false, hideOnZoom: false, hideInPanel: true,
    getMinHeight: () => 52, getMaxHeight: () => 52,
  });
  actions.serialize = false;
  states.set(node, { groupButton, stagingButton, sequence: 0, autoTagAvailable: true, deleting: new Set() });

  const group = widget(node, "group");
  if (group) {
    const callback = group.callback;
    group.callback = function (...args) {
      callback?.apply(this, args);
      states.get(node).group = undefined;
      const color = widget(node, "color_group");
      if (color) color.value = "";
      void refresh(node);
    };
  }
  const connectionsChanged = node.onConnectionsChange;
  node.onConnectionsChange = function (...args) {
    const result = connectionsChanged?.apply(this, args);
    queueMicrotask(() => { localize(this); void refresh(this); });
    return result;
  };
  const configured = node.onConfigure;
  node.onConfigure = function (...args) {
    const result = configured?.apply(this, args);
    identifyPack(this);
    removeAutoTagSocket(this);
    return result;
  };
  const serialized = node.onSerialize;
  node.onSerialize = function (data) {
    const result = serialized?.apply(this, arguments);
    // Embed the package identity even without Manager or a completed registry index.
    // Keep any version recorded by Manager instead of guessing a release number.
    identifyPack(this);
    identifyPack(data);
    return result;
  };
  const executed = node.onExecuted;
  node.onExecuted = function (message) {
    const result = executed?.apply(this, arguments);
    const state = states.get(this);
    state.output = message;
    const group = message.artifex_group?.[0];
    state.group = typeof group === "string" ? group : group?.value;
    void refresh(this);
    return result;
  };
  const removed = node.onRemoved;
  node.onRemoved = function (...args) {
    states.delete(this);
    return removed?.apply(this, args);
  };
  node.setSize([Math.max(node.size[0], 360), Math.max(node.size[1], 340)]);
  localize(node);
  void refresh(node);
}

app.registerExtension({
  name: "Artifex.SaveInArtifex",
  commands: [{ id: "Artifex.OpenGallery", label: () => t("gallery"), menubarLabel: "ArtifexOpenGallery", icon: "pi pi-images", function: () => open("gallery") }],
  menuCommands: [{ path: ["Extensions", "Artifex"], commands: ["Artifex.OpenGallery"] }],
  actionBarButtons: [{ icon: "pi pi-images", label: "Artifex", get tooltip() { return t("gallery"); }, onClick: () => open("gallery") }],
  setup() {
    const style = document.createElement("style");
    style.textContent = `.artifex-actions { display: flex; gap: 6px; padding: 2px 0; height: 32px; box-sizing: border-box; }
      .artifex-actions button { flex: 1; min-width: 0; cursor: pointer; border: 1px solid var(--border-color, #666); border-radius: 5px; background: var(--comfy-input-bg, #222); color: var(--input-text, #ddd); font: 12px sans-serif; white-space: nowrap; }
      .artifex-actions button:hover { background: var(--comfy-menu-bg, #444); }
      .artifex-actions button:focus-visible { outline: 2px solid #6aa9ff; outline-offset: 1px; }`;
    document.head.append(style);
    app.ui.settings.addEventListener("Comfy.Locale.change", () => { for (const node of states.keys()) localize(node); });
    window.addEventListener("focus", () => { for (const node of states.keys()) void refresh(node); });
  },
  nodeCreated(node) {
    if (node.comfyClass === "SaveInArtifex") addNodeUI(node);
  },
  loadedGraphNode(node) {
    if (node.comfyClass === "SaveInArtifex") { removeAutoTagSocket(node); localize(node); void refresh(node); }
  },
  getNodeMenuItems(node) {
    if (node.comfyClass !== "SaveInArtifex") return [];
    const index = node.imageIndex ?? node.overIndex;
    const items = [{ content: t("refresh"), callback: () => refresh(node, true) }];
    if (index != null && outputFor(node)?.artifex_images?.[index]) {
      items.unshift({ content: t("delete"), callback: () => deleteImage(node, index) });
    }
    return items;
  },
});
