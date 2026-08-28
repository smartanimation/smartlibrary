var JSON = JSON || {};

(function (json) {
  function parser(source) {
    var text = String(source || "");
    var at = 0;
    var ch = " ";

    function fail(message) {
      throw new Error("Invalid JSON: " + message + " at " + Math.max(0, at - 1));
    }

    function next(expected) {
      if (expected && expected !== ch) {
        fail("Expected '" + expected + "' instead of '" + ch + "'");
      }
      ch = text.charAt(at);
      at += 1;
      return ch;
    }

    function white() {
      while (ch && ch <= " ") {
        next();
      }
    }

    function parseString() {
      var value = "";
      var code;
      var hex;
      var i;
      if (ch !== "\"") {
        fail("Expected string");
      }
      while (next()) {
        if (ch === "\"") {
          next();
          return value;
        }
        if (ch === "\\") {
          next();
          if (ch === "u") {
            code = 0;
            for (i = 0; i < 4; i += 1) {
              hex = parseInt(next(), 16);
              if (!isFinite(hex)) {
                fail("Invalid unicode escape");
              }
              code = code * 16 + hex;
            }
            value += String.fromCharCode(code);
          } else if (ch === "b") {
            value += "\b";
          } else if (ch === "f") {
            value += "\f";
          } else if (ch === "n") {
            value += "\n";
          } else if (ch === "r") {
            value += "\r";
          } else if (ch === "t") {
            value += "\t";
          } else if (ch === "\"" || ch === "\\" || ch === "/") {
            value += ch;
          } else {
            fail("Invalid escape");
          }
        } else {
          value += ch;
        }
      }
      fail("Unterminated string");
    }

    function parseNumber() {
      var value = "";
      var number;
      if (ch === "-") {
        value = "-";
        next("-");
      }
      while (ch >= "0" && ch <= "9") {
        value += ch;
        next();
      }
      if (ch === ".") {
        value += ".";
        while (next() && ch >= "0" && ch <= "9") {
          value += ch;
        }
      }
      if (ch === "e" || ch === "E") {
        value += ch;
        next();
        if (ch === "-" || ch === "+") {
          value += ch;
          next();
        }
        while (ch >= "0" && ch <= "9") {
          value += ch;
          next();
        }
      }
      number = Number(value);
      if (!isFinite(number)) {
        fail("Invalid number");
      }
      return number;
    }

    function parseWord() {
      if (ch === "t") {
        next("t");
        next("r");
        next("u");
        next("e");
        return true;
      }
      if (ch === "f") {
        next("f");
        next("a");
        next("l");
        next("s");
        next("e");
        return false;
      }
      if (ch === "n") {
        next("n");
        next("u");
        next("l");
        next("l");
        return null;
      }
      fail("Unexpected token");
    }

    function parseArray() {
      var result = [];
      next("[");
      white();
      if (ch === "]") {
        next("]");
        return result;
      }
      while (ch) {
        result.push(parseValue());
        white();
        if (ch === "]") {
          next("]");
          return result;
        }
        next(",");
        white();
      }
      fail("Unterminated array");
    }

    function parseObject() {
      var result = {};
      var key;
      next("{");
      white();
      if (ch === "}") {
        next("}");
        return result;
      }
      while (ch) {
        key = parseString();
        white();
        next(":");
        result[key] = parseValue();
        white();
        if (ch === "}") {
          next("}");
          return result;
        }
        next(",");
        white();
      }
      fail("Unterminated object");
    }

    function parseValue() {
      white();
      if (ch === "{") {
        return parseObject();
      }
      if (ch === "[") {
        return parseArray();
      }
      if (ch === "\"") {
        return parseString();
      }
      if (ch === "-" || (ch >= "0" && ch <= "9")) {
        return parseNumber();
      }
      return parseWord();
    }

    next();
    var result = parseValue();
    white();
    if (ch) {
      fail("Unexpected trailing content");
    }
    return result;
  }

  function quote(value) {
    var escapes = {
      "\b": "\\b",
      "\t": "\\t",
      "\n": "\\n",
      "\f": "\\f",
      "\r": "\\r",
      "\"": "\\\"",
      "\\": "\\\\"
    };
    return "\"" + String(value).replace(/[\\\"\u0000-\u001f\u007f-\u009f]/g, function (character) {
      var escaped = escapes[character];
      var code;
      if (escaped) {
        return escaped;
      }
      code = character.charCodeAt(0).toString(16);
      return "\\u" + ("0000" + code).slice(-4);
    }) + "\"";
  }

  function stringifier(value) {
    var stack = [];

    function contains(reference) {
      var i;
      for (i = 0; i < stack.length; i += 1) {
        if (stack[i] === reference) {
          return true;
        }
      }
      return false;
    }

    function encode(current, inArray) {
      var type = typeof current;
      var parts;
      var key;
      var encoded;
      var i;
      if (current === null) {
        return "null";
      }
      if (type === "string") {
        return quote(current);
      }
      if (type === "number") {
        return isFinite(current) ? String(current) : "null";
      }
      if (type === "boolean") {
        return current ? "true" : "false";
      }
      if (type === "undefined" || type === "function") {
        return inArray ? "null" : undefined;
      }
      if (current && typeof current.toJSON === "function") {
        return encode(current.toJSON(), inArray);
      }
      if (contains(current)) {
        throw new Error("Converting circular structure to JSON");
      }
      stack.push(current);
      parts = [];
      if (current instanceof Array) {
        for (i = 0; i < current.length; i += 1) {
          parts.push(encode(current[i], true));
        }
        stack.pop();
        return "[" + parts.join(",") + "]";
      }
      for (key in current) {
        if (current.hasOwnProperty(key)) {
          encoded = encode(current[key], false);
          if (encoded !== undefined) {
            parts.push(quote(key) + ":" + encoded);
          }
        }
      }
      stack.pop();
      return "{" + parts.join(",") + "}";
    }

    return encode(value, false);
  }

  if (typeof json.parse !== "function") {
    json.parse = parser;
  }
  if (typeof json.stringify !== "function") {
    json.stringify = stringifier;
  }
}(JSON));

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

  function itemParentName(item) {
    try {
      return item.parentFolder ? String(item.parentFolder.name || "") : "";
    } catch (error) {
      return "";
    }
  }

  function footageFileSnapshot(item) {
    var file;
    var path = getFootagePath(item);
    var info = pathVersionTake(path);
    if (!path) {
      return null;
    }
    file = File(path);
    return {
      name: String(item.name || ""),
      layer: itemParentName(item),
      path: path,
      sourcePath: path,
      version: info.version,
      take: info.take,
      exists: file.exists,
      modified: file.exists ? String(file.modified.getTime()) : "",
      size: file.exists ? file.length : 0,
      isSequence: !(item.mainSource && item.mainSource.isStill === true)
    };
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
    width = Math.max(4, Math.round(parseNumber(width, 1920)));
    height = Math.max(4, Math.round(parseNumber(height, 1080)));
    duration = Math.max(1 / 24, parseNumber(duration, 1));
    fps = Math.max(1, parseNumber(fps, 24));
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
    var candidates = [];
    var shotFile;
    var rootParts;
    var shotIndex;
    var packageRoot;
    var i;
    if (data.shot_root || data.shotRoot) {
      candidates.push(joinPath(String(data.shot_root || data.shotRoot), "shot.json"));
    }
    packageRoot = String(data.package_root || data.packageRoot || "");
    if (packageRoot) {
      rootParts = packageRoot.replace(/\\/g, "/").split("/");
      shotIndex = rootParts.join("/").toLowerCase().indexOf("/publish/preview_render/");
      if (shotIndex !== -1) {
        candidates.push(rootParts.join("/").slice(0, shotIndex) + "/shot.json");
      }
      shotIndex = rootParts.join("/").toLowerCase().indexOf("/output/preview_render/");
      if (shotIndex !== -1) {
        candidates.push(rootParts.join("/").slice(0, shotIndex) + "/shot.json");
      }
    }
    if (projectRoot && data.episode && data.sequence && data.shot) {
      candidates.push(joinPath(projectRoot, "production/shots/" + data.episode + "/" + data.sequence + "/" + data.shot + "/shot.json"));
      candidates.push(joinPath(projectRoot, "shots/" + data.episode + "/" + data.sequence + "/" + data.shot + "/shot.json"));
    }
    for (i = 0; i < candidates.length; i += 1) {
      shotFile = File(candidates[i]);
      if (shotFile.exists) {
        try {
          return JSON.parse(readFile(shotFile));
        } catch (error) {
          return {};
        }
      }
    }
    return {};
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
      return normalizedResolution([match[1], match[2]]);
    }
    match = text.match(/resolution\s*:\s*(?:\r?\n)\s*-\s*(\d+)\s*(?:\r?\n)\s*-\s*(\d+)/i);
    return match ? normalizedResolution([match[1], match[2]]) : [];
  }

  function normalizedResolution(value) {
    var width;
    var height;
    if (!value || value.length < 2) {
      return [];
    }
    width = Number(value[0]);
    height = Number(value[1]);
    if (!isFinite(width) || !isFinite(height) || width <= 0 || height <= 0) {
      return [];
    }
    return [Math.round(width), Math.round(height)];
  }

  function buildTiming(data, items, shot) {
    shot = shot || readShotJson(data);
    var editorial = shot.editorial || {};
    var range = frameRangeFrom(editorial.frame_range || shot.frame_range, parseNumber(editorial.cut_in || shot.cut_in, 1), parseNumber(editorial.cut_out || shot.cut_out, 1));
    var fps = parseNumber(editorial.fps || shot.fps || data.fps, 24);
    var finalResolution = normalizedResolution(data.resolution);
    var stageResolution = [];
    var i;
    if (!finalResolution.length) {
      finalResolution = normalizedResolution(shot.resolution);
    }
    if (!finalResolution.length) {
      finalResolution = readProjectResolution(data);
    }
    for (i = 0; i < items.length && !stageResolution.length; i += 1) {
      stageResolution = normalizedResolution(items[i].resolution);
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
    var rawItems = (data.items && data.items.length) ? data.items : (data.rows || []);
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

  function validatePlayblastSettingsReceipts(data) {
    var items = data.items || [];
    var errors = [];
    var receiptFile;
    var receipt;
    var firstFile;
    var lastFile;
    var range;
    var resolution;
    var expectedCount;
    var item;
    var i;
    if (data.schema !== "smartpipeline.render_manifest.v1") {
      return errors;
    }
    if (!items.length) {
      return ["Playblast Settings has no material items."];
    }
    for (i = 0; i < items.length; i += 1) {
      item = items[i] || {};
      receiptFile = File(String(item.receipt_path || ""));
      if (!receiptFile.exists) {
        errors.push("Receipt is missing: " + (item.layer || receiptFile.fsName));
        continue;
      }
      try {
        receipt = JSON.parse(readFile(receiptFile));
      } catch (error) {
        errors.push("Receipt is invalid: " + receiptFile.fsName);
        continue;
      }
      if (String(receipt.status || "") !== "complete") {
        errors.push("Material is not complete: " + (item.layer || receiptFile.fsName));
      }
      if (String(receipt.settings_fingerprint || "") !== String(data.fingerprint || "")) {
        errors.push("Receipt belongs to different Playblast Settings: " + (item.layer || receiptFile.fsName));
      }
      firstFile = File(String(item.first_frame_file || item.sourcePath || ""));
      if (!firstFile.exists) {
        errors.push("First frame is missing: " + firstFile.fsName);
      }
      lastFile = File(joinPath(receiptFile.parent.fsName, String(receipt.last_file || "")));
      if (receipt.last_file && !lastFile.exists) {
        errors.push("Last frame is missing: " + lastFile.fsName);
      }
      range = item.frame_range || [];
      expectedCount = range.length >= 2
        ? Math.max(1, Number(range[1]) - Number(range[0]) + 1)
        : 0;
      if (expectedCount && Number(receipt.file_count || 0) !== expectedCount) {
        errors.push("Frame count differs from Playblast Settings: " + (item.layer || receiptFile.fsName));
      }
      resolution = item.resolution || [];
      if (resolution.length >= 2
          && (Number((receipt.resolution || [])[0]) !== Number(resolution[0])
            || Number((receipt.resolution || [])[1]) !== Number(resolution[1]))) {
        errors.push("Resolution differs from Playblast Settings: " + (item.layer || receiptFile.fsName));
      }
    }
    return errors;
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

  function shotRootFromData(data) {
    var packageRoot = String(data.package_root || data.packageRoot || "");
    var parts;
    var text;
    var index;
    if (data.shot_root || data.shotRoot) {
      return String(data.shot_root || data.shotRoot);
    }
    if (packageRoot) {
      parts = packageRoot.replace(/\\/g, "/").split("/");
      text = parts.join("/").toLowerCase();
      index = text.indexOf("/publish/preview_render/");
      if (index !== -1) {
        return parts.join("/").slice(0, index);
      }
      index = text.indexOf("/output/preview_render/");
      if (index !== -1) {
        return parts.join("/").slice(0, index);
      }
    }
    return "";
  }

  function resolveShotMediaFile(data, value) {
    var path = String(value || "");
    var projectRoot = String(data.projectRoot || data.project_root || "");
    var shotRoot = shotRootFromData(data);
    var candidates = [];
    var file;
    var i;
    if (!path) {
      return "";
    }
    if (isAbsolutePath(path)) {
      candidates.push(path);
    } else {
      if (projectRoot) {
        candidates.push(joinPath(projectRoot, path));
      }
      if (shotRoot) {
        candidates.push(joinPath(shotRoot, path));
      }
      candidates.push(path);
    }
    for (i = 0; i < candidates.length; i += 1) {
      file = File(candidates[i]);
      if (file.exists) {
        return file.fsName;
      }
    }
    return File(candidates[0]).fsName;
  }

  function shotAudioRow(data, shot) {
    var audio = data.audio || (shot ? shot.audio : null) || {};
    var path = resolveShotMediaFile(data, audio.path || audio.file || audio.source || "");
    if (!path) {
      return null;
    }
    return {
      name: audio.name || "audio",
      path: path
    };
  }

  function importAudioFile(row, folder) {
    var file;
    var options;
    var footage;
    if (!row || !row.path) {
      return null;
    }
    file = File(row.path);
    if (!file.exists) {
      return null;
    }
    footage = findFootageByPath(file.fsName);
    if (!footage) {
      options = new ImportOptions(file);
      if (options.canImportAs && options.canImportAs(ImportAsType.FOOTAGE)) {
        options.importAs = ImportAsType.FOOTAGE;
      }
      footage = app.project.importFile(options);
    }
    footage.name = row.name || basename(file.fsName);
    moveToFolder(footage, folder);
    return footage;
  }

  function addShotAudioToStage(stage, audioRow, folder) {
    var footage = importAudioFile(audioRow, folder);
    var layer;
    if (!audioRow || !audioRow.path) {
      return "";
    }
    if (!footage) {
      return "Missing audio: " + audioRow.path;
    }
    layer = addSourceLayer(stage, footage, audioRow.name || "audio");
    try {
      layer.moveToEnd();
    } catch (error) {}
    return "";
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

  SmartAEBrowser.inspectPrecompProject = function (payloadJson) {
    var payload = parseJson(payloadJson) || {};
    var finalCompConfig = payload.final_comp || {};
    var names = [String(finalCompConfig.name || "final")];
    var fallbackNames = finalCompConfig.fallback_names || [];
    var stageCompName = String(payload.stage_comp || "stage");
    var expectedLayerIds = payload.expected_layer_ids || [];
    var finalComp;
    var stageComp;
    var inputSchema = { schema: "smartpipeline.precomp_inputs.v1", inputs: {} };
    var composition = {};
    var validation = { status: "passed", results: [] };
    var dependencies = { schema: "smartpipeline.ae_dependency_snapshot.v1", items: [] };
    var stageLayers = [];
    var layersByName = {};
    var i;
    var j;
    var item;
    var layer;
    var itemName;
    var sourcePath;
    var missingEffects = {};
    var fonts = {};
    var inputCount = 0;

    function result(severity, code, message, data) {
      validation.results.push({
        severity: severity,
        code: code,
        message: message,
        data: data || {}
      });
      if (severity === "ERROR") {
        validation.status = "failed";
      }
    }

    if (fallbackNames instanceof Array) {
      for (i = 0; i < fallbackNames.length; i += 1) {
        names.push(String(fallbackNames[i] || ""));
      }
    } else if (String(fallbackNames || "")) {
      fallbackNames = splitCsv(String(fallbackNames));
      for (i = 0; i < fallbackNames.length; i += 1) {
        names.push(fallbackNames[i]);
      }
    }
    if (!app.project) {
      result("ERROR", "PROJECT_MISSING", "No After Effects project is open.");
      return stringify({ input_schema: inputSchema, composition: composition, validation: validation, dependency_snapshot: dependencies });
    }
    finalComp = findCompByNames(names);
    stageComp = findCompByNames([stageCompName]);
    if (!finalComp) {
      result("ERROR", "FINAL_COMP_MISSING", "Final comp was not found: " + names.join(", "));
    } else {
      composition = {
        schema: "smartpipeline.precomp_composition.v1",
        comp: String(finalComp.name || ""),
        fps: Number(finalComp.frameRate || 0),
        resolution: [Number(finalComp.width || 0), Number(finalComp.height || 0)],
        duration: Math.max(1, Math.round(Number(finalComp.duration || 0) * Number(finalComp.frameRate || 0))),
        duration_seconds: Number(finalComp.duration || 0),
        display_start_frame: Math.round(Number(finalComp.displayStartTime || 0) * Number(finalComp.frameRate || 0)),
        "final": { comp: String(finalComp.name || ""), layer_count: finalComp.numLayers, source: "" },
        stage: { comp: stageCompName, layers: stageLayers }
      };
      if (finalComp.numLayers !== 1) {
        result("ERROR", "FINAL_LAYER_COUNT", "Final comp must contain exactly one layer.", { count: finalComp.numLayers });
      }
      if (finalComp.numLayers >= 1) {
        layer = finalComp.layer(1);
        composition["final"].source = layer.source ? String(layer.source.name || "") : "";
        try {
          var finalEffects = layer.property("ADBE Effect Parade");
          if (finalEffects && finalEffects.numProperties > 0) {
            result("ERROR", "FINAL_EFFECTS_NOT_ALLOWED", "Final comp layer must not contain effects.", { count: finalEffects.numProperties });
          }
        } catch (finalEffectError) {}
      }
    }
    if (!stageComp) {
      result("ERROR", "STAGE_COMP_MISSING", "Stage comp was not found: " + stageCompName);
    } else {
      for (i = 1; i <= stageComp.numLayers; i += 1) {
        layer = stageComp.layer(i);
        itemName = String(layer.name || "");
        var sourceName = layer.source ? String(layer.source.name || "") : "";
        var layerRecord = {
          name: itemName,
          source: sourceName,
          order: i,
          enabled: layer.enabled !== false
        };
        stageLayers.push(layerRecord);
        if (!layersByName[itemName]) {
          layersByName[itemName] = [];
        }
        layersByName[itemName].push(layerRecord);
      }
      if (composition.stage) {
        composition.stage.layers = stageLayers;
      }
    }

    if (!(expectedLayerIds instanceof Array)) {
      expectedLayerIds = [];
    }
    if (!expectedLayerIds.length && stageComp) {
      for (i = 0; i < stageLayers.length; i += 1) {
        if (("|" + expectedLayerIds.join("|") + "|").indexOf("|" + stageLayers[i].name + "|") < 0) {
          expectedLayerIds.push(stageLayers[i].name);
        }
      }
    }
    for (i = 0; i < expectedLayerIds.length; i += 1) {
      var expectedId = String(expectedLayerIds[i] || "");
      var matches = layersByName[expectedId] || [];
      var uniqueSources = {};
      var occurrences = [];
      if (!matches.length) {
        result("ERROR", "STAGE_LAYER_MISSING", "Stage layer was not found: " + expectedId);
        continue;
      }
      for (j = 0; j < matches.length; j += 1) {
        uniqueSources[matches[j].source] = true;
        occurrences.push(matches[j].order);
      }
      var sourceCount = 0;
      var sourceKey;
      for (sourceKey in uniqueSources) {
        if (uniqueSources.hasOwnProperty(sourceKey)) {
          sourceCount += 1;
        }
      }
      if (sourceCount > 1) {
        result("WARNING", "STAGE_LAYER_AMBIGUOUS", "Stage layer name refers to different sources: " + expectedId, { sources: uniqueSources });
      }
      inputSchema.inputs[expectedId] = {
        placeholder: expectedId,
        composition: stageCompName,
        required: true,
        source: matches[0].source,
        occurrences: occurrences
      };
      inputCount += 1;
    }

    for (i = 1; i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      itemName = String(item.name || "");
      sourcePath = "";
      try {
        if (item instanceof FootageItem && item.file) {
          sourcePath = item.file.fsName;
          if (!item.file.exists) {
            result("ERROR", "MISSING_FOOTAGE", "Missing footage: " + itemName, { path: sourcePath });
          }
        }
      } catch (footageError) {
        result("ERROR", "MISSING_FOOTAGE", "Unreadable footage: " + itemName, { error: footageError.message });
      }
      if (sourcePath || item instanceof CompItem) {
        dependencies.items.push({
          name: itemName,
          path: sourcePath,
          type: item instanceof CompItem ? "composition" : "footage",
          stage_input: Boolean(inputSchema.inputs[itemName])
        });
      }
      if (item instanceof CompItem) {
        for (j = 1; j <= item.numLayers; j += 1) {
          layer = item.layer(j);
          try {
            if (layer.property("Source Text")) {
              fonts[String(layer.property("Source Text").value.font || "")] = true;
            }
          } catch (textError) {}
          try {
            var effects = layer.property("ADBE Effect Parade");
            var effectIndex;
            var effect;
            if (effects) {
              for (effectIndex = 1; effectIndex <= effects.numProperties; effectIndex += 1) {
                effect = effects.property(effectIndex);
                if (/missing/i.test(String(effect.name || "") + " " + String(effect.matchName || ""))) {
                  missingEffects[String(effect.name || effect.matchName)] = true;
                }
              }
            }
          } catch (effectError) {}
        }
      }
    }
    if (!inputCount) {
      result("ERROR", "STAGE_INPUT_MISSING", "No Stage input layer was resolved.");
    }
    for (i in missingEffects) {
      if (missingEffects.hasOwnProperty(i)) {
        result("ERROR", "MISSING_PLUGIN", "Missing effect/plugin: " + i);
      }
    }
    dependencies.fonts = [];
    for (i in fonts) {
      if (fonts.hasOwnProperty(i) && i) {
        dependencies.fonts.push(i);
      }
    }
    validation.summary = {
      final_comp: composition.comp || "",
      input_count: inputCount,
      dependency_count: dependencies.items.length,
      font_count: dependencies.fonts.length
    };
    return stringify({
      input_schema: inputSchema,
      composition: composition,
      validation: validation,
      dependency_snapshot: dependencies
    });
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
    var shot;
    var audioRow;
    var audioError;
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
      errors = validatePlayblastSettingsReceipts(data);
      if (errors.length) {
        return stringify({ error: errors.join("\n"), errors: errors });
      }
      templateFile = File(String(data.template_project || ""));
      if (templateFile.exists) {
        app.open(templateFile);
      } else if (!app.project) {
        app.newProject();
      }
      items = previewRenderBuildItems(data, manifestFile);
      shot = readShotJson(data);
      timing = buildTiming(data, items, shot);
      folders = {
        render: ensureProjectFolder("00_render"),
        comp: ensureProjectFolder("10_comp"),
        precomp: ensureProjectFolder("20_precomp"),
        footage: ensureProjectFolder("30_footage")
      };
      folders.dept = ensureProjectFolder(String(data.department || "anim"), folders.footage);
      folders.layers = ensureProjectFolder("layers", folders.dept);
      folders.audio = ensureProjectFolder("audio", folders.dept);

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

      audioRow = shotAudioRow(data, shot);
      audioError = addShotAudioToStage(stage, audioRow, folders.audio);
      if (audioError) {
        errors.push(audioError);
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

  SmartAEBrowser.snapshotProjectFootage = function () {
    var rows = [];
    var item;
    var row;
    var i;

    if (!app.project) {
      return stringify({ hasProject: false, items: rows });
    }

    for (i = 1; i <= app.project.numItems; i += 1) {
      item = app.project.item(i);
      if (!isFootageItem(item)) {
        continue;
      }
      row = footageFileSnapshot(item);
      if (row) {
        rows.push(row);
      }
    }

    return stringify({ hasProject: true, items: rows });
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
