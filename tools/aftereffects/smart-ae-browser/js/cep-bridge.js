(function () {
  "use strict";

  function Bridge() {
    this.isCep = Boolean(window.__adobe_cep__);
  }

  Bridge.prototype.evalScript = function (script, fallback) {
    return new Promise(function (resolve) {
      if (!window.__adobe_cep__) {
        resolve(typeof fallback === "function" ? fallback() : fallback);
        return;
      }

      window.__adobe_cep__.evalScript(script, function (result) {
        resolve(result);
      });
    });
  };

  Bridge.prototype.callHost = function (method, args, fallback) {
    var methodJson = JSON.stringify(String(method || ""));
    var payloadJson = args !== undefined ? JSON.stringify(JSON.stringify(args)) : null;
    var invocation = payloadJson === null ? "hostMethod()" : "hostMethod(" + payloadJson + ")";
    var script = [
      "(function () {",
      "  var methodName = " + methodJson + ";",
      "  try {",
      "    if (typeof SmartAEBrowser === 'undefined') {",
      "      return JSON.stringify({error: 'SmartAEBrowser host script is not loaded', method: methodName});",
      "    }",
      "    var hostMethod = SmartAEBrowser[methodName];",
      "    if (typeof hostMethod !== 'function') {",
      "      return JSON.stringify({error: 'After Effects host method is not available: ' + methodName, method: methodName});",
      "    }",
      "    var hostResult = " + invocation + ";",
      "    if (hostResult === undefined || hostResult === null || hostResult === '') {",
      "      return JSON.stringify({error: 'After Effects host method returned no result: ' + methodName, method: methodName});",
      "    }",
      "    return String(hostResult);",
      "  } catch (error) {",
      "    return JSON.stringify({",
      "      error: String(error && error.message ? error.message : error),",
      "      line: error && error.line ? error.line : 0,",
      "      method: methodName",
      "    });",
      "  }",
      "}())"
    ].join("\n");
    return this.evalScript(script, fallback);
  };

  window.SmartCEPBridge = new Bridge();
}());
