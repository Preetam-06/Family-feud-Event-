"""Vercel entrypoint for the Family Feud WSGI application."""
import json
import os
import tempfile
import traceback
from http.cookies import SimpleCookie

# Vercel's package filesystem is read-only. /tmp is writable, but is instance
# local; this adapter is intended for event/demo deployments, not durable data.
os.environ.setdefault("FEUD_DATA_DIR", os.path.join(tempfile.gettempdir(), "family-feud-data"))

import app as core

core.load()


def response(start_response, status, payload, headers=(), content_type="application/json"):
    if isinstance(payload, str):
        body = payload.encode("utf-8")
    elif isinstance(payload, bytes):
        body = payload
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    out = [("Content-Type", content_type + "; charset=utf-8"),
           ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
           ("X-Content-Type-Options", "nosniff"), *headers]
    start_response(status, out)
    return [body]


def cookie_value(environ, name):
    jar = SimpleCookie(environ.get("HTTP_COOKIE", ""))
    return jar[name].value if name in jar else None


def authed(environ):
    return cookie_value(environ, "feud_admin") in core.TOKENS


def body(environ):
    try:
        length = min(int(environ.get("CONTENT_LENGTH") or 0), 400_000)
        value = json.loads(environ["wsgi.input"].read(length) or b"{}")
        core.need(isinstance(value, dict), "Invalid JSON")
        return value
    except core.Err:
        raise
    except Exception:
        raise core.Err("Invalid JSON")


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET").upper()
    try:
        if method == "GET":
            if path == "/":
                return response(start_response, "302 Found", b"", [("Location", "/admin")], "text/plain")
            if path in core.PAGES:
                filename, content_type = core.PAGES[path]
                return response(start_response, "200 OK", (core.WEB / filename).read_bytes(), content_type=content_type)
            if path == "/api/screen":
                with core.LOCK:
                    return response(start_response, "200 OK", core.build(False))
            if path == "/api/admin":
                if not authed(environ):
                    return response(start_response, "401 Unauthorized", {"error": "Login required"})
                with core.LOCK:
                    return response(start_response, "200 OK", core.build(True))
            if path == "/api/health":
                return response(start_response, "200 OK", {"ok": True, "status": core.tstatus()})
            # Vercel does not keep streaming connections open reliably. A
            # short event response lets EventSource reconnect while polling
            # remains the reliable update path.
            if path == "/events":
                return response(start_response, "200 OK", f"data: {core.VERSION}\n\n", [("Cache-Control", "no-cache")], "text/event-stream")
        if method == "POST":
            if path == "/api/login":
                request = body(environ)
                if core.hmac.compare_digest(str(request.get("pin", "")), core.PIN):
                    token = core.secrets.token_urlsafe(24)
                    core.TOKENS.add(token)
                    return response(start_response, "200 OK", {"ok": True}, [("Set-Cookie", f"feud_admin={token}; HttpOnly; SameSite=Strict; Path=/")])
                return response(start_response, "401 Unauthorized", {"error": "Wrong PIN"})
            if path == "/api/logout":
                return response(start_response, "200 OK", {"ok": True}, [("Set-Cookie", "feud_admin=; Max-Age=0; Path=/")])
            if path == "/api/action":
                if not authed(environ):
                    return response(start_response, "401 Unauthorized", {"error": "Login required"})
                core.do_action(body(environ))
                return response(start_response, "200 OK", {"ok": True})
        return response(start_response, "404 Not Found", {"error": "Not found"})
    except core.Err as exc:
        return response(start_response, "400 Bad Request", {"error": str(exc)})
    except Exception:
        traceback.print_exc()
        return response(start_response, "500 Internal Server Error", {"error": "Server error - nothing was changed"})


