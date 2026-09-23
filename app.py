import io
import os
import time
from datetime import datetime

import requests
from flask import Flask, Response, jsonify, render_template, request

from birdnet_client import BirdnetClient

BASE_URL = os.environ.get("BIRDNET_BASE_URL", "http://localhost:8080/api/v2")
MAX_SWEEP_PAGES = 500  # safety cap for /api/search_all_ids
# CSV export reads GET /detections at 1000 rows/request instead of /search's
# fixed 20 rows/page (one busy month is ~46 requests vs ~2,300 pages).
EXPORT_PAGE_SIZE = 1000
EXPORT_MAX_REQUESTS = 1000  # 1M rows -- safety stop, well past all-time volume
# /detections ignores a lone start or end date, so a blank bound becomes one
# of these to keep open-ended ranges working.
EXPORT_OPEN_START = "1970-01-01"
EXPORT_OPEN_END = "2099-12-31"
MAX_SELECTION = 500  # absolute cap on how many detections can be selected/deleted at once
DELETE_BATCH_SIZE = 25  # frontend sends deletes in chunks this size, for progress + resilience
DELETE_THROTTLE_SECONDS = 0.05  # pause between individual deletes, to avoid hammering the DB/filesystem

app = Flask(__name__)
client = BirdnetClient(BASE_URL)

DEFAULT_SEARCH = {
    "species": "",
    "speciesScientific": [],
    "dateStart": "",
    "dateEnd": "",
    "confidenceMin": 0,
    "confidenceMax": 1,
    "verifiedStatus": "any",
    "lockedStatus": "any",
    "deviceFilter": "",
    "timeOfDay": "any",
    "page": 1,
    "sortBy": "date_desc",
}


# Columns offered on the Export CSV tab, in export column order. Keys match
# the /search field names they replaced; values come from GET /detections
# via detection_export_row() (latitude/longitude/device aren't in that
# response, so they're not offered) -- kept hand-in-sync with the checkboxes
# in templates/index.html rather than generated.
EXPORT_FIELDS = {
    "timestamp": "Timestamp",
    "commonName": "Common Name",
    "scientificName": "Scientific Name",
    "confidence": "Confidence",
    "verified": "Verified",
    "source": "Source",
    "modelType": "Model Type",
    "hasAudio": "Has Audio",
    "audioFilePath": "Audio File Path",
    "week": "Week",
    "id": "ID",
}


# Export-only alternate to the ISO 8601 timestamp BirdNET-Go returns
# (e.g. "2026-09-21T00:15:46-05:00") -- keeps the same local wall-clock
# time, just reformatted, for people who want a sortable/filename-safe
# stamp instead.
DATE_FORMATS = {
    "iso": None,
    "compact": "%Y%m%d_%H%M%S",
}

# These columns get double-quoted in the CSV even when they don't strictly
# need it (no comma/quote in the value) -- requested explicitly so species
# names are unambiguous to skim/grep in the raw file, e.g. "Blue Jay".
FORCE_QUOTE_FIELDS = {"commonName", "scientificName"}


def format_export_value(field: str, value, date_format: str = "iso") -> str:
    if value is None:
        return ""
    if field == "timestamp":
        strftime_fmt = DATE_FORMATS.get(date_format)
        if strftime_fmt:
            try:
                return datetime.fromisoformat(value).strftime(strftime_fmt)
            except (TypeError, ValueError):
                return value
        return value
    if field == "hasAudio":
        return "Yes" if value else "No"
    return value


def csv_cell(value, force_quote: bool = False) -> str:
    text = "" if value is None else str(value)
    if force_quote or any(c in text for c in ',"\n\r'):
        text = '"' + text.replace('"', '""') + '"'
    return text


def csv_header_row(fields: list[str]) -> str:
    return ",".join(csv_cell(EXPORT_FIELDS[f]) for f in fields) + "\r\n"


def csv_data_row(values: list, fields: list[str]) -> str:
    return ",".join(csv_cell(v, f in FORCE_QUOTE_FIELDS) for v, f in zip(values, fields)) + "\r\n"


def sweep_detections(date_start: str, date_end: str) -> tuple[list[dict], bool]:
    """Fetches every GET /detections row in the date range, oldest first.
    Returns (rows, truncated). Rows are de-duplicated by id: the endpoint
    pages newest-first by offset, so detections arriving mid-sweep shift
    later pages and would otherwise repeat a few rows."""
    start = date_start or EXPORT_OPEN_START
    end = date_end or EXPORT_OPEN_END
    by_id: dict[int, dict] = {}
    offset = 0
    for _ in range(EXPORT_MAX_REQUESTS):
        data = client.list_detections(start, end, EXPORT_PAGE_SIZE, offset)
        rows = data.get("data") or []
        for r in rows:
            by_id[r["id"]] = r
        offset += EXPORT_PAGE_SIZE
        if not rows or offset >= data.get("total", 0):
            break
    else:
        return sort_oldest_first(by_id.values()), True
    return sort_oldest_first(by_id.values()), False


def sort_oldest_first(rows) -> list[dict]:
    # Parsed, not string-sorted: the UTC offset in the timestamp flips at DST.
    return sorted(rows, key=lambda r: (datetime.fromisoformat(r["timestamp"]), r["id"]))


def detection_export_row(r: dict) -> dict:
    """Maps a GET /detections row onto the EXPORT_FIELDS keys, reproducing
    what /search returned for the same columns (verified row-for-row
    against the dev server 2026-09-23)."""
    ts = datetime.fromisoformat(r["timestamp"])
    clip = r.get("clipName")
    source = r.get("source")
    return {
        "timestamp": r["timestamp"],
        "commonName": r.get("commonName"),
        "scientificName": r.get("scientificName"),
        "confidence": r.get("confidence"),
        "verified": r.get("verified"),
        "source": source.get("displayName") if isinstance(source, dict) else source,
        "modelType": r.get("modelType"),
        "hasAudio": bool(clip),
        # Clips are filed under the detection's local YYYY/MM directory.
        "audioFilePath": f"{ts:%Y/%m}/{clip}" if clip else None,
        # BirdNET-Go's week field is the ISO week number.
        "week": ts.isocalendar().week,
        "id": r["id"],
    }


def build_payload(form: dict) -> dict:
    payload = dict(DEFAULT_SEARCH)
    for key in DEFAULT_SEARCH:
        if key in form:
            payload[key] = form[key]
    payload["confidenceMin"] = float(payload["confidenceMin"])
    payload["confidenceMax"] = float(payload["confidenceMax"])
    payload["page"] = int(payload["page"])
    return payload


def sweep_search(payload: dict) -> tuple[list[dict], bool]:
    """Fetches every page of a filtered search. Returns (all results, truncated)."""
    payload = dict(payload)
    payload["page"] = 1
    all_results: list[dict] = []
    for _ in range(MAX_SWEEP_PAGES):
        data = client.search(payload)
        results = data.get("results", [])
        all_results.extend(results)
        pages = data.get("pages", 1)
        current = data.get("currentPage", payload["page"])
        if not results or current >= pages:
            return all_results, False
        payload["page"] = current + 1
    return all_results, True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/sources")
def api_sources():
    try:
        data = client.realtime_settings()
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    names = []
    for stream in data.get("rtsp", {}).get("streams", []):
        if stream.get("enabled", True) and stream.get("name"):
            names.append(stream["name"])
    for source in data.get("audio", {}).get("sources", []):
        if source.get("name"):
            names.append(source["name"])
    return jsonify({"sources": names})


@app.route("/api/search", methods=["POST"])
def api_search():
    payload = build_payload(request.get_json(force=True) or {})
    try:
        data = client.search(payload)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(data)


@app.route("/api/search_all_ids", methods=["POST"])
def api_search_all_ids():
    payload = build_payload(request.get_json(force=True) or {})
    try:
        all_results, _truncated = sweep_search(payload)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({
        "ids": [r["id"] for r in all_results],
        "commonNames": {r["id"]: r.get("commonName", "") for r in all_results},
        "total": len(all_results),
    })


COUNT_OPS = {
    "lt": lambda count, value: count < value,
    "lte": lambda count, value: count <= value,
    "gt": lambda count, value: count > value,
    "gte": lambda count, value: count >= value,
    "eq": lambda count, value: count == value,
}

SORT_KEYS = {
    "date_desc": lambda r: r.get("timestamp", ""),
    "date_asc": lambda r: r.get("timestamp", ""),
    "species_asc": lambda r: (r.get("commonName") or "").lower(),
    "confidence_desc": lambda r: r.get("confidence", 0),
}
SORT_REVERSE = {"date_desc", "confidence_desc"}


# BirdNET-Go's own /search endpoint has no per-species aggregate/count
# filter (only individual detection rows) — this sweeps every matching
# page, groups the results by species, and keeps only the detections
# belonging to species whose total count in the filtered set satisfies
# the requested condition (e.g. "fewer than 5 nighttime detections in
# the last 30 days"). Reuses the same page-sweep pattern as
# /api/search_all_ids, capped the same way.
@app.route("/api/search_by_count", methods=["POST"])
def api_search_by_count():
    body = request.get_json(force=True) or {}
    count_op = body.get("countOp")
    if count_op not in COUNT_OPS:
        return jsonify({"error": "countOp must be one of lt/lte/gt/gte/eq"}), 400
    try:
        count_value = int(body.get("countValue"))
    except (TypeError, ValueError):
        return jsonify({"error": "countValue must be an integer"}), 400

    payload = build_payload(body)
    try:
        all_results, truncated = sweep_search(payload)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    groups: dict[str, list[dict]] = {}
    for r in all_results:
        groups.setdefault(r.get("scientificName", ""), []).append(r)

    op_fn = COUNT_OPS[count_op]
    matched: list[dict] = []
    species_matched = 0
    for rows in groups.values():
        count = len(rows)
        if op_fn(count, count_value):
            species_matched += 1
            for r in rows:
                r["speciesCount"] = count
            matched.extend(rows)

    sort_key = SORT_KEYS.get(payload["sortBy"], SORT_KEYS["date_desc"])
    matched.sort(key=sort_key, reverse=payload["sortBy"] in SORT_REVERSE)

    return jsonify({
        "results": matched,
        "total": len(matched),
        "speciesMatched": species_matched,
        "truncated": truncated,
    })


# Same sweep as /api/search_by_count, but returns one row per species
# (count + first/last seen + avg confidence) instead of individual
# detections — for "just show me the counts" queries. The optional
# countOp/countValue still narrows which species rows come back, e.g.
# "species with fewer than 5 nighttime detections in the last 30 days,
# as a list of species" rather than every one of their detections.
@app.route("/api/search_summary", methods=["POST"])
def api_search_summary():
    body = request.get_json(force=True) or {}
    payload = build_payload(body)
    try:
        all_results, truncated = sweep_search(payload)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    groups: dict[str, dict] = {}
    for r in all_results:
        key = r.get("scientificName", "")
        g = groups.get(key)
        if g is None:
            g = {
                "scientificName": key,
                "commonName": r.get("commonName", ""),
                "count": 0,
                "_confidenceSum": 0.0,
                "firstSeen": None,
                "lastSeen": None,
            }
            groups[key] = g
        g["count"] += 1
        g["_confidenceSum"] += r.get("confidence") or 0
        ts = r.get("timestamp") or ""
        if ts and (g["firstSeen"] is None or ts < g["firstSeen"]):
            g["firstSeen"] = ts
        if ts and (g["lastSeen"] is None or ts > g["lastSeen"]):
            g["lastSeen"] = ts

    summaries = []
    for g in groups.values():
        g["avgConfidence"] = g["_confidenceSum"] / g["count"] if g["count"] else 0
        del g["_confidenceSum"]
        summaries.append(g)

    count_op = body.get("countOp")
    if count_op:
        if count_op not in COUNT_OPS:
            return jsonify({"error": "countOp must be one of lt/lte/gt/gte/eq"}), 400
        try:
            count_value = int(body.get("countValue"))
        except (TypeError, ValueError):
            return jsonify({"error": "countValue must be an integer"}), 400
        op_fn = COUNT_OPS[count_op]
        summaries = [s for s in summaries if op_fn(s["count"], count_value)]

    summaries.sort(key=lambda s: s["count"])  # rarest species first by default

    return jsonify({
        "summaries": summaries,
        "speciesCount": len(summaries),
        "totalDetections": len(all_results),
        "truncated": truncated,
    })


# Streams a CSV of every detection in the given date range (read via
# GET /detections, see sweep_detections) with only the requested columns. GET + a real filename/Content-Disposition (rather than a JSON
# body) so it downloads like a normal file if hit directly, and the frontend
# still uses fetch()+blob to surface errors/truncation via the usual toast.
@app.route("/api/export.csv")
def api_export_csv():
    # Column order follows the order `fields` arrives in the query string --
    # the frontend sends them in the user's chosen order, not fixed order.
    fields = [f for f in request.args.getlist("fields") if f in EXPORT_FIELDS]
    if not fields:
        return jsonify({"error": "Select at least one field to export"}), 400
    date_format = request.args.get("dateFormat", "iso")
    if date_format not in DATE_FORMATS:
        date_format = "iso"

    date_start = request.args.get("dateStart", "")
    date_end = request.args.get("dateEnd", "")
    try:
        all_results, truncated = sweep_detections(date_start, date_end)
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    buf = io.StringIO()
    buf.write(csv_header_row(fields))
    for r in all_results:
        row = detection_export_row(r)
        values = [format_export_value(f, row.get(f), date_format) for f in fields]
        buf.write(csv_data_row(values, fields))

    date_part = f"{date_start or 'all'}_to_{date_end or 'all'}"
    headers = {
        "Content-Disposition": f'attachment; filename="birdnet-detections_{date_part}.csv"',
        "Content-Type": "text/csv; charset=utf-8",
    }
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(buf.getvalue(), headers=headers)


@app.route("/api/config")
def api_config():
    return jsonify({
        "maxSelection": MAX_SELECTION,
        "deleteBatchSize": DELETE_BATCH_SIZE,
        "deleteThrottleSeconds": DELETE_THROTTLE_SECONDS,
    })


# The frontend sends deletes in batches of DELETE_BATCH_SIZE (for progress
# feedback and so an interrupted operation only loses the in-flight batch,
# not the whole thing) -- this route still handles one batch per call.
# DELETE_THROTTLE_SECONDS between each individual delete keeps a batch from
# hammering BirdNET-Go's DB/filesystem in a tight burst, since each delete
# does a DB write plus a directory scan to remove spectrogram files.
@app.route("/api/delete", methods=["POST"])
def api_delete():
    body = request.get_json(force=True) or {}
    ids = body.get("ids", [])[:MAX_SELECTION]  # defensive cap regardless of caller
    deleted = 0
    failed_ids = []
    for i, detection_id in enumerate(ids):
        if client.delete_detection(str(detection_id)):
            deleted += 1
        else:
            failed_ids.append(detection_id)
        if i < len(ids) - 1:
            time.sleep(DELETE_THROTTLE_SECONDS)
    return jsonify({"deleted": deleted, "failed": len(failed_ids), "failed_ids": failed_ids})


# BirdNET-Go's POST /detections/ignore is a TOGGLE, not an idempotent add —
# calling it once per selected detection (instead of once per distinct
# species) would flip the same species excluded/included/excluded/... on
# every other call. This dedupes to the distinct commonNames the frontend
# sends, checks the current ignore list once, and only calls the toggle for
# names that aren't already excluded — so selecting 40 detections of the
# same species results in exactly one (correct) toggle call, not 40.
@app.route("/api/exclude_species", methods=["POST"])
def api_exclude_species():
    body = request.get_json(force=True) or {}
    seen = set()
    distinct_names = []
    for name in body.get("commonNames", []):
        name = (name or "").strip()
        if name and name not in seen:
            seen.add(name)
            distinct_names.append(name)

    try:
        already_ignored = set(client.list_ignored_species())
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    added = []
    already_excluded = []
    failed = []
    for name in distinct_names:
        if name in already_ignored:
            already_excluded.append(name)
            continue
        try:
            result = client.add_ignored_species(name)
        except requests.RequestException:
            failed.append(name)
            continue
        if result.get("is_excluded"):
            added.append(name)
        else:
            # Toggled the wrong direction (e.g. a race with another
            # client) -- surface as failed rather than claim success.
            failed.append(name)

    return jsonify({"added": added, "alreadyExcluded": already_excluded, "failed": failed})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    app.run(host="0.0.0.0", port=port)
