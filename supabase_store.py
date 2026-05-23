import json
import os
from urllib.parse import quote
import urllib.error
import urllib.request
from dataclasses import asdict

from xp_bot import RankingEntry, normalize_name, now_rank_text


def supabase_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL", "").strip() and supabase_key())


def supabase_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )


def postgrest_url(table: str) -> str:
    return f"{os.getenv('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/{table}"


def request_json(method: str, table: str, payload: dict | list, prefer: str = "return=representation") -> list[dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    key = supabase_key()
    request = urllib.request.Request(
        postgrest_url(table),
        data=body,
        method=method,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else []
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail[:300]}") from exc


def upsert_json(table: str, payload: dict | list, conflict: str) -> list[dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    key = supabase_key()
    request = urllib.request.Request(
        f"{postgrest_url(table)}?on_conflict={quote(conflict, safe=',')}",
        data=body,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else []
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail[:300]}") from exc


def delete_json(table: str, filter_query: str) -> None:
    key = supabase_key()
    request = urllib.request.Request(
        f"{postgrest_url(table)}?{filter_query}",
        method="DELETE",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail[:300]}") from exc


def get_json(path: str) -> list[dict]:
    key = supabase_key()
    request = urllib.request.Request(
        f"{os.getenv('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/{path}",
        method="GET",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
            return json.loads(text) if text else []
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase HTTP {exc.code}: {detail[:300]}") from exc


def reading_signature(players: list[dict]) -> tuple:
    return tuple(
        sorted(
            (
                player.get("player_key", ""),
                int(player.get("rank", 0)),
                int(player.get("level", 0)),
                int(player.get("points", 0)),
            )
            for player in players
        )
    )


def entries_signature(entries: list[RankingEntry]) -> tuple:
    return tuple(
        sorted(
            (
                normalize_name(entry.name),
                int(entry.rank),
                int(entry.level),
                int(entry.points),
            )
            for entry in entries
        )
    )


def latest_supabase_players(bucket: str) -> list[dict]:
    readings = get_json(
        "rank_readings"
        f"?bucket=eq.{quote(bucket, safe='')}"
        "&select=id"
        "&order=checked_at.desc"
        "&limit=1"
    )
    if not readings:
        return []
    return get_json(
        "rank_players"
        f"?reading_id=eq.{quote(readings[0]['id'], safe='')}"
        "&select=player_key,rank,level,points"
        "&order=rank.asc"
    )


def supabase_has_same_latest(bucket: str, entries: list[RankingEntry]) -> bool:
    if not supabase_configured() or not entries:
        return False
    players = latest_supabase_players(bucket)
    return bool(players) and reading_signature(players) == entries_signature(entries)


def save_rank_reading(
    bucket: str,
    category: int,
    world: str,
    entries: list[RankingEntry],
) -> str:
    if not supabase_configured() or not entries:
        return ""
    if supabase_has_same_latest(bucket, entries):
        return "duplicate"

    reading = request_json(
        "POST",
        "rank_readings",
        {
            "bucket": bucket,
            "category": category,
            "world": world,
            "checked_at": now_rank_text(),
            "updated_at": entries[0].updated_at,
        },
    )[0]
    reading_id = reading["id"]
    players = []
    for entry in entries:
        players.append(
            {
                "reading_id": reading_id,
                "player_key": normalize_name(entry.name),
                "name": entry.name,
                "rank": entry.rank,
                "vocation": entry.vocation,
                "world": entry.world,
                "level": entry.level,
                "points": entry.points,
                "raw": asdict(entry),
            }
        )

    request_json("POST", "rank_players", players, prefer="return=minimal")
    return reading_id


def save_party_config(party: dict) -> None:
    if not supabase_configured():
        return
    upsert_json(
        "party_configs",
        {
            "party_key": normalize_name(party.get("name", "")),
            "name": party.get("name", ""),
            "members": party.get("members", []),
            "highlight": bool(party.get("highlight")),
            "target_xp": int(party.get("target_xp", 0) or 0),
            "notes": party.get("notes", ""),
        },
        "party_key",
    )


def delete_party_config(name: str) -> None:
    if not supabase_configured():
        return
    delete_json("party_configs", f"party_key=eq.{quote(normalize_name(name), safe='')}")


def load_party_configs() -> list[dict]:
    if not supabase_configured():
        return []
    rows = get_json(
        "party_configs"
        "?select=party_key,name,members,highlight,target_xp,notes"
        "&order=highlight.desc,name.asc"
    )
    return [
        {
            "name": row.get("name", ""),
            "members": row.get("members", []) or [],
            "highlight": bool(row.get("highlight")),
            "target_xp": int(row.get("target_xp", 0) or 0),
            "notes": row.get("notes", ""),
        }
        for row in rows
    ]


def load_rank_history(bucket: str, limit: int = 3) -> list[dict]:
    if not supabase_configured():
        return []

    readings = get_json(
        "rank_readings"
        f"?bucket=eq.{quote(bucket, safe='')}"
        "&select=id,bucket,checked_at,updated_at"
        "&order=checked_at.desc"
        f"&limit={max(1, min(limit, 300))}"
    )
    if not readings:
        return []

    players = []
    reading_ids = [reading["id"] for reading in readings]
    for index in range(0, len(reading_ids), 2):
        ids = ",".join(reading_ids[index : index + 2])
        players.extend(
            get_json(
                "rank_players"
                f"?reading_id=in.({ids})"
                "&select=reading_id,player_key,name,rank,level,points,raw"
                "&order=rank.asc"
            )
        )
    by_reading: dict[str, dict[str, dict]] = {}
    for player in players:
        reading_players = by_reading.setdefault(player["reading_id"], {})
        raw = player.get("raw") or {}
        reading_players[player["player_key"]] = {
            "name": player["name"],
            "rank": int(player["rank"]),
            "level": int(player["level"]),
            "vocation": raw.get("vocation", ""),
            "world": raw.get("world", ""),
            "points": int(player["points"]),
            "updated_at": raw.get("updated_at", ""),
            "checked_at": "",
        }

    history = []
    for reading in reversed(readings):
        reading_players = by_reading.get(reading["id"], {})
        for player in reading_players.values():
            player["updated_at"] = player.get("updated_at") or reading.get("updated_at", "")
            player["checked_at"] = reading.get("checked_at", "")
        history.append(
            {
                "checked_at": reading.get("checked_at", ""),
                "updated_at": reading.get("updated_at", ""),
                "players": reading_players,
            }
        )
    return history
