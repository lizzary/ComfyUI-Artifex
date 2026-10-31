import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";

const source = fs.readFileSync(new URL("../web/artifex.js", import.meta.url), "utf8").replace(/^import .*;$/gm, "");
const tick = () => new Promise((resolve) => setImmediate(resolve));

function setup() {
  const requests = [], errors = [];
  let extension, locale = "zh";
  const options = {
    groups: [{ value: "id:1", label: "Sea(id:1)" }, { value: "id:2", label: "Sea(id:2)" }],
    color_groups: [{ value: "board:3", label: "V" }], auto_tag_available: false,
  };
  const app = {
    extensionManager: { setting: { get: () => locale }, toast: { add: (error) => errors.push(error) } },
    registerExtension: (value) => { extension = value; }, nodeOutputs: {},
    ui: { settings: { addEventListener() {} } },
  };
  const api = {
    async fetchApi(url, init) {
      requests.push([url, init]);
      return { ok: true, status: 200, json: async () => url.startsWith("/artifex/options") ? options : { opened: true, url: "http://127.0.0.1:7878/" } };
    },
  };
  const element = () => ({
    style: {}, children: [], events: new Map(),
    append(child) { this.children.push(child); },
    addEventListener(name, callback) { this.events.set(name, callback); },
  });
  const context = vm.createContext({
    app, api, console: { ...console, error() {} }, URLSearchParams, queueMicrotask, LiteGraph: { NODE_SLOT_HEIGHT: 20 },
    document: { createElement: element, head: { append() {} } },
    window: { addEventListener() {} },
  });
  vm.runInContext(source, context);
  function node(id = 1) {
    const value = {
      id, comfyClass: "SaveInArtifex", pos: [100, 200], size: [200, 100],
      widgets: ["group", "color_group", "auto_tag"].map((name) => ({
        name, value: name === "group" ? "id:1" : name === "auto_tag" ? true : "", options: {},
      })),
      inputs: [{ name: "images", type: "IMAGE", link: 1 }, { name: "group_name", type: "STRING", link: 5 }, { name: "color_group_name", type: "STRING", link: null }],
      isInputConnected(index) { return this.inputs[index]?.link != null; },
      removeInput(index) { this.inputs.splice(index, 1); },
      getInputNode() { return { type: "PrimitiveString", widgets: [{ value: "Sea" }] }; },
      getInputPos(index) {
        const offset = this.inputs[index].pos || [10, 14 + 20 * index];
        return [this.pos[0] + offset[0], this.pos[1] + offset[1]];
      },
      arrange() {
        this.widgets.forEach((widget, index) => { widget.y = this.widgets_start_y + index * 24; });
        this.inputs.forEach((input, index) => {
          const [x, y] = this.getInputPos(index);
          input.boundingRect = [x - 10, y - 10, 20, 20];
        });
      },
      addDOMWidget(_name, _type, element) { this.actionRow = element; return {}; },
      setSize(size) { this.size = size; this.arrange(); }, setDirtyCanvas() {}, imgs: [{}, {}, {}],
    };
    extension.nodeCreated(value);
    return value;
  }
  return { app, api, options, requests, errors, extension, node, setLocale: (value) => { locale = value; } };
}

test("all open actions ask the server to launch the system application without browser popups", async () => {
  const harness = setup();
  assert.deepEqual(Array.from(harness.extension.menuCommands[0].path), ["Extensions", "Artifex"]);
  await harness.extension.commands[0].function();
  assert.equal(harness.requests[0][0], "/artifex/open");
  assert.equal(JSON.parse(harness.requests[0][1].body).target, "gallery");
  assert.equal(harness.requests[0][1].method, "POST");
  assert.equal(harness.extension.actionBarButtons.length, 1);
  await harness.extension.actionBarButtons[0].onClick();
  const node = harness.node();
  for (const button of node.actionRow.children) button.events.get("click")({ stopPropagation() {} });
  await tick();
  assert.deepEqual(harness.requests.filter(([url]) => url === "/artifex/open").map(([, init]) => JSON.parse(init.body).target), ["gallery", "gallery", "group", "staging"]);
  assert.equal(harness.errors.length, 0);
  node.onRemoved();
});

test("connected inputs disable their fallback choices; option IDs retain readable labels", async () => {
  const harness = setup();
  const node = harness.node();
  await tick();
  assert.equal(node.widgets[0].disabled, true);
  assert.equal(node.widgets[1].disabled, false);
  assert.equal(node.widgets[2].disabled, true);
  assert.equal(node.widgets[2].value, true);
  assert.match(node.widgets[2].label, /暂不可用/);
  assert.equal(node.widgets[0].options.getOptionLabel("id:1"), "Sea(id:1)");
  assert.equal(node.widgets[1].options.getOptionLabel(""), "无");
  assert.equal(node.widgets[1].options.getOptionLabel(null), "无");
  assert.match(harness.requests[0][0], /group_name=Sea/);
  node.inputs.find((input) => input.name === "group_name").link = null;
  node.onConnectionsChange();
  await tick();
  assert.equal(node.widgets[0].disabled, false);
  harness.setLocale("en");
  harness.extension.loadedGraphNode(node);
  assert.match(node.widgets[2].label, /Temporarily unavailable/);
  node.onRemoved();
});

test("auto-tag remains available when capability validation is not implemented", async () => {
  const harness = setup();
  delete harness.options.auto_tag_available;
  const node = harness.node();
  assert.equal(node.widgets[2].disabled, false);
  await tick();
  assert.equal(node.widgets[2].disabled, false);
  assert.equal(node.widgets[2].value, true);
  assert.equal(node.widgets[2].label, "自动标签");
  node.onRemoved();
});

test("auto-tag stays a socketless toggle when old workflows restore its former socket", () => {
  const harness = setup();
  const node = harness.node();
  assert.equal(node.widgets[2].options.socketless, true);
  node.inputs.push({ name: "auto_tag", type: "BOOLEAN", widget: { name: "auto_tag" }, link: null });
  node.onConfigure({});
  assert.equal(node.inputs.some(input => input.name === "auto_tag"), false);
  assert.equal(node.inputs.findIndex(input => input.name === "group_name"), 1);
  assert.equal(node.inputs.findIndex(input => input.name === "color_group_name"), 2);
  assert.equal(node.widgets[2].value, true);
  node.onRemoved();
});

test("STRING sockets align native link positions and hit boxes with the fallback dropdowns", () => {
  const harness = setup();
  const node = harness.node();
  for (const [inputName, widgetIndex] of [["group_name", 0], ["color_group_name", 1]]) {
    const index = node.inputs.findIndex((input) => input.name === inputName);
    const input = node.inputs[index];
    assert.equal(input.type, "STRING");
    assert.equal(input.widget, undefined, "layout must not bind a Primitive input to the fallback value");
    assert.equal(node.getInputPos(index)[1], node.pos[1] + node.widgets[widgetIndex].y + 10);
    assert.equal(input.boundingRect[1] + 10, node.getInputPos(index)[1]);
  }
  node.pos = [400, 500];
  node.setSize([600, 700]);
  const groupIndex = node.inputs.findIndex((input) => input.name === "group_name");
  assert.equal(node.inputs[groupIndex].boundingRect[1] + 10, node.getInputPos(groupIndex)[1]);
  assert.equal(node.widgets[0].value, "id:1");
  node.onRemoved();
});

test("deleting a selected preview updates both Artifex and native ComfyUI output state", async () => {
  const harness = setup();
  const node = harness.node();
  const output = {
    images: [{ filename: "a" }, { filename: "b" }, { filename: "c" }],
    artifex_images: [{ id: "a" }, { id: "b" }, { id: "c" }], artifex_group: ["id:1"],
  };
  harness.app.nodeOutputs["1"] = output;
  node.onExecuted(output);
  node.imageIndex = 1;
  const menu = harness.extension.getNodeMenuItems(node);
  assert.match(menu[0].content, /删除/);
  await menu[0].callback();
  assert.deepEqual(harness.app.nodeOutputs["1"].images.map((image) => image.filename), ["a", "c"]);
  assert.deepEqual(harness.app.nodeOutputs["1"].artifex_images.map((image) => image.id), ["a", "c"]);
  assert.equal(node.imgs.length, 2);
  assert.equal(node.imageIndex, null);
  assert.equal(harness.requests.filter(([url]) => url === "/artifex/images/b").length, 1);
  node.onRemoved();
});

test("failed deletion preserves previews and reports the backend error", async () => {
  const harness = setup();
  const node = harness.node();
  await tick();
  const output = { images: [{ filename: "a" }], artifex_images: [{ id: "a" }] };
  harness.app.nodeOutputs["1"] = output;
  node.onExecuted(output);
  await tick();
  harness.api.fetchApi = async () => ({ ok: false, status: 503, json: async () => ({ error: "Artifex failed after 3 attempts" }) });
  node.imageIndex = 0;
  await harness.extension.getNodeMenuItems(node)[0].callback();
  assert.equal(harness.app.nodeOutputs["1"].images.length, 1);
  assert.equal(harness.errors.length, 1);
  assert.match(harness.errors[0].detail, /3 attempts/);
  node.onRemoved();
});
