import requests

class HttpError(Exception):
    def __init__(self, status: int, message: str = "", body: str = ""):
        self.status = status
        self.message = message or f"HTTP {status}"
        self.body = body
        super().__init__(self.message)

def safe_get(url: str, *, headers=None, params=None, timeout=(6, 20)):
    headers = headers or {}
    params = params or {}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise HttpError(-1, f"network_error:{e}") from e

    if r.status_code >= 400:
        # keep body (truncated) for logs, not for API response
        body = r.text[:500]
        if r.status_code in (401, 403):
            raise HttpError(r.status_code, "auth_or_plan_error", body)
        if r.status_code == 404:
            raise HttpError(404, "not_found", body)
        if r.status_code == 429:
            raise HttpError(429, "rate_limited", body)
        raise HttpError(r.status_code, "upstream_error", body)

    # Try json; fall back to text
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}
