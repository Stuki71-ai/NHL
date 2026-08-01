from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

from nhl_edge.config import MARKETS, ODDS_FORMAT, REGIONS, SPORT_KEY, env

ET = ZoneInfo("America/New_York")


def _odds_key() -> str:
    return env("ODDS_API_KEY") or env("ODDS_API_KEY_FALLBACK")


def fetch_odds(date_et: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Odds API events with h2h + spreads + totals. date_et=YYYY-MM-DD uses a
    historical snapshot (~17:30 ET that day — post injury report, pre evening tips).

    Returns (events, x-requests-remaining header or None).
    """
    key = _odds_key()
    if not key:
        raise RuntimeError("ODDS_API_KEY missing")

    if date_et:
        y, m, d = [int(x) for x in date_et.split("-")]
        snap_et = datetime(y, m, d, 17, 30, 0, tzinfo=ET)
        snap_utc = snap_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"https://api.the-odds-api.com/v4/historical/sports/{SPORT_KEY}/odds"
        params = {
            "apiKey": key,
            "regions": REGIONS,
            "markets": MARKETS,
            "oddsFormat": ODDS_FORMAT,
            "dateFormat": "iso",
            "date": snap_utc,
        }
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining")
        payload = r.json()
        if isinstance(payload, list):
            data = payload
        elif isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                data = data.get("data") or data.get("odds") or []
            if not isinstance(data, list):
                data = payload.get("odds") or []
        else:
            data = []
        print(f"[slate] historical odds snapshot={snap_utc} events={len(data)}")
        return data if isinstance(data, list) else [], remaining

    url = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds"
    r = requests.get(
        url,
        params={
            "apiKey": key,
            "regions": REGIONS,
            "markets": MARKETS,
            "oddsFormat": ODDS_FORMAT,
            "dateFormat": "iso",
        },
        timeout=45,
    )
    r.raise_for_status()
    remaining = r.headers.get("x-requests-remaining")
    data = r.json()
    return data if isinstance(data, list) else [], remaining


def teams_played_on(date_et: str) -> set[str]:
    """Team display names that played on a given ET date (ESPN scoreboard, public JSON).

    Used for back-to-back detection: today's team present in yesterday's set → 2nd night.
    Fail-open: an error returns an empty set (no B2B adjustment, never a blocked run).
    """
    try:
        ymd = date_et.replace("-", "")
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={ymd}",
            timeout=25,
        )
        r.raise_for_status()
        out: set[str] = set()
        for ev in r.json().get("events") or []:
            for comp in ev.get("competitions") or []:
                for c in comp.get("competitors") or []:
                    name = ((c.get("team") or {}).get("displayName")) or ""
                    if name:
                        out.add(name)
        return out
    except Exception as e:
        print(f"[slate] b2b scoreboard fetch failed ({str(e)[:100]}) — no B2B flags")
        return set()


def b2b_teams_for(date_et: str) -> set[str]:
    y, m, d = [int(x) for x in date_et.split("-")]
    prev = (datetime(y, m, d, tzinfo=ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    teams = teams_played_on(prev)
    if teams:
        print(f"[slate] {len(teams)} teams played {prev} (B2B candidates)")
    return teams


def filter_slate(
    events: list[dict[str, Any]],
    *,
    min_minutes: float = 15.0,
    date_et: str | None = None,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Game-day slate (EDGE family):

    - **Historical** (`date_et=YYYY-MM-DD`): every event on that ET calendar day.
    - **Live**: every event on **today ET** not yet started
      (`commence >= now + min_minutes`), through end of that ET day.
    """
    out: list[dict[str, Any]] = []
    if date_et:
        y, m, d = [int(x) for x in date_et.split("-")]
        target = datetime(y, m, d, tzinfo=ET).date()
        for e in events:
            ct = e.get("commence_time")
            if not ct:
                continue
            try:
                t = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            except Exception:
                continue
            if t.astimezone(ET).date() == target:
                out.append(e)
        return out

    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today_et = now.astimezone(ET).date()
    day_end_et = datetime(today_et.year, today_et.month, today_et.day, 23, 59, 59, tzinfo=ET)
    day_end_utc = day_end_et.astimezone(timezone.utc)
    min_start = now.timestamp() + min_minutes * 60

    for e in events:
        ct = e.get("commence_time")
        if not ct:
            continue
        try:
            t = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except Exception:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t.astimezone(ET).date() != today_et:
            continue
        if t.timestamp() < min_start:
            continue
        if t > day_end_utc:
            continue
        out.append(e)
    return out


def best_prices(event: dict[str, Any]) -> dict[str, Any]:
    """Best decimal prices: ML home/away, total over/under at the consensus line,
    spread home/away at the consensus home handicap."""
    home = event.get("home_team") or ""
    away = event.get("away_team") or ""
    ml_home = ml_away = 0.0
    totals: dict[float, dict[str, float]] = {}
    spreads: dict[float, dict[str, float]] = {}  # keyed by HOME handicap
    total_points: list[float] = []
    spread_points: list[float] = []

    for bk in event.get("bookmakers") or []:
        for mk in bk.get("markets") or []:
            key = mk.get("key")
            if key == "h2h":
                for o in mk.get("outcomes") or []:
                    name, price = o.get("name") or "", float(o.get("price") or 0)
                    if name == home and price > ml_home:
                        ml_home = price
                    if name == away and price > ml_away:
                        ml_away = price
            elif key == "totals":
                for o in mk.get("outcomes") or []:
                    pt = o.get("point")
                    if pt is None:
                        continue
                    pt = float(pt)
                    side = (o.get("name") or "").lower()
                    price = float(o.get("price") or 0)
                    bucket = totals.setdefault(pt, {"over": 0.0, "under": 0.0})
                    if side == "over":
                        total_points.append(pt)
                        if price > bucket["over"]:
                            bucket["over"] = price
                    if side == "under" and price > bucket["under"]:
                        bucket["under"] = price
            elif key == "spreads":
                for o in mk.get("outcomes") or []:
                    pt = o.get("point")
                    name = o.get("name") or ""
                    if pt is None or name not in (home, away):
                        continue
                    hp = float(pt) if name == home else -float(pt)
                    price = float(o.get("price") or 0)
                    bucket = spreads.setdefault(hp, {"home": 0.0, "away": 0.0})
                    if name == home:
                        spread_points.append(hp)
                        if price > bucket["home"]:
                            bucket["home"] = price
                    else:
                        if price > bucket["away"]:
                            bucket["away"] = price

    # consensus (most common) total line; ties → closest to the median offer
    line = None
    if total_points:
        cnt = Counter(total_points)
        top = max(cnt.values())
        cands = sorted([p for p, c in cnt.items() if c == top])
        line = cands[len(cands) // 2]

    spread_line = None
    if spread_points:
        cnt = Counter(spread_points)
        top = max(cnt.values())
        cands = sorted([p for p, c in cnt.items() if c == top])
        spread_line = cands[len(cands) // 2]

    return {
        "home": home,
        "away": away,
        "ml_home": ml_home,
        "ml_away": ml_away,
        "total_line": line,
        "total_over": totals.get(line, {}).get("over", 0.0) if line is not None else 0.0,
        "total_under": totals.get(line, {}).get("under", 0.0) if line is not None else 0.0,
        "spread_line": spread_line,
        "spread_home": spreads.get(spread_line, {}).get("home", 0.0) if spread_line is not None else 0.0,
        "spread_away": spreads.get(spread_line, {}).get("away", 0.0) if spread_line is not None else 0.0,
        "commence_time": event.get("commence_time"),
        "id": event.get("id"),
    }


def kickoff_et_label(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)
        return t.strftime("%I:%M %p ET").lstrip("0")
    except Exception:
        return ""
