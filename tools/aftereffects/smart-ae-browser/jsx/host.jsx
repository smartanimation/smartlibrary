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

    throw new Error(label + " template not found: " + requested.join(", ") + ". Available: " + available.join(", "));
  }

  function applyRenderSettingsTemplate(renderQueueItem, preferredName, aliases) {
    return applyNamedTemplate(renderQueueItem, preferredName || "Best Settings", aliases || [], availableRenderSettingsTemplates(renderQueueItem), "Render settings");
  }

  function applyOutputModuleTemplate(outputModule, preferredName, aliases) {
    return applyNamedTemplate(outputModule, preferredName || "Apple ProRes 422 Proxy", aliases || [], availableOutputModuleTemplates(outputModule), "Output module");
  }

  function outputModuleFormat(outputModule) {
    try {
      return outputModule.getSettings(GetSettingsFormat.STRING).Format || "";
    } catch (error) {
      return "";
    }
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
      $.evalFile(scriptFile);
      return stringify({ ok: true, script: scriptFile.fsName });
    } catch (error) {
      return stringify({ error: error.message });
    }
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
      try {
        app.endUndoGroup();
      } catch (endError) {}
      return stringify({ error: error.message });
    }
  };
}());
