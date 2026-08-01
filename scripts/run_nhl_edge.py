#!/usr/bin/env python3
"""CLI for NHL EDGE.

  python scripts/run_nhl_edge.py --dry-run
  python scripts/run_nhl_edge.py --live
  python scripts/run_nhl_edge.py --date 2026-07-26 --dry-run   # historical test
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nhl_edge.pipeline import run  # noqa: E402
from nhl_edge.utils import safe_print  # noqa: E402

ET = ZoneInfo("America/New_York")

# Stages that mean "pipeline completed as designed" (exit 0)
_OK_STAGES = frozenset(
    {"picks", "no-picks", "no-candidates", "all-dupes", "already-shipped-today"}
)


def main() -> int:
    ap = argparse.ArgumentParser(description="NHL EDGE runner")
    ap.add_argument("--dry-run", action="store_true", help="model only, no delivery")
    ap.add_argument("--live", action="store_true", help="full delivery")
    ap.add_argument(
        "--date",
        default=None,
        help="ET game day YYYY-MM-DD (historical). Use 'yesterday' for last night's slate.",
    )
    args = ap.parse_args()
    dry = args.dry_run or not args.live
    if args.live:
        dry = False
    date_et = args.date
    if date_et and date_et.lower() in ("yesterday", "last-night", "last_night"):
        date_et = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
    report = run(dry_run=dry, deliver=not dry, date_et=date_et)

    comp = report.get("composer") or {}
    n_picks = len(report.get("picks") or [])
    # Greppable one-liner for cron logs
    t = report.get("timing") or {}
    ops = (
        f"OPS slate={report.get('slate_n', '?')} modeled={report.get('modeled_n', '?')} "
        f"shortlist={report.get('shortlist_n', '?')} picks={n_picks} "
        f"dupes={len(report.get('dupes_dropped') or [])} "
        f"stage={report.get('stage')} "
        f"composer={comp.get('composer_effort_used') or comp.get('composer_effort') or '-'} "
        f"sec={comp.get('composer_seconds')} tok={comp.get('composer_tokens')} "
        f"fallback={comp.get('composer_fallback')} ok={report.get('ok')} "
        f"t_odds={t.get('odds_sec')} t_off={t.get('offense_sec')} "
        f"t_sonar={t.get('sonar_sec')} t_brain={t.get('brain_wall_sec')}"
    )
    safe_print("\n=== SUMMARY ===")
    safe_print(ops)
    safe_print("date_et:", report.get("date_et"))
    safe_print("stage:", report.get("stage"))
    safe_print("ok:", report.get("ok"))
    safe_print("picks:", n_picks)
    for i, p in enumerate(report.get("picks") or [], 1):
        safe_print(
            f"  {i}. {p.get('pick_name')} | {p.get('match')} | {p.get('odds_dec')} | {p.get('kickoff_et')}"
        )
        if p.get("rationale"):
            safe_print(f"     {p.get('rationale')[:200]}")
    if report.get("dupes_dropped"):
        safe_print("dupes_dropped:", len(report["dupes_dropped"]))
        for d in report["dupes_dropped"]:
            safe_print(f"  - {d.get('pick_name')} | {d.get('match')} | {d.get('selection_struct')}")

    if comp:
        safe_print("\n=== COMPOSER FACTS ===")
        safe_print(f"  model:      {comp.get('composer_model')}")
        safe_print(f"  effort:     {comp.get('composer_effort_used') or comp.get('composer_effort')}")
        safe_print(f"  seconds:    {comp.get('composer_seconds')}")
        safe_print(f"  tokens:     {comp.get('composer_tokens')}")
        safe_print(f"  input:      {comp.get('composer_input_tokens')}")
        safe_print(f"  output:     {comp.get('composer_output_tokens')}")
        safe_print(f"  reasoning:  {comp.get('composer_reasoning_tokens')}")
        safe_print(
            f"  attempts:   {comp.get('composer_attempts')} "
            f"(primary/opus={comp.get('composer_attempts_primary')} "
            f"fallback/gpt={comp.get('composer_attempts_fallback')})"
        )
        if comp.get("composer_phase"):
            safe_print(f"  phase:      {comp.get('composer_phase')}")
        safe_print(f"  fallback:   {comp.get('composer_fallback')}")
        if comp.get("composer_error"):
            safe_print(f"  error:      {comp.get('composer_error')}")

    safe_print(
        json.dumps(
            {
                "date_et": report.get("date_et"),
                "stage": report.get("stage"),
                "ops": ops,
                "n": n_picks,
                "slate_n": report.get("slate_n"),
                "shortlist_n": report.get("shortlist_n"),
                "composer": comp,
            }
        )
    )
    stage = str(report.get("stage") or "")
    if stage in _OK_STAGES or stage.startswith("all-dupes"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
