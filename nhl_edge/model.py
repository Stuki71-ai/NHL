from __future__ import annotations

from typing import Any

from nhl_edge.config import (
    B2B_OPP_MULT,
    B2B_OWN_MULT,
    HCA_AWAY_MULT,
    HCA_HOME_MULT,
    LEAGUE_GPG,
    MAX_GOALS,
)
from nhl_edge.utils import poisson_pmf


def score_matrix(lam_h: float, lam_a: float, max_goals: int = MAX_GOALS) -> list[list[float]]:
    """P(home=i, away=j) under independent Poisson (truncated, renormed)."""
    mat = []
    for i in range(max_goals + 1):
        row = []
        ph = poisson_pmf(i, lam_h)
        for j in range(max_goals + 1):
            row.append(ph * poisson_pmf(j, lam_a))
        mat.append(row)
    s = sum(sum(r) for r in mat)
    if s > 0:
        mat = [[p / s for p in row] for row in mat]
    return mat


def _win_total_cover(
    lam_h: float, lam_a: float, total_line: float | None, spread_line: float | None
) -> dict[str, Any]:
    mat = score_matrix(lam_h, lam_a)
    n = len(mat)
    ph = pa = tie = 0.0
    over = under = push_t = 0.0
    hc = ac = push_s = 0.0
    for i in range(n):
        for j in range(n):
            p = mat[i][j]
            if i > j:
                ph += p
            elif j > i:
                pa += p
            else:
                tie += p
            if total_line is not None:
                t = i + j
                if t > total_line:
                    over += p
                elif t < total_line:
                    under += p
                else:
                    push_t += p
            if spread_line is not None:
                adj = (i - j) + spread_line  # home margin + home handicap
                if adj > 0:
                    hc += p
                elif adj < 0:
                    ac += p
                else:
                    push_s += p

    # Moneyline includes OT/SO: regulation-tie mass splits ~50/50 (family simplification).
    ph += tie * 0.5
    pa += tie * 0.5
    tot = ph + pa
    if tot > 0:
        ph, pa = ph / tot, pa / tot

    out: dict[str, Any] = {"p_home": ph, "p_away": pa, "reg_tie_mass": tie}

    if total_line is not None:
        # OT adds ~one goal to ~23% of games sitting exactly on the regulation total;
        # the push-split renorm is the family-standard conservative approximation.
        over += push_t * 0.5
        under += push_t * 0.5
        s = over + under
        if s > 0:
            over, under = over / s, under / s
        out["p_over"], out["p_under"] = over, under
    else:
        out["p_over"] = out["p_under"] = None

    if spread_line is not None:
        # Puck line ±1.5 settles on regulation+OT final; ties impossible on half lines.
        hc += push_s * 0.5
        ac += push_s * 0.5
        s = hc + ac
        if s > 0:
            hc, ac = hc / s, ac / s
        out["p_home_cover"], out["p_away_cover"] = hc, ac
    else:
        out["p_home_cover"] = out["p_away_cover"] = None

    return out


def model_game(talent: dict[str, Any], total_line: float | None, spread_line: float | None) -> dict[str, Any]:
    """talent: {home_r, away_r} with off/def multipliers; league_gpg; b2b flags."""
    h, a = talent["home_r"], talent["away_r"]
    league = float(talent.get("league_gpg") or LEAGUE_GPG)

    lam_h = league * float(h["off_mult"]) * float(a["def_mult"]) * HCA_HOME_MULT
    lam_a = league * float(a["off_mult"]) * float(h["def_mult"]) * HCA_AWAY_MULT

    if talent.get("home_b2b"):
        lam_h *= B2B_OWN_MULT
        lam_a *= B2B_OPP_MULT
    if talent.get("away_b2b"):
        lam_a *= B2B_OWN_MULT
        lam_h *= B2B_OPP_MULT

    lam_h = max(1.2, min(6.0, lam_h))
    lam_a = max(1.2, min(6.0, lam_a))

    probs = _win_total_cover(lam_h, lam_a, total_line, spread_line)
    return {
        "lambda_home": round(lam_h, 3),
        "lambda_away": round(lam_a, 3),
        "exp_margin": round(lam_h - lam_a, 3),
        "exp_total": round(lam_h + lam_a, 3),
        "total_line": total_line,
        "spread_line": spread_line,
        **probs,
    }
