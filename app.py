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
