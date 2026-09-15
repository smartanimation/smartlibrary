"""Versioned evidence, deterministic fixtures and explicit baseline approval."""
from __future__ import annotations

import hashlib
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from smartlib.core.color_settings import project_color_contract, validation_hosts
from smartlib.core.path_resolver import configured_project_paths, ProjectPaths

HOSTS = ("Maya", "Nuke", "RV")
SCHEMA = "smartpipeline.color_validation.v1"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def prepare(project, run_id):
    import PyOpenColorIO as ocio
    from smartlib.color.images import chart, display_pixels, write_exr, write_preview
    contract = project_color_contract(project)
    if contract is None:
        raise ValueError("Enable project color settings first.")
    # This initial contract supports self-contained studio configs. External LUT
    # references must never be silently omitted from the fingerprint.
    if "!<FileTransform>" in Path(contract["config"]).read_text(encoding="utf-8-sig"):
        raise ValueError("External FileTransform bundles are not supported by this validation version.")
    config = ocio.Config.CreateFromFile(contract["config"])
    config.validate()
    if config.getColorSpace("scene_linear").getName() != "ACEScg":
        raise ValueError("OCIO scene_linear role must resolve to ACEScg.")
    if contract["display"] not in list(config.getDisplays()) or contract["view"] not in list(config.getViews(contract["display"])):
        raise ValueError("Configured Display/View does not exist in OCIO.")
    paths = configured_project_paths(project.project_root or project.config_dir, project)
    directory = paths.color_validation_dir(run_id)
    directory.mkdir(parents=True, exist_ok=False)
    names = ["manifest.json", "reference.exr", "reference_display.exr", "reference.png", "report.json", "report.html", "RV.log",
             "color_validation.ma", "color_validation.nk", "color_validation.rv", "Maya_render.exr", "Maya_render"]
    names += [f"{host}{suffix}" for host in HOSTS for suffix in ("_output.exr", "_display.exr", "_state.json", ".png", "_difference.png")]
    resolved = {name: str(paths.color_validation_file(run_id, name)) for name in names}
    pixels = chart()
    display = display_pixels(config, pixels, contract["display"], contract["view"])
    write_exr(resolved["reference.exr"], pixels, "ACEScg")
    write_exr(resolved["reference_display.exr"], display, "display-referred")
    write_preview(resolved["reference.png"], display)
    manifest = {"schema": SCHEMA, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "contract": contract, "hosts": contract["validation_hosts"], "generator": {"ocio": ocio.GetVersion(), "chart_version": 1}, "paths": resolved,
                "reference_sha256": sha256(resolved["reference.exr"]),
                "display_sha256": sha256(resolved["reference_display.exr"])}
    save_json(resolved["manifest.json"], manifest)
    return manifest


def load_manifest(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported color validation manifest.")
    return data


def compare_run(manifest, baseline=None, *, atol=1e-5, rtol=1e-5):
    from smartlib.color.images import compare, read_exr, write_preview
    p = manifest["paths"]
    contract = manifest["contract"]
    hosts = validation_hosts(manifest.get("hosts", HOSTS))
    checks = {}
    for name, key in (("reference.exr", "reference_sha256"), ("reference_display.exr", "display_sha256")):
        checks[f"integrity:{name}"] = {"passed": sha256(p[name]) == manifest[key]}
    from smartlib.core.color_settings import config_fingerprint
    checks["config_integrity"] = {"passed": sha256(contract["config"]) == contract["config_sha256"] and config_fingerprint(contract["config"]) == contract["bundle_sha256"]}
    for host in hosts:
        try:
            state = json.loads(Path(p[f"{host}_state.json"]).read_text(encoding="utf-8"))
            if state.get("status") == "failed":
                raise ValueError(state.get("error") or "Host validation failed")
            required = ("config_sha256", "bundle_sha256", "working_space", "display", "view")
            errors = [key for key in required if state.get(key) != contract[key]]
            if Path(state.get("config", "")).resolve() != Path(contract["config"]).resolve():
                errors.append("config")
            if state.get("host") != host or state.get("run_id") != manifest["run_id"]:
                errors.append("status/host/run_id")
            if not state.get("host_version") or not state.get("ocio_version"):
                errors.append("versions")
            # Read-back values, not environment intent, are required.
            if state.get("state_source") != "host_readback":
                errors.append("state_source")
            checks[f"{host}:settings"] = {"passed": not errors, "mismatches": errors, "host_version": state.get("host_version"), "ocio_version": state.get("ocio_version")}
            checks[f"{host}:complete"] = {"passed": state.get("status") == "complete", "capture_method": state.get("capture_method", "pending"), "reason": state.get("export_error", "")}
            for suffix, reference in (("_output.exr", "reference.exr"), ("_display.exr", "reference_display.exr")):
                name = host + suffix
                if sha256(p[name]) != state.get("artifacts", {}).get(name):
                    raise ValueError(f"Receipt hash mismatch: {name}")
                expected, actual = read_exr(p[reference]), read_exr(p[name])
                checks[f"{host}{suffix}"] = compare(expected, actual, atol=atol, rtol=rtol)
                if suffix == "_display.exr":
                    write_preview(p[host + ".png"], actual)
                    if expected.shape == actual.shape:
                        write_preview(p[host + "_difference.png"], abs(expected - actual) * 16)
            if host == "Maya":
                render = p["Maya_render.exr"]
                if sha256(render) != state.get("artifacts", {}).get("Maya_render.exr"):
                    raise ValueError("Arnold render evidence is missing or changed.")
                if not state.get("arnold_version") or not state.get("mtoa_version"):
                    raise ValueError("Arnold/MtoA versions are missing.")
                # The rendered emission card must reproduce its known ACEScg value.
                pixels = read_exr(render)
                import numpy as np
                checks["Maya:arnold"] = compare(np.full_like(pixels, 0.18), pixels, atol=atol, rtol=rtol)
                if state.get("license_status") == "failed":
                    checks["Maya:license"] = {"passed": False, "reason": state.get("license_error", "Arnold license failure")}
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            checks[f"{host}:evidence"] = {"passed": False, "reason": str(exc)}
    if baseline is not None:
        approval = json.loads(ProjectPaths.color_evidence_file(baseline, "approval.json").read_text(encoding="utf-8"))
        for name, digest in approval["hashes"].items():
            if sha256(ProjectPaths.color_evidence_file(baseline, name)) != digest:
                raise ValueError("Approved baseline has changed.")
        old = load_manifest(ProjectPaths.color_evidence_file(baseline, "manifest.json"))
        checks["baseline:hosts"] = {"passed": list(old.get("hosts", HOSTS)) == list(manifest.get("hosts", HOSTS))}
        keys = ("config_sha256", "bundle_sha256", "working_space", "display", "view")
        checks["baseline:contract"] = {"passed": all(old["contract"][k] == contract[k] for k in keys)}
        for host in hosts:
            for suffix in (("_output.exr", "_display.exr", "_render.exr") if host == "Maya" else ("_output.exr", "_display.exr")):
                name = host + suffix
                try:
                    checks[f"baseline:{name}"] = compare(read_exr(ProjectPaths.color_evidence_file(baseline, name)), read_exr(p[name]), atol=atol, rtol=rtol)
                except (OSError, ValueError, RuntimeError) as exc:
                    checks[f"baseline:{name}"] = {"passed": False, "reason": str(exc)}
    report = {"schema": SCHEMA, "run_id": manifest["run_id"], "hosts": hosts, "passed": all(c["passed"] for c in checks.values()), "checks": checks}
    save_json(p["report.json"], report)
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{'PASS' if v['passed'] else 'FAIL'}</td><td><pre>{html.escape(json.dumps(v, indent=2))}</pre></td></tr>" for k, v in checks.items())
    previews = '<h2>Display comparison</h2><p>Difference images are amplified 16x. Browser previews are for visual inspection; EXR values determine the result.</p><img src="reference.png" alt="Reference">'
    for host in hosts:
        if Path(p[host + ".png"]).exists():
            previews += f'<h3>{host}</h3><img src="{host}.png"><img src="{host}_difference.png">'
    Path(p["report.html"]).write_text('<!doctype html><meta charset="utf-8"><title>Color validation</title><style>body{font:16px sans-serif;background:#202124;color:#eee;margin:32px}td{padding:8px;border-bottom:1px solid #777}img{max-width:45%}</style>' + f'<h1>{"PASS" if report["passed"] else "FAIL"}: {html.escape(manifest["run_id"])}</h1><table>{rows}</table>{previews}', encoding="utf-8")
    return report


def approve(project, manifest, baseline_id, reviewer):
    if not reviewer.strip():
        raise ValueError("A named reviewer is required.")
    if not compare_run(manifest)["passed"]:
        raise ValueError("Cannot approve incomplete or failed validation.")
    paths = configured_project_paths(project.project_root or project.config_dir, project)
    target = paths.color_validation_dir(baseline_id, baseline=True)
    target.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, source in manifest["paths"].items():
        if Path(source).is_file():
            destination = paths.color_validation_file(baseline_id, name, baseline=True)
            shutil.copy2(source, destination)
            hashes[name] = sha256(destination)
    save_json(paths.color_validation_file(baseline_id, "approval.json", baseline=True),
              {"reviewer": reviewer, "approved_at": datetime.now(timezone.utc).isoformat(), "hashes": hashes})
    return target
