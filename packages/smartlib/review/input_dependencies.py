"""Asset-owned input relationships, independent of Qt and Maya.

Requested Use is never overwritten by a parent's temporary exclusion.
"""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=256)
def _dress_nodes(path, mtime, size):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    nodes = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("node"), str):
                nodes.add(value["node"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    return tuple(nodes)


def associate_inputs(rows, cast=None, *, reorder=False):
    """Annotate rows with parent IDs and effective Use; optionally group children."""
    cast = cast or {}
    for row in rows:
        if row.get('state') in {'PARENT OFF', 'TARGET MISSING'}:
            row['state'] = row.get('dependency_base_state', '')
        else:
            row['dependency_base_state'] = row.get('state', '')
    parents = {str(r.get("cast_key") or r.get("name")): r for r in rows
               if r.get("type") in {"rig", "usd"}}
    namespaces = {}
    for key, parent in parents.items():
        source = (parent.get("component") or {}).get("source") or {}
        namespace = str((cast.get(key) or {}).get("namespace") or source.get("namespace")
                        or (parent.get("component") or {}).get("namespace") or "").strip(":")
        if namespace:
            namespaces.setdefault(namespace, set()).add(key)
    for row in rows:
        kind = row.get("type")
        component = row.get("component") or {}
        source = component.get("source") or {}
        name = str(row.get("cast_key") or row.get("name") or "")
        targets, unresolved = set(), False
        if kind == "animation_curve":
            targets.add(str(source.get("cast_key") or name))
        elif kind == "set_dress":
            path = str(component.get("path") or "")
            try:
                stat = Path(path).stat()
                nodes = _dress_nodes(path, stat.st_mtime_ns, stat.st_size)
                unresolved = not nodes
                for node in nodes:
                    if node.startswith("/Shot/Assets/"):
                        matches = {node.split("/")[3]}
                    else:
                        matches = set()
                        for part in node.split("|"):
                            if ":" in part:
                                matches.update(namespaces.get(part.rsplit(":", 1)[0], ()))
                    if len(matches) != 1:
                        unresolved = True
                    else:
                        targets.update(matches)
            except (OSError, ValueError, TypeError):
                unresolved = True
        missing = targets - parents.keys()
        if kind == "animation_curve":
            missing |= {key for key in targets if key in parents and parents[key].get("type") != "rig"}
        disabled = {key for key in targets if key in parents and not parents[key].get("enabled", True)}
        unavailable = {key for key in targets if key in parents
                       and parents[key].get("state") == "MISSING"}
        reason = ""
        if missing or unresolved:
            reason = "TARGET MISSING: " + (", ".join(sorted(missing)) or "unresolved Set Dress targets")
        elif disabled:
            reason = "PARENT OFF: " + ", ".join(sorted(disabled))
        elif unavailable:
            reason = "TARGET MISSING: " + ", ".join(sorted(unavailable))
        row["parent_keys"] = sorted(targets)
        row["dependency_error"] = reason if row.get("enabled", True) and not disabled else ""
        row["dependency_note"] = reason
        row["effective_enabled"] = bool(row.get("enabled", True)) and not reason
        row["display_name"] = name
        if kind in {"animation_curve", "set_dress"}:
            if missing or unresolved:
                row["display_name"] = "[No target] " + name
            elif len(targets) == 1:
                row["display_name"] = "    ↳ " + name + " [" + next(iter(targets)) + "]"
            else:
                row["display_name"] = "[Shared: " + ", ".join(sorted(targets)) + "] " + name
        if reason and row.get("enabled", True):
            row["state"] = "PARENT OFF" if disabled else "TARGET MISSING"
    if not reorder:
        return rows
    children = {id(r) for r in rows if len(r["parent_keys"]) == 1
                and r["parent_keys"][0] in parents}
    result = []
    for row in rows:
        if id(row) in children:
            continue
        result.append(row)
        if row.get("type") in {"rig", "usd"}:
            key = str(row.get("cast_key") or row.get("name"))
            result.extend(r for r in rows if id(r) in children and r["parent_keys"] == [key])
    return result


def effective_components(components, cast):
    """Validate relationships before staging and return execution-only Use values."""
    rows = [dict(type=c.get("component_type"), cast_key=c.get("name"),
                 enabled=c.get("enabled", True), component=c) for c in components]
    associate_inputs(rows, cast)
    errors = [str(r["cast_key"]) + ": " + r["dependency_error"] for r in rows if r["dependency_error"]]
    if errors:
        raise ValueError("Input dependency validation failed: " + "; ".join(errors))
    return [dict(r["component"], enabled=r["effective_enabled"]) for r in rows]
