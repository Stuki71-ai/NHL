from __future__ import annotations

"""Team ratings: goals-for / goals-against rate multipliers vs league GPG.

Source: ESPN standings — for NHL, `pointsFor`/`pointsAgainst` are season GOAL
totals (displayNames "Goals For"/"Goals Against") and `gamesPlayed` is present
directly. The same endpoint family is proven reachable from both the PC and the
datacenter VPS (NBA/NFL EDGE).

Season boundary: previous-season multiplier deviations are shrunk by
PREV_SEASON_CARRYOVER (goalie/roster churn), then the current season blends in
with weight gp/(gp+K), K=12. At 0 games (October) ratings are 65% of last
season's deviations — the cold-start answer.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from nhl_edge.config import (
    LEAGUE_GPG,
    PREV_SEASON_CARRYOVER,
    RATINGS_BLEND_K,
)
from nhl_edge.utils import norm_team, safe_float

ET = ZoneInfo("America/New_York")


def current_season_end_year(now: datetime | None = None) -> int:
    """ESPN labels NHL seasons by END year (2025-26 => 2026); new season starts in October."""
    now = now or datetime.now(ET)
    return now.year + 1 if now.month >= 9 else now.year


def _from_espn_standings(season_end_year: int) -> dict[str, dict[str, float]]:
    r = requests.get(
        f"https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings?season={season_end_year}",
        timeout=30,
    )
    r.raise_for_status()
    out: dict[str, dict[str, float]] = {}
    for grp in r.json().get("children") or []:
        for e in (grp.get("standings") or {}).get("entries") or []:
            name = ((e.get("team") or {}).get("displayName")) or ""
            stats = {s.get("name"): s.get("value") for s in e.get("stats") or []}
            gf = safe_float(stats.get("pointsFor"), 0.0)      # goals for (total)
            ga = safe_float(stats.get("pointsAgainst"), 0.0)  # goals against (total)
            gp = safe_float(stats.get("gamesPlayed"), 0.0)
            if not name or gp <= 0 or gf <= 0:
                continue
            out[norm_team(name)] = {"team": name, "gf": gf / gp, "ga": ga / gp, "gp": gp}
    return out


def _multipliers(table: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """GF/g, GA/g → multiplicative strength vs that season's league GPG."""
    if not table:
        return {}
    league = sum(v["gf"] for v in table.values()) / len(table)
    out: dict[str, dict[str, float]] = {}
    for key, v in table.items():
        out[key] = {
            "team": v["team"],
            "off_mult": round(v["gf"] / league, 4),   # >1 = scores above league
            "def_mult": round(v["ga"] / league, 4),   # >1 = concedes above league (bad D/goalie)
            "gp": v["gp"],
            "league_gpg": round(league, 3),
        }
    return out


def load_season_ratings(season_end_year: int) -> dict[str, dict[str, float]]:
    try:
        table = _from_espn_standings(season_end_year)
        if len(table) >= 30:
            print(f"[ratings] ESPN standings {season_end_year}: {len(table)} teams")
            return _multipliers(table)
        print(f"[ratings] ESPN standings thin ({len(table)} teams) for {season_end_year}")
    except Exception as e:
        print(f"[ratings] ESPN standings {season_end_year} failed ({str(e)[:120]})")
    return {}


def _shrink(mult: float) -> float:
    """Shrink a multiplier's deviation from 1.0 by the carryover factor."""
    return round(1.0 + (mult - 1.0) * PREV_SEASON_CARRYOVER, 4)


def load_blended_ratings(now: datetime | None = None) -> dict[str, dict[str, float]]:
    y = current_season_end_year(now)
    cur = load_season_ratings(y)
    prev = load_season_ratings(y - 1)
    for v in prev.values():
        v["off_mult"] = _shrink(v["off_mult"])
        v["def_mult"] = _shrink(v["def_mult"])
    if not cur and not prev:
        print("[ratings] EMPTY — league averages only")
        return {}
    if not cur:
        print(f"[ratings] season {y} not started — shrunk previous season only")
        for v in prev.values():
            v["blend_w_current"] = 0.0
        return prev
    out: dict[str, dict[str, float]] = {}
    for key, c in cur.items():
        p = prev.get(key)
        gp = float(c.get("gp") or 0)
        w = gp / (gp + RATINGS_BLEND_K)
        if p is None:
            w = 1.0
            p = c
        out[key] = {
            "team": c["team"],
            "off_mult": round(w * c["off_mult"] + (1 - w) * p["off_mult"], 4),
            "def_mult": round(w * c["def_mult"] + (1 - w) * p["def_mult"], 4),
            "gp": gp,
            "league_gpg": c.get("league_gpg") or LEAGUE_GPG,
            "blend_w_current": round(w, 3),
        }
    for key, p in prev.items():
        if key not in out:
            p2 = dict(p)
            p2["blend_w_current"] = 0.0
            out[key] = p2
    return out


def league_averages(table: dict[str, dict[str, float]]) -> dict[str, float]:
    if not table:
        return {"gpg": LEAGUE_GPG}
    vals = [v.get("league_gpg") for v in table.values() if v.get("league_gpg")]
    return {"gpg": round(sum(vals) / len(vals), 3) if vals else LEAGUE_GPG}


def match_team(team_name: str, table: dict[str, dict[str, float]]) -> dict[str, float]:
    nt = norm_team(team_name)
    if nt in table:
        return table[nt]
    for key, val in table.items():
        if key in nt or nt in key:
            return val
    nick = team_name.split()[-1] if team_name else ""
    nk = norm_team(nick)
    for key, val in table.items():
        if nk and key.endswith(nk):
            return val
    return {
        "team": team_name,
        "off_mult": 1.0,
        "def_mult": 1.0,
        "gp": 0.0,
        "league_gpg": LEAGUE_GPG,
        "blend_w_current": 0.0,
    }


def build_game_talent(
    home: str,
    away: str,
    ratings: dict[str, dict[str, float]],
    league: dict[str, float],
    *,
    home_b2b: bool = False,
    away_b2b: bool = False,
) -> dict[str, Any]:
    h = match_team(home, ratings)
    a = match_team(away, ratings)
    return {
        "home": home,
        "away": away,
        "home_r": h,
        "away_r": a,
        "league_gpg": league.get("gpg", LEAGUE_GPG),
        "home_b2b": home_b2b,
        "away_b2b": away_b2b,
        "notes": {
            "home_off_mult": h.get("off_mult"), "home_def_mult": h.get("def_mult"),
            "away_off_mult": a.get("off_mult"), "away_def_mult": a.get("def_mult"),
            "home_gp": h.get("gp"), "away_gp": a.get("gp"),
            "home_blend_w": h.get("blend_w_current"), "away_blend_w": a.get("blend_w_current"),
            "home_b2b": home_b2b, "away_b2b": away_b2b,
        },
    }
