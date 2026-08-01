from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Prefer: NHL_EDGE_ENV -> shared CODE .env (Windows/Linux) -> local repo .env
_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_ENV = _ROOT / ".env"
_ENV_CANDIDATES = [
    Path(p)
    for p in (
        os.environ.get("NHL_EDGE_ENV") or "",
        r"C:\Users\istva\.claude\CODE\.env",
        str(Path.home() / ".claude" / "CODE" / ".env"),
        "/root/.claude/CODE/.env",
        str(_LOCAL_ENV),
    )
    if p
]
for _p in _ENV_CANDIDATES:
    if _p.is_file():
        load_dotenv(_p, override=False)
# Local .env always wins when present (operator overrides)
if _LOCAL_ENV.is_file():
    load_dotenv(_LOCAL_ENV, override=True)

# --- product gates (EDGE family) ---
MIN_ODDS_ML = 1.75
MIN_ODDS_SPREAD = 1.85
MIN_ODDS_TOTAL = 1.85
MIN_EDGE = 0.02  # 2%
MAX_PICKS = 3
MAX_EDGE_SUSPECT = 0.30  # drop absurd edges

# --- NHL model constants (literature values, deliberately NOT fitted) ---
# Goals are near-Poisson at ~3/team: independent Poisson score matrix (family lineage:
# NIGHT EDGE / MLB EDGE), multiplicative strength: lambda = league_gpg * off_mult * opp_def_mult.
HCA_HOME_MULT = 1.05   # home team scores ~+5% ...
HCA_AWAY_MULT = 0.97   # ... away team ~-3% (net home edge ~ +0.25 goals)
B2B_OWN_MULT = 0.93    # 2nd night of a back-to-back: own scoring dips ...
B2B_OPP_MULT = 1.05    # ... and tired legs/backup goalie leak (net ~ -0.35 goals margin)
MAX_GOALS = 12         # Poisson matrix truncation (renormed)
# Coin-flip: |expected goal margin| below this -> no ML/puck-line side (totals still allowed)
COINFLIP_MARGIN = 0.25
# Season-boundary blending: weight_current = games_played / (games_played + K)
RATINGS_BLEND_K = 12
# Roster/goalie churn: previous-season rate deviations carry ~65%.
PREV_SEASON_CARRYOVER = 0.65
LEAGUE_GPG = 3.05      # fallback league goals per team-game

SPORT_KEY = "icehockey_nhl"
REGIONS = "us"
MARKETS = "h2h,spreads,totals"
ODDS_FORMAT = "decimal"

# AI - pick composer ladder (EDGE family)
COMPOSER_PRIMARY_MODEL = "claude-opus-5"
COMPOSER_PRIMARY_EFFORT = "max"
COMPOSER_ATTEMPTS_PRIMARY = 3
COMPOSER_FALLBACK_MODEL = "gpt-5.6-sol"
COMPOSER_FALLBACK_EFFORT = "high"
COMPOSER_ATTEMPTS_FALLBACK = 3
SONAR_MODEL = "sonar-pro"  # fallback news engine - NEVER plain "sonar" (operator 2026-08-01)
# News primary: grok-4.5 live web+X search (family-proven), sonar-pro fallback
GROK_NEWS_MODEL = "grok-4.5"
GROK_NEWS_TIMEOUT = 300


def env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()
