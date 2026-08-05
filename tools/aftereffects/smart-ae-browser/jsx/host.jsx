var SmartAEBrowser = SmartAEBrowser || {};

(function () {
  function parseJson(value) {
    if (!value) {
      return null;
    }
    return JSON.parse(value);
  }

  function stringify(value) {
    return JSON.stringify(value);
  }

  function readFile(file) {
    file.encoding = "UTF-8";
    if (!file.open("r")) {
      throw new Error("Could not open " + file.fsName);
    }
    var text = file.read();
    file.close();
    return text;
  }

  function writeFile(file, text) {
    file.encoding = "UTF-8";
    if (!file.open("w")) {
      throw new Error("Could not write " + file.fsName);
    }
    file.write(text);
    file.close();
  }

  function normalizePath(value) {
    return String(value || "").replace(/\\/g, "/").toLowerCase();
  }

  function basename(value) {
    return String(value || "").replace(/\\/g, "/").split("/").pop();
  }

  function isAbsolutePath(value) {
    return /^[a-zA-Z]:[\/\\]/.test(String(value || "")) || /^\/\//.test(String(value || "")) || String(value || "").charAt(0) === "/";
  }

  function joinPath(base, child) {
    return String(base || "").replace(/[\/\\]+$/, "") + "/" + String(child || "").replace(/^[\/\\]+/, "");
  }

  function resolveManifestFile(data, manifestFile, value) {
    var path = String(value || "");
    var root = String(data.package_root || data.packageRoot || manifestFile.parent.fsName);
    if (!path) {
      return "";
    }
    return File(isAbsolutePath(path) ? path : joinPath(root, path)).fsName;
  }

  function ensureFolder(folder) {
    if (!folder.exists) {
      ensureFolder(folder.parent);
      folder.create();
    }
  }

  function isFootageItem(item) {
    return item && item.mainSource && item.replace;
  }

  function getFootagePath(item) {
    try {
      if (item.mainSource && item.mainSource.file) {
        return item.mainSource.file.fsName;
      }
    } catch (error) {
      return "";
    }
    return "";
  }

  function pathVersionTake(value) {
    var text = String(value || "");
    var normalized = text.replace(/\\/g, "/");
    var parts = normalized.split("/");
    var versionMatch = text.match(/(?:^|[_\-.\/\\])(v\d{2,5})(?:$|[_\-.\/\\])/i);
    var takeMatch = text.match(/(?:^|[_\-.\/\\])((?:t|take)\d{1,5})(?:$|[_\-.\/\\])/i);
    var version = versionMatch ? versionMatch[1].toLowerCase() : "";
    var take = takeMatch ? normalizeTake(takeMatch[1]) : "";
    var i;
    for (i = 0; i < parts.length; i += 1) {
      if (!version && /^v\d{2,5}$/i.test(parts[i])) {
        version = parts[i].toLowerCase();
      }
      if (!take && /^v\d{2,5}$/i.test(parts[i]) && parts[i + 1] && /^\d{1,5}$/.test(parts[i + 1])) {
        take = normalizeTake(parts[i + 1]);
      }
    }
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
    var digits = match[1];
    while (digits.length < 3) {
      digits = "0" + digits;
    }
    return "t" + digits;
  }

  function footageMatchesTake(item, mapping) {
    var current = pathVersionTake(getFootagePath(item));
    if (mapping.version && current.version && current.version !== String(mapping.version).toLowerCase()) {
      return false;
    }
    if (mapping.take && current.take && current.take !== normalizeTake(mapping.take)) {
      return false;
    }
    if (mapping.latestTake && current.take && current.take === normalizeTake(mapping.latestTake)) {
      return false;
    }
    return true;
  }

  function env(name) {
    try {
      return $.getenv(name) || "";
    } catch (error) {
      return "";
    }
  }

  function readJsonIfExists(path) {
    var file;
    if (!path) {
      return null;
    }
    file = File(path);
    if (!file.exists) {
      return null;
    }
    try {
      return JSON.parse(readFile(file));
    } catch (error) {
      return null;
    }
  }

  function patchReviewBuildScript(text) {
    var oldImport = [
      "var options = new ImportOptions(file);",
      "        options.sequence = true;",
      "        options.forceAlphabetical = true;",
      "        var footage = app.project.importFile(options);"
    ].join("\n");
    var newImport = [
      "var options = new ImportOptions(file);",
      "        var importAsSequence = row && row.image_sequence && String(row.image_sequence).indexOf(\"####\") !== -1 && Number(row.duration_frames || 0) > 1 && Number(row.start_frame || 0) < Number(row.end_frame || 0);",
      "        options.sequence = importAsSequence;",
      "        options.forceAlphabetical = importAsSequence;",
      "        log(\"Import mode for \" + row.layer + \": \" + (options.sequence ? \"sequence\" : \"still\"));",
      "        var footage = app.project.importFile(options);"
    ].join("\n");
    if (String(text || "").indexOf("shouldImportAsSequence(row)") !== -1 || String(text || "").indexOf("var importAsSequence = row && row.image_sequence") !== -1) {
      return text;
    }
    return String(text || "").split(oldImport).join(newImport);
  }

  function findFootage(mapping) {
    var sourcePath = normalizePath(mapping.sourcePath);
    var outputBase = basename(mapping.outputPath).toLowerCase();
    var name = String(mapping.name || "").toLowerCase();
    var item;
    var i;

    if (!app.project) {
      return null;
    }

    for (i = 1; i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (!isFootageItem(item)) {
        continue;
      }
      if (sourcePath && normalizePath(getFootagePath(item)) === sourcePath && footageMatchesTake(item, mapping)) {
        return item;
      }
    }

    for (i = 1; i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (!isFootageItem(item)) {
        continue;
      }
      if (
        name
        && item.parentFolder
        && String(item.parentFolder.name || "").toLowerCase() === name
        && footageMatchesTake(item, mapping)
      ) {
        return item;
      }
      if (name && String(item.name || "").toLowerCase() === name && footageMatchesTake(item, mapping)) {
        return item;
      }
      if (outputBase && String(item.name || "").toLowerCase() === outputBase && footageMatchesTake(item, mapping)) {
        return item;
      }
    }

    return null;
  }

  function splitCsv(value) {
    var parts = String(value || "").split(",");
    var result = [];
    var i;
    for (i = 0; i < parts.length; i += 1) {
      if (String(parts[i]).replace(/^\s+|\s+$/g, "")) {
        result.push(String(parts[i]).replace(/^\s+|\s+$/g, ""));
      }
    }
    return result;
  }

  function findCompByNames(names) {
    var item;
    var lookup = {};
    var i;
    for (i = 0; i < names.length; i += 1) {
      lookup[String(names[i] || "").toLowerCase()] = true;
    }
    if (!app.project) {
      return null;
    }
    for (i = 1; i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (item instanceof CompItem && lookup[String(item.name || "").toLowerCase()]) {
        return item;
      }
    }
    return null;
  }

  function setOutputAudio(outputModule, enabled) {
    var settings = {};
    settings["Audio Output"] = enabled ? "On" : "Off";
    try {
      outputModule.setSettings(settings);
      return "";
    } catch (error) {
      return error.message;
    }
  }

  function availableOutputModuleTemplates(outputModule) {
    try {
      return outputModule.templates || [];
    } catch (error) {
      return [];
    }
  }

  function availableRenderSettingsTemplates(renderQueueItem) {
    try {
      return renderQueueItem.templates || [];
    } catch (error) {
      return [];
    }
  }

  function applyNamedTemplate(target, preferredName, aliases, available, label) {
    var requested = [];
    var i;
    var j;

    if (preferredName) {
      requested.push(preferredName);
    }
    aliases = aliases || [];
    for (i = 0; i < aliases.length; i += 1) {
      requested.push(aliases[i]);
    }

    for (i = 0; i < requested.length; i += 1) {
      for (j = 0; j < available.length; j += 1) {
        if (String(available[j]) === String(requested[i])) {
          target.applyTemplate(available[j]);
          return available[j];
        }
      }
    }

    for (i = 0; i < requested.length; i += 1) {
      for (j = 0; j < available.length; j += 1) {
        if (String(available[j]).toLowerCase() === String(requested[i]).toLowerCase()) {
          target.applyTemplate(available[j]);
          return available[j];
        }
      }
    }

    for (i = 0; i < requested.length; i += 1) {
      for (j = 0; j < available.length; j += 1) {
        if (normalizeTemplateName(available[j]) === normalizeTemplateName(requested[i])) {
          target.applyTemplate(available[j]);
          return available[j];
        }
      }
    }

    throw new Error(label + " template not found: " + requested.join(", ") + ". Available: " + available.join(", "));
  }

  function applyRenderSettingsTemplate(renderQueueItem, preferredName, aliases) {
    return applyNamedTemplate(renderQueueItem, preferredName || "Best Settings", aliases || ["Best Settings"], availableRenderSettingsTemplates(renderQueueItem), "Render settings");
  }

  function applyOutputModuleTemplate(outputModule, preferredName, aliases) {
    return applyNamedTemplate(outputModule, preferredName || "Apple ProRes 422 Proxy", aliases || [], availableOutputModuleTemplates(outputModule), "Output module");
  }

  function normalizeTemplateName(value) {
    return String(value || "").replace(/[\u200B-\u200D\uFEFF]/g, "").replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "").toLowerCase();
  }

  function outputModuleFormat(outputModule) {
    try {
      return outputModule.getSettings(GetSettingsFormat.STRING).Format || "";
    } catch (error) {
      return "";
    }
  }

  function findFolder(name, parent) {
    var item;
    var i;
    for (i = 1; app.project && i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (!(item instanceof FolderItem) || item.name !== name) {
        continue;
      }
      if (!parent || item.parentFolder === parent) {
        return item;
      }
    }
    return null;
  }

  function ensureProjectFolder(name, parent) {
    var folder = findFolder(name, parent || null);
    if (!folder) {
      folder = app.project.items.addFolder(name);
    }
    if (parent) {
      try {
        folder.parentFolder = parent;
      } catch (error) {}
    }
    return folder;
  }

  function moveToFolder(item, folder) {
    if (item && folder) {
      try {
        item.parentFolder = folder;
      } catch (error) {}
    }
  }

  function findComp(name) {
    var item;
    var i;
    for (i = 1; app.project && i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (item instanceof CompItem && item.name === name) {
        return item;
      }
    }
    return null;
  }

  function ensureComp(name, width, height, duration, fps, folder) {
    var comp = findComp(name);
    if (!comp) {
      comp = app.project.items.addComp(name, width, height, 1.0, duration, fps);
    }
    try {
      comp.width = width;
    } catch (error) {}
    try {
      comp.height = height;
    } catch (error2) {}
    comp.duration = duration;
    comp.frameRate = fps;
    moveToFolder(comp, folder);
    return comp;
  }

  function clearComp(comp) {
    try {
      while (comp.numLayers > 0) {
        comp.layer(1).remove();
      }
    } catch (error) {}
  }

  function addSourceLayer(comp, source, name) {
    var layer = comp.layers.add(source);
    layer.startTime = 0;
    if (name) {
      layer.name = name;
    }
    return layer;
  }

  function parseNumber(value, fallback) {
    var number = Number(value);
    return isNaN(number) ? fallback : number;
  }

  function frameRangeFrom(value, fallbackStart, fallbackEnd) {
    if (value && value.length >= 2) {
      return [parseNumber(value[0], fallbackStart), parseNumber(value[1], fallbackEnd)];
    }
    return [fallbackStart, fallbackEnd];
  }

  function readShotJson(data) {
    var projectRoot = String(data.projectRoot || data.project_root || "");
    var shotFile;
    var rootParts;
    var shotIndex;
    var packageRoot;
    if (projectRoot && data.episode && data.sequence && data.shot) {
      shotFile = File(joinPath(projectRoot, "shots/" + data.episode + "/" + data.sequence + "/" + data.shot + "/shot.json"));
    } else {
      packageRoot = String(data.package_root || data.packageRoot || "");
      rootParts = packageRoot.replace(/\\/g, "/").split("/");
      shotIndex = rootParts.join("/").toLowerCase().indexOf("/publish/preview_render/");
      if (shotIndex !== -1) {
        shotFile = File(rootParts.join("/").slice(0, shotIndex) + "/shot.json");
      }
    }
    if (!shotFile || !shotFile.exists) {
      return {};
    }
    try {
      return JSON.parse(readFile(shotFile));
    } catch (error) {
      return {};
    }
  }

  function readProjectResolution(data) {
    var configDir = String(data.configDir || data.config_dir || "");
    var file;
    var text;
    var match;
    if (!configDir) {
      return [];
    }
    file = File(joinPath(configDir, "templates_base.yml"));
    if (!file.exists) {
      return [];
    }
    try {
      text = readFile(file);
    } catch (error) {
      return [];
    }
    match = text.match(/resolution\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*\]/i);
    if (match) {
      return [Number(match[1]), Number(match[2])];
    }
    match = text.match(/resolution\s*:\s*(?:\r?\n)\s*-\s*(\d+)\s*(?:\r?\n)\s*-\s*(\d+)/i);
    return match ? [Number(match[1]), Number(match[2])] : [];
  }

  function buildTiming(data, items) {
    var shot = readShotJson(data);
    var editorial = shot.editorial || {};
    var range = frameRangeFrom(editorial.frame_range || shot.frame_range, parseNumber(editorial.cut_in || shot.cut_in, 1), parseNumber(editorial.cut_out || shot.cut_out, 1));
    var fps = parseNumber(editorial.fps || shot.fps || data.fps, 24);
    var finalResolution = readProjectResolution(data);
    var stageResolution = [];
    var i;
    if (!finalResolution.length && shot.resolution && shot.resolution.length >= 2) {
      finalResolution = [Number(shot.resolution[0]), Number(shot.resolution[1])];
    }
    if (!finalResolution.length && data.resolution && data.resolution.length >= 2) {
      finalResolution = [Number(data.resolution[0]), Number(data.resolution[1])];
    }
    for (i = 0; i < items.length && !stageResolution.length; i += 1) {
      if (items[i].resolution && items[i].resolution.length >= 2) {
        stageResolution = [Number(items[i].resolution[0]), Number(items[i].resolution[1])];
      }
    }
    if (!finalResolution.length) {
      finalResolution = stageResolution.length ? stageResolution : [1920, 1080];
    }
    if (!stageResolution.length) {
      stageResolution = finalResolution;
    }
    return {
      start: range[0],
      end: range[1],
      frames: Math.max(1, range[1] - range[0] + 1),
      duration: Math.max(1, range[1] - range[0] + 1) / fps,
      fps: fps,
      finalWidth: finalResolution[0],
      finalHeight: finalResolution[1],
      stageWidth: stageResolution[0],
      stageHeight: stageResolution[1]
    };
  }

  function previewRenderBuildItems(data, manifestFile) {
    var rawItems = data.items || [];
    var groups = data.layers || data.groups || {};
    var order = data.layer_order || data.group_order || [];
    var byName = {};
    var result = [];
    var names = [];
    var item;
    var group;
    var name;
    var i;
    for (i = 0; i < rawItems.length; i += 1) {
      item = rawItems[i] || {};
      name = String(item.layer || item.name || item.id || "");
      if (name) {
        byName[name] = item;
      }
    }
    for (name in groups) {
      if (groups.hasOwnProperty(name) && !byName[name]) {
        byName[name] = { layer: name, name: name };
      }
    }
    names = order.length ? order : [];
    for (name in byName) {
      if (byName.hasOwnProperty(name) && names.join("|").indexOf(name) === -1) {
        names.push(name);
      }
    }
    for (i = 0; i < names.length; i += 1) {
      name = String(names[i]);
      item = byName[name] || {};
      group = groups[name] || {};
      result.push({
        layer: name,
        first_frame_file: item.first_frame_file || group.first_file || item.sourcePath || "",
        image_sequence: item.outputPath || group.pattern || "",
        file_count: parseNumber(item.file_count || group.file_count, 0),
        start_frame: parseNumber((item.frame_range || group.frame_range || [])[0], parseNumber(data.start_frame, 1)),
        end_frame: parseNumber((item.frame_range || group.frame_range || [])[1], parseNumber(data.end_frame, 1)),
        duration_frames: parseNumber(item.duration_frames || group.duration_frames, 0),
        resolution: item.resolution || group.resolution || [],
        order: parseNumber(group.order || item.order, i * 10),
        firstFramePath: resolveManifestFile(data, manifestFile, item.first_frame_file || group.first_file || item.sourcePath || "")
      });
    }
    return result.sort(function (a, b) {
      return a.order - b.order;
    });
  }

  function shouldImportAsSequence(row) {
    if (!row || String(row.image_sequence || "").indexOf("####") === -1) {
      return false;
    }
    if (row.file_count && Number(row.file_count) <= 1) {
      return false;
    }
    if (row.file_count && Number(row.file_count) > 1) {
      return true;
    }
    if (Number(row.duration_frames || 0) <= 1) {
      return false;
    }
    return Number(row.start_frame || 0) < Number(row.end_frame || 0);
  }

  function padFrame(value, width) {
    var digits = String(Math.max(0, parseNumber(value, 0)));
    while (digits.length < width) {
      digits = "0" + digits;
    }
    return digits;
  }

  function sequenceFootageName(row, file, importAsSequence) {
    var source = String(row.image_sequence || "");
    var name;
    var match;
    if (!importAsSequence) {
      return basename(file.fsName);
    }
    if (!source) {
      source = file.fsName;
    }
    name = basename(source);
    match = name.match(/(#+)(\.[^.]*)$/);
    if (match) {
      return name.replace(match[1] + match[2], "[" + padFrame(row.start_frame, match[1].length) + "-" + padFrame(row.end_frame, match[1].length) + "]" + match[2]);
    }
    match = name.match(/(\d+)(\.[^.]*)$/);
    if (match) {
      return name.replace(match[1] + match[2], "[" + padFrame(row.start_frame, match[1].length) + "-" + padFrame(row.end_frame, match[1].length) + "]" + match[2]);
    }
    return name;
  }

  function findFootageByPath(path) {
    var normalized = normalizePath(path);
    var item;
    var i;
    for (i = 1; app.project && i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (isFootageItem(item) && normalizePath(getFootagePath(item)) === normalized) {
        return item;
      }
    }
    return null;
  }

  function importPreviewFootage(row, folder) {
    var file = File(row.firstFramePath);
    var options;
    var footage;
    var importAsSequence;
    if (!file.exists) {
      return null;
    }
    importAsSequence = shouldImportAsSequence(row);
    footage = findFootageByPath(file.fsName);
    if (!footage) {
      options = new ImportOptions(file);
      if (options.canImportAs && options.canImportAs(ImportAsType.FOOTAGE)) {
        options.importAs = ImportAsType.FOOTAGE;
      }
      options.sequence = importAsSequence;
      options.forceAlphabetical = importAsSequence;
      footage = app.project.importFile(options);
    }
    footage.name = sequenceFootageName(row, file, importAsSequence);
    moveToFolder(footage, folder);
    return footage;
  }

  SmartAEBrowser.openManifestDialog = function () {
    var file = File.openDialog("Import smart render manifest", "JSON:*.json");
    if (!file) {
      return "";
    }
    try {
      return stringify({
        path: file.fsName,
        data: JSON.parse(readFile(file))
      });
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.getLaunchContext = function () {
    var appData = env("APPDATA");
    var contextPath = appData ? appData + "/smartuserdata/smart_ae_browser_context.json" : "";
    var fileContext = readJsonIfExists(contextPath) || {};
    var context = {
      source: env("SMART_CONTEXT_SOURCE") || fileContext.source || "",
      project: env("SMART_PROJECT") || env("PROJECT_NAME") || fileContext.project || "",
      projectRoot: env("SMART_PROJECT_ROOT") || env("PROJECT_ROOT") || fileContext.projectRoot || "",
      configDir: env("SMART_PROJECT_CONFIG_DIR") || env("PROJECT_CONFIG_DIR") || fileContext.configDir || "",
      episode: env("SMART_EPISODE") || env("EPISODE") || fileContext.episode || "",
      sequence: env("SMART_SEQUENCE") || env("SEQUENCE") || env("SEQ") || fileContext.sequence || "",
      shot: env("SMART_SHOT") || env("SHOT") || fileContext.shot || "",
      manifest: env("SMART_AE_MANIFEST") || fileContext.manifest || "",
      publishRoot: fileContext.publishRoot || "",
      contextPath: contextPath
    };
    return stringify(context);
  };

  SmartAEBrowser.openSettingsDialog = function () {
    var file = File.openDialog("Open smart AE browser settings", "JSON:*.json");
    if (!file) {
      return "";
    }
    try {
      return readFile(file);
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.saveSettingsDialog = function (payloadJson) {
    var payload = parseJson(payloadJson);
    var file = File.saveDialog("Save smart AE browser settings", "JSON:*.json");
    if (!file) {
      return "false";
    }
    try {
      writeFile(file, stringify(payload));
      return "true";
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.openAepProject = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var file = File(payload.path);
    if (!file.exists) {
      return "false";
    }
    try {
      app.open(file);
      return "true";
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.saveAepProject = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var file = File(payload.path);
    try {
      ensureFolder(file.parent);
      if (!app.project) {
        app.newProject();
      }
      app.project.save(file);
      return stringify({ ok: true, path: file.fsName });
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.runAeBuildManifest = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var manifestFile = File(payload.path);
    var data;
    var root;
    var scriptPath;
    var scriptFile;
    var scriptText;

    if (!manifestFile.exists) {
      return stringify({ error: "Build manifest was not found: " + manifestFile.fsName });
    }

    try {
      data = JSON.parse(readFile(manifestFile));
      root = data.package_root ? Folder(data.package_root) : manifestFile.parent.parent.parent;
      scriptPath = String(data.script || "ae/scripts/build_review.jsx");
      scriptFile = File(isAbsolutePath(scriptPath) ? scriptPath : joinPath(root.fsName, scriptPath));
      if (!scriptFile.exists) {
        return stringify({ error: "Build script was not found: " + scriptFile.fsName });
      }
      scriptText = patchReviewBuildScript(readFile(scriptFile));
      eval(scriptText);
      return stringify({ ok: true, script: scriptFile.fsName });
    } catch (error) {
      return stringify({ error: error.message });
    }
  };

  SmartAEBrowser.buildPreviewRenderManifest = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var manifestFile = File(payload.path);
    var data;
    var templateFile;
    var items;
    var imported = 0;
    var precomps = 0;
    var errors = [];
    var folders;
    var timing;
    var stage;
    var camera;
    var finalComp;
    var item;
    var footage;
    var precomp;
    var stageLayer;
    var layerWidth;
    var layerHeight;
    var i;

    if (!manifestFile.exists) {
      return stringify({ error: "Preview Render manifest was not found: " + manifestFile.fsName });
    }

    try {
      data = JSON.parse(readFile(manifestFile));
      templateFile = File(String(data.template_project || ""));
      if (templateFile.exists) {
        app.open(templateFile);
      } else if (!app.project) {
        app.newProject();
      }
      items = previewRenderBuildItems(data, manifestFile);
      timing = buildTiming(data, items);
      folders = {
        render: ensureProjectFolder("00_render"),
        comp: ensureProjectFolder("10_comp"),
        precomp: ensureProjectFolder("20_precomp"),
        footage: ensureProjectFolder("30_footage")
      };
      folders.dept = ensureProjectFolder(String(data.department || "anim"), folders.footage);
      folders.layers = ensureProjectFolder("layers", folders.dept);

      app.beginUndoGroup("Build Preview Render");
      finalComp = ensureComp("final", timing.finalWidth, timing.finalHeight, timing.duration, timing.fps, folders.render);
      camera = ensureComp("camera", timing.finalWidth, timing.finalHeight, timing.duration, timing.fps, folders.comp);
      stage = ensureComp("stage", timing.stageWidth, timing.stageHeight, timing.duration, timing.fps, folders.comp);
      try {
        finalComp.displayStartFrame = timing.start;
        camera.displayStartFrame = timing.start;
        stage.displayStartFrame = timing.start;
      } catch (displayError) {}
      clearComp(finalComp);
      clearComp(camera);
      clearComp(stage);

      // AE inserts added layers at index 1, so add bottom-to-top to preserve manifest order.
      for (i = items.length - 1; i >= 0; i -= 1) {
        item = items[i] || {};
        if (!item.firstFramePath || !File(item.firstFramePath).exists) {
          errors.push("Missing footage: " + (item.layer || item.firstFramePath));
          continue;
        }
        footage = importPreviewFootage(item, ensureProjectFolder(item.layer || "layer", folders.layers));
        if (!footage) {
          errors.push("Could not import footage: " + (item.layer || item.firstFramePath));
          continue;
        }
        imported += 1;
        layerWidth = item.resolution && item.resolution.length >= 2 ? Number(item.resolution[0]) : timing.stageWidth;
        layerHeight = item.resolution && item.resolution.length >= 2 ? Number(item.resolution[1]) : timing.stageHeight;
        precomp = ensureComp(item.layer, layerWidth, layerHeight, timing.duration, timing.fps, folders.precomp);
        clearComp(precomp);
        addSourceLayer(precomp, footage, "");
        stageLayer = addSourceLayer(stage, precomp, item.layer);
        precomps += 1;
      }

      addSourceLayer(camera, stage, "stage");
      addSourceLayer(finalComp, camera, "camera");
      try {
        finalComp.openInViewer();
        app.project.activeItem = finalComp;
        app.activate();
      } catch (activateError) {}
    } catch (error) {
      return stringify({ error: error.message, imported: imported, precomps: precomps, errors: errors });
    } finally {
      try {
        app.endUndoGroup();
      } catch (ignore) {}
    }
    return stringify({
      ok: errors.length === 0,
      imported: imported,
      precomps: precomps,
      template: templateFile && templateFile.exists ? templateFile.fsName : "",
      errors: errors
    });
  };

  SmartAEBrowser.snapshotOutputs = function (pathsJson) {
    var paths = parseJson(pathsJson) || [];
    var snapshots = [];
    var i;
    var file;

    for (i = 0; i < paths.length; i += 1) {
      file = File(paths[i]);
      snapshots.push({
        path: paths[i],
        exists: file.exists,
        modified: file.exists ? String(file.modified.getTime()) : "",
        size: file.exists ? file.length : 0
      });
    }

    return stringify(snapshots);
  };

  SmartAEBrowser.replaceAssets = function (mappingsJson) {
    var mappings = parseJson(mappingsJson) || [];
    var errors = [];
    var replaced = 0;
    var mapping;
    var item;
    var file;
    var i;

    if (!app.project) {
      return stringify({ replaced: 0, errors: ["No After Effects project is open"] });
    }

    app.beginUndoGroup("smart AE browser replace assets");
    try {
      for (i = 0; i < mappings.length; i += 1) {
        mapping = mappings[i];
        file = File(mapping.replacePath || mapping.checkPath || mapping.outputPath);
        if (!file.exists) {
          errors.push("Missing output: " + (mapping.replacePath || mapping.checkPath || mapping.outputPath));
          continue;
        }

        item = findFootage(mapping);
        if (!item) {
          errors.push("Footage not found: " + (mapping.name || mapping.sourcePath || mapping.outputPath));
          continue;
        }

        if (mapping.isSequence && item.replaceWithSequence) {
          item.replaceWithSequence(file, false);
        } else {
          item.replace(file);
        }
        if (mapping.name) {
          item.name = mapping.name;
        }
        replaced += 1;
      }
    } catch (error) {
      errors.push(error.message);
    } finally {
      app.endUndoGroup();
    }

    return stringify({
      replaced: replaced,
      errors: errors
    });
  };

  SmartAEBrowser.renderFinalComp = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var compNames = [payload.compName || "final"];
    var fallbackNames = payload.fallbackNames || splitCsv(payload.fallback_names || "");
    var outputPath = String(payload.outputPath || "");
    var comp;
    var file;
    var item;
    var module;
    var appliedRenderSettings;
    var appliedOutputTemplate;
    var audioWarning;
    var format;
    var warnings = [];
    var renderStarted = false;
    var i;

    for (i = 0; i < fallbackNames.length; i += 1) {
      compNames.push(fallbackNames[i]);
    }

    if (!app.project) {
      return stringify({ error: "No After Effects project is open" });
    }
    if (!outputPath) {
      return stringify({ error: "Output path is empty" });
    }

    comp = findCompByNames(compNames);
    if (!comp) {
      return stringify({ error: "Final comp was not found: " + compNames.join(", ") });
    }

    try {
      file = File(outputPath);
      ensureFolder(file.parent);
      app.beginUndoGroup("smart AE browser render final comp");
      item = app.project.renderQueue.items.add(comp);
      item.render = true;
      try {
        appliedRenderSettings = applyRenderSettingsTemplate(item, payload.renderSettingsTemplate || "Best Settings", payload.renderSettingsTemplateAliases || []);
      } catch (settingsError) {
        warnings.push(settingsError.message);
      }
      module = item.outputModule(1);
      appliedOutputTemplate = applyOutputModuleTemplate(module, payload.outputModuleTemplate || "Apple ProRes 422 Proxy", payload.outputModuleTemplateAliases || []);
      audioWarning = setOutputAudio(module, payload.audioOutput !== false);
      if (audioWarning) {
        warnings.push("Audio Output setting was not changed: " + audioWarning);
      }
      module = item.outputModule(1);
      module.file = file;
      format = outputModuleFormat(module);
      app.endUndoGroup();
      renderStarted = true;
      app.project.renderQueue.render();
      return stringify({
        ok: true,
        comp: comp.name,
        outputPath: file.fsName,
        renderSettingsTemplate: appliedRenderSettings || "",
        outputModuleTemplate: appliedOutputTemplate,
        outputModuleName: module.name,
        format: format,
        warning: warnings.join("; ")
      });
    } catch (error) {
      if (!renderStarted && item) {
        try {
          item.remove();
        } catch (removeError) {}
      }
      try {
        app.endUndoGroup();
      } catch (endError) {}
      return stringify({ error: error.message });
    }
  };
}());
