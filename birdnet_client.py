"""Thin client for BirdNET-Go's REST API v2 search/delete endpoints.

Mirrors the CSRF handling in the iOS app's Networking/APIClient.swift:
the server's Echo CSRF middleware uses double-submit cookies, so a
mutating request (POST /search, DELETE /detections/{id}) needs both the
`csrf` cookie (requests.Session carries this automatically once set) and
an X-CSRF-Token header echoing that cookie's value. A GET to /health
primes the cookie. A 403 means the token went stale; re-prime and retry
once, same as the app does.
"""

import requests


class BirdnetClient:
    def __init__(self, base_url: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _prime_csrf(self) -> None:
        try:
            self.session.get(f"{self.base_url}/health", timeout=self.timeout)
        except requests.RequestException:
            pass

    def _csrf_token(self) -> str | None:
        token = self.session.cookies.get("csrf")
        if token:
            return token
        self._prime_csrf()
        return self.session.cookies.get("csrf")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        token = self._csrf_token()
        headers = kwargs.pop("headers", {})
        if token:
            headers["X-CSRF-Token"] = token
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        if resp.status_code == 403:
            self._prime_csrf()
            token = self._csrf_token()
            if token:
                headers["X-CSRF-Token"] = token
            resp = self.session.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        return resp

    def search(self, payload: dict) -> dict:
        resp = self._request("POST", "/search", json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_detection(self, detection_id: str) -> bool:
        resp = self._request("DELETE", f"/detections/{detection_id}")
        return resp.ok
