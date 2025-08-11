from flask import current_app, request, abort

def require_token():
    token = request.headers.get("X-API-Key") or request.args.get("api_key")
    expected = current_app.config.get("API_KEY")
    if not expected:
        # If no API_KEY configured, allow but warn in logs
        current_app.logger.warning("API_KEY not configured; allowing request")
        return
    if token != expected:
        abort(401, description="Invalid or missing API key")
