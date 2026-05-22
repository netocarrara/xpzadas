import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, make_response, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")
os.environ.setdefault("RANKZADA_ALLOW_BROWSER_VERIFICATION", "false")

app = Flask(__name__)

try:
    from web_dashboard import (  # noqa: E402
        SESSION_TTL_SECONDS,
        auth_status,
        dashboard_payload,
        import_bookmarklet,
        import_ranking,
        import_ranking_with_token,
        is_authenticated,
        login as dashboard_login,
        logout as dashboard_logout,
        browser_verification_enabled,
        open_verification,
        parties_payload,
        public_update_enabled,
        remove_party,
        save_party,
        update_ranking,
    )
    IMPORTS_OK = True
except Exception as e:
    IMPORTS_OK = False
    print(f"Error importing web_dashboard: {e}")



class FlaskRequestAdapter:
    def __init__(self):
        self.headers = request.headers


def request_query() -> dict[str, list[str]]:
    return {key: request.args.getlist(key) for key in request.args}


def json_error(status: int, message: str):
    response = jsonify({"ok": False, "error": message})
    add_cors(response)
    return response, status


def add_cors(response):
    origin = request.headers.get("Origin", "")
    if origin == os.getenv("RUBINOT_BASE_URL", "https://rubinot.com.br").rstrip("/"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return response


def admin_required() -> bool:
    return is_authenticated(FlaskRequestAdapter())


@app.get("/health")
def health():
    status = "ok" if IMPORTS_OK else "error"
    return jsonify({"ok": IMPORTS_OK, "status": status})


@app.get("/api/dashboard")
def api_dashboard():
    if not IMPORTS_OK:
        return jsonify({"ok": False, "error": "Server initialization error"}), 500
    return jsonify(dashboard_payload(request_query()))


@app.get("/api/auth")
def api_auth():
    return jsonify(auth_status(FlaskRequestAdapter()))


@app.get("/api/parties")
def api_parties():
    payload = parties_payload()
    payload["authenticated"] = admin_required()
    return jsonify(payload)


@app.post("/api/login")
def api_login():
    class LoginAdapter(FlaskRequestAdapter):
        rfile = None

        @property
        def path(self):
            return request.path

        def send_response(self, status):
            self.status = status
            self.headers_out = {}

        def send_header(self, name, value):
            self.headers_out[name] = value

        def end_headers(self):
            pass

        @property
        def wfile(self):
            class Writer:
                body = b""

                def write(self, body):
                    self.body = body

            if not hasattr(self, "_writer"):
                self._writer = Writer()
            return self._writer

    adapter = LoginAdapter()
    body = request.get_data()
    adapter.rfile = type("Body", (), {"read": lambda _self, _length: body})()
    adapter.headers = {**request.headers, "Content-Length": str(len(body))}
    dashboard_login(adapter)
    response = make_response(adapter.wfile.body, getattr(adapter, "status", 200))
    for name, value in getattr(adapter, "headers_out", {}).items():
        response.headers[name] = value
    return response


@app.post("/api/logout")
def api_logout():
    class LogoutAdapter(FlaskRequestAdapter):
        def send_response(self, status):
            self.status = status
            self.headers_out = {}

        def send_header(self, name, value):
            self.headers_out[name] = value

        def end_headers(self):
            pass

        @property
        def wfile(self):
            class Writer:
                body = b""

                def write(self, body):
                    self.body = body

            if not hasattr(self, "_writer"):
                self._writer = Writer()
            return self._writer

    adapter = LogoutAdapter()
    adapter.headers = request.headers
    dashboard_logout(adapter)
    response = make_response(adapter.wfile.body, getattr(adapter, "status", 200))
    for name, value in getattr(adapter, "headers_out", {}).items():
        response.headers[name] = value
    return response


@app.post("/api/parties")
def api_save_party():
    if not admin_required():
        return json_error(401, "Login necessario.")
    try:
        return jsonify(save_party(request.get_json(silent=True) or {}))
    except Exception as exc:
        return json_error(400, str(exc))


@app.post("/api/delete-party")
def api_delete_party():
    if not admin_required():
        return json_error(401, "Login necessario.")
    try:
        return jsonify(remove_party(request.get_json(silent=True) or {}))
    except Exception as exc:
        return json_error(400, str(exc))


@app.post("/api/update-ranking")
def api_update_ranking():
    if not public_update_enabled() and not admin_required():
        return json_error(401, "Login necessario.")
    try:
        return jsonify(update_ranking())
    except Exception as exc:
        return json_error(500, str(exc))


@app.post("/api/import-ranking")
def api_import_ranking():
    if not admin_required():
        return json_error(401, "Login necessario.")
    try:
        return jsonify(import_ranking(request.get_json(silent=True) or {}))
    except Exception as exc:
        return json_error(400, str(exc))


@app.get("/api/import-bookmarklet")
def api_import_bookmarklet():
    if not admin_required():
        return json_error(401, "Login necessario.")
    try:
        return jsonify(import_bookmarklet(FlaskRequestAdapter()))
    except Exception as exc:
        return json_error(500, str(exc))


@app.post("/api/import-ranking-token")
def api_import_ranking_token():
    try:
        response = jsonify(import_ranking_with_token(request.args.get("token", ""), request.get_data(as_text=True)))
        return add_cors(response)
    except PermissionError as exc:
        return json_error(401, str(exc))
    except Exception as exc:
        return json_error(400, str(exc))


@app.post("/api/open-rubinot")
def api_open_rubinot():
    if not browser_verification_enabled():
        return json_error(400, "Verificacao por navegador esta desativada neste ambiente.")
    try:
        return jsonify(open_verification())
    except Exception as exc:
        return json_error(500, str(exc))
