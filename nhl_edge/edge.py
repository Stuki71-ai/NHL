from __future__ import annotations

from typing import Any

from nhl_edge.config import (
    COINFLIP_MARGIN,
    MAX_EDGE_SUSPECT,
    MAX_PICKS,
    MIN_EDGE,
    MIN_ODDS_ML,
    MIN_ODDS_SPREAD,
    MIN_ODDS_TOTAL,
)
from nhl_edge.utils import american_from_decimal


def _is_coinflip(exp_margin: float | None) -> bool:
    """True when the model sees no real side (near-zero expected margin).

    Blocks ML and spread sides; totals stay (the points environment can be
    mispriced even when the sides are even)."""
    try:
        m = float(exp_margin)
    except (TypeError, ValueError):
        return False
    return abs(m) < COINFLIP_MARGIN


def _min_odds(market: str) -> float:
    if market == "Moneyline":
        return MIN_ODDS_ML
    if market == "Asian Handicap":
        return MIN_ODDS_SPREAD
    return MIN_ODDS_TOTAL


def _cand(
    *,
    home: str,
    away: str,
    market: str,
    selection_struct: str,
    pick_name: str,
    model_p: float,
    price: float,
    kickoff_et: str,
    commence_time: str,
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    if model_p is None or model_p <= 0 or price is None or price <= 1:
        return None
    if price < _min_odds(market):
        return None
    edge = model_p * price - 1.0
    if edge < MIN_EDGE:
        return None
    if edge > MAX_EDGE_SUSPECT:
        return None  # SUSPECT — family pattern
    return {
        "home": home,
        "away": away,
        "match": f"{away} @ {home}",
        "market": market,
        "selection": selection_struct,
        "selection_struct": selection_struct,
        "pick_name": pick_name,
        "model_p": round(model_p, 4),
        "odds_dec": round(price, 2),
        "odds_us": american_from_decimal(price),
        "edge": round(edge, 4),
        "kickoff_et": kickoff_et,
        "commence_time": commence_time,
        "sport": "icehockey_nhl",
        "league": "NHL",
        "meta": meta,
    }


def _signed(x: float) -> str:
    return f"+{x:g}" if x > 0 else f"{x:g}"


def build_candidates(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """games items: {home, away, prices, model, talent, kickoff_et}"""
    cands: list[dict[str, Any]] = []
    for g in games:
        home, away = g["home"], g["away"]
        pr, mo = g["prices"], g["model"]
        kick = g.get("kickoff_et") or ""
        if not kick or str(kick).lower().startswith("tbd"):
            continue
        ct = pr.get("commence_time") or ""
        notes = (g.get("talent") or {}).get("notes") or {}
        meta = {
            "exp_margin": mo.get("exp_margin"),
            "exp_total": mo.get("exp_total"),
            "lambda_home": mo.get("lambda_home"),
            "lambda_away": mo.get("lambda_away"),
            "notes": notes,
        }

        sides_ok = not _is_coinflip(mo.get("exp_margin"))
        if sides_ok:
            c = _cand(
                home=home, away=away, market="Moneyline",
                selection_struct="HOME", pick_name=home,
                model_p=mo["p_home"], price=pr.get("ml_home") or 0,
                kickoff_et=kick, commence_time=ct, meta=meta,
            )
            if c:
                cands.append(c)
            c = _cand(
                home=home, away=away, market="Moneyline",
                selection_struct="AWAY", pick_name=away,
                model_p=mo["p_away"], price=pr.get("ml_away") or 0,
                kickoff_et=kick, commence_time=ct, meta=meta,
            )
            if c:
                cands.append(c)

            sl = pr.get("spread_line")
            if sl is not None and mo.get("p_home_cover") is not None:
                c = _cand(
                    home=home, away=away, market="Asian Handicap",
                    selection_struct=f"HOME {_signed(float(sl))}",
                    pick_name=f"{home} {_signed(float(sl))}",
                    model_p=mo["p_home_cover"], price=pr.get("spread_home") or 0,
                    kickoff_et=kick, commence_time=ct,
                    meta={**meta, "spread_line": sl},
                )
                if c:
                    cands.append(c)
                c = _cand(
                    home=home, away=away, market="Asian Handicap",
                    selection_struct=f"AWAY {_signed(-float(sl))}",
                    pick_name=f"{away} {_signed(-float(sl))}",
                    model_p=mo["p_away_cover"], price=pr.get("spread_away") or 0,
                    kickoff_et=kick, commence_time=ct,
                    meta={**meta, "spread_line": sl},
                )
                if c:
                    cands.append(c)

        line = pr.get("total_line")
        if line is not None and mo.get("p_over") is not None:
            c = _cand(
                home=home, away=away, market="Total",
                selection_struct=f"OVER {line}", pick_name=f"Over {line}",
                model_p=mo["p_over"], price=pr.get("total_over") or 0,
                kickoff_et=kick, commence_time=ct,
                meta={**meta, "total_line": line},
            )
            if c:
                cands.append(c)
            c = _cand(
                home=home, away=away, market="Total",
                selection_struct=f"UNDER {line}", pick_name=f"Under {line}",
                model_p=mo["p_under"], price=pr.get("total_under") or 0,
                kickoff_et=kick, commence_time=ct,
                meta={**meta, "total_line": line},
            )
            if c:
                cands.append(c)

    # max one pick per match — prefer higher win probability, then edge
    best: dict[str, dict[str, Any]] = {}
    for c in cands:
        key = f"{c['home']}||{c['away']}"
        prev = best.get(key)
        if prev is None or (c["model_p"], c["edge"]) > (prev["model_p"], prev["edge"]):
            best[key] = c

    ranked = sorted(best.values(), key=lambda x: (-x["model_p"], -x["edge"]))
    return ranked[: max(MAX_PICKS * 4, 12)]


def shortlist_brief(cands: list[dict[str, Any]]) -> str:
    if not cands:
        return "NO CANDIDATES CLEARED — no pick met min edge / odds floors."
    lines = [
        f"Top {len(cands)} pre-ranked candidates "
        f"(by model win probability, then edge — paid-sub hit rate > long-term EV):\n"
    ]
    for i, c in enumerate(cands, 1):
        notes = c.get("meta", {}).get("notes") or {}
        b2b = []
        if notes.get("home_b2b"):
            b2b.append("home B2B")
        if notes.get("away_b2b"):
            b2b.append("away B2B")
        lines.append(
            f"#{i} {c['match']} | {c['kickoff_et']}\n"
            f"  Pick: {c['pick_name']} ({c['market']} {c['selection_struct']})\n"
            f"  Model P: {c['model_p']*100:.1f}% | Price: {c['odds_dec']:.2f} | Edge: {c['edge']*100:.1f}%\n"
            f"  Model goals: {c['meta'].get('lambda_home')}-{c['meta'].get('lambda_away')} "
            f"(total {c['meta'].get('exp_total')}){' | ' + ', '.join(b2b) if b2b else ''}\n"
            f"  Rate mults home O/D {notes.get('home_off_mult')}/{notes.get('home_def_mult')} "
            f"away O/D {notes.get('away_off_mult')}/{notes.get('away_def_mult')} "
            f"(gp {notes.get('home_gp')}/{notes.get('away_gp')})\n"
        )
    return "\n".join(lines)
