import asyncio
import hashlib
import hmac
import os
import json
import secrets
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from supabase_store import (
    delete_party_config,
    load_party_configs,
    load_rank_history,
    save_party_config,
    save_rank_reading,
    supabase_configured,
)
from xp_bot import (
    DATA_FILE,
    append_rank_history,
    fetch_ranking_entries,
    get_daily_category,
    load_data,
    normalize_name,
    open_rubinot_verification_browser,
    ranking_snapshot,
    save_data,
    snapshot_bucket_key,
)


WEB_DIR = Path("web")
UPDATE_LOCK = threading.Lock()
DEFAULT_DASHBOARD_LIMIT = 100
SESSIONS: dict[str, float] = {}
SESSION_TTL_SECONDS = 8 * 60 * 60


def safe_save_data(data: dict) -> None:
    try:
        save_data(data)
    except OSError:
        # Serverless hosts such as Vercel should treat Supabase as the durable store.
        pass


def format_bucket(data: dict) -> str:
    config = data.get("config", {})
    category = get_daily_category(data)
    world = config.get("rank_world", "")
    return snapshot_bucket_key(category, world)


def player_rows(current: dict, previous: dict) -> list[dict]:
    rows = []
    previous_players = previous.get("players", {})
    for key, player in current.get("players", {}).items():
        old = previous_players.get(key)
        points = int(player.get("points", 0))
        old_points = int(old.get("points", 0)) if old else None
        rank = int(player.get("rank", 0))
        old_rank = int(old.get("rank", rank)) if old else rank
        level = int(player.get("level", 0) or 0)
        old_level = int(old.get("level", level) or level) if old else level
        rows.append(
            {
                "key": key,
                "name": player.get("name", ""),
                "rank": rank,
                "level": level,
                "levelMove": level - old_level,
                "vocation": player.get("vocation", ""),
                "world": player.get("world", ""),
                "points": points,
                "gainSinceLast": None if old_points is None else max(0, points - old_points),
                "rankMove": old_rank - rank,
                "updatedAt": player.get("updated_at") or current.get("updated_at"),
                "checkedAt": player.get("checked_at") or current.get("checked_at"),
            }
        )
    return sorted(rows, key=lambda item: item["rank"])


def parse_xp_value(value: str | int | float | None) -> int:
    if value is None:
        return 0
    text = str(value).lower().strip().replace(" ", "").replace(",", ".")
    multipliers = {"bi": 1_000_000_000, "b": 1_000_000_000, "kk": 1_000_000, "m": 1_000_000, "k": 1_000}
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)] or 0) * multiplier)
    try:
        return int(float(text or 0))
    except ValueError:
        return 0


def configured_parties(data: dict) -> list[dict]:
    parties = data.setdefault("config", {}).setdefault("parties", [])
    if not parties and supabase_configured():
        try:
            parties = load_party_configs()
            if parties:
                data["config"]["parties"] = parties
                safe_save_data(data)
        except Exception:
            return data.setdefault("config", {}).setdefault("parties", [])
    return parties


def party_rows(data: dict, rows: list[dict], previous: dict) -> list[dict]:
    by_key = {row["key"]: row for row in rows}
    previous_players = previous.get("players", {})
    parties = configured_parties(data)
    result = []

    for party in parties:
        members = []
        total = 0
        gain = 0
        missing = []
        target = int(party.get("target_xp", 0) or 0)
        for member in party.get("members", []):
            key = normalize_name(member)
            row = by_key.get(key)
            if not row:
                missing.append(member)
                continue
            old = previous_players.get(key)
            member_gain = row["points"] - int(old.get("points", 0)) if old else None
            members.append({**row, "gainSinceLast": None if member_gain is None else max(0, member_gain)})
            total += row["points"]
            if member_gain is not None:
                gain += max(0, member_gain)

        member_count = max(1, len(party.get("members", [])))
        current_gain = gain if previous_players else None
        if target > 0 and current_gain is not None:
            ratio = current_gain / target
            status = "Excelente" if ratio >= 1.1 else "Boa" if ratio >= 0.9 else "Ruim"
            missing_xp = max(0, target - current_gain)
        else:
            status = "Aguardando meta" if target <= 0 else "Aguardando leitura"
            missing_xp = None

        result.append(
            {
                "name": party.get("name", "PT"),
                "highlight": bool(party.get("highlight")),
                "members": members,
                "missing": missing,
                "targetXp": target,
                "totalDaily": total,
                "gainSinceLast": current_gain,
                "averagePerMember": None if current_gain is None else current_gain / member_count,
                "missingToTarget": missing_xp,
                "status": status,
                "notes": party.get("notes", ""),
            }
        )

    basis = "gainSinceLast" if previous.get("players") else "totalDaily"
    result = sorted(result, key=lambda item: item[basis] or 0, reverse=True)
    if previous.get("players"):
        gains = [item["gainSinceLast"] for item in result if item["gainSinceLast"] is not None]
        average_gain = sum(gains) / len(gains) if gains else 0
        for item in result:
            if item["targetXp"] <= 0 and item["gainSinceLast"] is not None and average_gain > 0:
                ratio = item["gainSinceLast"] / average_gain
                item["status"] = "Excelente" if ratio >= 1.15 else "Boa" if ratio >= 0.85 else "Ruim"
                item["missingToTarget"] = max(0, average_gain - item["gainSinceLast"])
                item["targetXp"] = int(average_gain)
    return result


def dashboard_limit(query: dict) -> int:
    try:
        raw_limit = int(query.get("limit", [DEFAULT_DASHBOARD_LIMIT])[0])
    except (TypeError, ValueError):
        raw_limit = DEFAULT_DASHBOARD_LIMIT
    return max(1, min(raw_limit, 100))


def dashboard_payload(query: dict) -> dict:
    data = load_data()
    bucket = query.get("bucket", [format_bucket(data)])[0]
    limit = dashboard_limit(query)
    history = data.get("rank_history", {}).get(bucket, [])

    source = "local"
    if not history and supabase_configured():
        try:
            history = load_rank_history(bucket, limit=3)
            source = "supabase" if history else "local"
        except Exception:
            history = []

    if not history and data.get("rank_snapshots", {}).get(bucket):
        history = [
            {
                "checked_at": "",
                "updated_at": "",
                "players": data["rank_snapshots"][bucket],
            }
        ]

    current = history[-1] if history else {"players": {}}
    previous = previous_distinct_reading(history, current)
    all_rows = player_rows(current, previous)
    rows = all_rows[:limit]
    gain_rows = [row for row in rows if row["gainSinceLast"] is not None]

    return {
        "bucket": bucket,
        "buckets": sorted(set(data.get("rank_snapshots", {})) | set(data.get("rank_history", {})) | ({bucket} if history else set())),
        "limit": limit,
        "totalPlayers": len(all_rows),
        "source": source,
        "supabaseConfigured": supabase_configured(),
        "world": data.get("config", {}).get("rank_world", ""),
        "updatedAt": current.get("updated_at", ""),
        "checkedAt": current.get("checked_at", ""),
        "hasPrevious": bool(previous.get("players")),
        "players": rows,
        "parties": party_rows(data, all_rows, previous),
        "leaders": {
            "highest": rows[0] if rows else None,
            "bestGain": max(gain_rows, key=lambda item: item["gainSinceLast"]) if gain_rows else None,
            "worstGain": min(gain_rows, key=lambda item: item["gainSinceLast"]) if gain_rows else None,
        },
    }


def snapshot_signature(reading: dict) -> tuple:
    players = reading.get("players", {})
    return tuple(
        sorted(
            (key, int(player.get("rank", 0)), int(player.get("level", 0)), int(player.get("points", 0)))
            for key, player in players.items()
        )
    )


def previous_distinct_reading(history: list[dict], current: dict) -> dict:
    current_signature = snapshot_signature(current)
    for reading in reversed(history[:-1]):
        if snapshot_signature(reading) != current_signature:
            return reading
    return {}


def json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def cookie_value(handler: SimpleHTTPRequestHandler, name: str) -> str:
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def is_authenticated(handler: SimpleHTTPRequestHandler) -> bool:
    token = cookie_value(handler, "rz_session")
    if validate_session_token(token):
        return True

    expires = SESSIONS.get(token, 0)
    if not token or expires < time.time():
        SESSIONS.pop(token, None)
        return False
    SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
    return True


def session_secret() -> str:
    return os.getenv("RANKZADA_SESSION_SECRET", "").strip() or os.getenv("RANKZADA_ADMIN_PASSWORD", "").strip() or "rankzada-local"


def make_session_token(user: str) -> str:
    expires = int(time.time() + SESSION_TTL_SECONDS)
    payload = f"{user}:{expires}"
    signature = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def validate_session_token(token: str) -> bool:
    parts = token.split(":")
    if len(parts) != 3:
        return False
    user, expires_raw, signature = parts
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if expires < time.time():
        return False
    expected_user = os.getenv("RANKZADA_ADMIN_USER", "admin")
    if user != expected_user:
        return False
    payload = f"{user}:{expires}"
    expected = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def require_auth(handler: SimpleHTTPRequestHandler) -> bool:
    if is_authenticated(handler):
        return True
    json_response(handler, 401, {"ok": False, "error": "Login necessario."})
    return False


def auth_status(handler: SimpleHTTPRequestHandler) -> dict:
    return {
        "ok": True,
        "authenticated": is_authenticated(handler),
        "user": os.getenv("RANKZADA_ADMIN_USER", "admin"),
        "configured": bool(os.getenv("RANKZADA_ADMIN_PASSWORD", "").strip()),
        "publicUpdate": public_update_enabled(),
    }


def public_update_enabled() -> bool:
    return os.getenv("RANKZADA_PUBLIC_UPDATE", "false").strip().lower() in {"1", "true", "yes", "sim"}


def login(handler: SimpleHTTPRequestHandler) -> None:
    payload = read_json_body(handler)
    user = os.getenv("RANKZADA_ADMIN_USER", "admin")
    password = os.getenv("RANKZADA_ADMIN_PASSWORD", "").strip()
    if not password:
        json_response(handler, 500, {"ok": False, "error": "Configure RANKZADA_ADMIN_PASSWORD no .env."})
        return
    if payload.get("user") != user or payload.get("password") != password:
        json_response(handler, 401, {"ok": False, "error": "Usuario ou senha invalidos."})
        return

    token = make_session_token(user)
    body = json.dumps({"ok": True, "authenticated": True, "user": user}).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    secure = "; Secure" if handler.headers.get("X-Forwarded-Proto", "").lower() == "https" or os.getenv("VERCEL") else ""
    handler.send_header("Set-Cookie", f"rz_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL_SECONDS}{secure}")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def logout(handler: SimpleHTTPRequestHandler) -> None:
    SESSIONS.pop(cookie_value(handler, "rz_session"), None)
    body = b'{"ok": true, "authenticated": false}'
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Set-Cookie", "rz_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def parties_payload() -> dict:
    data = load_data()
    return {"ok": True, "parties": configured_parties(data), "authenticated": False}


def save_party(payload: dict) -> dict:
    data = load_data()
    name = " ".join(str(payload.get("name", "")).split())
    members = [" ".join(str(item).split()) for item in payload.get("members", []) if str(item).strip()]
    if not name:
        raise ValueError("Informe o nome da PT.")
    if len(members) not in {4, 5}:
        raise ValueError("A PT precisa ter 4 ou 5 integrantes.")

    parties = configured_parties(data)
    highlight = bool(payload.get("highlight"))
    if highlight:
        for party in parties:
            party["highlight"] = False

    party = {
        "name": name,
        "members": members,
        "highlight": highlight,
        "target_xp": parse_xp_value(payload.get("target_xp")),
        "notes": str(payload.get("notes", "")).strip(),
    }
    key = normalize_name(name)
    existing = next((item for item in parties if normalize_name(item.get("name", "")) == key), None)
    if existing:
        existing.update(party)
    else:
        parties.append(party)
    data.setdefault("config", {})["parties"] = parties
    safe_save_data(data)
    try:
        save_party_config(party)
    except Exception:
        pass
    return {"ok": True, "party": party, "parties": parties}


def remove_party(payload: dict) -> dict:
    data = load_data()
    name = str(payload.get("name", "")).strip()
    key = normalize_name(name)
    parties = [party for party in configured_parties(data) if normalize_name(party.get("name", "")) != key]
    data.setdefault("config", {})["parties"] = parties
    safe_save_data(data)
    try:
        delete_party_config(name)
    except Exception:
        pass
    return {"ok": True, "parties": parties}


def update_ranking() -> dict:
    if not UPDATE_LOCK.acquire(blocking=False):
        payload = dashboard_payload({})
        payload["update"] = {
            "ok": False,
            "message": "Ja existe uma atualizacao em andamento. Aguarde terminar.",
            "busy": True,
            "supabase": {"configured": supabase_configured(), "saved": False, "error": ""},
        }
        return payload

    try:
        return perform_update_ranking()
    finally:
        UPDATE_LOCK.release()


def perform_update_ranking() -> dict:
    data = load_data()
    config = data.get("config", {})
    category = get_daily_category(data)
    world = config.get("rank_world", "")
    bucket = snapshot_bucket_key(category, world)

    entries = asyncio.run(fetch_ranking_entries(category, pages=1, world=world, allow_browser_fallback=False))
    data.setdefault("rank_snapshots", {})[bucket] = ranking_snapshot(entries)
    before_count = len(data.get("rank_history", {}).get(bucket, []))
    append_rank_history(data, bucket, entries)
    after_count = len(data.get("rank_history", {}).get(bucket, []))
    safe_save_data(data)

    supabase = {"configured": supabase_configured(), "saved": False, "error": ""}
    if entries and supabase["configured"]:
        try:
            supabase["readingId"] = save_rank_reading(bucket, category, world, entries)
            supabase["saved"] = bool(supabase["readingId"] and supabase["readingId"] != "duplicate")
            supabase["duplicate"] = supabase["readingId"] == "duplicate"
        except Exception as exc:
            supabase["error"] = str(exc)

    payload = dashboard_payload({"bucket": [bucket], "limit": [str(DEFAULT_DASHBOARD_LIMIT)]})
    payload["update"] = {
        "ok": True,
        "message": (
            f"Leitura nova salva com {len(entries)} players."
            if after_count > before_count
            else "Ranking consultado, mas o RubinOT ainda retornou a mesma leitura. Mantive a base anterior."
        ),
        "duplicate": after_count == before_count,
        "supabase": supabase,
    }
    return payload


def open_verification() -> dict:
    profile_dir = open_rubinot_verification_browser()
    return {
        "ok": True,
        "message": "Janela do RubinOT aberta. Faca a verificacao do Cloudflare, feche a janela e clique em Consultar RubinOT.",
        "profileDir": profile_dir,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            json_response(self, 200, {"ok": True})
            return

        if parsed.path == "/api/dashboard":
            json_response(self, 200, dashboard_payload(parse_qs(parsed.query)))
            return

        if parsed.path == "/api/auth":
            json_response(self, 200, auth_status(self))
            return

        if parsed.path == "/api/parties":
            payload = parties_payload()
            payload["authenticated"] = is_authenticated(self)
            json_response(self, 200, payload)
            return

        if parsed.path == "/" and (WEB_DIR / "index.html").exists():
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            login(self)
            return

        if parsed.path == "/api/logout":
            logout(self)
            return

        if parsed.path == "/api/parties":
            if not require_auth(self):
                return
            try:
                json_response(self, 200, save_party(read_json_body(self)))
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/delete-party":
            if not require_auth(self):
                return
            try:
                json_response(self, 200, remove_party(read_json_body(self)))
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/update-ranking":
            if not public_update_enabled() and not require_auth(self):
                return
            try:
                json_response(self, 200, update_ranking())
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/open-rubinot":
            if os.getenv("RANKZADA_ALLOW_BROWSER_VERIFICATION", "false").strip().lower() not in {"1", "true", "yes", "sim"}:
                json_response(self, 400, {"ok": False, "error": "Verificacao por navegador esta desativada neste ambiente."})
                return
            try:
                json_response(self, 200, open_verification())
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})
            return

        json_response(self, 404, {"ok": False, "error": "Rota nao encontrada."})


def main() -> None:
    load_dotenv()
    if not DATA_FILE.exists():
        DATA_FILE.write_text('{"players": {}, "days": {}, "config": {}}', encoding="utf-8")
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Painel aberto em http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
