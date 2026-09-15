"""Read-only Shot List integration; creation uses the existing Shot Manager paths."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
import re

from smartlib.apps.shot_manager.service import ShotCreateRequest
from smartlib.core.metadata import read_json, write_json


@dataclass
class ShotListRow:
    row_number: int
    values: dict
    source: dict
    error: str = ""


def sheet_location(settings):
    value = str(settings.get("shot_list_url") or settings.get("shot_list_id") or "").strip()
    if not value:
        raise ValueError("Set Shot List URL in Config Creator first.")
    gid = None
    if "://" in value:
        url = urlparse(value)
        match = re.fullmatch(r"/spreadsheets/d/([A-Za-z0-9_-]+)(?:/.*)?", url.path)
        if url.scheme != "https" or url.hostname != "docs.google.com" or not match:
            raise ValueError("Shot List URL must be a Google Sheets URL.")
        sheet_id = match.group(1)
        params = parse_qs(url.query)
        params.update(parse_qs(url.fragment))
        raw_gid = params.get("gid", [None])[0]
        if raw_gid is not None:
            if not raw_gid.isdigit():
                raise ValueError("Invalid Shot List worksheet gid.")
            gid = int(raw_gid)
    else:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Invalid Shot List spreadsheet ID.")
        sheet_id = value
    return sheet_id, gid


def parse_rows(values, source):
    if not values:
        raise ValueError("Shot List is empty.")
    headers = [str(v).strip().lower() for v in values[0]]
    if any(headers.count(key) != 1 for key in ("episode", "sequence", "shot")):
        raise ValueError("Shot List requires unique episode, sequence and shot columns in row 1.")
    if len([h for h in headers if h]) != len(set(h for h in headers if h)):
        raise ValueError("Shot List contains duplicate column names.")
    rows = []
    for number, cells in enumerate(values[1:], 2):
        if not any(str(v).strip() for v in cells):
            continue
        data = {key: str(cells[i]).strip() if i < len(cells) else ""
                for i, key in enumerate(headers) if key}
        rows.append(ShotListRow(number, data, dict(source, row=number)))
    counts = Counter(tuple(r.values[k].casefold() for k in ("episode", "sequence", "shot")) for r in rows)
    for row in rows:
        key = tuple(row.values[k].casefold() for k in ("episode", "sequence", "shot"))
        if counts[key] > 1:
            row.error = "Duplicate episode / sequence / shot in Shot List."
    return rows


class ShotListService:
    def __init__(self, shots):
        self.shots = shots

    def read(self):
        settings = self.shots.project_config.base.get("google_sheets") or {}
        sheet_id, gid = sheet_location(settings)
        credentials = self.shots.credentials_path()
        if not credentials or not credentials.is_file():
            raise RuntimeError("Google Sheets credentials were not found. Set them in Config Creator.")
        try:
            import gspread
        except ImportError as exc:
            raise RuntimeError("Install SmartPipeline's sheets extra (gspread) for this Python.") from exc
        client = gspread.service_account(filename=str(credentials), scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
        ])
        client.set_timeout(20)
        book = client.open_by_key(sheet_id)
        sheet = book.get_worksheet_by_id(gid) if gid is not None else book.sheet1
        source = {"kind": "shot_list", "spreadsheet_id": sheet_id,
                  "worksheet_id": sheet.id, "worksheet": sheet.title,
                  "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={sheet.id}"}
        return parse_rows(sheet.get_all_values(), source)

    def request(self, row, cut_in=1001, cut_out=1240):
        if row.error:
            raise ValueError(row.error)
        values = row.values
        tokens = {key: self.shots.paths.pipeline_token(values.get(key, ""))
                  for key in ("episode", "sequence", "shot")}
        if tokens["shot"].lower() == "all":
            raise ValueError("Shot 'all' is reserved for sequence aggregation.")
        def integer(key, default):
            value = values.get(key, "")
            if not value:
                return default
            if not re.fullmatch(r"-?\d+", value):
                raise ValueError(f"{key} must be an integer.")
            return int(value)
        if "range" in values:
            frame_range = values["range"].strip()
            if frame_range:
                match = re.fullmatch(r"(-?\d+)\s*[-–—~〜～]\s*(-?\d+)", frame_range)
                if not match:
                    raise ValueError("range must use start-end format (for example 1001-1240).")
                start, end = map(int, match.groups())
                for key, expected in (("cut_in", start), ("cut_out", end)):
                    if values.get(key) and integer(key, expected) != expected:
                        raise ValueError(f"range conflicts with {key}.")
            else:
                start, end = cut_in, cut_out
        else:
            # Compatibility for sheets created before the range column existed.
            start, end = integer("cut_in", cut_in), integer("cut_out", cut_out)
        head, tail = integer("handle_head", 0), integer("handle_tail", 0)
        if end < start:
            raise ValueError("cut_out must be greater than or equal to cut_in.")
        if head < 0 or tail < 0:
            raise ValueError("Handles must not be negative.")
        if values.get("fps") and integer("fps", self.shots.project_fps) != self.shots.project_fps:
            raise ValueError("Sheet FPS differs from project FPS.")
        return ShotCreateRequest(**tokens, fps=self.shots.project_fps,
            cut_in=start, cut_out=end, handle_head=head, handle_tail=tail,
            status=values.get("status") or "wip", timing_source=row.source,
            timing_comment=values.get("description", ""))

    def state(self, row, cut_in=1001, cut_out=1240):
        try:
            request = self.request(row, cut_in, cut_out)
            root = self.shots.shot_root(request.identity)
            if root.exists():
                path = self.shots.paths.artifact_file(root, "shot.json")
                return "Existing" if path.is_file() else "Folder already exists"
            return "New"
        except ValueError as exc:
            return str(exc)

    def create(self, row, cut_in=1001, cut_out=1240):
        request = self.request(row, cut_in, cut_out)
        state = self.state(row, cut_in, cut_out)
        if state != "New":
            raise ValueError(f"Row {row.row_number}: {state}")
        root = self.shots.create_shot(request)
        path = self.shots.paths.artifact_file(root, "shot.json")
        data = read_json(path, {})
        data["description"] = row.values.get("description", "")
        data["shot_list_source"] = row.source
        write_json(path, data)
        return request.identity
