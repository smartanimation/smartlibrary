from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from smartlib.core.metadata import read_json
from smartlib.review.ae import find_after_effects_executable

from .models import DeliveryPlan, ValidationResult


@dataclass(frozen=True)
class AERelinkArtifacts:
    script: Path
    reopen_script: Path
    result: Path
    reopen_result: Path


class AfterEffectsDeliveryAdapter:
    def __init__(self, project_config, *, timeout: float = 300.0):
        self.project_config = project_config
        self.timeout = timeout

    def relink_and_validate(self, plan: DeliveryPlan, metadata_root: Path) -> list[ValidationResult]:
        project_items = [item for item in plan.items if item.kind == "aep"]
        sequence_items = [item for item in plan.items if item.kind == "image_sequence"]
        if not project_items:
            return [ValidationResult("AE_RELINK_SKIPPED", "WARNING", "No AEP was selected.")]
        if not sequence_items:
            return [ValidationResult("AE_SEQUENCE_INPUT_MISSING", "ERROR", "AEP was selected but no image sequences were selected.", project_items[0].id)]
        executable = find_after_effects_executable(self.project_config)
        if not executable:
            return [ValidationResult("AE_EXECUTABLE_MISSING", "ERROR", "After Effects executable was not found.")]
        command = Path(executable).with_name("AfterFX.com")
        if not command.is_file():
            command = Path(executable)
        project = plan.package_root / project_items[0].destination
        deployment_root = Path(str(plan.metadata.get("deployment_root") or plan.package_root))
        mappings = _sequence_mappings(plan, content_root=deployment_root)
        artifacts = self.prepare(plan, metadata_root, project, mappings)
        first = self._run(command, artifacts.script, project.parent)
        if first:
            return [ValidationResult("AE_RELINK_FAILED", "ERROR", first, project_items[0].id)]
        relink = read_json(artifacts.result, {}) or {}
        results = _results_from_report(relink, phase="RELINK", item_id=project_items[0].id)
        if any(row.severity == "ERROR" for row in results):
            return results
        second = self._run(command, artifacts.reopen_script, project.parent)
        if second:
            results.append(ValidationResult("AE_REOPEN_FAILED", "ERROR", second, project_items[0].id))
            return results
        reopen = read_json(artifacts.reopen_result, {}) or {}
        results.extend(_results_from_report(reopen, phase="REOPEN", item_id=project_items[0].id))
        return results

    def prepare(self, plan: DeliveryPlan, metadata_root: Path, project: Path, mappings: list[dict]) -> AERelinkArtifacts:
        ae_root = metadata_root / "after_effects"
        ae_root.mkdir(parents=True, exist_ok=True)
        result = ae_root / "relink_result.json"
        reopen_result = ae_root / "reopen_result.json"
        script = ae_root / "relink.jsx"
        reopen_script = ae_root / "reopen_validate.jsx"
        deployment_root = Path(str(plan.metadata.get("deployment_root") or plan.package_root))
        allowed_root = deployment_root / str(plan.metadata.get("client_shot_root") or "shot")
        script.write_text(
            _jsx(project, allowed_root, mappings, result, relink=True),
            encoding="utf-8",
        )
        reopen_script.write_text(
            _jsx(project, allowed_root, mappings, reopen_result, relink=False),
            encoding="utf-8",
        )
        return AERelinkArtifacts(script, reopen_script, result, reopen_result)

    def _run(self, command: Path, script: Path, cwd: Path) -> str:
        try:
            completed = subprocess.run(
                [str(command), "-noui", "-r", str(script)],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except Exception as exc:
            return str(exc)
        if completed.returncode != 0:
            return "\n".join(value.strip() for value in (completed.stdout or "", completed.stderr or "") if value.strip()) or f"After Effects exited with {completed.returncode}."
        return "" if script.with_name("relink_result.json" if script.name == "relink.jsx" else "reopen_result.json").is_file() else "After Effects did not write its validation report."


def _sequence_mappings(plan: DeliveryPlan, *, content_root: Path | None = None) -> list[dict]:
    content_root = content_root or plan.package_root
    grouped: dict[str, list] = {}
    for item in plan.items:
        if item.kind == "image_sequence":
            grouped.setdefault(str(item.metadata.get("review_layer") or ""), []).append(item)
    mappings = []
    for layer, items in sorted(grouped.items()):
        items.sort(key=lambda row: str(row.metadata.get("frame") or ""))
        mappings.append(
            {
                "layer": layer,
                "file": str(content_root / items[0].destination).replace("\\", "/"),
                "count": len(items),
            }
        )
    return mappings


def _jsx(project: Path, allowed_root: Path, mappings: list[dict], result: Path, *, relink: bool) -> str:
    values = {
        "project": str(project).replace("\\", "/"),
        "allowed": str(allowed_root).replace("\\", "/"),
        "mappings": mappings,
        "result": str(result).replace("\\", "/"),
        "relink": relink,
    }
    return "\n".join(
        [
            "(function () {",
            f"  var cfg = {json.dumps(values, ensure_ascii=False)};",
            "  var report = {phase: cfg.relink ? 'relink' : 'reopen', matched: [], missing: [], external: [], errors: []};",
            "  function norm(value) { return String(value || '').replace(/\\\\/g, '/').toLowerCase(); }",
            "  function clean(value) { return String(value || '').replace(/[\\u200B-\\u200D\\uFEFF]/g, '').replace(/\\s+/g, '').toLowerCase(); }",
            "  function writeReport() { var f = new File(cfg.result); f.encoding = 'UTF-8'; f.open('w'); f.write(JSON.stringify(report, null, 2)); f.close(); }",
            "  try {",
            "    app.open(new File(cfg.project));",
            "    if (cfg.relink) {",
            "      for (var i = 1; i <= app.project.numItems; i++) {",
            "        var item = app.project.item(i);",
            "        if (!(item instanceof FootageItem) || !item.file) continue;",
            "        var oldPath = norm(item.file.fsName); var itemName = clean(item.name);",
            "        for (var m = 0; m < cfg.mappings.length; m++) {",
            "          var map = cfg.mappings[m]; var layer = clean(map.layer);",
            "          if (itemName !== layer && oldPath.indexOf('/layers/' + norm(map.layer) + '/') < 0 && oldPath.indexOf('/' + norm(map.layer) + '.') < 0) continue;",
            "          var replacement = new File(map.file);",
            "          if (!replacement.exists) { report.errors.push('Footage not found: ' + map.file); break; }",
            "          item.replaceWithSequence(replacement, false); report.matched.push({layer: map.layer, item: item.name, file: replacement.fsName, count: map.count}); break;",
            "        }",
            "      }",
            "      for (var x = 0; x < cfg.mappings.length; x++) { var found = false; for (var y = 0; y < report.matched.length; y++) if (report.matched[y].layer === cfg.mappings[x].layer) found = true; if (!found) report.errors.push('No FootageItem matched review layer: ' + cfg.mappings[x].layer); }",
            "      app.project.save(new File(cfg.project));",
            "    }",
            "    for (var j = 1; j <= app.project.numItems; j++) {",
            "      var footage = app.project.item(j); if (!(footage instanceof FootageItem) || !footage.file) continue;",
            "      var file = footage.file; var path = norm(file.fsName);",
            "      if (!file.exists) report.missing.push({item: footage.name, file: file.fsName});",
            "      if (path.indexOf(norm(cfg.allowed) + '/') !== 0) report.external.push({item: footage.name, file: file.fsName});",
            "    }",
            "    if (cfg.relink) app.project.save(new File(cfg.project));",
            "    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);",
            "  } catch (error) { report.errors.push(String(error) + ' line ' + (error.line || '')); try { app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES); } catch (ignored) {} }",
            "  writeReport();",
            "}());",
        ]
    )


def _results_from_report(report: dict, *, phase: str, item_id: str) -> list[ValidationResult]:
    results = []
    for message in report.get("errors") or []:
        results.append(ValidationResult(f"AE_{phase}_ERROR", "ERROR", str(message), item_id))
    missing = report.get("missing") or []
    external = report.get("external") or []
    results.append(ValidationResult(f"AE_{phase}_MISSING_FOOTAGE", "ERROR" if missing else "PASS", f"{len(missing)} missing footage item(s)", item_id))
    results.append(ValidationResult(f"AE_{phase}_EXTERNAL_FOOTAGE", "ERROR" if external else "PASS", f"{len(external)} package-external footage item(s)", item_id))
    if phase == "RELINK":
        matched = report.get("matched") or []
        results.append(ValidationResult("AE_RELINK_MATCHED", "PASS" if matched else "ERROR", f"{len(matched)} review layer(s) relinked", item_id))
    return results
