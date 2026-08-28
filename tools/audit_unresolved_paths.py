"""Audit runtime code for production paths that may bypass path resolvers."""

from __future__ import annotations

import ast
import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "docs" / "audits"
PRODUCTION_PARTS = {
    "assets", "shots", "sequences", "editorial", "incoming",
    "publish", "data", "work", "output", "review", "deliveries",
}
IGNORE_PARTS = {
    ".git", "__pycache__", "runtime", "third_party", "node_modules",
    ".pytest_cache", ".cache", "docs", "tests", "test", "examples",
}
RESOLVER_NAMES = {
    "resolve", "resolver", "path_resolver", "output_resolver",
    "resolve_path", "resolve_uri", "project_path", "asset_path",
    "shot_path", "sequence_path",
}
ABSOLUTE_RE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\[a-z0-9_.-]+[\\/])")


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Path
    line: int
    kind: str
    snippet: str
    reason: str


def runtime_files():
    roots = [ROOT / "packages" / "smartlib", ROOT / "bat", ROOT / "launcher.py"]
    for base in roots:
        if base.is_file():
            yield base
            continue
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.suffix.lower() not in {".py", ".bat", ".ps1"}:
                continue
            if any(part.lower() in IGNORE_PARTS for part in path.parts):
                continue
            yield path


def resolver_inventory():
    rows = []
    for path in (ROOT / "packages" / "smartlib").rglob("*.py"):
        if "resolver" not in path.name.lower() and "path" not in path.name.lower():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.append((path.relative_to(ROOT).as_posix(), node.lineno, node.name))
    return sorted(rows)


def has_resolver_context(lines, index):
    text = " ".join(lines[max(0, index - 3): index + 2]).lower()
    return any(name in text for name in RESOLVER_NAMES)


def scan_file(path):
    rel = path.relative_to(ROOT)
    rel_lower = rel.as_posix().lower()
    is_resolver = "resolver" in path.name.lower() or "/core/path" in rel_lower
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return []
    findings = []
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ABSOLUTE_RE.search(line):
            severity = "P2" if any(word in line.lower() for word in ("example", "fallback", "default")) else "P0"
            findings.append(Finding(severity, rel, index + 1, "absolute-path", line[:240],
                                    "Runtime code contains an absolute drive/UNC path."))
        if is_resolver or has_resolver_context(lines, index):
            continue
        lower = line.lower()
        named_parts = [part for part in PRODUCTION_PARTS if re.search(rf"['\"]{part}['\"]", lower)]
        builds_path = any(token in lower for token in ("os.path.join", "path(", " / ", ".joinpath("))
        if named_parts and builds_path:
            severity = "P1" if any(part in named_parts for part in ("assets", "shots", "sequences", "editorial")) else "P2"
            findings.append(Finding(severity, rel, index + 1, "manual-production-path", line[:240],
                                    "Production hierarchy is assembled outside a resolver module."))
    return findings


def dedupe(findings):
    return sorted(set(findings), key=lambda f: ({"P0": 0, "P1": 1, "P2": 2}[f.severity], f.path.as_posix(), f.line))


def write_report(findings, inventory):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = REPORT_DIR / f"path_resolver_audit_{date.today().isoformat()}.md"
    counts = {level: sum(f.severity == level for f in findings) for level in ("P0", "P1", "P2")}
    out = [
        "# Path Resolver Audit", "",
        f"Generated: {date.today().isoformat()}", "",
        "## Summary", "",
        f"- P0 hardcoded absolute runtime paths: {counts['P0']}",
        f"- P1 production paths assembled outside resolvers: {counts['P1']}",
        f"- P2 review candidates/fallbacks: {counts['P2']}", "",
        "This is a static audit. Each finding must be confirmed against call flow and configuration ownership.", "",
        "## Findings", "",
    ]
    for f in findings:
        out += [f"### {f.severity} `{f.path.as_posix()}:{f.line}`", "",
                f"- Kind: `{f.kind}`", f"- Reason: {f.reason}",
                f"- Code: `{f.snippet.replace('`', '')}`", ""]
    out += ["## Resolver API Inventory", ""]
    out += [f"- `{path}:{line}` `{name}()`" for path, line, name in inventory]
    report.write_text("\n".join(out) + "\n", encoding="utf-8")
    return report, counts


def write_svg(findings, counts):
    shown = findings[:45]
    height = 230 + len(shown) * 34
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="{height}">',
           '<rect width="100%" height="100%" fill="#f5f6f8"/>',
           '<style>text{font-family:Consolas,monospace;fill:#17202a}.h{font-size:30px;font-weight:bold}.s{font-size:20px}.r{font-size:17px}</style>',
           '<text x="35" y="48" class="h">Path Resolver Audit</text>',
           f'<text x="35" y="82" class="s">P0: {counts["P0"]}   P1: {counts["P1"]}   P2: {counts["P2"]}   Showing: {len(shown)}/{len(findings)}</text>']
    y = 125
    for f in shown:
        color = {"P0": "#b42318", "P1": "#b54708", "P2": "#475467"}[f.severity]
        label = f"{f.severity}  {f.path.as_posix()}:{f.line}  [{f.kind}]  {f.snippet}"
        svg.append(f'<text x="35" y="{y}" class="r" fill="{color}">{html.escape(label[:185])}</text>')
        y += 34
    svg.append('</svg>')
    path = REPORT_DIR / f"path_resolver_audit_{date.today().isoformat()}.svg"
    path.write_text("\n".join(svg), encoding="utf-8")
    return path


def main():
    findings = dedupe([finding for path in runtime_files() for finding in scan_file(path)])
    report, counts = write_report(findings, resolver_inventory())
    svg = write_svg(findings, counts)
    print(report)
    print(svg)


if __name__ == "__main__":
    main()
