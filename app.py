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
    payload["page"] = 1
    ids: list[str] = []
    for _ in range(MAX_SWEEP_PAGES):
        try:
            data = client.search(payload)
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502
        results = data.get("results", [])
        ids.extend(r["id"] for r in results)
        pages = data.get("pages", 1)
        current = data.get("currentPage", payload["page"])
        if not results or current >= pages:
            break
        payload["page"] = current + 1
    return jsonify({"ids": ids, "total": len(ids)})


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
    payload["page"] = 1
    all_results: list[dict] = []
    truncated = True
    for _ in range(MAX_SWEEP_PAGES):
        try:
            data = client.search(payload)
        except requests.RequestException as exc:
            return jsonify({"error": str(exc)}), 502
        results = data.get("results", [])
        all_results.extend(results)
        pages = data.get("pages", 1)
        current = data.get("currentPage", payload["page"])
        if not results or current >= pages:
            truncated = False
            break
        payload["page"] = current + 1

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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    app.run(host="0.0.0.0", port=port)
