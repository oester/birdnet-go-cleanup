# Installing BirdNET-Go Cleanup

## Prerequisites

- Python 3.10 or newer, with the `venv` module available.
  - On Debian/Ubuntu this sometimes needs a separate package, e.g.
    `sudo apt install python3.11-venv` (adjust the version suffix to match
    `python3 --version`).
- A running [BirdNET-Go](https://github.com/tphakala/birdnet-go) server,
  reachable over HTTP from wherever you run this tool.
- `git`, to clone the repo.

## 1. Get the code

```bash
git clone <this-repo-url> birdnet-go-cleanup
cd birdnet-go-cleanup
```

## 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 3. Configure where BirdNET-Go is

Set `BIRDNET_BASE_URL` to BirdNET-Go's API base URL (include the `/api/v2`
suffix). The default, `http://localhost:8080/api/v2`, is correct if you run
this tool on the same machine as BirdNET-Go (e.g. the same server, as a
second systemd service). If BirdNET-Go runs elsewhere, point at its host:

```bash
export BIRDNET_BASE_URL="http://192.168.1.50:8080/api/v2"
```

Optionally set `PORT` (default `5055`) for the port this tool listens on.

## 4. Run it

```bash
venv/bin/python app.py
```

Open `http://<this-host>:5055/` in a browser. Run a search to confirm it can
reach BirdNET-Go — if it can't, you'll see a "Search failed" error with the
underlying connection error.

Stop it with Ctrl+C. For anything beyond a one-off test, run it as a
persistent service instead (next section).

## 5. (Recommended) Run as a systemd service

This keeps it running across reboots and restarts it automatically if it
crashes. Skip this if you're not on a systemd-based Linux system, or prefer
another process manager (pm2, supervisor, a plain `nohup`, Docker, etc. all
work the same way — `app.py` is a normal Flask app).

1. Copy the template and fill in the placeholders:

   ```bash
   cp birdnet-cleanup.service.example birdnet-cleanup.service
   ```

   Edit `birdnet-cleanup.service`:
   - `User=` — the user to run it as.
   - `WorkingDirectory=` and both paths in `ExecStart=` — where you cloned
     this repo (e.g. `/home/youruser/birdnet-go-cleanup`).
   - `BIRDNET_BASE_URL` — as in step 3, if not using the default.
   - `PORT` — if you want something other than 5055.

2. Install and start it:

   ```bash
   sudo cp birdnet-cleanup.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now birdnet-cleanup.service
   ```

3. Check it's running:

   ```bash
   sudo systemctl status birdnet-cleanup.service
   ```

4. To update after pulling new code:

   ```bash
   git pull
   venv/bin/pip install -r requirements.txt   # only if requirements.txt changed
   sudo systemctl restart birdnet-cleanup.service
   ```

## Troubleshooting

- **"Search failed" / connection errors** — confirm `BIRDNET_BASE_URL` is
  correct and reachable from this machine (`curl $BIRDNET_BASE_URL/health`
  should return JSON with `"status":"healthy"`).
- **403 / CSRF errors that don't clear up** — this tool re-primes BirdNET-Go's
  CSRF cookie and retries once automatically; a 403 that persists past that
  usually means BirdNET-Go itself is misconfigured or unreachable, not this
  tool.
- **A delete fails for one detection but not others** — BirdNET-Go refuses to
  delete a *locked* detection (403 "detection is locked"). This shows up as
  a per-item failure in the delete result, not a total failure; unlock it in
  BirdNET-Go's own UI first if you actually want it gone.
- **A large search/count/summary query is slow or gets cut off** — this tool
  sweeps every matching page of BirdNET-Go's search results server-side for
  the count filter, summary view, and "select all matching." That's fast for
  a filtered query (hundreds to low thousands of results) but capped at 500
  pages as a safety limit for very broad/unfiltered ones (a truncation
  warning appears if you hit it) — narrow the date range or add another
  filter rather than raising the cap.
- **BirdNET-Go has a login/password configured** — this tool currently only
  implements BirdNET-Go's CSRF handling, not its login flow. Requests to a
  password-protected BirdNET-Go instance will likely fail; running both on
  the same trusted network with BirdNET-Go's default (no login) setup is the
  supported case for now.
