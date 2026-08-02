from __future__ import annotations

# ERROR POLICY (EDGE family / US EDGE lead):
#   In case of any errors, the AI brain is solely responsible for autonomous resolution
#   (composer ladder → edge-rank; honest []; shortlist-only — never invent data).
#   ntfy ONLY when ALL three hold:
#     (1) production-critical
#     (2) exhausted repeated autonomous attempts
#     (3) resolution cannot wait for the next scheduled slot
#   Everything else: fix/retry/suppress, log, stay silent toward customers.

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from nhl_edge import brain, delivery, edge, model, news, ratings, slate
from nhl_edge.dedupe import (
    drop_already_sent,
    filter_to_claim_keys,
    load_sent_keys,
    load_shared_claim_keys,
    record_sent,
)
from nhl_edge.preflight import missing_live_secrets
from nhl_edge.utils import safe_print, team_eq

ET = ZoneInfo("America/New_York")
OUT = Path(__file__).resolve().parents[1] / "out"
DELIVERY_ATTEMPTS = 3


def _is_b2b(team: str, played_yesterday: set[str]) -> bool:
    return any(team_eq(team, t) for t in played_yesterday)


def _write_run(date_et: str, report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"run_{date_et}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )


def _deliver_channel(name: str, fn: Callable[[], bool]) -> bool:
    """Autonomous retries; returns True if any attempt succeeds. Caller ntfys only on False."""
    last = ""
    for i in range(DELIVERY_ATTEMPTS):
        try:
            if bool(fn()):
                if i > 0:
                    print(f"[delivery] {name} recovered on attempt {i + 1}")
                return True
            last = f"{name} returned False"
            print(f"[delivery] {name} attempt {i + 1}/{DELIVERY_ATTEMPTS}: {last}")
        except Exception as e:
            last = str(e)[:200]
            print(f"[delivery] {name} attempt {i + 1}/{DELIVERY_ATTEMPTS}: {last}")
        if i < DELIVERY_ATTEMPTS - 1:
            time.sleep(1.5 * (i + 1))
    print(f"[delivery] {name} exhausted {DELIVERY_ATTEMPTS} attempts: {last}")
    return False


def run(
    *,
    dry_run: bool = False,
    deliver: bool = True,
    date_et: str | None = None,
) -> dict[str, Any]:
    """
    date_et: optional YYYY-MM-DD (America/New_York game day) for historical/test replays.
    Forces dry-run delivery off unless deliver=True is explicit with live (still blocked for historical).
    """
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "ok": False,
        "picks": [],
        "stage": "",
        "date_et": date_et,
        "slate_n": 0,
        "shortlist_n": 0,
        "picks_n": 0,
    }

    # Historical test runs never deliver by default
    if date_et and deliver and not dry_run:
        print("[pipeline] historical date set — forcing dry-run (no customer delivery)")
        dry_run = True
        deliver = False

    try:
        if not date_et:
            date_et = datetime.now(ET).strftime("%Y-%m-%d")
        report["date_et"] = date_et

        # Live preflight: one critical, no partial spend/ship
        if not dry_run and deliver:
            miss = missing_live_secrets()
            if miss:
                msg = "missing secrets: " + ", ".join(miss)
                report["stage"] = f"preflight-fail: {msg}"
                report["preflight_missing"] = miss
                print(f"[preflight] {msg}")
                delivery.ntfy_critical("NHL EDGE CRITICAL: preflight secrets", msg)
                (OUT / "last_error.json").write_text(
                    json.dumps(report, indent=2, default=str), encoding="utf-8"
                )
                return report

        timing: dict[str, float] = {}
        print(f"[1] Fetch odds slate… date_et={date_et}")
        t0 = time.perf_counter()
        is_today = date_et == datetime.now(ET).strftime("%Y-%m-%d")
        slot_label = datetime.now(ET).strftime("%H:%M") + " ET"  # this run's fire time (title of no-new-picks ntfy)
        if is_today:
            events, remaining = slate.fetch_odds(date_et=None)
            events = slate.filter_slate(events)
        else:
            events, remaining = slate.fetch_odds(date_et=date_et)
            events = slate.filter_slate(events, date_et=date_et)
        timing["odds_sec"] = round(time.perf_counter() - t0, 3)
        report["odds_remaining"] = remaining
        report["slate_n"] = len(events)
        print(
            f"    events in window: {len(events)} (credits remaining={remaining}) "
            f"sec={timing['odds_sec']}"
        )

        print("[2] Back-to-back detection (ESPN scoreboard, yesterday)…")
        t0 = time.perf_counter()
        played_yesterday = slate.b2b_teams_for(date_et)
        timing["b2b_sec"] = round(time.perf_counter() - t0, 3)

        print("[3] Ratings (GF/GA rate multipliers, season-blended + shrunk carryover)…")
        t0 = time.perf_counter()
        table = ratings.load_blended_ratings()
        league = ratings.league_averages(table)
        report["league_avgs"] = league
        timing["ratings_sec"] = round(time.perf_counter() - t0, 3)
        print(
            f"    teams={len(table)} league GPG={league.get('gpg')} "
            f"sec={timing['ratings_sec']}"
        )

        games = []
        for ev in events:
            pr = slate.best_prices(ev)
            if not pr["home"] or not pr["away"]:
                continue
            tal = ratings.build_game_talent(
                pr["home"], pr["away"], table, league,
                home_b2b=_is_b2b(pr["home"], played_yesterday),
                away_b2b=_is_b2b(pr["away"], played_yesterday),
            )
            mo = model.model_game(tal, pr.get("total_line"), pr.get("spread_line"))
            kick = slate.kickoff_et_label(pr.get("commence_time"))
            games.append(
                {
                    "home": pr["home"],
                    "away": pr["away"],
                    "prices": pr,
                    "talent": tal,
                    "model": mo,
                    "kickoff_et": kick,
                }
            )

        print(f"[4] Modeled games: {len(games)}")
        report["modeled_n"] = len(games)
        cands = edge.build_candidates(games)
        brief = edge.shortlist_brief(cands)
        report["shortlist_n"] = len(cands)
        safe_print(f"[5] Shortlist: {len(cands)}")
        safe_print(brief[:1500])

        report["shortlist"] = cands
        report["model_brief"] = brief

        if not cands:
            report["stage"] = "no-candidates"
            if not dry_run:
                delivery.ntfy_no_picks()
            _write_run(date_et, report)
            return report

        safe_print("[6] Team news (sonar-pro per game + Serper fallback + grok-4.5 X-INTEL)…")
        t0 = time.perf_counter()
        news_brief = news.team_news(
            [
                {
                    "home": c["home"],
                    "away": c["away"],
                    "home_b2b": (c["meta"].get("notes") or {}).get("home_b2b"),
                    "away_b2b": (c["meta"].get("notes") or {}).get("away_b2b"),
                }
                for c in cands[:10]
            ]
        )
        timing["sonar_sec"] = round(time.perf_counter() - t0, 3)
        report["news"] = news_brief[:12000]
        safe_print(f"    sonar_sec={timing['sonar_sec']}")
        safe_print(news_brief[:800])

        safe_print("[7] Brain compose…")
        t0 = time.perf_counter()
        picks, composer_stats = brain.compose_picks(
            cands, news_brief, brief, date_et=date_et
        )
        timing["brain_wall_sec"] = round(time.perf_counter() - t0, 3)
        report["composer"] = composer_stats
        report["timing"] = timing
        safe_print(f"    raw picks={len(picks)}")
        safe_print(
            f"    composer facts: model={composer_stats.get('composer_model')} "
            f"effort={composer_stats.get('composer_effort')} "
            f"seconds={composer_stats.get('composer_seconds')} "
            f"tokens={composer_stats.get('composer_tokens')} "
            f"in={composer_stats.get('composer_input_tokens')} "
            f"out={composer_stats.get('composer_output_tokens')} "
            f"reasoning={composer_stats.get('composer_reasoning_tokens')} "
            f"fallback={composer_stats.get('composer_fallback')}"
        )
        safe_print(f"    timing={timing}")

        # Dedupe: (1) local multi-cron last_sent  (2) shared EDGE-family Picks sheet claim keys
        already = load_sent_keys(OUT, date_et)
        # ONE customer proposal round per ET day (operator 2026-08-01):
        # Weekend multi-slots (11:45 / 14:00 / 16:45 ET) may still fire, but if we
        # already shipped any picks today (email/Whop/grader), later slots stay
        # silent — no second proposal email/Whop and no new sheet claims.
        if already and not dry_run and deliver:
            report["stage"] = "already-shipped-today"
            report["ok"] = True
            report["picks"] = []
            report["picks_n"] = 0
            report["dupes_dropped"] = [
                {
                    "pick_name": p.get("pick_name"),
                    "match": p.get("match"),
                    "selection_struct": p.get("selection_struct"),
                    "reason": "already-shipped-today",
                }
                for p in picks
            ]
            print(
                f"[dedupe] already shipped {len(already)} pick(s) for {date_et} — "
                "silence (one customer proposal per ET day; no 2nd round)"
            )
            delivery.ntfy_no_new_picks(slot_label)
            _write_run(date_et, report)
            return report

        shared: set[str] = set()
        if not dry_run and deliver:
            try:
                shared = load_shared_claim_keys()
                print(f"[dedupe] shared Picks sheet keys loaded: {len(shared)}")
            except Exception as e:
                # Fail-closed for live: without sheet we risk double-shipping vs US EDGE
                print(f"[dedupe] shared sheet load failed: {e}")
                report["stage"] = "pipeline-error: shared-dedupe-sheet"
                report["ok"] = False
                if not dry_run:
                    delivery.ntfy_critical(
                        "NHL EDGE CRITICAL: shared dedupe sheet",
                        f"cannot load shared Picks keys for family dedupe: {e}",
                    )
                _write_run(date_et, report)
                return report
        fresh, dups = drop_already_sent(picks, already, shared_claim_keys=shared)
        report["dupes_dropped"] = [
            {
                "pick_name": d.get("pick_name"),
                "match": d.get("match"),
                "selection_struct": d.get("selection_struct"),
            }
            for d in dups
        ]
        if dups:
            print(
                f"[dedupe] dropped {len(dups)} already-sent pick(s) "
                f"(local and/or shared sheet); fresh={len(fresh)}"
            )
        picks = fresh
        report["picks"] = picks
        report["picks_n"] = len(picks)

        if not picks:
            # Honest empty vs all dups: silence either way; no-picks ntfy only if not pure dups
            if dups:
                report["stage"] = "all-dupes"
                report["ok"] = True  # healthy multi-cron / cross-product re-fire
                print("[dedupe] all picks already sent (local or family sheet) — silence")
                if not dry_run:
                    delivery.ntfy_no_new_picks(slot_label)
            else:
                report["stage"] = "no-picks"
                if not dry_run:
                    delivery.ntfy_no_picks()
            _write_run(date_et, report)
            return report

        report["stage"] = "picks"
        report["ok"] = True

        if dry_run or not deliver:
            print("[8] dry-run — skip delivery")
        else:
            # Claim-first (shared sheet): only ship email/Whop for keys the grader accepted.
            # Prevents NHL from Whop'ing a pick another EDGE product already owns (and vice versa).
            print("[8] Deliver — grader claim first (family dedupe)…")
            claim_ok = False
            accepted: list[str] = []

            def _claim() -> bool:
                nonlocal claim_ok, accepted
                ok_c, keys = delivery.post_grader(picks)
                claim_ok = ok_c
                accepted = keys
                return ok_c

            if not _deliver_channel("grader", _claim):
                delivery.ntfy_critical(
                    "NHL EDGE CRITICAL: grader failed",
                    f"grader claim dead after {DELIVERY_ATTEMPTS} autonomous attempts",
                )
                report["delivery"] = {"grader": False, "email": False, "whop": False}
                print("[dedupe] grader claim failed — no email/Whop")
                _write_run(date_et, report)
                return report

            accepted_set = set(accepted)
            claimed = filter_to_claim_keys(picks, accepted_set)
            report["claim_accepted"] = list(accepted_set)
            report["claim_dropped"] = len(picks) - len(claimed)
            if not claimed:
                report["stage"] = "all-dupes"
                report["ok"] = True
                report["picks"] = []
                report["picks_n"] = 0
                report["delivery"] = {"grader": True, "email": False, "whop": False}
                print("[dedupe] grader accepted 0 keys (already on sheet / US EDGE) — silence")
                delivery.ntfy_no_new_picks(slot_label)
                _write_run(date_et, report)
                return report

            picks = claimed
            report["picks"] = picks
            report["picks_n"] = len(picks)
            print(f"[dedupe] grader accepted {len(picks)} pick(s) for customer delivery")

            subj, html = delivery.build_email_html(picks)
            report["emailSubject"] = subj
            delivery_ok: dict[str, bool] = {"grader": True}
            for name, fn in (
                ("email", lambda: delivery.send_email(subj, html)),
                ("whop", lambda: delivery.post_whop(picks)),
            ):
                ok = _deliver_channel(name, fn)
                delivery_ok[name] = ok
                if not ok:
                    delivery.ntfy_critical(
                        f"NHL EDGE CRITICAL: {name} failed",
                        f"{name} dead after {DELIVERY_ATTEMPTS} autonomous attempts",
                    )
            report["delivery"] = delivery_ok
            # Record local last_sent for multi-cron; sheet already has claim keys
            if any(delivery_ok.get(k) for k in ("email", "whop", "grader")):
                rows = record_sent(OUT, date_et, picks, prior=already)
                report["delivered_keys"] = rows
            else:
                print("[dedupe] no customer channel succeeded — local last_sent not updated")

        _write_run(date_et, report)
        return report

    except Exception as e:
        report["stage"] = f"pipeline-error: {e}"
        report["trace"] = traceback.format_exc()
        print(report["trace"])
        if not dry_run:
            delivery.ntfy_critical("NHL EDGE CRITICAL: pipeline error", str(e))
        (OUT / "last_error.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        return report
