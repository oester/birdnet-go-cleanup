import os

import requests
from flask import Flask, jsonify, render_template, request

from birdnet_client import BirdnetClient

BASE_URL = os.environ.get("BIRDNET_BASE_URL", "http://localhost:8080/api/v2")
MAX_SWEEP_PAGES = 500  # safety cap for /api/search_all_ids

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


@app.route("/api/delete", methods=["POST"])
def api_delete():
    body = request.get_json(force=True) or {}
    ids = body.get("ids", [])
    deleted = 0
    failed_ids = []
    for detection_id in ids:
        if client.delete_detection(str(detection_id)):
            deleted += 1
        else:
            failed_ids.append(detection_id)
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
