#!/usr/bin/env python3
"""Grade picks from an NHL EDGE out/run_YYYY-MM-DD.json against NHL final scores (ESPN).

Convenience tool only — production grading is the shared GQ Sports Grader."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def norm(s: str) -> str:
    return "".join(c for c in (s or "").lower() if c.isalnum())


def find_game(sched: list, home: str, away: str):
    nh, na = norm(home), norm(away)
    for g in sched:
        gh = norm(g.get("home_name") or g.get("home_team") or "")
        ga = norm(g.get("away_name") or g.get("away_team") or "")
        if (gh == nh and ga == na) or (gh == na and ga == nh):
            return g
    return None


def grade_pick(p: dict, g: dict | None) -> tuple[str, int | None, int | None, str]:
    if not g:
        return "OPEN", None, None, "no game match"
    status = g.get("status") or ""
    hs, as_ = g.get("home_score"), g.get("away_score")
    if hs is None or as_ is None:
        return "OPEN", hs, as_, status
    hs, as_ = int(hs), int(as_)
    # orient scores to pick's home/away if teams swapped in API
    g_home = g.get("home_name") or g.get("home_team") or ""
    if norm(g_home) != norm(p.get("home") or ""):
        hs, as_ = as_, hs  # swap to pick orientation

    mkt = (p.get("market") or "").lower()
    sel = (p.get("selection_struct") or p.get("selection") or "").strip().upper()

    if "moneyline" in mkt or sel in ("HOME", "AWAY"):
        if hs == as_:
            return "P", hs, as_, status
        if sel == "HOME":
            return ("W" if hs > as_ else "L"), hs, as_, status
        if sel == "AWAY":
            return ("W" if as_ > hs else "L"), hs, as_, status

    if "handicap" in mkt or re.match(r"^(HOME|AWAY)\s+[+-]", sel):
        m = re.search(r"(HOME|AWAY)\s+([+-]?[\d.]+)", sel)
        if not m:
            return "OPEN", hs, as_, "bad spread selection"
        side, hcp = m.group(1), float(m.group(2))
        margin = (hs - as_) if side == "HOME" else (as_ - hs)
        adj = margin + hcp
        if abs(adj) < 1e-9:
            return "P", hs, as_, status
        return ("W" if adj > 0 else "L"), hs, as_, status

    if "total" in mkt or sel.startswith("OVER") or sel.startswith("UNDER"):
        m = re.search(r"(OVER|UNDER)\s+([\d.]+)", sel, re.I)
        if not m:
            return "OPEN", hs, as_, "bad total selection"
        side, line = m.group(1).upper(), float(m.group(2))
        total = hs + as_
        if abs(total - line) < 1e-9:
            return "P", hs, as_, status
        if side == "OVER":
            return ("W" if total > line else "L"), hs, as_, status
        return ("W" if total < line else "L"), hs, as_, status

    return "OPEN", hs, as_, "unknown market"


def espn_scores(date_et: str) -> list[dict]:
    """Final scores from the public ESPN scoreboard for one ET date."""
    import requests

    ymd = date_et.replace("-", "")
    r = requests.get(
        f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={ymd}",
        timeout=25,
    )
    r.raise_for_status()
    games = []
    for ev in r.json().get("events") or []:
        for comp in ev.get("competitions") or []:
            g = {"status": ((ev.get("status") or {}).get("type") or {}).get("name") or ""}
            done = ((ev.get("status") or {}).get("type") or {}).get("completed")
            for c in comp.get("competitors") or []:
                name = ((c.get("team") or {}).get("displayName")) or ""
                score = c.get("score")
                side = "home" if c.get("homeAway") == "home" else "away"
                g[f"{side}_name"] = name
                g[f"{side}_score"] = int(score) if (done and score is not None) else None
            games.append(g)
    return games


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-26", help="ET slate date YYYY-MM-DD")
    args = ap.parse_args()
    path = ROOT / "out" / f"run_{args.date}.json"
    if not path.is_file():
        print(f"missing {path}")
        return 1

    report = json.loads(path.read_text(encoding="utf-8"))
    picks = report.get("picks") or []
    print(f"=== NHL EDGE GRADES — {args.date} ===")
    print(f"picks file: {path.name} | n={len(picks)}\n")

    sched = espn_scores(args.date)
    print(f"NHL games that day: {len(sched)}\n")

    w = l = psh = open_n = 0
    unit = 0.0
    rows = []
    for i, pick in enumerate(picks, 1):
        g = find_game(sched, pick.get("home") or "", pick.get("away") or "")
        code, hs, as_, st = grade_pick(pick, g)
        dec = float(pick.get("odds_dec") or 0)
        if code == "W":
            w += 1
            u = dec - 1
            unit += u
        elif code == "L":
            l += 1
            u = -1.0
            unit += u
        elif code == "P":
            psh += 1
            u = 0.0
        else:
            open_n += 1
            u = 0.0
        score = f"{as_}-{hs}" if hs is not None else "n/a"
        print(
            f"{i}. [{code}] {pick.get('pick_name')} | {pick.get('match')} | "
            f"{pick.get('market')} {pick.get('selection_struct')} @ {dec:.2f}"
        )
        print(f"   final {score} ({st}) | {u:+.2f}u")
        rows.append({"code": code, "u": u, "pick": pick})

    rec = f"{w}-{l}" + (f"-{psh}" if psh else "")
    print(f"\n=== RECORD {rec} | P/L {unit:+.2f}u (1u flat) | open={open_n} ===")

    out = {
        "date": args.date,
        "record": rec,
        "pnl_u": round(unit, 2),
        "W": w,
        "L": l,
        "P": psh,
        "OPEN": open_n,
        "details": [
            {
                "result": r["code"],
                "units": r["u"],
                "pick_name": r["pick"].get("pick_name"),
                "match": r["pick"].get("match"),
                "selection": r["pick"].get("selection_struct"),
                "odds_dec": r["pick"].get("odds_dec"),
            }
            for r in rows
        ],
    }
    out_path = ROOT / "out" / f"grades_{args.date}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
