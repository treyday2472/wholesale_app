import time
from functools import wraps
from flask import request, jsonify

# Simple in-memory rate limiter. Good enough for a single-process dev box.
# Use Flask-Limiter in production.
_BUCKETS = {}

def ratelimit(max_calls: int = 30, window_sec: int = 60):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            now = time.time()
            key = f"{request.remote_addr}:{request.endpoint or fn.__name__}"
            bucket = _BUCKETS.get(key, [])
            # drop old timestamps
            bucket = [t for t in bucket if now - t < window_sec]
            if len(bucket) >= max_calls:
                retry = int(window_sec - (now - bucket[0]))
                return jsonify({"error": "rate_limited", "retry_in_seconds": max(1, retry)}), 429
            bucket.append(now)
            _BUCKETS[key] = bucket
            return fn(*args, **kwargs)
        return wrapper
    return decorator
