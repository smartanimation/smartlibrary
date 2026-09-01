(function () {
  "use strict";

  var STORAGE_KEY = "smart-ae-browser-state-v1";
  var POLL_INTERVAL_MS = 4000;
  var PIPELINE_ROOT = "P:/dev/smartlibrary";
  var DEFAULT_CONFIG_ROOT = (
    typeof process !== "undefined"
    && process.env
    && process.env.SMARTPIPELINE_PROJECT_CONFIG_ROOT
  ) ? String(process.env.SMARTPIPELINE_PROJECT_CONFIG_ROOT).replace(/\\/g, "/")
    : "P:/dev/smartprojects/config";
  var DEFAULT_AE_RENDER_CONFIG = {
    final_comp: {
      name: "final",
      fallback_names: "render_final,anim_final"
    },
    render_queue: {
      render_settings_template: "最良設定",
      render_settings_template_aliases: "Best Settings",
      output_module_template: "Apple ProRes 422 Proxy",
      output_module_template_aliases: "Apple ProRes 422 Prox,ProRes 422 Proxy,QuickTime Apple ProRes 422 Proxy",
      output_module_fallback_template: "High Quality",
      output_module_fallback_template_aliases: "高品質",
      audio_output: true,
      quality: "best"
    }
  };
  var DEFAULT_NAMING_CONFIG = {
    smart_aftereffects: {
      dcc: "ae",
      work_task: "preComp",
      work_option: "main",
      task: "compTemp",
      aep_filename: "{project}*{episode}*{sequence}*{shot}*{task}_v{version}_t{take}.{ext}",
      output_filename: "{project}*{episode}*{sequence}*{shot}*{task}_v{version}_t{take}.{ext}"
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
    resolvedWorkRoots: {},
    resolvedReviewMoviePaths: {},
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
      var defaultConfigDir = path.join(PIPELINE_ROOT, "config", "default");
      var defaultNamingPath = path.join(defaultConfigDir, "naming.yml");
      var defaultNaming = fs.existsSync(defaultNamingPath) ? parseSimpleYaml(fs.readFileSync(defaultNamingPath, "utf8")) : {};
      var defaultAeRenderPath = path.join(defaultConfigDir, "ae_render.yml");
      var defaultAeRender = fs.existsSync(defaultAeRenderPath) ? parseSimpleYaml(fs.readFileSync(defaultAeRenderPath, "utf8")) : {};
      var defaultTemplates = loadPathTemplates(fs, path, defaultConfigDir);
      fs.readdirSync(configRoot, { withFileTypes: true }).forEach(function (entry) {
        var templatePath;
        var aeRenderPath;
        var namingPath;
        var data;
        var anchors;
        var templates;
        if (!entry.isDirectory() || entry.name === "default") {
          return;
        }
        templatePath = path.join(configRoot, entry.name, "templates_base.yml");
        aeRenderPath = path.join(configRoot, entry.name, "ae_render.yml");
        namingPath = path.join(configRoot, entry.name, "naming.yml");
        if (!fs.existsSync(templatePath)) {
          return;
        }
        data = parseSimpleYaml(fs.readFileSync(templatePath, "utf8"));
        anchors = data.anchors || {};
        templates = mergeObjects(defaultTemplates, loadPathTemplates(fs, path, path.join(configRoot, entry.name)));
        templates.project_root = String(anchors.project_root || templates.project_root || "");
        templates.project_name = String(anchors.project_name || templates.project_name || entry.name);
        projects.push({
          id: entry.name,
          name: String(anchors.project_name || entry.name),
          root: String(anchors.project_root || ""),
          configDir: path.join(configRoot, entry.name).replace(/\\/g, "/"),
          templates: templates,
          aeRender: mergeObjects(defaultAeRender, fs.existsSync(aeRenderPath) ? parseSimpleYaml(fs.readFileSync(aeRenderPath, "utf8")) : {}),
          naming: mergeObjects(defaultNaming, fs.existsSync(namingPath) ? parseSimpleYaml(fs.readFileSync(namingPath, "utf8")) : {}),
          shots: loadShotContexts(String(anchors.project_root || ""), String(anchors.project_name || entry.name), templates)
        });
      });
    } catch (error) {
      setStatus("Could not read project config: " + error.message);
    }

    return projects.sort(function (a, b) {
      return naturalCompare(a.name, b.name);
    });
  }

  function loadPathTemplates(fs, path, configDir) {
    var templates = {};
    ["templates_base.yml", "templates_assets.yml", "templates_shots.yml"].forEach(function (filename) {
      var filePath = path.join(configDir, filename);
      var data;
      if (!fs.existsSync(filePath)) {
        return;
      }
      data = parseSimpleYaml(fs.readFileSync(filePath, "utf8"));
      templates = mergeObjects(templates, data.templates || {});
    });
    return templates;
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

  function loadShotContexts(projectRoot, projectName, templates) {
    var fs = nodeRequire("fs");
    var path = nodeRequire("path");
    var shotsRoot;
    var fallbackRoot;
    var contexts = [];
    if (!fs || !path || !projectRoot) {
      return contexts;
    }

    shotsRoot = resolveShotsRoot(projectRoot, projectName, templates || {});
    fallbackRoot = path.join(projectRoot, "shots");
    if (!fs.existsSync(shotsRoot) && fs.existsSync(fallbackRoot)) {
      shotsRoot = fallbackRoot;
    }
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

  function resolveShotsRoot(projectRoot, projectName, templates) {
    var values = {
      project_root: projectRoot,
      project_name: projectName,
      project: projectName
    };
    var template = templates.shots_root || "";
    var shotTemplate;
    if (!template && templates.shot_root) {
      shotTemplate = String(templates.shot_root);
      if (shotTemplate.indexOf("{episode}") !== -1) {
        template = shotTemplate.split("{episode}", 1)[0].replace(/[\/\\]+$/, "");
      }
    }
    return resolveConfiguredTemplate(template || "{project_root}/shots", values, templates);
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
      refreshManifestCatalog(false);
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
    var manifestIds = {};
    manifests.forEach(function (manifest) {
      manifestIds[manifest.id] = true;
    });
    state.manifests = state.manifests.filter(function (manifest) {
      return !manifestMatchesFilters(manifest) || manifestIds[manifest.id];
    });
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
      setStatus(manifests.length ? "Manifest list refreshed: " + manifests.length + " render manifest" + (manifests.length === 1 ? "" : "s") + " found" : "No render manifests found for the selected shot");
    }
    return added;
  }

  function collectReviewBuildManifests() {
    var manifests = collectResolvedRenderManifests();
    manifests.sort(function (a, b) {
      return String(b.exportedAt || "").localeCompare(String(a.exportedAt || ""));
    });
    return manifests;
  }

  function collectResolvedRenderManifests() {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var childProcess = nodeRequire("child_process");
    var project = selectedProject();
    var pipelineRoot;
    var scriptPath;
    var python;
    var rows = {};
    if (!fs || !pathModule || !childProcess || !project || !project.configDir) {
      return [];
    }
    pipelineRoot = PIPELINE_ROOT;
    scriptPath = pathModule.join(pipelineRoot, "scripts", "list_ae_render_manifests.py");
    if (!fs.existsSync(scriptPath)) {
      return [];
    }
    python = findPythonExecutable(fs, pathModule, pipelineRoot);
    selectedShotContexts().forEach(function (context) {
      var result;
      var payload;
      try {
        result = childProcess.spawnSync(python, [
          scriptPath,
          "--config-dir", project.configDir,
          "--episode", context.episode,
          "--sequence", context.sequence,
          "--shot", context.shot,
          "--department", currentDepartment(),
          "--task", "main"
        ], { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
      } catch (error) {
        return;
      }
      if (!result || result.status !== 0) {
        return;
      }
      payload = parseLastJsonLine(result.stdout);
      asArray(payload.manifests || []).forEach(function (entry) {
        var manifest = normalizeManifest(entry.data || {}, entry.path || "", entry.context || context);
        if (manifest.path) {
          rows[manifest.path] = manifest;
        }
      });
    });
    return Object.keys(rows).map(function (path) { return rows[path]; });
  }

  function selectedShotContexts() {
    var rows = [];
    state.projects.forEach(function (project) {
      (project.shots || []).forEach(function (context) {
        if (matchesFilters(context)) {
          rows.push(context);
        }
      });
    });
    return rows;
  }

  function scanRenderManifestManifests(root, context, manifests, maxFiles) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    if (!fs || !pathModule || !root || !fs.existsSync(root)) {
      return;
    }
    try {
      fs.readdirSync(root, { withFileTypes: true }).forEach(function (departmentEntry) {
        var departmentRoot;
        if (!departmentEntry.isDirectory() || manifests.length >= maxFiles) {
          return;
        }
        departmentRoot = pathModule.join(root, departmentEntry.name);
        fs.readdirSync(departmentRoot, { withFileTypes: true }).forEach(function (taskEntry) {
          var taskRoot;
          var latestPath;
          var latest;
          var manifestPath;
          var data;
          var items;
          var complete;
          if (!taskEntry.isDirectory() || manifests.length >= maxFiles) {
            return;
          }
          taskRoot = pathModule.join(departmentRoot, taskEntry.name);
          latestPath = pathModule.join(taskRoot, "latest.json");
          if (!fs.existsSync(latestPath)) {
            return;
          }
          try {
            latest = JSON.parse(fs.readFileSync(latestPath, "utf8"));
            manifestPath = pathModule.join(taskRoot, String(latest.path || ""));
            data = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
          } catch (error) {
            return;
          }
          if (!data || data.schema !== "smartpipeline.render_manifest.v1") {
            return;
          }
          items = Array.isArray(data.items) ? data.items : [];
          complete = items.length > 0;
          items.forEach(function (item) {
            var receipt;
            var firstFile;
            var lastFile;
            var range;
            var resolution;
            var expectedCount;
            try {
              receipt = JSON.parse(fs.readFileSync(String(item.receipt_path || ""), "utf8"));
              firstFile = String(item.first_frame_file || item.sourcePath || "");
              lastFile = pathModule.join(
                pathModule.dirname(String(item.receipt_path || "")),
                String(receipt.last_file || "")
              );
              range = item.frame_range || [];
              resolution = item.resolution || [];
              expectedCount = range.length >= 2
                ? Math.max(1, Number(range[1]) - Number(range[0]) + 1)
                : 0;
              if (receipt.status !== "complete"
                  || String(receipt.settings_fingerprint || "") !== String(data.fingerprint || "")
                  || !firstFile
                  || !fs.existsSync(firstFile)
                  || (receipt.last_file && !fs.existsSync(lastFile))
                  || (expectedCount && Number(receipt.file_count || 0) !== expectedCount)
                  || (resolution.length >= 2
                    && (Number((receipt.resolution || [])[0]) !== Number(resolution[0])
                      || Number((receipt.resolution || [])[1]) !== Number(resolution[1])))) {
                complete = false;
                return;
              }
              item.status = "ready";
              item.file_count = Number(receipt.file_count || 0);
            } catch (error) {
              complete = false;
            }
          });
          if (!complete) {
            return;
          }
          data.material_status = "complete";
          manifests.push(normalizeManifest(
            data,
            manifestPath.replace(/\\/g, "/"),
            Object.assign({}, context, {
              department: departmentEntry.name,
              area: "data"
            })
          ));
        });
      });
    } catch (error) {
      return;
    }
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
    var latestManifest = latestPreviewRenderManifest(manifest);
    var latestItems = {};
    if (latestManifest) {
      collectManifestItems(latestManifest).forEach(function (latestItem, latestIndex) {
        var latestId = latestItem.id || latestItem.output_id || latestItem.layer || latestItem.name || "item-" + latestIndex;
        latestItems[String(latestId)] = latestItem;
      });
    }
    return rawItems.map(function (item, index) {
      var itemContext = getContext(item, manifestContext);
      var itemId = item.id || item.output_id || item.layer || item.name || "item-" + index;
      var latestItem = latestItems[String(itemId)] || item;
      var outputPath = resolveManifestPath(manifest, item.outputPath || item.output || item.path || item.latestPath || item.image_sequence || item.first_frame_file || "");
      var sourcePath = resolveManifestPath(manifest, item.sourcePath || item.currentPath || item.aePath || item.footagePath || "");
      var publishedOutputPath = resolveManifestPath(latestManifest || manifest, latestItem.outputPath || latestItem.output || latestItem.path || latestItem.latestPath || latestItem.image_sequence || latestItem.first_frame_file || "");
      var publishedFirstFrame = resolveManifestPath(latestManifest || manifest, latestItem.first_frame_file || "");
      var versionInfo = extractVersionTake(sourcePath || manifest.path || outputPath);
      var latestInfo = extractVersionTake(publishedOutputPath || outputPath || sourcePath);
      var version = normalizeVersionToken(item.version || versionInfo.version || manifest.version || "");
      var latestVersion = normalizeVersionToken(latestItem.version || latestItem.latestVersion || latestItem.outputVersion || latestInfo.version || version);
      var take = normalizeTake(item.take || versionInfo.take || "");
      var latestTake = normalizeTake(latestItem.take || latestItem.latestTake || latestItem.outputTake || latestAvailableTake(publishedOutputPath || outputPath || manifest.path) || latestInfo.take || take);
      var latestOutputPath = publishedOutputPath || outputPath;
      var latestFirstFrame = publishedFirstFrame || resolveManifestPath(manifest, item.first_frame_file || "");
      var checkPath = latestFirstFrame || latestOutputPath || resolveManifestPath(manifest, item.checkPath || "") || outputPath;
      var currentLabel = versionTakeLabel(version, take);
      var latestLabel = versionTakeLabel(latestVersion, latestTake);
      var inferredStatus = inferStatus(currentLabel, latestLabel, latestOutputPath);
      var status = inferredStatus === "replace"
        ? inferredStatus
        : (item.status || inferredStatus);
      return {
        id: itemId,
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

  function rowsFromCurrentFootage(footages) {
    var fallbackContext = selectedShotContext() || getContext(getSelectedManifest() || {});
    var seen = {};
    var rows = [];
    asArray(footages).forEach(function (footage, index) {
      var row = normalizeFootageRow(footage, fallbackContext, index);
      var key;
      if (!row || isIgnoredFootageRow(row)) {
        return;
      }
      key = [row.name.toLowerCase(), cleanFilePath(row.sourcePath).toLowerCase()].join("|");
      if (seen[key]) {
        return;
      }
      seen[key] = true;
      rows.push(row);
    });
    return rows.sort(function (a, b) {
      return naturalCompare(a.name || "", b.name || "");
    });
  }

  function normalizeFootageRow(footage, fallbackContext, index) {
    var sourcePath = cleanFilePath(footage.sourcePath || footage.path || "");
    var context = getContext({ path: sourcePath, sourcePath: sourcePath, name: footage.name || footage.layer || "" }, fallbackContext || {});
    var layer = inferFootageLayer(footage, sourcePath);
    var department = inferFootageDepartment(sourcePath) || currentDepartment();
    var latest = latestManifestItemForFootage(layer, context, department);
    var latestItem = latest ? latest.item : null;
    var latestManifest = latest ? latest.manifest : null;
    var versionInfo = extractVersionTake(sourcePath || footage.name || "");
    var latestOutputPath = latestItem
      ? resolveManifestPath(latestManifest, latestItem.outputPath || latestItem.output || latestItem.path || latestItem.latestPath || latestItem.image_sequence || latestItem.first_frame_file || "")
      : sourcePath;
    var latestFirstFrame = latestItem ? resolveManifestPath(latestManifest, latestItem.first_frame_file || latestItem.first_file || "") : "";
    var latestInfo = extractVersionTake(latestOutputPath || latestFirstFrame || sourcePath);
    var version = normalizeVersionToken(footage.version || versionInfo.version || "");
    var take = normalizeTake(footage.take || versionInfo.take || "");
    var latestVersion = latestItem ? normalizeVersionToken(latestItem.version || latestItem.latestVersion || latestItem.outputVersion || latestInfo.version || version) : version;
    var latestTake = latestItem ? normalizeTake(latestItem.take || latestItem.latestTake || latestItem.outputTake || latestAvailableTake(latestOutputPath) || latestInfo.take || take) : take;
    var currentLabel = versionTakeLabel(version, take);
    var latestLabel = versionTakeLabel(latestVersion, latestTake);
    var status = footage.exists === false ? "missing" : inferStatus(currentLabel, latestLabel, latestOutputPath || sourcePath);
    return {
      id: "footage-" + (footage.name || layer || index),
      name: layer || footage.name || basename(sourcePath) || "footage-" + (index + 1),
      sourcePath: sourcePath,
      outputPath: latestOutputPath || sourcePath,
      currentOutputPath: sourcePath,
      checkPath: latestFirstFrame || latestOutputPath || sourcePath,
      version: version,
      latestVersion: latestVersion,
      take: take,
      latestTake: latestTake,
      currentLabel: currentLabel,
      latestLabel: latestLabel,
      status: status,
      exists: footage.exists,
      modified: footage.modified || "",
      size: footage.size || 0,
      project: context.project,
      episode: context.episode,
      sequence: context.sequence,
      shot: context.shot
    };
  }

  function isIgnoredFootageRow(row) {
    var extension = String(row.sourcePath || "").split(".").pop().toLowerCase();
    var name = String(row.name || "").toLowerCase();
    if (!row.sourcePath) {
      return true;
    }
    if (/^(wav|wave|mp3|aif|aiff|m4a|aac|ogg)$/.test(extension)) {
      return true;
    }
    return name === "audio";
  }

  function latestManifestItemForFootage(layer, context, department) {
    var targetLayer = normalizeLayerKey(layer);
    var candidates;
    var found = null;
    if (!targetLayer) {
      return null;
    }
    candidates = state.manifests.filter(function (manifest) {
      var manifestContext;
      if (manifest.schema !== "smartpipeline.render_manifest.v1") {
        return false;
      }
      manifestContext = getContext(manifest, context || {});
      return (!context.project || manifestContext.project === context.project)
        && (!context.episode || manifestContext.episode === context.episode)
        && (!context.sequence || manifestContext.sequence === context.sequence)
        && (!context.shot || manifestContext.shot === context.shot)
        && (!department || String(manifest.department || "").toLowerCase() === String(department || "").toLowerCase());
    });
    candidates.sort(function (a, b) {
      var aVersion = Number(String(a.version || "").replace(/\D/g, "")) || 0;
      var bVersion = Number(String(b.version || "").replace(/\D/g, "")) || 0;
      return bVersion - aVersion || manifestSortTime(b) - manifestSortTime(a);
    });
    candidates.some(function (manifest) {
      return collectManifestItems(manifest).some(function (item) {
        if (normalizeLayerKey(item.layer || item.name || item.id || item.output_id) !== targetLayer) {
          return false;
        }
        found = { manifest: manifest, item: item };
        return true;
      });
    });
    return found;
  }

  function inferFootageLayer(footage, sourcePath) {
    var path = String(sourcePath || "").replace(/\\/g, "/");
    var file = basename(path);
    var match = path.match(/\/layers\/([^\/]+)/i);
    if (match && !/^v\d{2,5}$/i.test(match[1]) && !isTakeFolder(match[1])) {
      return match[1];
    }
    match = file.match(/_([A-Za-z0-9]+)_v\d{2,5}[_\-.](?:t|take)?\d{1,5}/i);
    if (match) {
      return match[1];
    }
    if (footage.layer && !/^(layers|audio|30_footage|footage)$/i.test(String(footage.layer))) {
      return String(footage.layer);
    }
    return String(footage.name || "");
  }

  function inferFootageDepartment(sourcePath) {
    var path = String(sourcePath || "").replace(/\\/g, "/");
    var match = path.match(/\/(?:render|preview_render)\/([^\/]+)\/(?:layers|packages)\//i);
    if (match) {
      return match[1];
    }
    match = path.match(/\/publish\/preview_render\/([^\/]+)\//i);
    return match ? match[1] : "";
  }

  function normalizeLayerKey(value) {
    return String(value || "").replace(/^\s+|\s+$/g, "").toLowerCase();
  }

  function latestPreviewRenderManifest(manifest) {
    var context;
    var department;
    var candidates;
    if (!manifest || manifest.schema !== "smartpipeline.render_manifest.v1") {
      return null;
    }
    context = getContext(manifest);
    department = String(manifest.department || "").toLowerCase();
    candidates = state.manifests.filter(function (candidate) {
      var candidateContext;
      if (candidate.schema !== "smartpipeline.render_manifest.v1") {
        return false;
      }
      candidateContext = getContext(candidate);
      return candidateContext.project === context.project
        && candidateContext.episode === context.episode
        && candidateContext.sequence === context.sequence
        && candidateContext.shot === context.shot
        && String(candidate.department || "").toLowerCase() === department;
    });
    candidates.sort(function (a, b) {
      var aVersion = Number(String(a.version || "").replace(/\D/g, "")) || 0;
      var bVersion = Number(String(b.version || "").replace(/\D/g, "")) || 0;
      return bVersion - aVersion || manifestSortTime(b) - manifestSortTime(a);
    });
    return candidates[0] || manifest;
  }

  function collectManifestItems(manifest) {
    var items = [];
    var byKey = {};
    var result = [];

    function mergeItem(candidate) {
      var key;
      var existing;
      if (!candidate) {
        return;
      }
      normalizeManifestItemFields(candidate);
      key = manifestItemKey(candidate);
      if (!key) {
        result.push(candidate);
        return;
      }
      existing = byKey[key];
      if (!existing) {
        byKey[key] = candidate;
        result.push(candidate);
        return;
      }
      Object.keys(candidate).forEach(function (field) {
        if (existing[field] === undefined || existing[field] === null || existing[field] === "") {
          existing[field] = candidate[field];
        }
      });
    }

    ["items", "rows", "outputs", "assets", "renders"].forEach(function (key) {
      items = items.concat(asArray(manifest[key] || []));
    });
    asArray(manifest.layers || []).forEach(function (layer) {
      items.push(Object.assign({ status: "ready" }, layer));
    });
    if (manifest.slate && (manifest.slate.image_sequence || manifest.slate.first_frame_file)) {
      items.push(Object.assign({ id: "slate", name: "slate", layer: "slate", status: "ready" }, manifest.slate));
    }
    items.forEach(mergeItem);
    return result;
  }

  function normalizeManifestItemFields(item) {
    if (!item.layer && (item.name || item.id || item.output_id)) {
      item.layer = item.name || item.id || item.output_id;
    }
    if (!item.first_frame_file && item.first_file) {
      item.first_frame_file = item.first_file;
    }
    if (!item.image_sequence && item.pattern) {
      item.image_sequence = item.pattern;
    }
  }

  function manifestItemKey(item) {
    var layer = item.layer || item.name || item.id || item.output_id || "";
    var path = item.outputPath || item.output || item.path || item.latestPath || item.image_sequence || item.first_frame_file || "";
    if (layer) {
      return "layer:" + String(layer).toLowerCase();
    }
    if (path) {
      return "path:" + cleanFilePath(path).toLowerCase();
    }
    return "";
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

  function normalizeVersionToken(value) {
    var text = String(value || "").trim();
    var match;
    if (!text) {
      return "";
    }
    match = text.match(/^v0*(\d+)$/i);
    if (match) {
      return "v" + String(Number(match[1])).padStart(3, "0");
    }
    match = text.match(/^0*(\d+)$/);
    if (match) {
      return "v" + String(Number(match[1])).padStart(3, "0");
    }
    return text;
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
    var now = Date.now();

    if (state.aepFilesCacheKey === key && now - state.aepFilesCacheAt < 15000) {
      return state.aepFiles;
    }

    collectWorkAepRoots().forEach(function (root) {
      scanAepFiles(root, "Work").forEach(function (row) {
        rows[row.path] = row;
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

  function collectWorkAepRoots() {
    return selectedShotRoots().map(function (root) {
      return workAepRoot(root, currentDepartment());
    });
  }

  function workAepRoot(shotRoot, department) {
    return resolveWorkAepRootFromBackend(shotRoot, department);
  }

  function resolveWorkAepRootFromBackend(shotRoot, department) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var childProcess = nodeRequire("child_process");
    var project = selectedProject();
    var tokens = shotTokensFromRoot(shotRoot);
    var dcc = afterEffectsDcc();
    var task = afterEffectsWorkTask();
    var option = afterEffectsWorkOption();
    var key;
    var pipelineRoot;
    var scriptPath;
    var python;
    var result;
    var payload;
    if (!fs || !pathModule || !childProcess || !project || !project.configDir) {
      return "";
    }
    key = [
      project.configDir,
      tokens.episode,
      tokens.sequence,
      tokens.shot,
      department || "anim",
      dcc,
      task,
      option
    ].join("|");
    if (state.resolvedWorkRoots[key]) {
      return state.resolvedWorkRoots[key];
    }
    pipelineRoot = PIPELINE_ROOT;
    scriptPath = pathModule.join(pipelineRoot, "scripts", "resolve_ae_work_path.py");
    if (!fs.existsSync(scriptPath)) {
      return "";
    }
    python = findPythonExecutable(fs, pathModule, pipelineRoot);
    try {
      result = childProcess.spawnSync(python, [
        scriptPath,
        "--config-dir", project.configDir,
        "--episode", tokens.episode,
        "--sequence", tokens.sequence,
        "--shot", tokens.shot,
        "--department", department || "anim",
        "--dcc", dcc,
        "--task", task,
        "--option", option
      ], { encoding: "utf8", maxBuffer: 1024 * 1024 });
    } catch (error) {
      return "";
    }
    if (!result || result.status !== 0) {
      return "";
    }
    payload = parseLastJsonLine(result.stdout);
    if (payload && payload.ok && payload.work_root) {
      state.resolvedWorkRoots[key] = String(payload.work_root).replace(/\\/g, "/");
      return state.resolvedWorkRoots[key];
    }
    return "";
  }

  function resolveReviewMoviePathFromBackend(shotRoot, department, filename) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var childProcess = nodeRequire("child_process");
    var project = selectedProject();
    var tokens = shotTokensFromRoot(shotRoot);
    var key;
    var pipelineRoot;
    var scriptPath;
    var python;
    var result;
    var payload;
    if (!fs || !pathModule || !childProcess || !project || !project.configDir || !filename) {
      return "";
    }
    key = [
      project.configDir,
      tokens.episode,
      tokens.sequence,
      tokens.shot,
      department || "anim",
      filename
    ].join("|");
    if (state.resolvedReviewMoviePaths[key]) {
      return state.resolvedReviewMoviePaths[key];
    }
    pipelineRoot = PIPELINE_ROOT;
    scriptPath = pathModule.join(
      pipelineRoot, "scripts", "resolve_ae_review_movie_path.py"
    );
    if (!fs.existsSync(scriptPath)) {
      return "";
    }
    python = findPythonExecutable(fs, pathModule, pipelineRoot);
    try {
      result = childProcess.spawnSync(python, [
        scriptPath,
        "--config-dir", project.configDir,
        "--episode", tokens.episode,
        "--sequence", tokens.sequence,
        "--shot", tokens.shot,
        "--department", department || "anim",
        "--filename", filename
      ], { encoding: "utf8", maxBuffer: 1024 * 1024 });
    } catch (error) {
      return "";
    }
    if (!result || result.status !== 0) {
      return "";
    }
    payload = parseLastJsonLine(result.stdout);
    if (payload && payload.ok && payload.path) {
      state.resolvedReviewMoviePaths[key] = String(payload.path).replace(/\\/g, "/");
      return state.resolvedReviewMoviePaths[key];
    }
    return "";
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
    var match = text.replace(/\\/g, "/").match(/\/data\/render_manifest\/([^\/]+)\//i);
    if (match) {
      return match[1];
    }
    match = text.replace(/\\/g, "/").match(/\/(?:work|output|publish)\/(?:review\/)?([^\/]+)\/(?:ae|v\d+)/i);
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
      project: state.filters.project || selectedProjectName(),
      episode: tokens.episode,
      sequence: tokens.sequence,
      shot: tokens.shot,
      task: afterEffectsTask(),
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
    var fileMatch = basename(path).match(/_v(\d{3,5})_(?:t|take)?(\d{2,5})\.aep$/i);
    var version = "";
    var take = "";
    var department = "";
    if (fileMatch) {
      return "v" + fileMatch[1] + "/t" + String(Number(fileMatch[2])).padStart(2, "0");
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
      task: afterEffectsTask(),
      version: versionNumberLabel(versionTake.version || "v001"),
      take: takeNumberLabel(versionTake.take || "t001"),
      version_label: versionLabel(versionTake.version || "v001"),
      take_label: takeLabel(versionTake.take || "t001")
    };
    values.output_filename = afterEffectsFilename("output", values, "mov");
    return {
      compName: String(finalComp.name || "final"),
      fallbackNames: splitCsv(finalComp.fallback_names || ""),
      renderSettingsTemplate: String(queue.render_settings_template || "最良設定"),
      renderSettingsTemplateAliases: splitCsv(queue.render_settings_template_aliases || ""),
      outputModuleTemplate: String(queue.output_module_template || "Apple ProRes 422 Proxy"),
      outputModuleTemplateAliases: splitCsv(queue.output_module_template_aliases || ""),
      outputModuleFallbackTemplate: String(queue.output_module_fallback_template || "High Quality"),
      outputModuleFallbackTemplateAliases: splitCsv(queue.output_module_fallback_template_aliases || "高品質"),
      audioOutput: queue.audio_output !== false && String(queue.audio_output).toLowerCase() !== "false",
      quality: String(queue.quality || "best"),
      outputPath: values.shot_root ? resolveReviewMoviePathFromBackend(
        values.shot_root, values.dept, values.output_filename
      ) : "",
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

  function currentAeNamingConfig() {
    var project = selectedProject();
    return mergeObjects(DEFAULT_NAMING_CONFIG, project ? project.naming || {} : {});
  }

  function afterEffectsTask() {
    var naming = currentAeNamingConfig().smart_aftereffects || {};
    return String(naming.task || currentDepartment() || "compTemp");
  }

  function afterEffectsDcc() {
    var naming = currentAeNamingConfig().smart_aftereffects || {};
    return String(naming.dcc || "ae");
  }

  function afterEffectsWorkTask() {
    var naming = currentAeNamingConfig().smart_aftereffects || {};
    return String(naming.work_task || "preComp");
  }

  function afterEffectsWorkOption() {
    var naming = currentAeNamingConfig().smart_aftereffects || {};
    return String(naming.work_option || "main");
  }

  function afterEffectsFilename(kind, values, ext) {
    var naming = currentAeNamingConfig().smart_aftereffects || {};
    var key = kind === "aep" ? "aep_filename" : "output_filename";
    var template = String(naming[key] || DEFAULT_NAMING_CONFIG.smart_aftereffects[key]);
    var data = Object.assign({}, values, {
      project: values.project || selectedProjectName(),
      task: values.task || afterEffectsTask(),
      ext: ext || ""
    });
    return sanitizeFilename(formatTemplate(template, data).replace(/\*/g, "_"));
  }

  function resolveConfiguredTemplate(template, values, templates) {
    var merged = mergeObjects(templates || {}, values || {});
    var previous = "";
    var resolved = String(template || "");
    var guard = 0;
    while (resolved !== previous && guard < 10) {
      previous = resolved;
      resolved = formatTemplate(resolved, merged);
      guard += 1;
    }
    return resolved.replace(/\\/g, "/");
  }

  function findPythonExecutable(fs, pathModule, pipelineRoot) {
    var candidates = [
      (typeof process !== "undefined" && process.env) ? process.env.SMARTPIPELINE_PYTHON : "",
      pathModule.join(pathModule.dirname(pipelineRoot), "smarttools", "python", "python.exe"),
      pathModule.join(pipelineRoot, "runtime", "python", "python.exe"),
      pathModule.join(pipelineRoot, ".venv", "Scripts", "python.exe"),
      "python"
    ];
    var i;
    for (i = 0; i < candidates.length; i += 1) {
      if (candidates[i] && (candidates[i] === "python" || fs.existsSync(candidates[i]))) {
        return candidates[i];
      }
    }
    return "python";
  }

  function parseLastJsonLine(text) {
    var lines = String(text || "").trim().split(/\r?\n/);
    var i;
    for (i = lines.length - 1; i >= 0; i -= 1) {
      try {
        return JSON.parse(lines[i]);
      } catch (error) {}
    }
    return {};
  }

  function selectedProjectName() {
    var project = selectedProject();
    return state.filters.project || (project ? project.name || project.id : "");
  }

  function versionLabel(value) {
    var number = versionNumberLabel(value);
    return "v" + number;
  }

  function takeLabel(value) {
    var number = takeNumberLabel(value);
    return "t" + number;
  }

  function versionNumberLabel(value) {
    var match = String(value || "").match(/v?0*(\d+)/i);
    return String(match ? Number(match[1]) : 1).padStart(3, "0");
  }

  function takeNumberLabel(value) {
    var match = String(value || "").match(/(?:t|take)?0*(\d+)/i);
    return String(match ? Number(match[1]) : 1).padStart(3, "0");
  }

  function sanitizeFilename(value) {
    return String(value || "").replace(/[\\/:?"<>|]/g, "_");
  }

  function replacePathBasename(path, filename) {
    var normalized = String(path || "").replace(/\\/g, "/");
    var index = normalized.lastIndexOf("/");
    if (index === -1) {
      return filename;
    }
    return normalized.slice(0, index + 1) + filename;
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
    var currentLabels;
    var latestLabels;
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
    currentLabels = uniqueValues(rows.map(function (row) { return row.currentLabel || versionTakeLabel(row.version, row.take); }));
    latestLabels = uniqueValues(rows.map(function (row) { return row.latestLabel || versionTakeLabel(row.latestVersion, row.latestTake); }));
    if (currentLabels.length === 1 && latestLabels.length === 1 && currentLabels[0] && latestLabels[0]) {
      elements.takeSummary.textContent = "Current " + currentLabels[0] + " -> Latest " + latestLabels[0];
      return;
    }
    current = maxTake(rows.map(function (row) { return row.take; }));
    latest = maxTake(rows.map(function (row) { return row.latestTake || row.take; }));
    elements.takeSummary.textContent = current && latest ? "Current " + shortTake(current) + " -> Latest " + shortTake(latest) : "";
  }

  function uniqueValues(values) {
    var seen = {};
    var result = [];
    values.forEach(function (value) {
      value = String(value || "");
      if (!value || seen[value]) {
        return;
      }
      seen[value] = true;
      result.push(value);
    });
    return result;
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
      shot_root: data.shot_root || data.shotRoot || context.shotRoot || "",
      episode: data.episode || data.ep || context.episode || "",
      sequence: data.sequence || data.seq || context.sequence || "",
      shot: data.shot || data.sh || context.shot || "",
      department: data.department || data.dept || context.department || "",
      task: data.task || data.subset || context.task || "",
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
      items: asArray(data.items || data.rows || data.outputs || data.assets || data.renders || []),
      rows: asArray(data.rows || [])
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

  async function handleBuildButton() {
    var manifest = getSelectedManifest();
    if (!manifest) {
      setStatus("Select an AE build manifest first");
      return;
    }
    try {
      if (isAeBuildManifest(manifest)) {
        await runSelectedManifestBuild(manifest);
        return;
      }
      buildFromSelectedManifest(true);
    } catch (error) {
      setStatus("Build failed: " + error.message);
    }
  }

  function isAeBuildManifest(manifest) {
    var manifestPath = String(manifest.path || "");
    return manifest.schema === "smart_render_ae_build"
      || manifest.schema === "smartpipeline.render_manifest.v1"
      || /(?:review_build\.json|_build_v\d{2,5}_\d{1,5}\.json)$/i.test(manifestPath);
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
    commitWorkSave(next);
    state.selectedAepPath = next.path;
    clearAepCache();
    renderRows();
    setStatus("Saved AEP: " + basename(next.path));
  }

  async function handlePublishButton() {
    var context = currentWorkContext();
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var os = nodeRequire("os");
    var childProcess = nodeRequire("child_process");
    var source = state.selectedAepPath;
    var project = selectedProject();
    var renderConfig = currentAeRenderConfig();
    var selectedManifest = getSelectedManifest();
    var expectedLayerIds = [];
    var inspectionText;
    var inspection;
    var saveText;
    var saveResult;
    var metadataPath;
    var pipelineRoot;
    var scriptPath;
    var python;
    var candidates;
    var args;
    var processResult;
    var outputLines;
    var result;
    var i;
    if (!context) {
      setStatus("Select a shot before publishing");
      return;
    }
    if (!fs || !pathModule || !os || !childProcess) {
      setStatus("PreComp Publish failed: CEP Node runtime is unavailable");
      return;
    }
    if (!source || !fs.existsSync(source)) {
      setStatus("Select and Save an AEP before publishing");
      return;
    }
    setStatus("Inspecting PreComp structure in After Effects...");
    (selectedManifest ? selectedManifest.items || [] : []).forEach(function (item) {
      var layerId = String(item.layer || item.id || item.name || "").trim();
      if (layerId && layerId.toLowerCase() !== "slate" && expectedLayerIds.indexOf(layerId) < 0) {
        expectedLayerIds.push(layerId);
      }
    });
    inspectionText = await window.SmartCEPBridge.callHost("inspectPrecompProject", {
      final_comp: renderConfig.final_comp || {},
      stage_comp: "stage",
      expected_layer_ids: expectedLayerIds
    }, "");
    try {
      inspection = inspectionText ? JSON.parse(inspectionText) : {};
    } catch (error) {
      setStatus("PreComp inspection failed: " + error.message);
      return;
    }
    if (!inspection.validation || inspection.validation.status === "failed") {
      result = ((inspection.validation || {}).results || []).filter(function (row) {
        return String(row.severity || "").toUpperCase() === "ERROR";
      }).map(function (row) { return row.message; }).join("; ");
      setStatus("PreComp validation failed: " + (result || "Unknown structural error"));
      return;
    }
    setStatus("Saving inspected AEP before PreComp Publish...");
    saveText = await window.SmartCEPBridge.callHost("saveAepProject", { path: source }, "");
    try {
      saveResult = saveText ? JSON.parse(saveText) : {};
    } catch (saveError) {
      saveResult = { error: saveError.message };
    }
    if (saveResult.error) {
      setStatus("PreComp Publish save failed: " + saveResult.error);
      return;
    }
    pipelineRoot = PIPELINE_ROOT;
    scriptPath = pathModule.join(pipelineRoot, "scripts", "publish_precomp.py");
    if (!fs.existsSync(scriptPath)) {
      setStatus("PreComp Publish failed: backend script was not found: " + scriptPath);
      return;
    }
    candidates = [
      (typeof process !== "undefined" && process.env) ? process.env.SMARTPIPELINE_PYTHON : "",
      pathModule.join(pathModule.dirname(pipelineRoot), "smarttools", "python", "python.exe"),
      pathModule.join(pipelineRoot, "runtime", "python", "python.exe"),
      pathModule.join(pipelineRoot, ".venv", "Scripts", "python.exe"),
      "python"
    ];
    python = "python";
    for (i = 0; i < candidates.length; i += 1) {
      if (candidates[i] && (candidates[i] === "python" || fs.existsSync(candidates[i]))) {
        python = candidates[i];
        break;
      }
    }
    metadataPath = pathModule.join(
      os.tmpdir(),
      "smart_precomp_" + Date.now() + "_" + Math.floor(Math.random() * 100000) + ".json"
    );
    fs.writeFileSync(metadataPath, JSON.stringify(inspection, null, 2), "utf8");
    args = [
      scriptPath,
      "--config-dir", (project && project.configDir) || state.launchContext.configDir || "",
      "--episode", context.episode,
      "--sequence", context.sequence,
      "--shot", context.shot,
      "--source", source,
      "--metadata-json", metadataPath,
      "--author", (typeof process !== "undefined" && process.env) ? (process.env.USERNAME || process.env.USER || "") : "",
      "--comment", "Published from Smart AE Browser"
    ];
    setStatus("Publishing validated PreComp...");
    try {
      processResult = childProcess.spawnSync(python, args, { encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
    } catch (processError) {
      processResult = { status: 1, stdout: "", stderr: processError.message };
    } finally {
      try { fs.unlinkSync(metadataPath); } catch (cleanupError) {}
    }
    outputLines = String(processResult.stdout || "").trim().split(/\r?\n/);
    result = {};
    for (i = outputLines.length - 1; i >= 0; i -= 1) {
      try {
        result = JSON.parse(outputLines[i]);
        break;
      } catch (parseError) {}
    }
    if (processResult.status !== 0 || !result.ok) {
      setStatus("PreComp Publish failed: " + (result.error || String(processResult.stderr || processResult.stdout || "Unknown backend error").trim()));
      return;
    }
    state.nextSaveVersions[workContextKey(context)] = Math.max(
      latestWorkAepVersionTake(context).version + 1,
      currentContextVersion() + 1,
      1
    );
    clearAepCache();
    persistState();
    renderRows();
    setStatus("Published PreComp " + result.version + ": " + result.project);
  }

  function nextWorkSavePath() {
    var context = currentWorkContext();
    var latest;
    var forcedVersion;
    var manifestVersion;
    var version;
    var take;
    if (!context) {
      return null;
    }
    latest = latestWorkAepVersionTake(context);
    forcedVersion = state.nextSaveVersions[workContextKey(context)];
    manifestVersion = currentContextVersion();
    version = forcedVersion || Math.max(latest.version, manifestVersion, 1);
    take = forcedVersion || latest.version !== version ? 1 : latest.take + 1;
    if (!take || take < 1) {
      take = 1;
    }
    return {
      path: joinPath(context.root, afterEffectsFilename("aep", {
        project: context.project,
        episode: context.episode,
        sequence: context.sequence,
        shot: context.shot,
        dept: context.department,
        task: context.task,
        version: String(version).padStart(3, "0"),
        take: String(take).padStart(2, "0"),
        version_label: "v" + String(version).padStart(3, "0"),
        take_label: "t" + String(take).padStart(2, "0")
      }, "aep")),
      version: version,
      take: take,
      contextKey: workContextKey(context),
      consumesForcedVersion: Boolean(forcedVersion)
    };
  }

  function commitWorkSave(saveTarget) {
    if (!saveTarget || !saveTarget.consumesForcedVersion || !saveTarget.contextKey) {
      return;
    }
    delete state.nextSaveVersions[saveTarget.contextKey];
    persistState();
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
        match = entry.name.match(/_v(\d{3,5})_(?:t|take)?(\d{2,5})\.aep$/i);
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
    var hostMethod = manifest.schema === "smartpipeline.render_manifest.v1"
      ? "buildPreviewRenderManifest"
      : "runAeBuildManifest";
    var result = await window.SmartCEPBridge.callHost(hostMethod, { path: manifest.path }, "");
    var payload;
    var message;
    var saveTarget;
    var saveResult;
    var savePayload;
    if (!result) {
      setStatus("Build failed: After Effects returned no response");
      return;
    }
    try {
      payload = result ? JSON.parse(result) : {};
    } catch (error) {
      setStatus("Build failed: " + String(result).slice(0, 240));
      return;
    }
    if (payload.error) {
      message = payload.error;
      if (payload.method) {
        message += " [" + payload.method + (payload.line ? ":" + payload.line : "") + "]";
      }
      setStatus("Build failed: " + message);
      return;
    }
    if (payload.ok === false) {
      message = (payload.errors || []).filter(Boolean).join("; ");
      setStatus("Build failed: " + (message || "After Effects did not complete the build"));
      return;
    }
    saveTarget = nextWorkSavePath();
    if (!saveTarget || !saveTarget.path) {
      setStatus("Build completed, but the work AEP path could not be resolved");
      return;
    }
    setStatus("Build completed. Saving AEP: " + basename(saveTarget.path));
    saveResult = await window.SmartCEPBridge.callHost(
      "saveAepProject", { path: saveTarget.path }, ""
    );
    try {
      savePayload = saveResult ? JSON.parse(saveResult) : {};
    } catch (saveError) {
      savePayload = { error: String(saveResult || saveError.message) };
    }
    if (!saveResult || savePayload.error || savePayload.ok === false) {
      setStatus(
        "Build completed, but Save As failed: "
        + (savePayload.error || "After Effects returned no response")
      );
      return;
    }
    commitWorkSave(saveTarget);
    state.selectedAepPath = saveTarget.path;
    clearAepCache();
    buildFromSelectedManifest(false);
    renderRows();
    setStatus(
      "Built and saved AEP: " + basename(saveTarget.path)
      + (payload.imported !== undefined ? " / " + payload.imported + " footage" : "")
    );
  }

  async function renderFinalComp() {
    var context = renderQueueContext();
    var result;
    var payload;
    var transcode;
    if (!context.outputPath) {
      setStatus("Could not resolve review movie path for the selected shot");
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
    if (payload.requiresTranscode) {
      setStatus("Encoding Apple ProRes 422 Proxy...");
      transcode = transcodeAeIntermediate(payload, context);
      if (!transcode.ok) {
        setStatus(
          "Render failed during ProRes encoding: "
          + (transcode.error || "Unknown ffmpeg error")
          + (payload.intermediatePath ? " / Intermediate: " + payload.intermediatePath : "")
        );
        return;
      }
      payload.outputModuleTemplate = transcode.codec || context.outputModuleTemplate;
      payload.format = "QuickTime";
    }
    setStatus(payload.warning ? "Rendered with warning: " + payload.warning : "Rendered " + basename(context.outputPath) + " / " + (payload.outputModuleTemplate || payload.outputModuleName || context.outputModuleTemplate) + (payload.format ? " / " + payload.format : ""));
  }

  function transcodeAeIntermediate(payload, context) {
    var fs = nodeRequire("fs");
    var pathModule = nodeRequire("path");
    var childProcess = nodeRequire("child_process");
    var project = selectedProject();
    var pipelineRoot;
    var scriptPath;
    var python;
    var args;
    var result;
    var response;
    if (!fs || !pathModule || !childProcess) {
      return { ok: false, error: "CEP Node runtime is unavailable" };
    }
    pipelineRoot = PIPELINE_ROOT;
    scriptPath = pathModule.join(pipelineRoot, "scripts", "transcode_ae_render.py");
    if (!fs.existsSync(scriptPath)) {
      return { ok: false, error: "ProRes transcoder was not found: " + scriptPath };
    }
    python = findPythonExecutable(fs, pathModule, pipelineRoot);
    args = [
      scriptPath,
      "--input", String(payload.intermediatePath || payload.renderedPath || ""),
      "--output", String(context.outputPath || payload.outputPath || ""),
      "--remove-source"
    ];
    if (project && project.configDir) {
      args.push("--config-dir", project.configDir);
    }
    try {
      result = childProcess.spawnSync(python, args, {
        encoding: "utf8",
        maxBuffer: 10 * 1024 * 1024
      });
    } catch (error) {
      return { ok: false, error: error.message };
    }
    response = parseLastJsonLine(result ? result.stdout : "");
    if (!response.ok) {
      response.error = response.error
        || String((result && (result.stderr || result.stdout)) || "ProRes encoding failed").trim();
    }
    return response;
  }

  async function refreshProjectFootageRows() {
    var result = await window.SmartCEPBridge.callHost("snapshotProjectFootage", undefined, "");
    var payload;
    var footages;
    if (!result) {
      return false;
    }
    try {
      payload = JSON.parse(result);
    } catch (error) {
      return false;
    }
    if (!payload || payload.hasProject === false) {
      return false;
    }
    footages = payload.items || payload;
    state.rows = rowsFromCurrentFootage(footages);
    return true;
  }

  async function refreshOutputStatus() {
    var usedProjectFootage = await refreshProjectFootageRows();
    if (usedProjectFootage) {
      renderRows();
      return;
    }

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

  async function copyManifestPath() {
    var manifest = getSelectedManifest();
    if (!manifest) {
      return;
    }
    if (await copyTextToClipboard(manifest.path)) {
      setStatus("Manifest path copied");
      return;
    }
    setStatus("Manifest path copy failed");
  }

  async function copyTextToClipboard(text) {
    var textarea;
    var copied = false;
    var childProcess;
    if (!text) {
      return false;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (error) {}
    }
    try {
      textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "readonly");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      textarea.style.top = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      copied = document.execCommand && document.execCommand("copy");
    } catch (error) {
      copied = false;
    } finally {
      if (textarea && textarea.parentNode) {
        textarea.parentNode.removeChild(textarea);
      }
    }
    if (copied) {
      return true;
    }
    childProcess = nodeRequire("child_process");
    if (childProcess) {
      try {
        return childProcess.spawnSync("clip.exe", [], { input: text, encoding: "utf8" }).status === 0;
      } catch (error) {}
    }
    return false;
  }

  function startPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
    }
    state.pollTimer = setInterval(refreshOutputStatus, POLL_INTERVAL_MS);
  }

  document.addEventListener("DOMContentLoaded", init);
}());
