# BirdNET-Go Cleanup

A small self-hosted web tool for searching and bulk-cleaning up detections
in a [BirdNET-Go](https://github.com/tphakala/birdnet-go) server's database,
through BirdNET-Go's existing REST API — no direct database access.

Built for personal use as a companion to BirdNET-Go's web UI: BirdNET-Go's
own Search page returns individual detections one page at a time with no
per-species aggregation and no multi-page bulk actions. This tool adds:

- **Filtered search** — species, source/device, date range, confidence
  range, verified/locked status, time of day, sort — mirroring BirdNET-Go's
  own search filters.
- **Bulk select + delete across every matching page**, not just the one
  page you're looking at.
- **Species detection-count filter** — e.g. "nighttime detections in the
  last 30 days for species with fewer than 5 total," useful for surfacing
  rare/one-off misidentifications worth reviewing.
- **Species summary view** — one row per species (count, first/last seen,
  average confidence) instead of every individual detection, with a
  drill-down button back into the filtered individual view.
- **Add-to-exclude-list action** — adds the species of the selected
  detections to BirdNET-Go's excluded-species list in one dedup'd call, even
  when dozens of detections of the same species are selected (BirdNET-Go's
  underlying endpoint is a toggle, not an idempotent add — this tool handles
  that correctly).

Deleting a detection through this tool also removes its audio clip and
spectrogram files from disk, the same as deleting it in BirdNET-Go's own UI —
that cleanup happens server-side in BirdNET-Go itself.

## Requirements

- Python 3.10+
- A running BirdNET-Go server reachable over HTTP from wherever you run this

## Quick start

See [INSTALL.md](INSTALL.md) for full setup, including running it as a
systemd service. Short version:

```bash
git clone <this-repo-url>
cd birdnet-go-cleanup
python3 -m venv venv
venv/bin/pip install -r requirements.txt
BIRDNET_BASE_URL="http://<your-birdnet-go-host>:8080/api/v2" venv/bin/python app.py
```

Then open `http://localhost:5055/` (or whatever host you ran it on).

## Configuration

Both are environment variables read at startup — see `app.py`.

| Variable            | Default                          | Meaning                                                              |
|---------------------|-----------------------------------|------------------------------------------------------------------------|
| `BIRDNET_BASE_URL`  | `http://localhost:8080/api/v2`   | BirdNET-Go's API base URL. Defaults to co-located deployment.        |
| `PORT`              | `5055`                           | Port this tool listens on (binds `0.0.0.0`).                         |

## Security

**This tool has no built-in authentication.** It talks to BirdNET-Go using
whatever access BirdNET-Go itself allows (if BirdNET-Go has a login
configured, requests to it will need that — this tool does not currently
implement BirdNET-Go's login flow, only its CSRF handling). It's intended to
run on a private/trusted network, same as BirdNET-Go's own default posture.
If you need to expose it beyond your LAN, put it behind a reverse proxy with
its own auth (or a VPN/Tailscale) rather than exposing it directly.

It also runs on Flask's built-in development server (`app.run(...)`), which
is fine for personal/LAN use but isn't hardened for internet-facing or
high-traffic deployment. Put a real WSGI server (gunicorn, uwsgi) behind a
reverse proxy if that matters for your setup.

It can permanently delete detections (and their audio clips/spectrograms) and
change BirdNET-Go's excluded-species list. Both actions are confirmed with a
dialog before they run, but there's no undo.

## How it talks to BirdNET-Go

BirdNET-Go's REST API (`/api/v2/...`) has no bulk-delete or per-species
aggregate-count endpoint — only individual-detection search
(`POST /search`) and single-detection delete (`DELETE /detections/{id}`).
This tool's server-side (`app.py`) sweeps every page of a filtered search
when it needs an aggregate or a full matching set (bulk select/delete,
the count filter, the summary view), and loops single deletes for bulk
delete. See the comments in `app.py` and `birdnet_client.py` for specifics,
including the CSRF double-submit-cookie handling BirdNET-Go's API requires
on any mutating request.

## License

MIT — see [LICENSE](LICENSE).
