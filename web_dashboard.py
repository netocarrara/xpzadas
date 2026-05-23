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
from urllib.parse import parse_qs, quote_plus, urlparse

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
    DEFAULT_RUBINOT_BASE_URL,
    HIGHSCORES_PATH,
    append_rank_history,
    api_entries,
    browser_fallback_enabled,
    fetch_ranking_entries,
    get_daily_category,
    get_highscore_category,
    load_data,
    normalize_name,
    open_rubinot_verification_browser,
    parse_rank_history_time,
    rank_cycle_bounds,
    rank_cycle_key,
    ranking_snapshot,
    resolve_world_id,
    save_data,
    snapshot_bucket_key,
)


WEB_DIR = Path("web")
UPDATE_LOCK = threading.Lock()
DEFAULT_DASHBOARD_LIMIT = 100
DEFAULT_RANK_WORLD = "Grimoria ll"
DEFAULT_MIN_READING_PLAYERS = 50
SESSIONS: dict[str, float] = {}
SESSION_TTL_SECONDS = 8 * 60 * 60


def configured_rank_world(data: dict) -> str:
    return (
        data.get("config", {}).get("rank_world", "")
        or os.getenv("RUBINOT_DEFAULT_WORLD", DEFAULT_RANK_WORLD)
    ).strip()


def safe_save_data(data: dict) -> None:
    try:
        save_data(data)
    except OSError:
        # Serverless hosts such as Vercel should treat Supabase as the durable store.
        pass


def min_reading_players() -> int:
    try:
        configured = int(os.getenv("RANKZADA_MIN_READING_PLAYERS", DEFAULT_MIN_READING_PLAYERS))
    except ValueError:
        configured = DEFAULT_MIN_READING_PLAYERS
    return max(1, configured)


def reading_player_count(reading: dict) -> int:
    players = reading.get("players", {})
    return len(players) if isinstance(players, dict) else 0


def valid_rank_history(history: list[dict]) -> list[dict]:
    minimum = min_reading_players()
    return [reading for reading in history if reading_player_count(reading) >= minimum]


def current_cycle_history(history: list[dict]) -> list[dict]:
    start, end = rank_cycle_bounds()
    result = []
    for reading in history:
        checked_at = parse_rank_history_time(reading)
        if checked_at and start <= checked_at < end:
            result.append(reading)
    return result


def parse_history_time(reading: dict) -> float:
    parsed = parse_rank_history_time(reading)
    return parsed.timestamp() if parsed else 0


def pick_rank_history(local_history: list[dict], supabase_history: list[dict]) -> tuple[list[dict], str]:
    local_history = current_cycle_history(valid_rank_history(local_history))
    supabase_history = current_cycle_history(valid_rank_history(supabase_history))
    merged: dict[tuple, dict] = {}
    sources: set[str] = set()

    for source, history in (("local", local_history), ("supabase", supabase_history)):
        if history:
            sources.add(source)
        for reading in history:
            signature = snapshot_signature(reading)
            if not signature:
                continue

            current = merged.get(signature)
            if current is None or parse_history_time(reading) >= parse_history_time(current):
                merged[signature] = reading

    if not merged:
        return [], "local"

    source_name = "+".join(source for source in ("local", "supabase") if source in sources)
    return sorted(merged.values(), key=parse_history_time), source_name or "local"


def format_bucket(data: dict) -> str:
    category = get_daily_category(data)
    world = configured_rank_world(data)
    return snapshot_bucket_key(category, world)


def cycle_progress(current: dict) -> float:
    start, end = rank_cycle_bounds()
    checked_at = parse_rank_history_time(current) or start
    elapsed = max(0, min((checked_at - start).total_seconds(), (end - start).total_seconds()))
    return elapsed / max(1, (end - start).total_seconds())


def player_cycle_events(history: list[dict]) -> dict[str, dict]:
    events: dict[str, dict] = {}
    for previous, current in zip(history, history[1:]):
        previous_players = previous.get("players", {})
        current_players = current.get("players", {})
        checked_at = current.get("checked_at", "")
        for key, player in current_players.items():
            old = previous_players.get(key)
            if not old:
                continue
            delta = int(player.get("points", 0)) - int(old.get("points", 0))
            level_delta = int(player.get("level", 0) or 0) - int(old.get("level", 0) or 0)
            item = events.setdefault(
                key,
                {"deathCount": 0, "xpLost": 0, "lastDeathAt": "", "levelUps": 0},
            )
            if delta < 0:
                item["deathCount"] += 1
                item["xpLost"] += abs(delta)
                item["lastDeathAt"] = checked_at
            if level_delta > 0:
                item["levelUps"] += level_delta
    return events


def player_rows(current: dict, previous: dict, events: dict[str, dict] | None = None) -> list[dict]:
    rows = []
    events = events or {}
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
                "gainSinceLast": None if old_points is None else points - old_points,
                "deathCount": int(events.get(key, {}).get("deathCount", 0)),
                "xpLost": int(events.get(key, {}).get("xpLost", 0)),
                "lastDeathAt": events.get(key, {}).get("lastDeathAt", ""),
                "levelUps": int(events.get(key, {}).get("levelUps", max(0, level - old_level))),
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


def party_rows(data: dict, rows: list[dict], previous: dict, progress: float) -> list[dict]:
    by_key = {row["key"]: row for row in rows}
    previous_players = previous.get("players", {})
    parties = configured_parties(data)
    result = []

    for party in parties:
        members = []
        total = 0
        net_gain = 0
        gross_gain = 0
        xp_lost = 0
        deaths = 0
        level_ups = 0
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
            member_loss = max(0, -member_gain) if member_gain is not None else int(row.get("xpLost", 0))
            members.append(
                {
                    **row,
                    "gainSinceLast": member_gain,
                    "grossGain": None if member_gain is None else max(0, member_gain),
                    "xpLost": max(int(row.get("xpLost", 0)), member_loss),
                }
            )
            total += row["points"]
            if member_gain is not None:
                net_gain += member_gain
                gross_gain += max(0, member_gain)
                xp_lost += max(int(row.get("xpLost", 0)), member_loss)
            deaths += int(row.get("deathCount", 0))
            level_ups += int(row.get("levelUps", 0))

        member_count = max(1, len(party.get("members", [])))
        current_gain = net_gain if previous_players else None
        projected_gain = None
        projected_missing = None
        target_status = "sem meta"
        if current_gain is not None and progress > 0:
            projected_gain = int(current_gain / progress)
        if target > 0 and projected_gain is not None:
            projected_missing = max(0, target - projected_gain)
            target_status = "alcanca" if projected_gain >= target else "nao alcanca"

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
                "grossGain": None if not previous_players else gross_gain,
                "xpLost": xp_lost,
                "deathCount": deaths,
                "levelUps": level_ups,
                "averagePerMember": None if current_gain is None else current_gain / member_count,
                "missingToTarget": missing_xp,
                "projectedGain": projected_gain,
                "projectedMissing": projected_missing,
                "targetStatus": target_status,
                "cycleProgress": progress,
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
                if item["projectedGain"] is not None:
                    item["projectedMissing"] = max(0, item["targetXp"] - item["projectedGain"])
                    item["targetStatus"] = "alcanca" if item["projectedGain"] >= item["targetXp"] else "nao alcanca"
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
    local_history = data.get("rank_history", {}).get(bucket, [])
    supabase_history = []
    if supabase_configured():
        try:
            supabase_history = load_rank_history(bucket, limit=288)
        except Exception:
            supabase_history = []
    history, source = pick_rank_history(local_history, supabase_history)

    if not history and data.get("rank_snapshots", {}).get(bucket):
        fallback_history = [
            {
                "checked_at": "",
                "updated_at": "",
                "players": data["rank_snapshots"][bucket],
            }
        ]
        history, source = pick_rank_history(fallback_history, [])

    current = history[-1] if history else {"players": {}}
    previous = cycle_base_reading(history, current)
    progress = cycle_progress(current)
    all_rows = player_rows(current, previous, player_cycle_events(history))
    rows = all_rows[:limit]
    gain_rows = [row for row in rows if row["gainSinceLast"] is not None]

    return {
        "bucket": bucket,
        "buckets": sorted(set(data.get("rank_snapshots", {})) | set(data.get("rank_history", {})) | ({bucket} if history else set())),
        "limit": limit,
        "totalPlayers": len(all_rows),
        "source": source,
        "supabaseConfigured": supabase_configured(),
        "world": configured_rank_world(data),
        "rubinotImportUrl": rubinot_import_url(data),
        "rubinotPageUrl": os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/") + "/highscores",
        "updatedAt": current.get("updated_at", ""),
        "checkedAt": current.get("checked_at", ""),
        "cycleKey": rank_cycle_key(),
        "cycleStart": rank_cycle_bounds()[0].isoformat(timespec="minutes"),
        "cycleEnd": rank_cycle_bounds()[1].isoformat(timespec="minutes"),
        "cycleProgress": progress,
        "hasPrevious": bool(previous.get("players")),
        "players": rows,
        "parties": party_rows(data, all_rows, previous, progress),
        "leaders": {
            "highest": rows[0] if rows else None,
            "bestGain": max(gain_rows, key=lambda item: item["gainSinceLast"]) if gain_rows else None,
            "worstGain": min(gain_rows, key=lambda item: item["gainSinceLast"]) if gain_rows else None,
        },
    }


def rubinot_import_url(data: dict) -> str:
    category = get_daily_category(data)
    world = configured_rank_world(data)
    api_path = HIGHSCORES_PATH.format(
        category=quote_plus(get_highscore_category(category)),
        world=quote_plus(resolve_world_id(world)),
    )
    base_url = os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/")
    return base_url + api_path


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


def cycle_base_reading(history: list[dict], current: dict) -> dict:
    current_signature = snapshot_signature(current)
    for reading in history[:-1]:
        if snapshot_signature(reading) != current_signature:
            return reading
    return {}


def json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    origin = handler.headers.get("Origin", "")
    if origin == os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/"):
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def read_text_body(handler: SimpleHTTPRequestHandler) -> str:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return ""
    return handler.rfile.read(length).decode("utf-8")


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
        "browserVerification": browser_verification_enabled(),
    }


def public_update_enabled() -> bool:
    return os.getenv("RANKZADA_PUBLIC_UPDATE", "false").strip().lower() in {"1", "true", "yes", "sim"}


def browser_verification_enabled() -> bool:
    return (
        os.getenv("RANKZADA_ALLOW_BROWSER_VERIFICATION", "false").strip().lower()
        in {"1", "true", "yes", "sim"}
        or browser_fallback_enabled()
    )


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
    category = get_daily_category(data)
    world = configured_rank_world(data)
    bucket = snapshot_bucket_key(category, world)

    entries = asyncio.run(fetch_ranking_entries(category, pages=1, world=world, allow_browser_fallback=True))
    return save_ranking_entries(entries, bucket, category, world, "Leitura nova salva com {count} players.")


def save_ranking_entries(entries: list, bucket: str, category: int, world: str, success_message: str) -> dict:
    data = load_data()
    if not entries:
        raise RuntimeError("Nenhum player encontrado na leitura importada.")
    minimum = min_reading_players()
    if len(entries) < minimum:
        raise RuntimeError(
            f"Leitura incompleta: recebi {len(entries)} player(s), mas o minimo para salvar e {minimum}. "
            "Abra o JSON completo do RubinOT e importe novamente."
        )

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
            success_message.format(count=len(entries))
            if after_count > before_count
            else "Ranking consultado, mas o RubinOT ainda retornou a mesma leitura. Mantive a base anterior."
        ),
        "duplicate": after_count == before_count,
        "supabase": supabase,
    }
    return payload


def import_ranking(payload: dict) -> dict:
    raw_payload = payload.get("payload", payload)
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    if not isinstance(raw_payload, dict):
        raise RuntimeError("Cole o JSON completo da resposta highscores do RubinOT.")

    raw_entries = raw_payload.get("players")
    if not isinstance(raw_entries, list):
        raise RuntimeError("Nao encontrei a lista players no JSON colado.")

    data = load_data()
    category = get_daily_category(data)
    world = configured_rank_world(data)
    bucket = snapshot_bucket_key(category, world)
    entries = api_entries(raw_payload)
    return save_ranking_entries(entries, bucket, category, world, "Leitura importada com {count} players.")


def public_base_url(handler: SimpleHTTPRequestHandler) -> str:
    configured = os.getenv("RANKZADA_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host", "")
    proto = handler.headers.get("X-Forwarded-Proto") or ("https" if host and not host.startswith(("127.", "localhost")) else "http")
    return f"{proto}://{host}".rstrip("/")


def import_bookmarklet(handler: SimpleHTTPRequestHandler) -> dict:
    data = load_data()
    api_url = rubinot_import_url(data)
    api_path = urlparse(api_url).path + ("?" + urlparse(api_url).query if urlparse(api_url).query else "")
    token = make_session_token(os.getenv("RANKZADA_ADMIN_USER", "admin"))
    target = public_base_url(handler) + "/api/import-ranking-token"
    script = (
        "javascript:(async()=>{"
        f"const api={json.dumps(api_path)},target={json.dumps(target)},token={json.dumps(token)};"
        "try{"
        "const r=await fetch(api,{credentials:'include'});"
        "const t=await r.text();"
        "if(!r.ok)throw new Error('RubinOT HTTP '+r.status+': '+t.slice(0,160));"
        "const up=await fetch(target+'?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8'},body:t});"
        "const out=await up.json().catch(()=>({}));"
        "if(!up.ok||out.ok===false)throw new Error(out.error||('Rankzada HTTP '+up.status));"
        "alert(out.update?.message||'Leitura enviada para o Rankzada.');"
        "}catch(e){alert('Rankzada: '+(e.message||e));}"
        "})();"
    )
    return {"ok": True, "bookmarklet": script}


def import_ranking_with_token(token: str, payload: str) -> dict:
    if not validate_session_token(token):
        raise PermissionError("Token de importacao invalido ou expirado. Gere o atalho novamente no painel.")
    return import_ranking({"payload": payload})


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

        if parsed.path == "/api/import-bookmarklet":
            if not require_auth(self):
                return
            try:
                json_response(self, 200, import_bookmarklet(self))
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})
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

        if parsed.path == "/api/import-ranking":
            if not require_auth(self):
                return
            try:
                json_response(self, 200, import_ranking(read_json_body(self)))
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/import-ranking-token":
            try:
                token = parse_qs(parsed.query).get("token", [""])[0]
                json_response(self, 200, import_ranking_with_token(token, read_text_body(self)))
            except PermissionError as exc:
                json_response(self, 401, {"ok": False, "error": str(exc)})
            except Exception as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/open-rubinot":
            if not browser_verification_enabled():
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
