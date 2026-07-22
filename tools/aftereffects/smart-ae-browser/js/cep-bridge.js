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
    var payload = "";
    if (args !== undefined) {
      payload = "(" + JSON.stringify(JSON.stringify(args)) + ")";
    } else {
      payload = "()";
    }
    return this.evalScript("SmartAEBrowser." + method + payload, fallback);
  };

  window.SmartCEPBridge = new Bridge();
}());
