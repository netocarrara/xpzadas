import asyncio
import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from html import unescape
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


DATA_FILE = Path("xp_data.json")
DEFAULT_RUBINOT_BASE_URL = "https://rubinot.com.br"
EXPERIENCE_CATEGORY = 6
DAILY_EXPERIENCE_CATEGORY = 7
HIGHSCORES_PATH = "/api/highscores?world={world}&category={category}&vocation=0"
MAX_HIGHSCORE_PAGES = 20
DEFAULT_INTERVAL_MINUTES = 5
DEFAULT_RANK_LIMIT = 10
MAX_RANK_HISTORY_ITEMS = 288

WORLD_IDS = {
    "grimoria ii": "27",
    "grimoria ll": "27",
}

VOCATIONS = {
    1: "Sorcerer",
    2: "Druid",
    3: "Paladin",
    4: "Knight",
    5: "Master Sorcerer",
    6: "Elder Druid",
    7: "Royal Paladin",
    8: "Elite Knight",
    10: "Exalted Monk",
}


@dataclass
class PlayerXp:
    name: str
    start_xp: int
    end_xp: Optional[int]
    multiplier: float
    created_at: str
    level: Optional[int] = None
    last_checked: Optional[str] = None

    @property
    def gained(self) -> int:
        if self.end_xp is None:
            return 0
        return max(0, self.end_xp - self.start_xp)

    @property
    def normalized(self) -> float:
        if self.multiplier <= 0:
            return float(self.gained)
        return self.gained / self.multiplier


@dataclass
class CharacterSnapshot:
    name: str
    level: int
    total_xp: int
    updated_at: str


@dataclass
class RankingEntry:
    rank: int
    name: str
    vocation: str
    world: str
    level: int
    points: int
    updated_at: str


def today_key() -> str:
    return date.today().isoformat()


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_ts() -> float:
    return datetime.now().timestamp()


def normalize_name(name: str) -> str:
    return " ".join(name.strip().split()).lower()


def resolve_world_id(world: str) -> str:
    cleaned = " ".join(world.strip().split())
    if not cleaned:
        return ""
    if cleaned.isdigit():
        return cleaned
    return WORLD_IDS.get(cleaned.lower(), cleaned)


def get_highscore_category(category: int | str | None = None) -> str:
    if isinstance(category, str):
        return category
    return os.getenv("RUBINOT_HIGHSCORE_CATEGORY", "experience")


def get_cf_cookie() -> Optional[str]:
    clearance = os.getenv("RUBINOT_CF_CLEARANCE", "").strip()
    if not clearance:
        return None
    return f"cf_clearance={clearance}"


def browser_fallback_enabled() -> bool:
    return os.getenv("RUBINOT_BROWSER_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "sim"}


def fetch_highscores_with_browser(category: int | str, world: str) -> list[RankingEntry]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise RuntimeError("Instale selenium com `pip install -r requirements.txt` para usar o fallback por navegador.") from exc

    base_url = os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/")
    api_path = HIGHSCORES_PATH.format(
        category=quote_plus(get_highscore_category(category)),
        world=quote_plus(resolve_world_id(world)),
    )
    profile_dir = str(Path(os.getenv("RUBINOT_BROWSER_PROFILE_DIR", "chrome-profile-rubinot")).resolve())
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1280,900")

    driver = webdriver.Chrome(options=options)
    try:
        driver.set_script_timeout(45)
        clearance = os.getenv("RUBINOT_CF_CLEARANCE", "").strip()
        if clearance and "cole_o_cf_clearance" not in clearance:
            driver.get(base_url)
            driver.add_cookie(
                {
                    "name": "cf_clearance",
                    "value": clearance,
                    "domain": ".rubinot.com.br",
                    "path": "/",
                    "secure": True,
                }
            )
        driver.get(base_url + "/highscores")
        wait = WebDriverWait(driver, 120)
        wait.until(lambda item: "Just a moment" not in item.title)
        wait.until(lambda item: item.find_elements(By.TAG_NAME, "body"))

        script = """
            const apiPath = arguments[0];
            const done = arguments[arguments.length - 1];
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 30000);
            fetch(apiPath, { credentials: 'include', signal: controller.signal })
                .then(async response => {
                    clearTimeout(timeout);
                    const text = await response.text();
                    if (!response.ok) {
                        done({ ok: false, error: `HTTP ${response.status}: ${text.slice(0, 200)}` });
                        return;
                    }
                    done({ ok: true, data: JSON.parse(text) });
                })
                .catch(error => done({ ok: false, error: String(error) }));
        """
        result = driver.execute_async_script(script, api_path)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "falha desconhecida no navegador"))

        return api_entries(result["data"])
    finally:
        driver.quit()


def open_rubinot_verification_browser() -> str:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("Instale selenium com `pip install -r requirements.txt` para abrir a verificacao.") from exc

    base_url = os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/")
    profile_dir = str(Path(os.getenv("RUBINOT_BROWSER_PROFILE_DIR", "chrome-profile-rubinot")).resolve())
    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-size=1280,900")
    options.add_experimental_option("detach", True)

    driver = webdriver.Chrome(options=options)
    clearance = os.getenv("RUBINOT_CF_CLEARANCE", "").strip()
    if clearance and "cole_o_cf_clearance" not in clearance:
        driver.get(base_url)
        driver.add_cookie(
            {
                "name": "cf_clearance",
                "value": clearance,
                "domain": ".rubinot.com.br",
                "path": "/",
                "secure": True,
            }
        )
    driver.get(base_url + "/highscores")
    return profile_dir


def parse_xp(value: str) -> int:
    cleaned = value.lower().strip().replace(" ", "").replace(",", ".")
    multipliers = {"bi": 1_000_000_000, "b": 1_000_000_000, "kk": 1_000_000, "m": 1_000_000, "k": 1_000}

    for suffix, multiplier in multipliers.items():
        if cleaned.endswith(suffix):
            return int(float(cleaned[: -len(suffix)]) * multiplier)

    return int(float(cleaned))


def parse_int(value: str) -> int:
    return int(re.sub(r"[^\d]", "", value))


def format_xp(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}bi"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}kk"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(int(value))


def load_data() -> dict:
    if not DATA_FILE.exists():
        return {"players": {}, "days": {}, "config": {}}

    with DATA_FILE.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    data.setdefault("players", {})
    data.setdefault("days", {})
    data.setdefault("config", {})
    return data


def save_data(data: dict) -> None:
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def get_day(data: dict, day: Optional[str] = None) -> dict:
    key = day or today_key()
    data.setdefault("days", {})
    data["days"].setdefault(key, {})
    return data["days"][key]


def to_player(raw: dict) -> PlayerXp:
    return PlayerXp(
        name=raw["name"],
        start_xp=int(raw["start_xp"]),
        end_xp=raw.get("end_xp"),
        multiplier=float(raw.get("multiplier", 1)),
        created_at=raw.get("created_at", now_text()),
        level=raw.get("level"),
        last_checked=raw.get("last_checked"),
    )


def normalize_html_text(html: str) -> str:
    without_scripts = re.sub(r"<(script|style).*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return re.sub(r"\s+", " ", unescape(without_tags))


def extract_highscore_update(html: str) -> str:
    text = normalize_html_text(html)
    update_match = re.search(r"Highscores Last Update:\s*([0-9:]+)", text, flags=re.IGNORECASE)
    return update_match.group(1).strip() if update_match else now_text()


def extract_snapshots(html: str) -> dict[str, CharacterSnapshot]:
    entries = extract_ranking_entries(html)
    return {
        normalize_name(entry.name): CharacterSnapshot(
            name=entry.name,
            level=entry.level,
            total_xp=entry.points,
            updated_at=entry.updated_at,
        )
        for entry in entries
    }


def extract_ranking_entries(html: str) -> list[RankingEntry]:
    text = normalize_html_text(html)
    updated_at = extract_highscore_update(html)
    rows: list[RankingEntry] = []
    header = "Rank Name Vocation World Level Points"
    if header in text:
        text = text.split(header, 1)[1]

    pattern = re.compile(
        r"(?P<rank>\d{1,4})\s+"
        r"(?P<name>.+?)\s+"
        r"(?P<vocation>Master Sorcerer|Elder Druid|Elite Knight|Royal Paladin|Exalted Monk|Sorcerer|Druid|Knight|Paladin|Monk|None)\s+"
        r"(?P<world>[A-Za-z][A-Za-z\s]*)\s+"
        r"(?P<level>[\d,\.]+)\s+"
        r"(?P<points>[\d,\.]+)",
        flags=re.IGNORECASE,
    )

    for match in pattern.finditer(text):
        name = " ".join(match.group("name").split())
        rows.append(RankingEntry(
            rank=parse_int(match.group("rank")),
            name=name,
            vocation=match.group("vocation"),
            world=" ".join(match.group("world").split()),
            level=parse_int(match.group("level")),
            points=parse_int(match.group("points")),
            updated_at=updated_at,
        ))

    return rows


def get_daily_category(data: Optional[dict] = None) -> int:
    if data:
        configured = data.get("config", {}).get("daily_category")
        if configured:
            return int(configured)
    return int(os.getenv("RUBINOT_DAILY_CATEGORY", DAILY_EXPERIENCE_CATEGORY))


async def fetch_highscore_page(
    session: aiohttp.ClientSession,
    page: int,
    category: int | str = EXPERIENCE_CATEGORY,
    world: str = "",
) -> str:
    base_url = os.getenv("RUBINOT_BASE_URL", DEFAULT_RUBINOT_BASE_URL).rstrip("/")
    url = base_url + HIGHSCORES_PATH.format(
        category=quote_plus(get_highscore_category(category)),
        world=quote_plus(resolve_world_id(world)),
    )
    headers = {
        "User-Agent": os.getenv(
            "RUBINOT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        ),
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": base_url + "/highscores",
        "Priority": "u=1, i",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    cookie = get_cf_cookie()
    if cookie:
        headers["Cookie"] = cookie

    async with session.get(url, headers=headers, timeout=30) as response:
        if response.status == 403:
            raise RuntimeError(
                "RubinOT bloqueou a leitura automatica com Cloudflare. "
                "Precisamos usar uma API liberada pelo site ou uma sessao de navegador autenticada."
            )
        response.raise_for_status()
        return await response.text()


def api_entries(payload: dict) -> list[RankingEntry]:
    entries: list[RankingEntry] = []
    cached_at = payload.get("cachedAt")
    updated_at = now_text()
    if cached_at:
        updated_at = datetime.fromtimestamp(int(cached_at) / 1000).isoformat(timespec="seconds")

    for player in payload.get("players", []):
        vocation_id = int(player.get("vocation", 0))
        entries.append(
            RankingEntry(
                rank=int(player["rank"]),
                name=player["name"],
                vocation=VOCATIONS.get(vocation_id, f"Vocation {vocation_id}"),
                world=player.get("worldName", ""),
                level=int(player["level"]),
                points=int(player["value"]),
                updated_at=updated_at,
            )
        )

    return entries


async def fetch_tracked_snapshots(session: aiohttp.ClientSession, names: list[str]) -> tuple[dict[str, CharacterSnapshot], list[str]]:
    wanted = {normalize_name(name) for name in names}
    found: dict[str, CharacterSnapshot] = {}

    for page in range(1, MAX_HIGHSCORE_PAGES + 1):
        html = await fetch_highscore_page(session, page, EXPERIENCE_CATEGORY)
        snapshots = extract_snapshots(html)
        for key in wanted - set(found):
            if key in snapshots:
                found[key] = snapshots[key]

        if wanted.issubset(found):
            break

        await asyncio.sleep(1)

    missing = sorted(wanted - set(found))
    return found, missing


async def fetch_ranking_entries(
    category: int,
    pages: int = MAX_HIGHSCORE_PAGES,
    world: str = "",
    allow_browser_fallback: bool = True,
) -> list[RankingEntry]:
    try:
        async with aiohttp.ClientSession() as session:
            text = await fetch_highscore_page(session, 1, category, world)
        return api_entries(json.loads(text))
    except Exception:
        if not allow_browser_fallback or not browser_fallback_enabled():
            raise
        return await asyncio.to_thread(fetch_highscores_with_browser, category, world)


async def update_tracked_players() -> tuple[list[PlayerXp], list[str]]:
    data = load_data()
    tracked = data.get("players", {})
    day = get_day(data)
    updated: list[PlayerXp] = []
    errors: list[str] = []

    if not tracked:
        return updated, ["Nenhum player cadastrado. Use `/adicionar_player`."]

    names = [raw["name"] for raw in tracked.values()]

    async with aiohttp.ClientSession() as session:
        try:
            snapshots, missing = await fetch_tracked_snapshots(session, names)
        except Exception as exc:
            return updated, [f"Falha ao ler highscores do RubinOT: {exc}"]

        for missing_key in missing:
            errors.append(f"{tracked[missing_key]['name']}: nao achei no top {MAX_HIGHSCORE_PAGES * 50} de Experience Points")

        for key, snapshot in snapshots.items():
            raw = tracked[key]
            name = raw["name"]

            existing = day.get(key)
            if existing:
                player = to_player(existing)
                player.end_xp = snapshot.total_xp
                player.level = snapshot.level
                player.last_checked = now_text()
            else:
                player = PlayerXp(
                    name=name,
                    start_xp=snapshot.total_xp,
                    end_xp=snapshot.total_xp,
                    multiplier=float(raw.get("multiplier", 1)),
                    created_at=now_text(),
                    level=snapshot.level,
                    last_checked=now_text(),
                )

            day[key] = asdict(player)
            updated.append(player)

    save_data(data)
    return updated, errors


def build_report(players: list[PlayerXp], errors: Optional[list[str]] = None, day: Optional[str] = None) -> str:
    if not players:
        return "Ainda nao tem player atualizado hoje."

    players = sorted(players, key=lambda item: item.normalized, reverse=True)
    best = players[0]
    total_raw = sum(player.gained for player in players)
    total_normalized = sum(player.normalized for player in players)

    lines = [
        f"**XP da PT - {day or today_key()}**",
        f"Melhor ate agora: **{best.name}** com {format_xp(best.normalized)} de XP justa.",
        "",
    ]

    for index, player in enumerate(players, start=1):
        level = f"lvl {player.level}" if player.level else "lvl ?"
        lines.append(
            f"{index}. **{player.name}** ({level}) | inicio {format_xp(player.start_xp)} | "
            f"atual {format_xp(player.end_xp or player.start_xp)} | fez {format_xp(player.gained)} | "
            f"x{player.multiplier:g} | justa {format_xp(player.normalized)}"
        )

    lines.extend(
        [
            "",
            f"Total bruto: **{format_xp(total_raw)}**",
            f"Total justo: **{format_xp(total_normalized)}**",
        ]
    )

    if errors:
        lines.append("")
        lines.append("Falhas: " + " | ".join(errors[:3]))

    return "\n".join(lines)


def build_top_report(entries: list[RankingEntry], limit: int = 10, world: str = "") -> str:
    if not entries:
        return "Nao consegui ler o ranking diario do RubinOT."

    scope = f" - {world}" if world else ""
    lines = [
        f"**Top {min(limit, len(entries))} - Daily Experience raw{scope}**",
        f"Atualizacao do highscore: {entries[0].updated_at}",
        "",
    ]

    for entry in entries[:limit]:
        lines.append(
            f"{entry.rank}. **{entry.name}** ({entry.vocation}, lvl {entry.level}) | {format_xp(entry.points)}"
        )

    return "\n".join(lines)


def snapshot_bucket_key(category: int, world: str) -> str:
    return f"{category}:{world.strip().lower() or 'all'}"


def build_rank_update_report(
    entries: list[RankingEntry],
    previous: dict[str, dict],
    limit: int = 10,
    world: str = "",
) -> str:
    if not entries:
        return "Nao consegui ler o ranking diario do RubinOT."

    scope = f" - {world}" if world else ""
    lines = [
        f"**Rank Daily Experience raw{scope}**",
        f"Atualizacao do highscore: {entries[0].updated_at}",
        "",
    ]

    for entry in entries[:limit]:
        old = previous.get(normalize_name(entry.name))
        if old is None:
            gained = "primeira leitura"
        else:
            delta = max(0, entry.points - int(old.get("points", 0)))
            gained = f"+{format_xp(delta)} raw"

        lines.append(
            f"{entry.rank}. **{entry.name}** | lvl {entry.level} | "
            f"XP rank {format_xp(entry.points)} | {gained}"
        )

    return "\n".join(lines)


def ranking_snapshot(entries: list[RankingEntry]) -> dict[str, dict]:
    return {
        normalize_name(entry.name): {
            "name": entry.name,
            "rank": entry.rank,
            "level": entry.level,
            "vocation": entry.vocation,
            "world": entry.world,
            "points": entry.points,
            "updated_at": entry.updated_at,
            "checked_at": now_text(),
        }
        for entry in entries
    }


def append_rank_history(data: dict, bucket: str, entries: list[RankingEntry]) -> None:
    if not entries:
        return

    history = data.setdefault("rank_history", {}).setdefault(bucket, [])
    snapshot = ranking_snapshot(entries)
    if history:
        last = history[-1]
        last_players = last.get("players", {})
        same_update = last.get("updated_at") == entries[0].updated_at
        same_points = all(
            key in last_players and int(last_players[key].get("points", 0)) == int(player.get("points", 0))
            for key, player in snapshot.items()
        )
        if same_update and same_points:
            last["checked_at"] = now_text()
            last["players"] = snapshot
            return

    history.append({"checked_at": now_text(), "updated_at": entries[0].updated_at, "players": snapshot})
    if len(history) > MAX_RANK_HISTORY_ITEMS:
        del history[:-MAX_RANK_HISTORY_ITEMS]


def parse_member_list(members: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", members) if item.strip()]


def configured_parties(data: dict) -> list[dict]:
    return data.setdefault("config", {}).setdefault("parties", [])


def build_parties_report(data: dict) -> str:
    parties = configured_parties(data)
    if not parties:
        return "Nenhuma PT cadastrada. Use `/pt_definir nome:Minha PT membros:Char Um, Char Dois minha_pt:True`."

    lines = ["**PTs cadastradas**"]
    for party in parties:
        marker = " (minha PT)" if party.get("highlight") else ""
        members = ", ".join(party.get("members", [])) or "sem membros"
        lines.append(f"- **{party.get('name', 'PT')}**{marker}: {members}")
    return "\n".join(lines)


def build_party_comparison(entries: list[RankingEntry], party_xp: int, world: str = "") -> str:
    if not entries:
        return "Nao consegui ler o ranking diario do RubinOT."

    position = len(entries) + 1
    for index, entry in enumerate(entries, start=1):
        if party_xp >= entry.points:
            position = index
            break

    top = entries[0]
    scope = f" - {world}" if world else ""
    lines = [
        f"**Comparativo da PT vs Daily Experience raw{scope}**",
        f"XP total da PT: **{format_xp(party_xp)}**",
        f"Atualizacao do highscore: {top.updated_at}",
        "",
    ]

    if position == 1:
        diff = party_xp - top.points
        lines.append(f"A PT ficaria em **1 lugar**, passando **{top.name}** por {format_xp(diff)}.")
    elif position <= len(entries):
        ahead = entries[position - 2]
        behind = entries[position - 1]
        lines.append(f"A PT ficaria em **{position} lugar** no ranking lido.")
        lines.append(f"Falta {format_xp(ahead.points - party_xp)} para passar **{ahead.name}**.")
        lines.append(f"Ficaria acima de **{behind.name}** por {format_xp(party_xp - behind.points)}.")
    else:
        last = entries[-1]
        lines.append(f"A PT nao entraria no top {len(entries)} lido.")
        lines.append(f"Faltam {format_xp(last.points - party_xp)} para alcancar o rank {last.rank}, **{last.name}**.")

    lines.append("")
    lines.append("Referencia do topo:")
    for entry in entries[:5]:
        lines.append(f"{entry.rank}. **{entry.name}** | {format_xp(entry.points)}")

    return "\n".join(lines)


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    await bot.tree.sync()
    if not auto_update.is_running():
        auto_update.start()
    print(f"Bot online como {bot.user}", flush=True)


@tasks.loop(minutes=DEFAULT_INTERVAL_MINUTES)
async def auto_update() -> None:
    data = load_data()
    await maybe_send_rank_update(data)

    channel_id = data.get("config", {}).get("report_channel_id")
    if not channel_id:
        return

    players, errors = await update_tracked_players()
    channel = bot.get_channel(int(channel_id))
    if channel and players:
        await channel.send(build_report(players, errors))


async def maybe_send_rank_update(data: dict) -> None:
    config = data.get("config", {})
    channel_id = config.get("rank_channel_id")
    if not channel_id:
        return

    interval_minutes = max(5, int(config.get("rank_interval_minutes", DEFAULT_INTERVAL_MINUTES)))
    last_sent = float(config.get("rank_last_sent", 0))
    if now_ts() - last_sent < interval_minutes * 60:
        return

    category = get_daily_category(data)
    world = config.get("rank_world", "")
    limit = max(1, min(int(config.get("rank_limit", DEFAULT_RANK_LIMIT)), 20))
    try:
        entries = await fetch_ranking_entries(category, pages=1, world=world)
    except Exception as exc:
        entries = []
        error_text = f"Nao consegui ler o ranking do RubinOT agora: {exc}"
    else:
        error_text = ""
    bucket = snapshot_bucket_key(category, world)
    previous = data.setdefault("rank_snapshots", {}).get(bucket, {})

    channel = bot.get_channel(int(channel_id))
    if channel:
        if error_text:
            await channel.send(error_text)
        else:
            await channel.send(build_rank_update_report(entries, previous, limit, world))

    if entries:
        data["rank_snapshots"][bucket] = ranking_snapshot(entries)
        append_rank_history(data, bucket, entries)
    data["config"]["rank_last_sent"] = now_ts()
    save_data(data)


@bot.tree.command(name="canal_xp", description="Define o canal onde o bot vai mandar atualizacoes automaticas.")
async def canal_xp(interaction: discord.Interaction) -> None:
    data = load_data()
    data["config"]["report_channel_id"] = interaction.channel_id
    save_data(data)
    await interaction.response.send_message("Canal de XP configurado. Vou atualizar a cada 5 minutos.")


@bot.tree.command(name="canal_rank", description="Define este canal para receber o ranking diario automaticamente.")
async def canal_rank(interaction: discord.Interaction) -> None:
    data = load_data()
    data["config"]["rank_channel_id"] = interaction.channel_id
    data["config"].setdefault("rank_interval_minutes", DEFAULT_INTERVAL_MINUTES)
    data["config"].setdefault("rank_limit", DEFAULT_RANK_LIMIT)
    save_data(data)
    await interaction.response.send_message("Canal de rank configurado. Vou postar o ranking automaticamente.")


@bot.tree.command(name="mundo_rank", description="Define o mundo usado no ranking automatico.")
@app_commands.describe(mundo="Nome do mundo. Ex: Cellenium. Use vazio para todos os mundos.")
async def mundo_rank(interaction: discord.Interaction, mundo: str = "") -> None:
    data = load_data()
    data["config"]["rank_world"] = mundo.strip()
    save_data(data)
    label = mundo.strip() or "todos os mundos"
    await interaction.response.send_message(f"Ranking automatico configurado para **{label}**.")


@bot.tree.command(name="intervalo_rank", description="Define de quantos em quantos minutos o rank automatico sera postado.")
@app_commands.describe(minutos="Minimo 5 minutos.", limite="Quantos players mostrar. Maximo 20.")
async def intervalo_rank(interaction: discord.Interaction, minutos: int = 5, limite: int = 10) -> None:
    data = load_data()
    data["config"]["rank_interval_minutes"] = max(5, minutos)
    data["config"]["rank_limit"] = max(1, min(limite, 20))
    save_data(data)
    await interaction.response.send_message(
        f"Ranking automatico configurado para cada {data['config']['rank_interval_minutes']} minutos, "
        f"mostrando top {data['config']['rank_limit']}."
    )


@bot.tree.command(name="adicionar_player", description="Adiciona um personagem para monitorar pelo highscore do RubinOT.")
@app_commands.describe(
    nome="Nome exato do personagem no Tibia",
    multiplicador="Use 5 para player x5, 1 para normal, 2 para x2 etc.",
)
async def adicionar_player(interaction: discord.Interaction, nome: str, multiplicador: float = 1.0) -> None:
    data = load_data()
    key = normalize_name(nome)
    data["players"][key] = {"name": nome, "multiplier": multiplicador}
    save_data(data)

    await interaction.response.send_message(
        f"Adicionado **{nome}** com multiplicador x{multiplicador:g}. "
        "Na primeira atualizacao do dia, essa XP vira o inicio."
    )


@bot.tree.command(name="remover_player", description="Remove um personagem do monitoramento.")
@app_commands.describe(nome="Nome do personagem")
async def remover_player(interaction: discord.Interaction, nome: str) -> None:
    data = load_data()
    removed = data["players"].pop(normalize_name(nome), None)
    save_data(data)

    if removed:
        await interaction.response.send_message(f"Removi **{removed['name']}** do monitoramento.")
    else:
        await interaction.response.send_message(f"**{nome}** nao estava cadastrado.", ephemeral=True)


@bot.tree.command(name="players", description="Mostra os personagens monitorados.")
async def players(interaction: discord.Interaction) -> None:
    data = load_data()
    tracked = data.get("players", {})
    if not tracked:
        await interaction.response.send_message("Nenhum player cadastrado.")
        return

    lines = ["**Players monitorados**"]
    for raw in tracked.values():
        lines.append(f"- **{raw['name']}** | x{float(raw.get('multiplier', 1)):g}")

    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="atualizar_xp", description="Atualiza agora pelo RubinOT e mostra o relatorio dos players cadastrados.")
async def atualizar_xp(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    players, errors = await update_tracked_players()
    await interaction.followup.send(build_report(players, errors))


@bot.tree.command(name="top_diario", description="Mostra o top Daily Experience raw do RubinOT.")
@app_commands.describe(
    limite="Quantos players mostrar. Padrao: 10.",
    categoria="ID da categoria no RubinOT, se precisar ajustar.",
    mundo="Mundo especifico. Ex: Cellenium. Vazio usa todos/configurado.",
    salvar_leitura="Se sim, usa esta consulta como base para calcular a proxima atualizacao.",
)
async def top_diario(
    interaction: discord.Interaction,
    limite: int = 10,
    categoria: Optional[int] = None,
    mundo: Optional[str] = None,
    salvar_leitura: bool = False,
) -> None:
    await interaction.response.defer()
    data = load_data()
    selected_category = categoria or get_daily_category(data)
    selected_world = mundo if mundo is not None else data.get("config", {}).get("rank_world", "")
    try:
        entries = await fetch_ranking_entries(selected_category, pages=1, world=selected_world)
    except Exception as exc:
        await interaction.followup.send(f"Nao consegui ler o ranking do RubinOT agora: {exc}")
        return
    bucket = snapshot_bucket_key(selected_category, selected_world)
    previous = data.setdefault("rank_snapshots", {}).get(bucket, {})

    if salvar_leitura:
        data["rank_snapshots"][bucket] = ranking_snapshot(entries)
        append_rank_history(data, bucket, entries)
        save_data(data)

    await interaction.followup.send(
        build_rank_update_report(entries, previous, max(1, min(limite, 20)), selected_world)
    )


@bot.tree.command(name="comparar_pt", description="Compara a XP total da sua PT contra o rank Daily Experience raw.")
@app_commands.describe(
    xp="XP total da PT. Ex: 250kk, 1.5bi, 123456789",
    categoria="ID da categoria no RubinOT, se precisar ajustar.",
    mundo="Mundo especifico. Ex: Cellenium. Vazio usa todos/configurado.",
)
async def comparar_pt(
    interaction: discord.Interaction,
    xp: str,
    categoria: Optional[int] = None,
    mundo: Optional[str] = None,
) -> None:
    await interaction.response.defer()
    data = load_data()
    selected_category = categoria or get_daily_category(data)
    selected_world = mundo if mundo is not None else data.get("config", {}).get("rank_world", "")
    party_xp = parse_xp(xp)
    try:
        entries = await fetch_ranking_entries(selected_category, world=selected_world)
    except Exception as exc:
        await interaction.followup.send(f"Nao consegui ler o ranking do RubinOT agora: {exc}")
        return
    await interaction.followup.send(build_party_comparison(entries, party_xp, selected_world))


@bot.tree.command(name="pt_definir", description="Cadastra ou atualiza uma PT para comparar no painel web.")
@app_commands.describe(
    nome="Nome da PT. Ex: Minha PT ou Meninos",
    membros="Personagens separados por virgula. Ex: Char Um, Char Dois, Char Tres",
    minha_pt="Marque True para destacar como a sua PT.",
)
async def pt_definir(
    interaction: discord.Interaction,
    nome: str,
    membros: str,
    minha_pt: bool = False,
) -> None:
    data = load_data()
    party_name = nome.strip()
    member_names = parse_member_list(membros)
    if not party_name or not member_names:
        await interaction.response.send_message("Informe o nome da PT e pelo menos um membro.", ephemeral=True)
        return

    parties = configured_parties(data)
    if minha_pt:
        for party in parties:
            party["highlight"] = False

    key = normalize_name(party_name)
    existing = next((party for party in parties if normalize_name(party.get("name", "")) == key), None)
    payload = {"name": party_name, "members": member_names, "highlight": minha_pt}
    if existing:
        existing.update(payload)
    else:
        parties.append(payload)

    save_data(data)
    await interaction.response.send_message(f"PT **{party_name}** salva com {len(member_names)} membros.")


@bot.tree.command(name="pt_remover", description="Remove uma PT do painel web.")
@app_commands.describe(nome="Nome da PT cadastrada.")
async def pt_remover(interaction: discord.Interaction, nome: str) -> None:
    data = load_data()
    parties = configured_parties(data)
    key = normalize_name(nome)
    kept = [party for party in parties if normalize_name(party.get("name", "")) != key]
    data["config"]["parties"] = kept
    save_data(data)

    if len(kept) == len(parties):
        await interaction.response.send_message(f"PT **{nome}** nao estava cadastrada.", ephemeral=True)
    else:
        await interaction.response.send_message(f"PT **{nome}** removida.")


@bot.tree.command(name="pts", description="Mostra as PTs cadastradas para comparacao.")
async def pts(interaction: discord.Interaction) -> None:
    data = load_data()
    await interaction.response.send_message(build_parties_report(data))


@bot.tree.command(name="categoria_diaria", description="Define o ID da categoria Daily Experience raw do RubinOT.")
@app_commands.describe(categoria="ID da categoria no highscore do RubinOT.")
async def categoria_diaria(interaction: discord.Interaction, categoria: int) -> None:
    data = load_data()
    data["config"]["daily_category"] = categoria
    save_data(data)
    await interaction.response.send_message(f"Categoria diaria configurada como `{categoria}`.")


@bot.tree.command(name="ranking", description="Mostra o ranking salvo do dia.")
@app_commands.describe(dia="Opcional. Data no formato YYYY-MM-DD.")
async def ranking(interaction: discord.Interaction, dia: Optional[str] = None) -> None:
    data = load_data()
    key = dia or today_key()
    day = data.get("days", {}).get(key, {})

    if not day:
        await interaction.response.send_message(f"Nenhum registro encontrado para {key}.")
        return

    players = [to_player(raw) for raw in day.values()]
    await interaction.response.send_message(build_report(players, day=key))


@bot.tree.command(name="fixar_inicio", description="Ajusta manualmente a XP inicial de um player no dia atual.")
@app_commands.describe(nome="Nome do player", xp="XP inicial. Ex: 250kk ou 123456789")
async def fixar_inicio(interaction: discord.Interaction, nome: str, xp: str) -> None:
    data = load_data()
    day = get_day(data)
    key = normalize_name(nome)

    if key not in day:
        await interaction.response.send_message(
            f"**{nome}** ainda nao tem leitura hoje. Use `/atualizar_xp` primeiro.",
            ephemeral=True,
        )
        return

    player = to_player(day[key])
    player.start_xp = parse_xp(xp)
    day[key] = asdict(player)
    save_data(data)

    await interaction.response.send_message(f"Inicio de **{player.name}** ajustado para {format_xp(player.start_xp)}.")


@bot.tree.command(name="limpar_dia", description="Apaga os registros de um dia.")
@app_commands.describe(dia="Opcional. Data no formato YYYY-MM-DD.")
async def limpar_dia(interaction: discord.Interaction, dia: Optional[str] = None) -> None:
    data = load_data()
    key = dia or today_key()

    if key not in data.get("days", {}):
        await interaction.response.send_message(f"Nao existe registro para {key}.", ephemeral=True)
        return

    del data["days"][key]
    save_data(data)
    await interaction.response.send_message(f"Registros de {key} apagados.")


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Configure DISCORD_TOKEN no arquivo .env.")

    bot.run(token)


if __name__ == "__main__":
    main()
