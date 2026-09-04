"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../tools/aftereffects/smart-ae-browser/js/app.js"), "utf8");
const html = fs.readFileSync(path.resolve(__dirname, "../tools/aftereffects/smart-ae-browser/index.html"), "utf8");
const storageKey = "smart-ae-browser-state-v1";
const original = { project: "OLD", episode: "ep01", sequence: "s001", shot: "c001" };
const current = {
  project: "ELCD", episode: "ep02", sequence: "s027", shot: "c002",
  configDir: "P:/config/ELCD", source: "shot_manager"
};
const plain = value => JSON.parse(JSON.stringify(value));

function panel(saved) {
  const storage = new Map(saved ? [[storageKey, JSON.stringify(saved)]] : []);
  const calls = [];
  const shared = { ok: true, context: { ...current } };
  const sandbox = {
    window: { SmartCEPBridge: { callHost: async name => {
      calls.push(name);
      return JSON.stringify({ ...current, manifest: "launch.json" });
    } } },
    localStorage: {
      getItem: key => storage.get(key),
      setItem: (key, value) => storage.set(key, value)
    },
    shared, calls
  };
  // Substitute external I/O at the existing closure boundary, keeping state logic real.
  const hooks = `
    elements.currentContextButton = { disabled: false };
    elements.statusLine = { textContent: "" };
    readSharedShotContext = function () { calls.push("read"); return shared; };
    reloadProjectsFromConfigDir = function (dir) {
      calls.push("config:" + dir);
      state.projects = [{
        name: "ELCD", configDir: "P:/config/ELCD",
        shots: [
          {project: "ELCD", episode: "ep02", sequence: "s027", shot: "c002"},
          {project: "ELCD", episode: "ep02", sequence: "s027", shot: "c003"}
        ]
      }];
    };
    render = function () {};
    collectReviewBuildManifests = function () { calls.push("catalog"); return []; };
    buildFromSelectedManifest = function () {};
    importManifestFromPath = function (file) { calls.push("import:" + file); };
    globalThis.panel = {
      state: state, elements: elements, loadState: loadState,
      applyLaunchContext: applyLaunchContext,
      current: selectCurrentShotContext, persistState: persistState,
      latestKey: latestRenderOutputKey, rowsFromFootage: rowsFromCurrentFootage,
      versionNumberLabel: versionNumberLabel, takeNumberLabel: takeNumberLabel
    };
  `;
  const marker = 'document.addEventListener("DOMContentLoaded", init);';
  assert.ok(source.includes(marker));
  vm.runInNewContext(source.replace(marker, hooks), sandbox);
  const api = sandbox.panel;
  api.state.projects = [{ name: "OLD", configDir: "P:/config/OLD" }];
  return { ...api, storage, shared, calls };
}

test("Output Watch is consolidated into Replace Queue", () => {
  assert.doesNotMatch(html, /Output Watch|data-tab="watch"|id="watchView"/);
  assert.match(html, /data-tab="queue">Replace Queue/);
});

test("AE naming keeps three-digit versions and two-digit takes", () => {
  const p = panel();
  assert.equal(p.versionNumberLabel("v1"), "001");
  assert.equal(p.takeNumberLabel("t1"), "01");
  assert.equal(p.takeNumberLabel("t002"), "02");
});

test("Replace Queue compares active footage with the latest receipt take", () => {
  const p = panel();
  p.state.projects = [{
    name: "ELCD", configDir: "P:/config/ELCD",
    shots: [{project: "ELCD", episode: "ep02", sequence: "s027", shot: "c002"}]
  }];
  p.state.filters = {project: "ELCD", episode: "ep02", sequence: "s027", shot: "c002"};
  const context = {...p.state.filters};
  const output = "D:/Projects/ELCD/workspace/cg/shots/ep02/s027/c002/render/anim/layers/CHA/v001/t02/ELCD_ep02_s027_c002_anim_CHA_v001_t02_####.png";
  p.state.latestRenderOutputs[p.latestKey(context, "anim", "CHA")] = {
    manifest: {...context, department: "anim"},
    item: {layer: "CHA", version: "v001", take: "t02", outputPath: output}
  };
  const rows = p.rowsFromFootage([{
    name: "CHA",
    sourcePath: output.replace("/t02/", "/t01/").replace("t02_####", "t01_0632"),
    exists: true
  }]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].currentLabel, "v001-01");
  assert.equal(rows[0].latestLabel, "v001-02");
  assert.equal(rows[0].status, "replace");
  assert.equal(rows[0].outputPath, output);
});

test("reopening keeps saved filters, tab and AEP despite a different launch shot", async () => {
  const p = panel({
    filters: original, activeTab: "aep", selectedAepPath: "old.aep",
    selectedManifestId: "previous", configDir: "P:/config/OLD"
  });
  p.loadState();
  await p.applyLaunchContext(!p.state.hasRestoredState);
  assert.deepEqual(plain(p.state.filters), original);
  assert.equal(p.state.activeTab, "aep");
  assert.equal(p.state.selectedAepPath, "old.aep");
  assert.equal(p.state.selectedManifestId, "previous");
  assert.equal(p.state.restoredConfigDir, "P:/config/OLD");
  assert.deepEqual(p.calls, ["getLaunchContext"]);
});

test("saved All is a saved selection, while first launch still applies launch context", async () => {
  const all = { project: "", episode: "", sequence: "", shot: "" };
  const restored = panel({ filters: all, activeTab: "invalid" });
  restored.loadState();
  await restored.applyLaunchContext(!restored.state.hasRestoredState);
  assert.deepEqual(plain(restored.state.filters), all);
  assert.equal(restored.state.activeTab, "queue");
  const fresh = panel();
  fresh.loadState();
  await fresh.applyLaunchContext(!fresh.state.hasRestoredState);
  assert.equal(fresh.state.filters.shot, "c002");
  assert.ok(fresh.calls.includes("import:launch.json"));
});

test("Current rereads shared selection, replaces every axis and persists without opening or building", () => {
  const p = panel({ filters: original, activeTab: "aep", selectedAepPath: "old.aep" });
  p.loadState();
  p.state.launchContext = { manifest: "stale.json" };
  p.current();
  assert.deepEqual(plain(p.state.filters), {
    project: "ELCD", episode: "ep02", sequence: "s027", shot: "c002"
  });
  assert.equal(p.state.selectedAepPath, "");
  assert.equal(p.state.launchContext.manifest, undefined);
  assert.equal(p.state.activeTab, "aep");
  assert.equal(p.elements.currentContextButton.disabled, false);
  assert.match(p.elements.statusLine.textContent, /^Current: ELCD/);
  const persisted = JSON.parse(p.storage.get(storageKey));
  assert.equal(persisted.filters.shot, "c002");
  assert.equal(persisted.configDir, current.configDir);
  p.shared.context.shot = "c003";
  p.current();
  assert.equal(p.state.filters.shot, "c003");
  assert.equal(p.calls.filter(value => value === "read").length, 2);
  assert.equal(p.calls.filter(value => value === "catalog").length, 2);
  assert.ok(p.calls.every(value => ["read", "catalog", "config:" + current.configDir].includes(value)));
});

for (const [name, payload] of [
  ["no selection", { ok: false, error: "No shot is selected in Shot Manager" }],
  ["unknown project", { ok: true, context: { ...current, project: "MISSING" } }],
  ["incomplete selection", { ok: true, context: { ...current, shot: "" } }],
  ["unknown shot", { ok: true, context: { ...current, shot: "c999" } }]
]) {
  test("Current preserves prior state on " + name, () => {
    const p = panel({ filters: original, selectedAepPath: "old.aep" });
    p.loadState();
    const projects = p.state.projects;
    Object.assign(p.shared, payload);
    p.current();
    assert.deepEqual(plain(p.state.filters), original);
    assert.equal(p.state.projects, projects);
    assert.equal(p.state.selectedAepPath, "old.aep");
    assert.equal(p.elements.currentContextButton.disabled, false);
    assert.match(p.elements.statusLine.textContent, /^Current failed:/);
    assert.ok(!p.calls.includes("catalog"));
    assert.equal(JSON.parse(p.storage.get(storageKey)).selectedAepPath, "old.aep");
  });
}
