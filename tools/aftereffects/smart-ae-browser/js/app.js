(function () {
  "use strict";

  var STORAGE_KEY = "smart-ae-browser-state-v1";
  var POLL_INTERVAL_MS = 4000;
  var DEFAULT_CONFIG_ROOT = "P:/dev/smartlibrary/config";
  var DEFAULT_AE_RENDER_CONFIG = {
    final_comp: {
      name: "final",
      fallback_names: "render_final,anim_final"
    },
    render_queue: {
      render_settings_template: "Best Settings",
      render_settings_template_aliases: "最高設定,最良設定",
      output_module_template: "Apple ProRes 422 Proxy",
      output_module_template_aliases: "Apple ProRes 422 Prox,ProRes 422 Proxy,QuickTime Apple ProRes 422 Proxy",
      audio_output: true,
      quality: "best",
      output_path: "{shot_root}/output/review/{dept}/{version}/{take}/mov/{episode}_{sequence}_{shot}_{dept}_{version}_{take}.mov"
    }
  };

  var state = {
    manifests: [],
    selectedManifestId: "",
    projects: [],
    launchContext: {},
    aepFiles: [],
    aepFilesCacheKey: "",
    aepFilesCacheAt: 0,
    selectedAepPath: "",
    nextSaveVersions: {},
    rows: [],
    activeTab: "watch",
    filters: {
      project: "",
      episode: "",
      sequence: "",
      shot: ""
    },
    pollTimer: null
  };

  var elements = {};

  function nodeRequire(moduleName) {
    try {
      if (typeof require === "function") {
        return require(moduleName);
      }
      if (window.cep_node && typeof window.cep_node.require === "function") {
        return window.cep_node.require(moduleName);
      }
    } catch (error) {
      return null;
    }
    return null;
  }

  function loadProjectsFromConfig(configRoot) {
    var fs = nodeRequire("fs");
    var path = nodeRequire("path");
    var projects = [];
    if (!fs || !path) {
      return projects;
    }

    try {
      fs.readdirSync(configRoot, { withFileTypes: true }).forEach(function (entry) {
        var templatePath;
        var aeRenderPath;
        var data;
        var anchors;
        if (!entry.isDirectory() || entry.name === "default") {
          return;
        }
        templatePath = path.join(configRoot, entry.name, "templates_base.yml");
        aeRenderPath = path.join(configRoot, entry.name, "ae_render.yml");
        if (!fs.existsSync(templatePath)) {
          return;
        }
        data = parseSimpleYaml(fs.readFileSync(templatePath, "utf8"));
        anchors = data.anchors || {};
        projects.push({
          id: entry.name,
          name: String(anchors.project_name || entry.name),
          root: String(anchors.project_root || ""),
          configDir: path.join(configRoot, entry.name).replace(/\\/g, "/"),
          aeRender: fs.existsSync(aeRenderPath) ? parseSimpleYaml(fs.readFileSync(aeRenderPath, "utf8")) : {},
          shots: loadShotContexts(String(anchors.project_root || ""), String(anchors.project_name || entry.name))
        });
      });
    } catch (error) {
      setStatus("Could not read project config: " + error.message);
    }

    return projects.sort(function (a, b) {
      return naturalCompare(a.name, b.name);
    });
  }

  function parseSimpleYaml(text) {
    var root = {};
    var stack = [{ indent: -1, value: root }];
    String(text || "").split(/\r?\n/).forEach(function (rawLine) {
      var line = rawLine.split("#", 1)[0].replace(/\s+$/, "");
      var indent;
      var stripped;
      var parent;
      var parts;
      var key;
      var value;
      var container;
      if (!line.trim()) {
        return;
      }
      indent = line.length - line.replace(/^\s+/, "").length;
      stripped = line.trim();
      if (stripped.indexOf("- ") === 0) {
        while (stack.length && indent < stack[stack.length - 1].indent) {
          stack.pop();
        }
        parent = stack[stack.length - 1].value;
        if (Array.isArray(parent)) {
          parent.push(parseYamlScalar(stripped.slice(2).trim()));
        }
        return;
      }
      if (stripped.indexOf(":") === -1) {
        return;
      }
      while (stack.length && indent <= stack[stack.length - 1].indent) {
        stack.pop();
      }
      parent = stack[stack.length - 1].value;
      if (!parent || typeof parent !== "object" || Array.isArray(parent)) {
        return;
      }
      parts = stripped.split(":");
      key = parts.shift().trim();
      value = parts.join(":").trim();
      if (value) {
        parent[key] = parseYamlScalar(value);
      } else {
        container = {};
        parent[key] = container;
        stack.push({ indent: indent, value: container });
      }
    });
    return root;
  }

  function parseYamlScalar(value) {
    value = String(value || "").trim();
    if ((value.charAt(0) === "\"" && value.charAt(value.length - 1) === "\"") || (value.charAt(0) === "'" && value.charAt(value.length - 1) === "'")) {
      return value.slice(1, -1);
    }
    if (/^(true|false)$/i.test(value)) {
      return value.toLowerCase() === "true";
    }
    if (/^\d+$/.test(value)) {
      return Number(value);
    }
    return value;
  }

  function projectExists(projectName) {
    return state.projects.some(function (project) {
      return project.name === projectName;
    });
  }

  function loadShotContexts(projectRoot, projectName) {
    var fs = nodeRequire("fs");
    var path = nodeRequire("path");
    var shotsRoot;
    var contexts = [];
    if (!fs || !path || !projectRoot) {
      return contexts;
    }

    shotsRoot = path.join(projectRoot, "shots");
    try {
      if (!fs.existsSync(shotsRoot)) {
        return contexts;
      }
      fs.readdirSync(shotsRoot, { withFileTypes: true }).forEach(function (episodeEntry) {
        var episodePath;
        if (!episodeEntry.isDirectory()) {
          return;
        }
        episodePath = path.join(shotsRoot, episodeEntry.name);
        fs.readdirSync(episodePath, { withFileTypes: true }).forEach(function (sequenceEntry) {
          var sequencePath;
          if (!sequenceEntry.isDirectory()) {
            return;
          }
          sequencePath = path.join(episodePath, sequenceEntry.name);
          fs.readdirSync(sequencePath, { withFileTypes: true }).forEach(function (shotEntry) {
            if (!shotEntry.isDirectory()) {
              return;
            }
            contexts.push({
              project: projectName,
              episode: normalizeToken(episodeEntry.name),
              sequence: normalizeToken(sequenceEntry.name),
              shot: normalizeToken(shotEntry.name),
              shotRoot: path.join(sequencePath, shotEntry.name).replace(/\\/g, "/")
            });
          });
        });
      });
    } catch (error) {
      return contexts;
    }
    return contexts;
  }

  async function init() {
    bindElements();
    loadState();
    state.projects = loadProjectsFromConfig(DEFAULT_CONFIG_ROOT);
    bindEvents();
    await applyLaunchContext();
    if (!state.filters.project && projectExists("STKB")) {
      state.filters.project = "STKB";
    }
    ensureSelectedManifestVisible();
    buildFromSelectedManifest(false);
    render();
    startPolling();
  }

  function bindElements() {
    [
      "manifestList",
      "manifestCount",
      "projectSelect",
      "episodeSelect",
      "sequenceSelect",
      "shotSelect",
      "manifestPath",
      "metadataRow",
      "outputRows",
      "queueRows",
      "aepRows",
      "aepFileName",
      "renderRows",
      "watchView",
      "queueView",
      "aepView",
      "renderView",
      "statusLine",
      "takeSummary",
      "replaceAllButton",
      "renderCompButton",
      "refreshButton",
      "refreshOutputsButton",
      "refreshManifestButton",
      "addManifestButton",
      "importManifestButton",
      "openButton",
      "saveButton",
      "publishButton",
      "buildButton",
      "copyManifestPathButton"
    ].forEach(function (id) {
      elements[id] = document.getElementById(id);
    });
  }

  function bindEvents() {
    ["project", "episode", "sequence", "shot"].forEach(function (key) {
      elements[key + "Select"].addEventListener("change", function (event) {
        state.filters[key] = event.target.value;
        normalizeFilterCascade(key);
        ensureSelectedManifestVisible();
        buildFromSelectedManifest(false);
      });
    });

    elements.importManifestButton.addEventListener("click", importManifest);
    elements.refreshManifestButton.addEventListener("click", function () {
      clearAepCache();
      refreshManifestCatalog(true);
    });

    elements.openButton.addEventListener("click", handleOpenButton);
    elements.saveButton.addEventListener("click", handleSaveButton);
    elements.publishButton.addEventListener("click", handlePublishButton);
    elements.buildButton.addEventListener("click", handleBuildButton);
    elements.refreshButton.addEventListener("click", function () {
      clearAepCache();
      refreshManifestCatalog(false);
      refreshOutputStatus();
    });
    elements.refreshOutputsButton.addEventListener("click", function () {
      clearAepCache();
      refreshOutputStatus();
    });
    elements.replaceAllButton.addEventListener("click", replaceAll);
    elements.renderCompButton.addEventListener("click", renderFinalComp);
    elements.copyManifestPathButton.addEventListener("click", copyManifestPath);

    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (button) {
      button.addEventListener("click", function () {
        state.activeTab = button.getAttribute("data-tab");
        renderTabs();
      });
    });
  }

  function loadState() {
    try {
      var stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      if (stored.manifests && stored.manifests.length) {
        state.manifests = stored.manifests.filter(function (manifest) {
          return !(manifest.project === "Showcase" && String(manifest.path || "").indexOf("/Projects/Showcase/exports/project_v") !== -1);
        });
      }
      if (stored.selectedManifestId) {
        state.selectedManifestId = stored.selectedManifestId;
      }
      if (stored.filters) {
        state.filters = Object.assign(state.filters, stored.filters);
        if (state.filters.project === "Showcase") {
          state.filters.project = "";
        }
      }
      if (stored.nextSaveVersions) {
        state.nextSaveVersions = stored.nextSaveVersions;
      }
    } catch (error) {
      setStatus("Could not load saved panel state");
    }
  }

  async function applyLaunchContext() {
    var context = {};
    var result = await window.SmartCEPBridge.callHost("getLaunchContext", undefined, "");
    if (result) {
      try {
        context = JSON.parse(result);
      } catch (error) {
        context = {};
      }
    }
    context = mergeContext(readNodeEnvContext(), context);
    state.launchContext = context;

    if (context.configDir) {
      reloadProjectsFromConfigDir(context.configDir);
    }
    if (context.project && projectExists(context.project)) {
      state.filters.project = context.project;
    }
    if (context.episode) {
      state.filters.episode = normalizeToken(context.episode);
    }
    if (context.sequence) {
      state.filters.sequence = normalizeToken(context.sequence);
    }
    if (context.shot) {
      state.filters.shot = normalizeToken(context.shot);
    }
    if (context.manifest) {
      importManifestFromPath(context.manifest, context);
    }
    persistState();
  }

  function readNodeEnvContext() {
    var proc = typeof process !== "undefined" ? process : (window.cep_node && window.cep_node.process ? window.cep_node.process : null);
    var env = proc && proc.env ? proc.env : {};
    return {
      source: env.SMART_CONTEXT_SOURCE || "",
      project: env.SMART_PROJECT || env.PROJECT_NAME || "",
      projectRoot: env.SMART_PROJECT_ROOT || env.PROJECT_ROOT || "",
      configDir: env.SMART_PROJECT_CONFIG_DIR || env.PROJECT_CONFIG_DIR || "",
      episode: env.SMART_EPISODE || env.EPISODE || "",
      sequence: env.SMART_SEQUENCE || env.SEQUENCE || env.SEQ || "",
      shot: env.SMART_SHOT || env.SHOT || "",
      manifest: env.SMART_AE_MANIFEST || ""
    };
  }

  function mergeContext(base, override) {
    var merged = Object.assign({}, base || {});
    Object.keys(override || {}).forEach(function (key) {
      if (override[key] !== undefined && override[key] !== null && String(override[key]) !== "") {
        merged[key] = override[key];
      }
    });
    return merged;
  }

  function reloadProjectsFromConfigDir(configDir) {
    var path = nodeRequire("path");
    var root;
    if (!path || !configDir) {
      return;
    }
    root = path.dirname(String(configDir).replace(/\\/g, "/"));
    state.projects = loadProjectsFromConfig(root);
  }

  function importManifestFromPath(manifestPath, context) {
    var fs = nodeRequire("fs");
    if (!fs || !manifestPath) {
      return;
    }
    try {
      if (!fs.existsSync(manifestPath)) {
        return;
      }
      ingestManifestResult({
        path: manifestPath,
        data: JSON.parse(fs.readFileSync(manifestPath, "utf8")),
        context: context || {}
      });
    } catch (error) {
      setStatus("Could not read launch manifest: " + error.message);
    }
  }

  function refreshManifestCatalog(showStatus) {
    var manifests = collectReviewBuildManifests();
    var added = 0;
    manifests.forEach(function (manifest) {
      if (!state.manifests.some(function (item) { return item.id === manifest.id; })) {
        added += 1;
      }
      upsertManifest(manifest);
    });
    ensureSelectedManifestVisible();
    buildFromSelectedManifest(false);
    persistState();
    render();
    if (showStatus) {
      setStatus(manifests.length ? "Manifest list refreshed: " + manifests.length + " build file" + (manifests.length === 1 ? "" : "s") + " found" : "No review build manifests found for the selected shot");
    }
    return added;
  }

  function collectReviewBuildManifests() {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var manifests = [];
    var maxFiles = 200;
    if (!fs || !pathModule) {
      return manifests;
    }

    selectedShotRoots().forEach(function (shotRoot) {
      var shotContext = shotContextForRoot(shotRoot) || {};
      ["output", "publish"].forEach(function (area) {
        var reviewRoot = pathModule.join(shotRoot, area, "review");
        if (!fs.existsSync(reviewRoot)) {
          return;
        }
        try {
          fs.readdirSync(reviewRoot, { withFileTypes: true }).forEach(function (deptEntry) {
            var buildRoot;
            if (!deptEntry.isDirectory() || manifests.length >= maxFiles) {
              return;
            }
            buildRoot = pathModule.join(reviewRoot, deptEntry.name, "review_build");
            scanReviewBuildRoot(buildRoot, Object.assign({}, shotContext, { department: deptEntry.name, area: area }), manifests, maxFiles);
          });
        } catch (error) {
          return;
        }
      });
    });

    manifests.sort(function (a, b) {
      return String(b.exportedAt || "").localeCompare(String(a.exportedAt || ""));
    });
    return manifests;
  }

  function scanReviewBuildRoot(root, context, manifests, maxFiles) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    if (!fs || !pathModule || !root || !fs.existsSync(root)) {
      return;
    }

    function walk(dir, depth) {
      var entries;
      if (manifests.length >= maxFiles || depth > 5) {
        return;
      }
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch (error) {
        return;
      }
      entries.forEach(function (entry) {
        var fullPath;
        var stat;
        var data;
        if (manifests.length >= maxFiles) {
          return;
        }
        fullPath = pathModule.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(fullPath, depth + 1);
          return;
        }
        if (!entry.isFile() || !/\.json$/i.test(entry.name)) {
          return;
        }
        try {
          data = JSON.parse(fs.readFileSync(fullPath, "utf8"));
        } catch (error) {
          return;
        }
        if (!data || data.schema !== "smart_render_ae_build") {
          return;
        }
        try {
          stat = fs.statSync(fullPath);
          data.created_at = data.created_at || formatDate(stat.mtime);
        } catch (error) {}
        manifests.push(normalizeManifest(data, fullPath.replace(/\\/g, "/"), context));
      });
    }

    walk(root, 0);
  }

  function shotContextForRoot(shotRoot) {
    var normalized = String(shotRoot || "").replace(/\\/g, "/").toLowerCase();
    var found = null;
    state.projects.some(function (project) {
      return (project.shots || []).some(function (context) {
        if (String(context.shotRoot || "").replace(/\\/g, "/").toLowerCase() === normalized) {
          found = context;
          return true;
        }
        return false;
      });
    });
    return found;
  }

  function persistState() {
    var compactState = {
      manifests: state.manifests,
      selectedManifestId: state.selectedManifestId,
      filters: state.filters,
      nextSaveVersions: state.nextSaveVersions
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(compactState));
  }

  function getSelectedManifest() {
    return state.manifests.filter(function (manifest) {
      return manifest.id === state.selectedManifestId;
    })[0] || null;
  }

  function getContext(source, fallback) {
    fallback = fallback || {};
    var pathHint = [
      source.path,
      source.sourcePath,
      source.outputPath,
      source.name,
      source.id
    ].join("/");

    return {
      project: firstValue(source.project, source.projectName, source.show, fallback.project, parseProject(pathHint)),
      episode: normalizeToken(firstValue(source.episode, source.ep, fallback.episode, parseToken(pathHint, "episode"))),
      sequence: normalizeToken(firstValue(source.sequence, source.seq, fallback.sequence, parseToken(pathHint, "sequence"))),
      shot: normalizeToken(firstValue(source.shot, source.sh, fallback.shot, parseToken(pathHint, "shot")))
    };
  }

  function firstValue() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i] !== undefined && arguments[i] !== null && String(arguments[i]) !== "") {
        return String(arguments[i]);
      }
    }
    return "";
  }

  function normalizeToken(value) {
    return String(value || "").trim().toLowerCase();
  }

  function parseProject(value) {
    var match = String(value || "").match(/[\/\\]Projects[\/\\]([^\/\\]+)/i);
    return match ? match[1] : "";
  }

  function parseToken(value, kind) {
    var patternMap = {
      episode: /(?:^|[\/\\_\-.])(ep\d+|ep##)(?:$|[\/\\_\-.])/i,
      sequence: /(?:^|[\/\\_\-.])((?:sq|seq)\d+)(?:$|[\/\\_\-.])/i,
      shot: /(?:^|[\/\\_\-.])((?:sh|shot)\d+)(?:$|[\/\\_\-.])/i
    };
    var match = String(value || "").match(patternMap[kind]);
    return match ? match[1] : "";
  }

  function matchesFilters(record) {
    return ["project", "episode", "sequence", "shot"].every(function (key) {
      return !state.filters[key] || String(record[key] || "") === state.filters[key];
    });
  }

  function manifestMatchesFilters(manifest) {
    var context = getContext(manifest);
    if (matchesFilters(context)) {
      return true;
    }
    return normalizeItems(manifest).some(matchesFilters);
  }

  function getVisibleManifests() {
    return state.manifests.filter(function (manifest) {
      return manifestMatchesFilters(manifest);
    }).sort(function (a, b) {
      var timeDiff = manifestSortTime(b) - manifestSortTime(a);
      return timeDiff || naturalCompare(a.name, b.name);
    });
  }

  function manifestSortTime(manifest) {
    var fs = nodeRequire("fs");
    var stat;
    var parsed;
    try {
      if (fs && manifest.path && fs.existsSync(manifest.path)) {
        stat = fs.statSync(manifest.path);
        return stat.mtime ? stat.mtime.getTime() : 0;
      }
    } catch (error) {}
    parsed = Date.parse(manifest.exportedAt || manifest.createdAt || "");
    return isNaN(parsed) ? 0 : parsed;
  }

  function getVisibleRows() {
    return state.rows.filter(matchesFilters);
  }

  function ensureSelectedManifestVisible() {
    var visible = getVisibleManifests();
    var selectedIsVisible = visible.some(function (manifest) {
      return manifest.id === state.selectedManifestId;
    });
    if (!selectedIsVisible && visible.length) {
      state.selectedManifestId = visible[0].id;
    } else if (!selectedIsVisible) {
      state.selectedManifestId = "";
      state.rows = [];
    }
    persistState();
  }

  function setFiltersFromManifest(manifest) {
    var context = getContext(manifest);
    ["project", "episode", "sequence", "shot"].forEach(function (key) {
      state.filters[key] = context[key] || "";
    });
  }

  function normalizeFilterCascade(changedKey) {
    var order = ["project", "episode", "sequence", "shot"];
    var changedIndex = order.indexOf(changedKey);
    for (var i = changedIndex + 1; i < order.length; i += 1) {
      state.filters[order[i]] = "";
    }
    renderFilters();
  }

  function renderFilters() {
    var projectOptions = collectProjectOptions();
    setSelectOptions(elements.projectSelect, projectOptions, state.filters.project, "All");

    var episodeOptions = collectOptions("episode", { project: state.filters.project });
    if (state.filters.episode && episodeOptions.indexOf(state.filters.episode) === -1) {
      state.filters.episode = "";
    }
    setSelectOptions(elements.episodeSelect, episodeOptions, state.filters.episode, "All");

    var sequenceOptions = collectOptions("sequence", {
      project: state.filters.project,
      episode: state.filters.episode
    });
    if (state.filters.sequence && sequenceOptions.indexOf(state.filters.sequence) === -1) {
      state.filters.sequence = "";
    }
    setSelectOptions(elements.sequenceSelect, sequenceOptions, state.filters.sequence, "All");

    var shotOptions = collectOptions("shot", {
      project: state.filters.project,
      episode: state.filters.episode,
      sequence: state.filters.sequence
    });
    if (state.filters.shot && shotOptions.indexOf(state.filters.shot) === -1) {
      state.filters.shot = "";
    }
    setSelectOptions(elements.shotSelect, shotOptions, state.filters.shot, "All");
  }

  function collectProjectOptions() {
    var values = {};
    state.projects.forEach(function (project) {
      if (project.name) {
        values[project.name] = true;
      }
    });
    collectOptions("project", {}).forEach(function (project) {
      values[project] = true;
    });
    return Object.keys(values).sort(naturalCompare);
  }

  function collectOptions(key, filters) {
    var values = {};
    collectShotContextOptions(key, filters).forEach(function (value) {
      values[value] = true;
    });
    state.manifests.forEach(function (manifest) {
      [getContext(manifest)].concat(normalizeItems(manifest)).forEach(function (context) {
        var matches = Object.keys(filters).every(function (filterKey) {
          return !filters[filterKey] || context[filterKey] === filters[filterKey];
        });
        if (matches && context[key]) {
          values[context[key]] = true;
        }
      });
    });
    return Object.keys(values).sort(naturalCompare);
  }

  function collectShotContextOptions(key, filters) {
    var values = {};
    state.projects.forEach(function (project) {
      (project.shots || []).forEach(function (context) {
        var matches = Object.keys(filters).every(function (filterKey) {
          return !filters[filterKey] || context[filterKey] === filters[filterKey];
        });
        if (matches && context[key]) {
          values[context[key]] = true;
        }
      });
    });
    return Object.keys(values);
  }

  function naturalCompare(a, b) {
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
  }

  function setSelectOptions(select, values, selectedValue, allLabel) {
    var previousValue = select.value;
    select.innerHTML = "";
    select.appendChild(makeOption("", allLabel));
    values.forEach(function (value) {
      select.appendChild(makeOption(value, value));
    });
    select.value = selectedValue;
    if (select.value !== selectedValue) {
      select.value = "";
    }
    if (previousValue !== select.value) {
      select.setAttribute("data-changed", "true");
    }
  }

  function makeOption(value, label) {
    var option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    return option;
  }

  function buildFromSelectedManifest(showStatus) {
    var manifest = getSelectedManifest();
    state.rows = manifest ? normalizeItems(manifest) : [];
    if (showStatus) {
      setStatus("Loaded " + state.rows.length + " output item" + (state.rows.length === 1 ? "" : "s") + " from manifest");
    }
    render();
    refreshOutputStatus();
  }

  function normalizeItems(manifest) {
    var manifestContext = getContext(manifest);
    var rawItems = collectManifestItems(manifest);
    return rawItems.map(function (item, index) {
      var itemContext = getContext(item, manifestContext);
      var outputPath = resolveManifestPath(manifest, item.outputPath || item.output || item.path || item.latestPath || item.image_sequence || item.first_frame_file || "");
      var sourcePath = resolveManifestPath(manifest, item.sourcePath || item.currentPath || item.aePath || item.footagePath || "");
      var versionInfo = extractVersionTake(sourcePath || manifest.path || outputPath);
      var latestInfo = extractVersionTake(outputPath || sourcePath);
      var version = item.version || versionInfo.version || manifest.version || "";
      var latestVersion = item.latestVersion || item.outputVersion || latestInfo.version || version;
      var take = item.take || versionInfo.take || latestInfo.take || "";
      var latestTake = item.latestTake || item.outputTake || latestAvailableTake(outputPath || manifest.path) || latestInfo.take || take;
      var latestOutputPath = latestTake && take && latestTake !== take ? replaceTakeInPath(outputPath, latestTake) : outputPath;
      var latestFirstFrame = latestTake && take && latestTake !== take ? replaceTakeInPath(resolveManifestPath(manifest, item.first_frame_file || ""), latestTake) : resolveManifestPath(manifest, item.first_frame_file || "");
      var checkPath = resolveManifestPath(manifest, item.checkPath || "") || latestFirstFrame || latestOutputPath || outputPath;
      var currentLabel = versionTakeLabel(version, take);
      var latestLabel = versionTakeLabel(latestVersion, latestTake);
      var status = item.status || inferStatus(currentLabel, latestLabel, outputPath);
      return {
        id: item.id || item.output_id || item.layer || item.name || "item-" + index,
        name: item.name || item.layer || item.output || basename(outputPath) || basename(sourcePath) || "item-" + (index + 1),
        sourcePath: sourcePath,
        outputPath: latestOutputPath,
        currentOutputPath: outputPath,
        checkPath: checkPath,
        version: version,
        latestVersion: latestVersion,
        take: take,
        latestTake: latestTake,
        currentLabel: currentLabel,
        latestLabel: latestLabel,
        status: status,
        exists: item.exists,
        modified: item.modified || "",
        size: item.size || 0,
        project: itemContext.project,
        episode: itemContext.episode,
        sequence: itemContext.sequence,
        shot: itemContext.shot
      };
    });
  }

  function collectManifestItems(manifest) {
    var items = [];
    ["items", "outputs", "assets", "renders"].forEach(function (key) {
      items = items.concat(asArray(manifest[key] || []));
    });
    asArray(manifest.layers || []).forEach(function (layer) {
      items.push(Object.assign({ status: "ready" }, layer));
    });
    if (manifest.slate && (manifest.slate.image_sequence || manifest.slate.first_frame_file)) {
      items.push(Object.assign({ id: "slate", name: "slate", layer: "slate", status: "ready" }, manifest.slate));
    }
    return items;
  }

  function resolveManifestPath(manifest, value) {
    var path = String(value || "");
    var base = String(manifest.package_root || manifest.packageRoot || manifest.publishRoot || "");
    if (!path) {
      return "";
    }
    if (/^[a-zA-Z]:[\/\\]/.test(path) || /^\/\//.test(path) || path.charAt(0) === "/") {
      return cleanFilePath(path);
    }
    return cleanFilePath(base ? joinPath(base, path) : path);
  }

  function joinPath(base, child) {
    return String(base || "").replace(/[\/\\]+$/, "") + "/" + String(child || "").replace(/^[\/\\]+/, "");
  }

  function cleanFilePath(value) {
    var pathModule = nodeRequire("path");
    var text = String(value || "").replace(/\\/g, "/");
    if (pathModule) {
      try {
        return pathModule.normalize(text).replace(/\\/g, "/");
      } catch (error) {
        return text;
      }
    }
    return text.replace(/\/[^\/]+\/\.\.\//g, "/");
  }

  function asArray(value) {
    if (Array.isArray(value)) {
      return value;
    }
    if (!value || typeof value !== "object") {
      return [];
    }
    return Object.keys(value).map(function (key) {
      var item = value[key];
      if (item && typeof item === "object") {
        item.id = item.id || key;
        item.name = item.name || key;
        return item;
      }
      return {
        id: key,
        name: key,
        outputPath: String(item || "")
      };
    });
  }

  function inferStatus(version, latestVersion, outputPath) {
    if (!outputPath) {
      return "missing";
    }
    if (version && latestVersion && version !== latestVersion) {
      return "replace";
    }
    return "ready";
  }

  function extractVersion(value) {
    return extractVersionTake(value).version;
  }

  function extractVersionTake(value) {
    var text = String(value || "");
    var normalized = text.replace(/\\/g, "/");
    var parts = normalized.split("/");
    var versionMatch = text.match(/(?:^|[_\-.\/\\])(v\d{2,5})(?:$|[_\-.\/\\])/i);
    var takeMatch = text.match(/(?:^|[_\-.\/\\])((?:t|take)\d{1,5})(?:$|[_\-.\/\\])/i);
    var version = versionMatch ? versionMatch[1].toLowerCase() : "";
    var take = takeMatch ? normalizeTake(takeMatch[1]) : "";
    parts.forEach(function (part, index) {
      if (!version && /^v\d{2,5}$/i.test(part)) {
        version = part.toLowerCase();
      }
      if (!take && /^v\d{2,5}$/i.test(part) && parts[index + 1] && /^\d{1,5}$/.test(parts[index + 1])) {
        take = normalizeTake(parts[index + 1]);
      }
    });
    return {
      version: version,
      take: take
    };
  }

  function normalizeTake(value) {
    var match = String(value || "").match(/(?:t|take)?(\d{1,5})/i);
    if (!match) {
      return "";
    }
    return "t" + match[1].padStart(3, "0");
  }

  function versionTakeLabel(version, take) {
    if (version && take) {
      return version + "-" + shortTake(take);
    }
    return version || take || "";
  }

  function shortTake(value) {
    var match = String(value || "").match(/t0*(\d+)/i);
    return match ? match[1].padStart(2, "0") : "";
  }

  function latestAvailableTake(path) {
    var fs = nodeRequire("fs");
    var info = versionTakeFolderInfo(path);
    var max = 0;
    if (!fs || !info.versionDir) {
      return "";
    }
    try {
      if (!fs.existsSync(info.versionDir)) {
        return "";
      }
      fs.readdirSync(info.versionDir, { withFileTypes: true }).forEach(function (entry) {
        var takeNumber;
        if (!entry.isDirectory()) {
          return;
        }
        takeNumber = takeFolderNumber(entry.name);
        if (takeNumber > max) {
          max = takeNumber;
        }
      });
    } catch (error) {
      return "";
    }
    return max ? "t" + String(max).padStart(3, "0") : "";
  }

  function replaceTakeInPath(path, take) {
    var info = versionTakeFolderInfo(path);
    var replacement = takeFolderLabel(take, info.takePart);
    if (!info.takePart || !replacement) {
      return path;
    }
    return info.parts.map(function (part, index) {
      if (index === info.takeIndex) {
        return replacement;
      }
      return replaceTakeTokenInName(part, info.takePart, take);
    }).join("/");
  }

  function replaceTakeTokenInName(name, previous, take) {
    var oldNumber = takeFolderNumber(previous);
    var newNumber = takeFolderNumber(take);
    var newShort;
    var result = String(name || "");
    if (!oldNumber || !newNumber) {
      return result;
    }
    newShort = String(newNumber).padStart(String(previous || "").replace(/\D/g, "").length || 2, "0");
    result = result.replace(new RegExp("([_\\-.])0*" + oldNumber + "(?=[_\\-.])", "g"), "$1" + newShort);
    result = result.replace(new RegExp("([_\\-.])t0*" + oldNumber + "(?=[_\\-.])", "ig"), "$1t" + String(newNumber).padStart(3, "0"));
    result = result.replace(new RegExp("([_\\-.])take0*" + oldNumber + "(?=[_\\-.])", "ig"), "$1take" + String(newNumber).padStart(3, "0"));
    return result;
  }

  function versionTakeFolderInfo(path) {
    var normalized = String(path || "").replace(/\\/g, "/");
    var parts = normalized.split("/");
    var info = { parts: parts, versionIndex: -1, takeIndex: -1, takePart: "", versionDir: "" };
    parts.forEach(function (part, index) {
      if (info.versionIndex === -1 && /^v\d{2,5}$/i.test(part)) {
        info.versionIndex = index;
        if (parts[index + 1] && isTakeFolder(parts[index + 1])) {
          info.takeIndex = index + 1;
          info.takePart = parts[index + 1];
          info.versionDir = parts.slice(0, index + 1).join("/");
        }
      }
    });
    return info;
  }

  function isTakeFolder(value) {
    return /^\d{1,5}$/.test(String(value || "")) || /^(?:t|take)\d{1,5}$/i.test(String(value || ""));
  }

  function takeFolderNumber(value) {
    var match = String(value || "").match(/(?:t|take)?0*(\d{1,5})/i);
    return match ? Number(match[1]) : 0;
  }

  function takeFolderLabel(take, previous) {
    var number = takeFolderNumber(take);
    if (!number) {
      return "";
    }
    if (/^\d+$/.test(String(previous || ""))) {
      return String(number).padStart(String(previous).length, "0");
    }
    if (/^take/i.test(String(previous || ""))) {
      return "take" + String(number).padStart(String(previous).replace(/^take/i, "").length || 3, "0");
    }
    return "t" + String(number).padStart(String(previous).replace(/^t/i, "").length || 3, "0");
  }

  function basename(path) {
    return String(path || "").split(/[\/\\]/).pop();
  }

  function collectAepFiles() {
    var key = [
      state.selectedManifestId,
      state.filters.project,
      state.filters.episode,
      state.filters.sequence,
      state.filters.shot
    ].join("|");
    var rows = {};
    var manifest = getSelectedManifest();
    var roots = selectedShotRoots();
    var now = Date.now();

    if (state.aepFilesCacheKey === key && now - state.aepFilesCacheAt < 15000) {
      return state.aepFiles;
    }

    if (manifest) {
      addManifestAepRows(rows, manifest);
    }

    collectWorkAepRoots().forEach(function (root) {
      scanAepFiles(root, "Work").forEach(function (row) {
        rows[row.path] = row;
      });
    });

    roots.forEach(function (root) {
      scanAepFiles(joinPath(root, "output/review/" + currentDepartment()), "Output").forEach(function (row) {
        if (!rows[row.path]) {
          rows[row.path] = row;
        }
      });
    });

    state.aepFiles = Object.keys(rows).map(function (path) {
      return rows[path];
    }).sort(function (a, b) {
      return (b.modifiedTime || 0) - (a.modifiedTime || 0) || naturalCompare(a.path, b.path);
    });
    state.aepFilesCacheKey = key;
    state.aepFilesCacheAt = now;
    return state.aepFiles;
  }

  function clearAepCache() {
    state.aepFiles = [];
    state.aepFilesCacheKey = "";
    state.aepFilesCacheAt = 0;
  }

  function addManifestAepRows(rows, manifest) {
    var packageRoot = manifest.package_root || manifest.packageRoot || manifest.publishRoot || "";
    var candidates = [
      manifest.template_project,
      manifest.templateProject,
      manifest.ae_project,
      manifest.aeProject
    ];
    if (manifest.ae && typeof manifest.ae === "object") {
      candidates.push(manifest.ae.project);
    }
    candidates.forEach(function (candidate) {
      var path = resolveManifestPath(manifest, candidate || "");
      if (path && /\.aep$/i.test(path)) {
        rows[path] = inspectAepFile(path, "Manifest");
      }
    });
    if (packageRoot) {
      scanAepFiles(joinPath(packageRoot, "ae"), "Output").forEach(function (row) {
        row.source = row.source || "Output";
        rows[row.path] = row;
      });
    }
  }

  function collectWorkAepRoots() {
    return selectedShotRoots().map(function (root) {
      return workAepRoot(root, currentDepartment());
    });
  }

  function workAepRoot(shotRoot, department) {
    return joinPath(shotRoot, "work/" + (department || "anim") + "/ae/main");
  }

  function selectedShotRoots() {
    var roots = {};
    state.projects.forEach(function (project) {
      (project.shots || []).forEach(function (context) {
        if (matchesFilters(context) && context.shotRoot) {
          roots[context.shotRoot] = true;
        }
      });
    });
    return Object.keys(roots);
  }

  function selectedShotContext() {
    var found = null;
    state.projects.some(function (project) {
      return (project.shots || []).some(function (context) {
        if (matchesFilters(context)) {
          found = context;
          return true;
        }
        return false;
      });
    });
    return found;
  }

  function currentDepartment() {
    var manifest = getSelectedManifest();
    var text = [
      manifest ? manifest.path : "",
      manifest ? manifest.package_root : "",
      state.selectedAepPath
    ].join("/");
    var match = text.replace(/\\/g, "/").match(/\/(?:work|output|publish)\/(?:review\/)?([^\/]+)\/(?:ae|v\d+)/i);
    return match ? match[1] : "anim";
  }

  function currentWorkContext() {
    var shot = selectedShotContext();
    var department = currentDepartment();
    var tokens;
    if (!shot || !shot.shotRoot) {
      return null;
    }
    tokens = shotTokensFromRoot(shot.shotRoot);
    return {
      shotRoot: shot.shotRoot,
      department: department,
      root: workAepRoot(shot.shotRoot, department),
      prefix: tokens.episode + "_" + tokens.sequence + "_" + tokens.shot + "_" + department
    };
  }

  function shotTokensFromRoot(shotRoot) {
    var parts = String(shotRoot || "").replace(/\\/g, "/").split("/");
    var index = parts.map(function (part) { return part.toLowerCase(); }).lastIndexOf("shots");
    return {
      episode: parts[index + 1] || state.filters.episode || "",
      sequence: parts[index + 2] || state.filters.sequence || "",
      shot: parts[index + 3] || state.filters.shot || ""
    };
  }

  function scanAepFiles(root, source) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var rows = [];
    var maxRows = 200;
    if (!fs || !pathModule || !root) {
      return rows;
    }

    function walk(dir, depth) {
      var entries;
      if (rows.length >= maxRows || depth > 8) {
        return;
      }
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch (error) {
        return;
      }
      entries.forEach(function (entry) {
        var fullPath;
        if (rows.length >= maxRows) {
          return;
        }
        fullPath = pathModule.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(fullPath, depth + 1);
        } else if (/\.aep$/i.test(entry.name)) {
          rows.push(inspectAepFile(fullPath.replace(/\\/g, "/"), source || "Work"));
        }
      });
    }

    walk(root, 0);
    return rows;
  }

  function inspectAepFile(path, source) {
    var fs = nodeRequire("fs");
    var exists = false;
    var modified = "";
    var modifiedTime = 0;
    try {
      if (fs && fs.existsSync(path)) {
        exists = true;
        modifiedTime = fs.statSync(path).mtime.getTime();
        modified = formatDate(new Date(modifiedTime));
      }
    } catch (error) {
      exists = false;
    }
    return {
      path: String(path || "").replace(/\\/g, "/"),
      modified: modified,
      modifiedTime: modifiedTime,
      source: source || "",
      exists: exists,
      versionLabel: aepVersionLabel(path)
    };
  }

  function aepVersionLabel(path) {
    var normalized = String(path || "").replace(/\\/g, "/");
    var parts = normalized.split("/");
    var fileMatch = basename(path).match(/_v(\d{3,5})_(\d{2,5})\.aep$/i);
    var version = "";
    var take = "";
    var department = "";
    if (fileMatch) {
      return "v" + fileMatch[1] + "/" + fileMatch[2];
    }
    parts.forEach(function (part, index) {
      if (/^v\d+/i.test(part)) {
        version = part;
        take = parts[index + 1] && /^t\d+/i.test(parts[index + 1]) ? parts[index + 1] : "";
        department = parts[index - 1] || "";
      }
    });
    if (version && take) {
      return department ? department + " " + version + "/" + take : version + "/" + take;
    }
    if (version) {
      return department ? department + " " + version : version;
    }
    return parentFolderName(path);
  }

  function parentFolderName(path) {
    var parts = String(path || "").replace(/\\/g, "/").split("/");
    return parts.length > 1 ? parts[parts.length - 2] : "";
  }

  function formatDate(date) {
    function pad(value) {
      return String(value).padStart(2, "0");
    }
    if (!date || isNaN(date.getTime())) {
      return "";
    }
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  function render() {
    renderFilters();
    renderManifestList();
    renderSummary();
    renderRows();
    renderTabs();
  }

  function renderManifestList() {
    var manifests = getVisibleManifests();

    elements.manifestList.innerHTML = "";
    manifests.forEach(function (manifest) {
      var button = document.createElement("button");
      button.className = "manifest-item" + (manifest.id === state.selectedManifestId ? " is-active" : "");
      button.innerHTML = [
        "<span>",
        '<span class="manifest-name"></span>',
        '<span class="manifest-date"></span>',
        "</span>",
        '<span class="dot ' + summarizeManifestStatus(manifest) + '"></span>'
      ].join("");
      button.querySelector(".manifest-name").textContent = manifest.name;
      button.querySelector(".manifest-date").textContent = manifest.exportedAt || "";
      button.addEventListener("click", function () {
        state.selectedManifestId = manifest.id;
        setFiltersFromManifest(manifest);
        persistState();
        buildFromSelectedManifest(false);
      });
      elements.manifestList.appendChild(button);
    });

    elements.manifestCount.textContent = state.manifests.length + " manifest" + (state.manifests.length === 1 ? "" : "s");
  }

  function summarizeManifestStatus(manifest) {
    var items = normalizeItems(manifest);
    if (items.some(function (item) { return item.status === "missing"; })) {
      return "missing";
    }
    if (items.some(function (item) { return item.status === "updated"; })) {
      return "updated";
    }
    return "ready";
  }

  function renderSummary() {
    var manifest = getSelectedManifest();
    elements.manifestPath.textContent = manifest ? manifest.path : "No manifest selected";
    elements.metadataRow.innerHTML = "";

    if (!manifest) {
      return;
    }

    [
      ["Exported", manifest.exportedAt || "-"],
      ["Renderer", manifest.renderer || "smart render"],
      ["Items", String(normalizeItems(manifest).length)],
      ["Version", manifest.version || "-"]
    ].forEach(function (entry) {
      var pill = document.createElement("span");
      pill.className = "pill";
      pill.innerHTML = '<span class="pill-label"></span><span></span>';
      pill.children[0].textContent = entry[0];
      pill.children[1].textContent = entry[1];
      elements.metadataRow.appendChild(pill);
    });
  }

  function renderRows() {
    var aepFiles;
    elements.outputRows.innerHTML = "";
    elements.queueRows.innerHTML = "";
    elements.aepRows.innerHTML = "";
    elements.renderRows.innerHTML = "";

    getVisibleRows().forEach(function (row) {
      elements.outputRows.appendChild(renderOutputRow(row));
      elements.queueRows.appendChild(renderQueueRow(row));
    });
    renderTakeSummary();

    aepFiles = collectAepFiles();
    if (state.selectedAepPath && !aepFiles.some(function (row) { return row.path === state.selectedAepPath; })) {
      state.selectedAepPath = "";
    }
    aepFiles.forEach(function (row) {
      elements.aepRows.appendChild(renderAepRow(row));
    });
    renderSelectedAepFileName();
    elements.renderRows.appendChild(renderRenderQueueRow(renderQueueContext()));
  }

  function renderOutputRow(row) {
    var tr = document.createElement("tr");
    tr.innerHTML = [
      '<td><div class="path-cell"><span class="folder-icon"></span><span></span></div></td>',
      "<td></td>",
      "<td></td>",
      '<td><span class="status"><span class="dot"></span><span></span></span></td>',
      '<td><button class="row-action" title="Replace this item"></button></td>'
    ].join("");
    tr.querySelector(".path-cell span:last-child").textContent = row.outputPath || row.name;
    tr.children[1].textContent = row.version || "-";
    tr.children[2].textContent = row.latestVersion || "-";
    setStatusCell(tr.children[3].querySelector(".status"), row.status);
    tr.querySelector(".row-action").addEventListener("click", function () {
      replaceRows([row]);
    });
    return tr;
  }

  function renderQueueRow(row) {
    var tr = document.createElement("tr");
    tr.innerHTML = [
      "<td></td>",
      "<td></td>",
      "<td></td>",
      '<td><span class="status"><span class="dot"></span><span></span></span></td>',
      '<td><button class="row-action" title="Replace this item"></button></td>'
    ].join("");
    tr.children[0].textContent = row.name || basename(row.sourcePath) || "-";
    tr.children[1].textContent = row.currentLabel || row.version || "-";
    tr.children[2].textContent = row.latestLabel || row.latestVersion || "-";
    setStatusCell(tr.children[3].querySelector(".status"), row.status);
    tr.querySelector(".row-action").addEventListener("click", function () {
      replaceRows([row]);
    });
    return tr;
  }

  function renderAepRow(row) {
    var tr = document.createElement("tr");
    var filename = basename(row.path);
    tr.innerHTML = [
      '<td><div class="path-cell aep-name"><span></span></div></td>',
      "<td></td>",
      "<td></td>",
      '<td><span class="status"><span class="dot"></span><span></span></span></td>',
      '<td><button class="row-action" title="Open AEP"></button></td>'
    ].join("");
    tr.className = row.path === state.selectedAepPath ? "is-selected" : "";
    tr.title = row.path;
    tr.querySelector(".path-cell span").textContent = filename || row.path;
    tr.children[1].textContent = row.versionLabel || "-";
    tr.children[2].textContent = row.modified || "-";
    setStatusCell(tr.children[3].querySelector(".status"), row.exists === false ? "missing" : "ready");
    tr.addEventListener("click", function () {
      selectAepFile(row.path);
    });
    tr.querySelector(".row-action").addEventListener("click", function () {
      openAepProject(row.path);
    });
    return tr;
  }

  function selectAepFile(path) {
    state.selectedAepPath = path || "";
    renderRows();
  }

  function renderSelectedAepFileName() {
    if (!elements.aepFileName) {
      return;
    }
    elements.aepFileName.value = state.selectedAepPath ? selectedAepDisplayName() : "";
    elements.aepFileName.title = state.selectedAepPath || "";
  }

  function selectedAepDisplayName() {
    var row = (state.aepFiles || []).filter(function (item) {
      return item.path === state.selectedAepPath;
    })[0];
    var filename = basename(state.selectedAepPath);
    if (row && row.versionLabel) {
      return row.versionLabel + " / " + filename;
    }
    return filename;
  }

  function renderRenderQueueRow(context) {
    var tr = document.createElement("tr");
    tr.innerHTML = [
      "<td></td>",
      "<td></td>",
      "<td></td>",
      '<td><span class="status"><span class="dot"></span><span></span></span></td>'
    ].join("");
    tr.children[0].textContent = context.compName || "-";
    tr.children[1].textContent = context.outputPath || "Select a shot";
    tr.children[1].title = context.outputPath || "";
    tr.children[2].textContent = context.outputModuleTemplate || "-";
    setStatusCell(tr.children[3].querySelector(".status"), context.outputPath ? "ready" : "missing");
    return tr;
  }

  function renderQueueContext() {
    var config = currentAeRenderConfig();
    var finalComp = config.final_comp || {};
    var queue = config.render_queue || {};
    var shot = selectedShotContext() || {};
    var tokens = shot.shotRoot ? shotTokensFromRoot(shot.shotRoot) : {
      episode: state.filters.episode,
      sequence: state.filters.sequence,
      shot: state.filters.shot
    };
    var versionTake = currentRenderVersionTake();
    var values = {
      project: state.filters.project,
      episode: tokens.episode || state.filters.episode,
      sequence: tokens.sequence || state.filters.sequence,
      shot: tokens.shot || state.filters.shot,
      shot_root: shot.shotRoot || "",
      dept: currentDepartment(),
      version: versionTake.version || "v001",
      take: shortTake(versionTake.take || "t001")
    };
    return {
      compName: String(finalComp.name || "final"),
      fallbackNames: splitCsv(finalComp.fallback_names || ""),
      renderSettingsTemplate: String(queue.render_settings_template || "Best Settings"),
      renderSettingsTemplateAliases: splitCsv(queue.render_settings_template_aliases || ""),
      outputModuleTemplate: String(queue.output_module_template || "Apple ProRes 422 Proxy"),
      outputModuleTemplateAliases: splitCsv(queue.output_module_template_aliases || ""),
      audioOutput: queue.audio_output !== false && String(queue.audio_output).toLowerCase() !== "false",
      quality: String(queue.quality || "best"),
      outputPath: values.shot_root ? formatTemplate(String(queue.output_path || DEFAULT_AE_RENDER_CONFIG.render_queue.output_path), values) : "",
      values: values
    };
  }

  function currentAeRenderConfig() {
    var project = selectedProject();
    return mergeObjects(DEFAULT_AE_RENDER_CONFIG, project ? project.aeRender || {} : {});
  }

  function selectedProject() {
    return state.projects.filter(function (project) {
      return project.name === state.filters.project || project.id === state.filters.project;
    })[0] || null;
  }

  function mergeObjects(base, override) {
    var result = {};
    Object.keys(base || {}).forEach(function (key) {
      if (base[key] && typeof base[key] === "object" && !Array.isArray(base[key])) {
        result[key] = mergeObjects(base[key], {});
      } else {
        result[key] = base[key];
      }
    });
    Object.keys(override || {}).forEach(function (key) {
      if (override[key] && typeof override[key] === "object" && !Array.isArray(override[key]) && result[key] && typeof result[key] === "object") {
        result[key] = mergeObjects(result[key], override[key]);
      } else {
        result[key] = override[key];
      }
    });
    return result;
  }

  function currentRenderVersionTake() {
    var rows = getVisibleRows();
    var version = "";
    var take = "";
    rows.forEach(function (row) {
      if (row.latestVersion) {
        version = maxVersion(version, row.latestVersion);
      } else if (row.version) {
        version = maxVersion(version, row.version);
      }
    });
    take = maxTake(rows.map(function (row) { return row.latestTake || row.take; }));
    if (!version || !take) {
      [getSelectedManifest(), { path: state.selectedAepPath }].forEach(function (source) {
        var info = source ? extractVersionTake([source.path, source.package_root].join("/")) : {};
        version = version || info.version;
        take = take || info.take;
      });
    }
    return {
      version: version || "v001",
      take: take || "t001"
    };
  }

  function maxVersion(left, right) {
    var leftNumber = Number(String(left || "").replace(/^v/i, "")) || 0;
    var rightNumber = Number(String(right || "").replace(/^v/i, "")) || 0;
    return rightNumber > leftNumber ? right : left;
  }

  function splitCsv(value) {
    return String(value || "").split(",").map(function (item) {
      return item.trim();
    }).filter(Boolean);
  }

  function formatTemplate(template, values) {
    return String(template || "").replace(/\{([^}]+)\}/g, function (match, key) {
      return values[key] !== undefined ? values[key] : match;
    });
  }

  function setStatusCell(element, status) {
    var normalized = normalizeStatus(status);
    element.className = "status " + normalized;
    element.querySelector(".dot").className = "dot " + normalized;
    element.querySelector("span:last-child").textContent = titleCase(normalized);
  }

  function normalizeStatus(status) {
    status = String(status || "ready").toLowerCase();
    if (["replace", "updated", "missing", "changed", "error"].indexOf(status) !== -1) {
      return status;
    }
    return "ready";
  }

  function titleCase(value) {
    return value.charAt(0).toUpperCase() + value.slice(1);
  }

  function renderTabs() {
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (button) {
      button.classList.toggle("is-active", button.getAttribute("data-tab") === state.activeTab);
    });
    elements.watchView.classList.toggle("is-hidden", state.activeTab !== "watch");
    elements.queueView.classList.toggle("is-hidden", state.activeTab !== "queue");
    elements.aepView.classList.toggle("is-hidden", state.activeTab !== "aep");
    elements.renderView.classList.toggle("is-hidden", state.activeTab !== "render");
    elements.replaceAllButton.style.display = state.activeTab === "queue" || state.activeTab === "watch" ? "" : "none";
    elements.renderCompButton.style.display = state.activeTab === "render" ? "" : "none";
    renderTakeSummary();
  }

  function renderTakeSummary() {
    var rows;
    var current;
    var latest;
    if (!elements.takeSummary) {
      return;
    }
    if (state.activeTab !== "queue") {
      elements.takeSummary.textContent = "";
      return;
    }
    rows = getVisibleRows().filter(function (row) {
      return row.take || row.latestTake;
    });
    if (!rows.length) {
      elements.takeSummary.textContent = "";
      return;
    }
    current = maxTake(rows.map(function (row) { return row.take; }));
    latest = maxTake(rows.map(function (row) { return row.latestTake || row.take; }));
    elements.takeSummary.textContent = current && latest ? "Current " + shortTake(current) + " -> Latest " + shortTake(latest) : "";
  }

  function maxTake(values) {
    var max = 0;
    values.forEach(function (value) {
      var match = String(value || "").match(/t0*(\d+)/i);
      var number = match ? Number(match[1]) : 0;
      if (number > max) {
        max = number;
      }
    });
    return max ? "t" + String(max).padStart(3, "0") : "";
  }

  function setStatus(message) {
    if (elements.statusLine) {
      elements.statusLine.textContent = message;
    }
  }

  async function importManifest() {
    setStatus("Opening manifest...");
    var result = await window.SmartCEPBridge.callHost("openManifestDialog", undefined, "");
    if (!result) {
      setStatus("Import canceled");
      return;
    }
    ingestManifestResult(result);
  }

  function ingestManifestResult(result) {
    try {
      var payload = typeof result === "string" ? JSON.parse(result) : result;
      if (payload.error) {
        setStatus(payload.error);
        return;
      }
      var manifest = normalizeManifest(payload.data || payload, payload.path, payload.context || state.launchContext || {});
      upsertManifest(manifest);
      state.selectedManifestId = manifest.id;
      setFiltersFromManifest(manifest);
      persistState();
      buildFromSelectedManifest(true);
      setStatus("Imported " + manifest.name);
    } catch (error) {
      setStatus("Could not read manifest: " + error.message);
    }
  }

  function normalizeManifest(data, path, context) {
    context = context || {};
    var name = data.name || basename(path) || "manifest.json";
    return {
      id: path || name + "-" + Date.now(),
      name: name,
      schema: data.schema || "",
      project: data.project || data.projectName || data.show || context.project || "",
      projectRoot: data.projectRoot || context.projectRoot || "",
      configDir: data.configDir || context.configDir || "",
      episode: data.episode || data.ep || context.episode || "",
      sequence: data.sequence || data.seq || context.sequence || "",
      shot: data.shot || data.sh || context.shot || "",
      path: path || data.path || name,
      package_root: data.package_root || data.packageRoot || context.publishRoot || "",
      publishRoot: data.publishRoot || context.publishRoot || "",
      template_project: data.template_project || data.templateProject || "",
      ae_project: data.ae_project || data.aeProject || "",
      ae: data.ae || null,
      layers: asArray(data.layers || []),
      slate: data.slate || null,
      exportedAt: data.exportedAt || data.exported_at || data.createdAt || data.created_at || "",
      renderer: data.renderer || data.renderEngine || data.render_engine || "smart render",
      version: data.version || extractVersion(name) || "",
      items: asArray(data.items || data.outputs || data.assets || data.renders || [])
    };
  }

  function upsertManifest(manifest) {
    var index = state.manifests.findIndex(function (item) {
      return item.id === manifest.id;
    });
    if (index >= 0) {
      state.manifests[index] = manifest;
    } else {
      state.manifests.unshift(manifest);
    }
  }

  async function openSettings() {
    setStatus("Opening saved browser state...");
    var result = await window.SmartCEPBridge.callHost("openSettingsDialog", undefined, "");
    if (!result) {
      setStatus("Open canceled");
      return;
    }
    try {
      var payload = JSON.parse(result);
      state.manifests = payload.manifests || state.manifests;
      state.selectedManifestId = payload.selectedManifestId || state.selectedManifestId;
      state.filters = Object.assign(state.filters, payload.filters || {});
      persistState();
      buildFromSelectedManifest(false);
      setStatus("Settings opened");
    } catch (error) {
      setStatus("Could not open settings: " + error.message);
    }
  }

  function handleOpenButton() {
    if (state.activeTab === "aep") {
      if (!state.selectedAepPath) {
        setStatus("Select an AEP file first");
        return;
      }
      openAepProject(state.selectedAepPath);
      return;
    }
    openSettings();
  }

  function handleBuildButton() {
    var manifest = getSelectedManifest();
    if (!manifest) {
      setStatus("Select an AE build manifest first");
      return;
    }
    if (isAeBuildManifest(manifest)) {
      runSelectedManifestBuild(manifest);
      return;
    }
    buildFromSelectedManifest(true);
  }

  function isAeBuildManifest(manifest) {
    var manifestPath = String(manifest.path || "");
    return manifest.schema === "smart_render_ae_build" || /(?:review_build\.json|_build_v\d{2,5}_\d{1,5}\.json)$/i.test(manifestPath);
  }

  async function saveSettings() {
    var payload = {
      manifests: state.manifests,
      selectedManifestId: state.selectedManifestId,
      filters: state.filters
    };
    setStatus("Saving browser state...");
    var result = await window.SmartCEPBridge.callHost("saveSettingsDialog", payload, "saved");
    setStatus(result && result !== "false" ? "Settings saved" : "Save canceled");
  }

  async function handleSaveButton() {
    var next = nextWorkSavePath();
    var result;
    var payload;
    if (!next) {
      setStatus("Select a shot before saving AEP");
      return;
    }
    setStatus("Saving AEP: " + basename(next.path));
    result = await window.SmartCEPBridge.callHost("saveAepProject", { path: next.path }, "");
    try {
      payload = result ? JSON.parse(result) : {};
    } catch (error) {
      payload = {};
    }
    if (payload.error) {
      setStatus("Save failed: " + payload.error);
      return;
    }
    state.selectedAepPath = next.path;
    clearAepCache();
    renderRows();
    setStatus("Saved AEP: " + basename(next.path));
  }

  function handlePublishButton() {
    var context = currentWorkContext();
    var latest;
    var baseVersion;
    var nextVersion;
    if (!context) {
      setStatus("Select a shot before publishing");
      return;
    }
    latest = latestWorkAepVersionTake(context);
    baseVersion = latest.version || currentContextVersion() || 1;
    nextVersion = baseVersion + 1;
    state.nextSaveVersions[workContextKey(context)] = nextVersion;
    persistState();
    setStatus("Published " + context.prefix + " v" + String(baseVersion).padStart(3, "0") + ". Next Save: " + context.prefix + "_v" + String(nextVersion).padStart(3, "0") + "_01.aep");
  }

  function nextWorkSavePath() {
    var context = currentWorkContext();
    var latest;
    var forcedVersion;
    var version;
    var take;
    if (!context) {
      return null;
    }
    latest = latestWorkAepVersionTake(context);
    forcedVersion = state.nextSaveVersions[workContextKey(context)];
    version = forcedVersion || latest.version || currentContextVersion() || 1;
    take = forcedVersion ? 1 : latest.take + 1;
    if (!take || take < 1) {
      take = 1;
    }
    if (forcedVersion) {
      delete state.nextSaveVersions[workContextKey(context)];
      persistState();
    }
    return {
      path: joinPath(context.root, context.prefix + "_v" + String(version).padStart(3, "0") + "_" + String(take).padStart(2, "0") + ".aep"),
      version: version,
      take: take
    };
  }

  function latestWorkAepVersionTake(context) {
    var fs = nodeRequire("fs");
    var latest = { version: 0, take: 0 };
    if (!fs || !context || !context.root) {
      return latest;
    }
    try {
      if (!fs.existsSync(context.root)) {
        return latest;
      }
      fs.readdirSync(context.root, { withFileTypes: true }).forEach(function (entry) {
        var match;
        var version;
        var take;
        if (!entry.isFile() || !/\.aep$/i.test(entry.name)) {
          return;
        }
        match = entry.name.match(/_v(\d{3,5})_(\d{2,5})\.aep$/i);
        if (!match) {
          return;
        }
        version = Number(match[1]);
        take = Number(match[2]);
        if (version > latest.version || (version === latest.version && take > latest.take)) {
          latest = { version: version, take: take };
        }
      });
    } catch (error) {
      return latest;
    }
    return latest;
  }

  function currentContextVersion() {
    var manifest = getSelectedManifest();
    var candidates = [
      manifest ? manifest.path : "",
      manifest ? manifest.package_root : "",
      state.selectedAepPath
    ];
    var version = 0;
    candidates.some(function (value) {
      var info = extractVersionTake(value);
      if (info.version) {
        version = Number(info.version.replace(/^v/i, ""));
        return true;
      }
      return false;
    });
    return version;
  }

  function workContextKey(context) {
    return [context.shotRoot, context.department].join("|");
  }

  async function openAepProject(path) {
    if (!path) {
      return;
    }
    setStatus("Opening AEP...");
    var result = await window.SmartCEPBridge.callHost("openAepProject", { path: path }, "true");
    setStatus(result && result !== "false" ? "Opened " + basename(path) : "Could not open AEP");
  }

  async function runSelectedManifestBuild(manifest) {
    setStatus("Running AE build: " + basename(manifest.path));
    var result = await window.SmartCEPBridge.callHost("runAeBuildManifest", { path: manifest.path }, "");
    var payload;
    try {
      payload = result ? JSON.parse(result) : {};
    } catch (error) {
      payload = {};
    }
    if (payload.error) {
      setStatus("Build failed: " + payload.error);
      return;
    }
    buildFromSelectedManifest(false);
    setStatus("Build executed: " + basename(manifest.path));
  }

  async function renderFinalComp() {
    var context = renderQueueContext();
    var result;
    var payload;
    if (!context.outputPath) {
      setStatus("Select a shot before rendering");
      return;
    }
    setStatus("Rendering " + context.compName + "...");
    result = await window.SmartCEPBridge.callHost("renderFinalComp", context, "");
    try {
      payload = result ? JSON.parse(result) : {};
    } catch (error) {
      payload = {};
    }
    if (payload.error) {
      setStatus("Render failed: " + payload.error);
      return;
    }
    setStatus(payload.warning ? "Rendered with warning: " + payload.warning : "Rendered " + basename(context.outputPath) + " / " + (payload.outputModuleTemplate || payload.outputModuleName || context.outputModuleTemplate) + (payload.format ? " / " + payload.format : ""));
  }

  async function refreshOutputStatus() {
    if (!state.rows.length) {
      renderRows();
      return;
    }

    var paths = state.rows.map(function (row) {
      return row.checkPath || row.outputPath;
    }).filter(function (path) {
      return path && String(path).indexOf("####") === -1;
    });
    if (!paths.length) {
      renderRows();
      return;
    }
    var result = await window.SmartCEPBridge.callHost("snapshotOutputs", paths, "");
    if (result) {
      try {
        var snapshots = JSON.parse(result);
        applySnapshots(snapshots);
      } catch (error) {
        setStatus("Could not refresh output status: " + error.message);
      }
    }
    renderRows();
  }

  function applySnapshots(snapshots) {
    var byPath = {};
    snapshots.forEach(function (snapshot) {
      byPath[snapshot.path] = snapshot;
    });
    state.rows = state.rows.map(function (row) {
      var snapshot = byPath[row.checkPath || row.outputPath];
      if (!snapshot) {
        return row;
      }
      var next = Object.assign({}, row, snapshot);
      if (snapshot.exists === false) {
        next.status = "missing";
      } else if (row.modified && snapshot.modified && row.modified !== snapshot.modified) {
        next.status = "updated";
      } else if (row.status === "missing" && snapshot.exists) {
        next.status = "ready";
      }
      return next;
    });
  }

  function replaceAll() {
    var rows = getVisibleRows().filter(function (row) {
      return row.status === "replace" || row.status === "updated" || row.status === "changed";
    });
    if (!rows.length) {
      setStatus("No take updates to replace");
      return;
    }
    replaceRows(rows);
  }

  async function replaceRows(rows) {
    var mappings = rows.map(function (row) {
      return {
        name: row.name,
        sourcePath: row.sourcePath,
        outputPath: row.outputPath,
        checkPath: row.checkPath,
        replacePath: row.checkPath || row.outputPath,
        isSequence: String(row.outputPath || "").indexOf("####") !== -1,
        version: row.version,
        latestVersion: row.latestVersion,
        take: row.take,
        latestTake: row.latestTake
      };
    });
    setStatus("Replacing " + rows.length + " footage item" + (rows.length === 1 ? "" : "s") + "...");
    var result = await window.SmartCEPBridge.callHost("replaceAssets", mappings, JSON.stringify({ replaced: rows.length, errors: [] }));
    try {
      var payload = JSON.parse(result);
      var replaced = Number(payload.replaced || 0);
      var errors = payload.errors || [];
      rows.forEach(function (row) {
        row.status = errors.length ? row.status : "ready";
        row.version = row.latestVersion || row.version;
        row.take = row.latestTake || row.take;
        row.currentLabel = versionTakeLabel(row.version, row.take);
      });
      renderRows();
      setStatus(errors.length ? errors[0] : "Replaced " + replaced + " item" + (replaced === 1 ? "" : "s"));
    } catch (error) {
      setStatus("Replace completed");
    }
  }

  function copyManifestPath() {
    var manifest = getSelectedManifest();
    if (!manifest) {
      return;
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(manifest.path);
    }
    setStatus("Manifest path copied");
  }

  function startPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
    }
    state.pollTimer = setInterval(refreshOutputStatus, POLL_INTERVAL_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
}());
