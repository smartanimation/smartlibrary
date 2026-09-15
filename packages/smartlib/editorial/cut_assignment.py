"""Explicit editorial occurrences mapped to production shot frame ranges."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re


def event_signature(event):
    return hashlib.sha256(json.dumps({
        "episode": event.episode, "sequence": event.sequence,
        "cut": event.shot, "event": event.event_id,
        "in": event.cut_in, "out": event.cut_out,
        "source_in": event.source_in, "source_out": event.source_out,
        "clip": event.clip,
    }, sort_keys=True).encode()).hexdigest()


def default_assignments(events, shots):
    rows = []
    for event in events:
        handles = [event.handle_head, event.handle_tail]
        work = shots._anim_work_range(event.cut_in, event.cut_out, handles)
        cut = shots._anim_cut_range_in_work(work, event.cut_in, event.cut_out, handles)
        rows.append({"signature": event_signature(event), "enabled": True,
                     "cut": event.shot, "event_id": event.event_id,
                     "sequence": event.sequence,
                     "record_in": event.cut_in, "record_out": event.cut_out,
                     "work_shot": event.shot, "maya_in": cut[0], "maya_out": cut[1]})
    return rows


def compile_assignments(events, plan):
    """Validate before any publish; all ranges are inclusive and offline is baked."""
    if plan.get("schema") != "smartpipeline.cut_assignment.v1":
        raise ValueError("Unsupported cut assignment schema")
    if not plan.get("alignment_confirmed"):
        raise ValueError("Confirm the edit points against the offline movie first.")
    rows = plan.get("rows") or []
    if len(rows) != len(events):
        raise ValueError("Markers changed. Reload the cut assignments.")
    origin = int(plan["offline_origin"])
    selected, groups = [], {}
    seen = set()
    for event, row in zip(events, rows):
        if row.get("signature") != event_signature(event):
            raise ValueError("Markers changed. Reload and confirm the cut assignments.")
        if not row.get("enabled"):
            continue
        shot = str(row.get("work_shot") or "").strip()
        sequence = production_sequence(row, event, plan)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sequence):
            raise ValueError(f"{event.shot}: invalid sequence name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", shot):
            raise ValueError(f"{event.shot}: invalid work shot name")
        key = event.shot
        if (sequence, key) in seen:
            raise ValueError(f"Duplicate edit cut: {key}")
        seen.add((sequence, key))
        start, end = int(row["maya_in"]), int(row["maya_out"])
        if end - start + 1 != event.duration:
            raise ValueError(f"{key}: Maya range must contain {event.duration} frames (offline playback).")
        if event.cut_in < origin:
            raise ValueError(f"{key}: edit starts before offline frame zero")
        assigned = replace(event, sequence=sequence)
        selected.append(assigned)
        segment = {"kind": "clip", "cut": key, "event_id": event.event_id,
                   "record_in": event.cut_in, "record_out": event.cut_out,
                   "source_in": start, "source_out": end,
                   "maya_in": start, "maya_out": end, "retime": 1.0,
                   "offline_in": event.cut_in - origin,
                   "offline_out": event.cut_out - origin}
        segment["source_sequence"] = event.sequence
        groups.setdefault((event.episode, sequence, shot), []).append((assigned, segment))
    if not selected:
        raise ValueError("Select at least one cut to export.")
    production = []
    for (_episode, sequence, shot), items in groups.items():
        ordered = sorted(items, key=lambda pair: pair[1]["maya_in"])
        for previous, current in zip(ordered, ordered[1:]):
            if current[1]["maya_in"] <= previous[1]["maya_out"]:
                raise ValueError(f"{sequence}/{shot}: overlapping Maya ranges for {previous[1]['cut']} / {current[1]['cut']}")
        segments = [segment for _, segment in ordered]
        maya_range = (segments[0]["maya_in"], segments[-1]["maya_out"])
        production.append(replace(items[0][0], shot=shot,
            cut_in=maya_range[0], cut_out=maya_range[1],
            maya_range=maya_range, editorial_segments=segments))
    return selected, production


def production_sequence(row, event, plan):
    """New mappings are explicit; sequence is supported for saved legacy plans."""
    return str(row.get("production_sequence", row.get("sequence",
        "" if plan.get("editorial_unit") else event.sequence))).strip()
