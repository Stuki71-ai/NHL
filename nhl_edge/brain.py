from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from nhl_edge.config import (
    COMPOSER_ATTEMPTS_FALLBACK,
    COMPOSER_ATTEMPTS_PRIMARY,
    COMPOSER_FALLBACK_EFFORT,
    COMPOSER_FALLBACK_MODEL,
    COMPOSER_PRIMARY_EFFORT,
    COMPOSER_PRIMARY_MODEL,
    MAX_PICKS,
    env,
)

ET = ZoneInfo("America/New_York")


def _empty_composer_stats(**extra: Any) -> dict[str, Any]:
    base = {
        "composer_model": COMPOSER_PRIMARY_MODEL,
        "composer_effort": COMPOSER_PRIMARY_EFFORT,
        "composer_seconds": None,
        "composer_tokens": None,
        "composer_input_tokens": None,
        "composer_output_tokens": None,
        "composer_reasoning_tokens": None,
        "composer_fallback": False,
        "composer_error": None,
        "composer_attempts": 0,
        "composer_attempts_primary": 0,
        "composer_attempts_fallback": 0,
        "composer_effort_used": None,
        "composer_phase": None,
    }
    base.update(extra)
    return base


def _usage_from_openai_response(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize OpenAI Responses API usage into flat facts."""
    u = data.get("usage") or {}
    out_details = u.get("output_tokens_details") or u.get("completion_tokens_details") or {}
    total = u.get("total_tokens")
    inp = u.get("input_tokens")
    if inp is None:
        inp = u.get("prompt_tokens")
    outp = u.get("output_tokens")
    if outp is None:
        outp = u.get("completion_tokens")
    reason = out_details.get("reasoning_tokens")
    if total is None and (inp is not None or outp is not None):
        total = (inp or 0) + (outp or 0)
    return {
        "composer_tokens": total,
        "composer_input_tokens": inp,
        "composer_output_tokens": outp,
        "composer_reasoning_tokens": reason,
    }


def _usage_from_anthropic_response(data: dict[str, Any]) -> dict[str, Any]:
    u = data.get("usage") or {}
    inp = u.get("input_tokens")
    outp = u.get("output_tokens")
    # Anthropic may include cache / thinking tokens under nested keys; total is best-effort.
    total = None
    if inp is not None or outp is not None:
        total = (inp or 0) + (outp or 0)
    return {
        "composer_tokens": total,
        "composer_input_tokens": inp,
        "composer_output_tokens": outp,
        "composer_reasoning_tokens": u.get("thinking_tokens") or u.get("reasoning_tokens"),
    }


SYS = """You are the pick composer for a paid sports-betting product for recreational bettors.
You receive a PRE-RANKED shortlist with prices already locked. You may ONLY select from that shortlist.
Never invent teams, prices, lines, or kickoffs.

Pick count:
- Up to THREE highest-conviction picks. Can be fewer than 3 — quality over quantity. 0 is allowed (output []).
- Never pad. Never invent a fourth pick. Max 1 pick per match.

SELECTION PRIORITY (paid subscribers — HARD):
- These picks are sold to paying recreational bettors. They care about WINNING the bet more than abstract long-horizon +EV.
- Prefer higher model win probability (model_p) over higher long-term expected value / edge when candidates conflict.
- A solid favorite or strong-probability side that clears floors beats a juicier longshot with a bigger theoretical edge but lower win chance.
- Do not chase max edge % or long-run ROI at the expense of hit rate. Shortlist edges already cleared floors — among survivors, rank by likelihood of winning first, then strength of the case.
- Still quality over quantity: skip soft/coin-flip leftovers rather than padding with low-probability tickets.

Odds / schedule:
- Odds already filtered (>=1.75 ML / >=1.85 spreads and totals) — keep given odds_dec/odds_us exactly.
- kickoff_et must be the real time from the shortlist (never TBD).

AVAILABILITY VETO (NHL-specific — HARD):
- The news brief leads with CONFIRMED STARTING GOALIES. If a shortlisted pick leans on a team
  and that team starts its BACKUP goalie (or the starter is out/unconfirmed on a goalie-sensitive
  pick), SKIP it — the ratings model cannot see the goalie; you are the veto. The starting goalie
  is the single most important availability signal in this sport.
- Same veto for a top scorer / #1 defenseman OUT when the pick's case leans on full strength.
- An UNCONFIRMED goalie alone is not an automatic skip — judge whether the case survives either netminder.

RATIONALE — EDGE family (NIGHT rules; length band updated for product):
For each pick, deliver a Vic Mackey rationale (The Shield): brutal, street-smart, direct, 2 sentences max. ZERO numbers, odds, percentages, or line-movement figures inside the rationale text — no digits AND no spelled-out numbers; numbers live in the header line only. ONE verifiable fact per rationale (name, streak, record, injury, suspension, H2H, season or tournament stat, or anything similar) woven naturally into narrative that makes the reader go "damn, I did not know that". NEVER name a stadium, arena or venue ("T-Mobile Park", "TQL Stadium"). No punter jargon. No repeated wording across rationales. No strategy secrets. These entertaining rationales are a primary selling factor for paying recreational subscribers.

LENGTH HARD TARGET (family — mandatory):
- Each rationale MUST land at ~55–75 words (HARD). Aim inside the band: not essays (>85 words), not stubs (<45 words).
- Never close by restating the pick or issuing a call to action (“Back the hosts.”, “Keep it under.”, “Go low.”) — the pick line above already says it. End on the last piece of substance.
- Strip citation markers.
- Strip throat-clearing / filler OPENER so it STARTS ON SUBSTANCE — drop leading fillers such as "Listen,", "Listen up,", "Mackey here,", "Listen, Mackey here—", "Straight talk:", "Real talk:", "Here's the deal,", "Look,", "Alright,", "Bottom line:" and any similar lead-in, including a dash/colon/comma left behind, then capitalize the new first word.
- Kill shot first: opening clause is a verdict, not a stat dump.
- Complete sentences only; never cut mid-sentence.
- KEEP: harsh streetwise Mackey voice; the SINGLE strongest verifiable fact (names and numbers EXACT and unaltered); pick core logic.
- Every number, name, team or claim you KEEP must be reproduced EXACTLY; never invent, alter or round one.

Fact style (not "baby talk" — glanceable sports data):
- Use data types a recreational bettor understands at a glance: player/team name, goalie news, injury news, streak, record, H2H, goals per game, power-play form, save percentage stated plainly, home/road splits, back-to-back fatigue, win/loss form.
- Numbers are fine when they read like box-score / leaderboard facts, not lab reports.
- ONE fact only — still woven into Mackey narrative, not a stats dump.

Hard bans in rationales (strategy secrets / pipeline shop talk — NOT fan data):
- Never say: model, projection, lambda, edge %, fair price, EV (expected value), xG, Corsi, Fenwick, PDO, "expected goals", rate multipliers, blend, shortlist, Poisson, quant, "our system", process metrics, or how the pick was selected.
- Never lecture staking, units, CLV, or bankroll.
- Never put bookmaker names, prices, lines, odds, sharp/soft labels, or selection-strategy talk in the subscriber rationale.
- Do not invent injuries, stats, or weather/atmospheric details — mention weather only if it is in the brief; every fact must be traceable to the news brief or shortlist numbers you were given.

Output ONLY a JSON array (no markdown fences). Each object:
  date (YYYY-MM-DD ET), sport "icehockey_nhl", league "NHL", home, away, match "Away @ Home",
  kickoff_et, market "Moneyline"|"Total"|"Asian Handicap", selection_struct (HOME|AWAY|OVER x|UNDER x|HOME ±x|AWAY ±x — puck line),
  pick_name, odds_dec, odds_us, rationale, model_edge (number), model_p (number)
If nothing clears the bar, output [].
"""


def compose_picks(
    shortlist: list[dict[str, Any]],
    news_brief: str,
    model_brief: str,
    date_et: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Returns (picks, composer_stats).

    Retry ladder:
      3× Claude Opus 5 (effort max)
      → GPT-5.6 Sol (effort high)
      → edge-rank (no LLM)

    Unsuccessful = timeout/HTTP/empty text/JSON parse error/all picks rejected vs shortlist.
    Successful empty [] (honest no-plays) does NOT step down the ladder.
    """
    if not shortlist:
        return [], _empty_composer_stats(composer_fallback=False, composer_error="empty_shortlist")

    if not date_et:
        date_et = datetime.now(ET).strftime("%Y-%m-%d")

    anth_key = env("ANTHROPIC_API_KEY")
    oai_key = env("OPENAI_API_KEY")
    if not anth_key and not oai_key:
        picks = _fallback_select(shortlist, date_et=date_et)
        return picks, _empty_composer_stats(
            composer_fallback=True,
            composer_error="missing_ANTHROPIC_API_KEY_and_OPENAI_API_KEY",
        )

    payload = {
        "date_et": date_et,
        "max_picks": MAX_PICKS,
        "shortlist": shortlist[:12],
        "news": news_brief[:6000],
        "model_notes": model_brief[:4000],
    }
    user = (
        "Select the highest-conviction picks from shortlist only "
        "(max 3, can be less — quality over quantity; [] if none clear the bar). "
        "PAID SUBSCRIBERS: prefer higher model_p (win probability) over higher long-term edge/EV "
        "when choosing among shortlist candidates — hit rate beats theoretical +EV. "
        "For each pick: Vic Mackey voice — direct, one exploitable edge, "
        "ONE verifiable fact woven in (damn-I-didn't-know-that), fully traceable, "
        "NO strategy secrets, NO punter jargon, NO repeated wording across picks. "
        "Rationale length HARD TARGET ~55–75 words (EDGE family; not >85, not <45); "
        "never close by restating the pick or with a call to action. "
        "Start on substance (no Listen/Look/Bottom-line openers); kill-shot first. "
        "Rationales sell the product to paid recreational bettors.\n"
        f"DATA:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    # Phase plan: primary Opus max × N, then GPT Sol high × M
    phases: list[tuple[str, str, str, str]] = []
    if anth_key:
        for _ in range(COMPOSER_ATTEMPTS_PRIMARY):
            phases.append(("primary", COMPOSER_PRIMARY_MODEL, COMPOSER_PRIMARY_EFFORT, "anthropic"))
    if oai_key:
        for _ in range(COMPOSER_ATTEMPTS_FALLBACK):
            phases.append(("fallback", COMPOSER_FALLBACK_MODEL, COMPOSER_FALLBACK_EFFORT, "openai"))
    if not phases:
        picks = _fallback_select(shortlist, date_et=date_et)
        return picks, _empty_composer_stats(
            composer_fallback=True,
            composer_error="no_composer_provider_keys",
        )

    t_all = time.perf_counter()
    n_primary = n_fallback = 0
    last_err = None
    sum_tokens = sum_in = sum_out = sum_reason = 0
    any_usage = False
    total_attempts = len(phases)

    for attempt, (phase, model, effort, provider) in enumerate(phases):
        if phase == "primary":
            n_primary += 1
        else:
            n_fallback += 1

        t0 = time.perf_counter()
        try:
            if provider == "anthropic":
                data, usage = _call_anthropic(anth_key, model, effort, user)
            else:
                data, usage = _call_openai(oai_key, model, effort, user)
            elapsed = round(time.perf_counter() - t0, 3)

            if usage.get("composer_tokens") is not None:
                any_usage = True
                sum_tokens += usage.get("composer_tokens") or 0
                sum_in += usage.get("composer_input_tokens") or 0
                sum_out += usage.get("composer_output_tokens") or 0
                sum_reason += usage.get("composer_reasoning_tokens") or 0

            text = _extract_text(data, provider=provider)
            if not text:
                raise ValueError("empty composer text")
            raw_picks = _parse_json_array(text)
            if not isinstance(raw_picks, list):
                raise ValueError("composer output not a JSON array")
            picks = _validate_against_shortlist(raw_picks, shortlist, date_et)
            if len(raw_picks) > 0 and len(picks) == 0:
                raise ValueError("all composer picks rejected vs shortlist")

            total_sec = round(time.perf_counter() - t_all, 3)
            stats = _empty_composer_stats(
                composer_model=model,
                composer_effort=effort,
                composer_effort_used=effort,
                composer_phase=phase,
                composer_seconds=total_sec,
                composer_fallback=phase == "fallback",
                composer_attempts=attempt + 1,
                composer_attempts_primary=n_primary,
                composer_attempts_fallback=n_fallback,
            )
            if any_usage:
                stats["composer_tokens"] = sum_tokens
                stats["composer_input_tokens"] = sum_in
                stats["composer_output_tokens"] = sum_out
                stats["composer_reasoning_tokens"] = sum_reason
            print(
                f"[brain] composer ok attempt={attempt + 1}/{total_attempts} "
                f"phase={phase} model={model} effort={effort} call_s={elapsed} total_s={total_sec} "
                f"tokens={stats.get('composer_tokens')} "
                f"(in={stats.get('composer_input_tokens')} out={stats.get('composer_output_tokens')} "
                f"reasoning={stats.get('composer_reasoning_tokens')}) picks={len(picks)}"
            )
            return picks, stats

        except Exception as e:
            last_err = str(e)[:300]
            elapsed = round(time.perf_counter() - t0, 3)
            print(
                f"[brain] composer fail attempt={attempt + 1}/{total_attempts} "
                f"phase={phase} model={model} effort={effort} seconds={elapsed}: {last_err}"
            )
            if attempt < total_attempts - 1:
                time.sleep(1.5 * (1 if attempt < 2 else 2))

    # All LLM attempts exhausted → edge-rank fallback
    total_sec = round(time.perf_counter() - t_all, 3)
    print(
        f"[brain] all {total_attempts} composer attempts failed "
        f"(primary={n_primary} fallback={n_fallback}); edge-ranked fallback"
    )
    picks = _fallback_select(shortlist, date_et=date_et)
    stats = _empty_composer_stats(
        composer_model=COMPOSER_PRIMARY_MODEL,
        composer_effort=COMPOSER_PRIMARY_EFFORT,
        composer_effort_used="edge-rank",
        composer_phase="edge-rank",
        composer_seconds=total_sec,
        composer_fallback=True,
        composer_error=last_err,
        composer_attempts=total_attempts,
        composer_attempts_primary=n_primary,
        composer_attempts_fallback=n_fallback,
    )
    if any_usage:
        stats["composer_tokens"] = sum_tokens
        stats["composer_input_tokens"] = sum_in
        stats["composer_output_tokens"] = sum_out
        stats["composer_reasoning_tokens"] = sum_reason
    return picks, stats


def _call_anthropic(
    key: str, model: str, effort: str, user: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Claude Opus 5 Messages API (official docs):
    - Thinking is ON by default on Opus 5; `thinking: {type: adaptive}` is
      valid and equivalent to the default — we set it explicitly so max-effort
      runs with adaptive thinking (never disabled).
    - Depth is controlled by `output_config.effort` (max here), not by
      budget_tokens. Adaptive is a thinking mode, not an effort value.
    - At effort xhigh/max, `thinking: {type: disabled}` is a 400.
    - Non-default temperature/top_p/top_k are a 400 on Opus 5 — omit them.
    - max effort: large max_tokens (thinking + text share the budget); 64k default.
    """
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 64000,
        # Explicit adaptive thinking (docs: default on Opus 5; still set for clarity)
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "system": SYS,
        "messages": [{"role": "user", "content": user}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=600,
    )
    if r.status_code >= 400:
        raise ValueError(f"anthropic HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    return data, _usage_from_anthropic_response(data)


def _call_openai(
    key: str, model: str, effort: str, user: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": [
                {"role": "system", "content": SYS},
                {"role": "user", "content": user},
            ],
            "reasoning": {"effort": effort},
        },
        timeout=300,
    )
    if r.status_code >= 400:
        raise ValueError(f"openai HTTP {r.status_code}: {r.text[:400]}")
    data = r.json()
    return data, _usage_from_openai_response(data)


def _extract_text(r: dict[str, Any], provider: str) -> str:
    if provider == "anthropic":
        out = ""
        for block in r.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                out += block.get("text") or ""
        return out.strip()

    # OpenAI Responses API (and similar)
    out = ""
    for item in r.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    out += c.get("text") or ""
    if not out and isinstance(r.get("output_text"), str):
        out = r["output_text"]
    if not out:
        # chat.completions-style safety net
        choices = r.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message") or {}
            out = msg.get("content") or ""
    return out.strip()


def _parse_json_array(raw: str) -> list:
    content = (raw or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    start, end = content.find("["), content.rfind("]")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    data = json.loads(content)
    return data if isinstance(data, list) else []


def _kickoff_ok(kick: Any) -> bool:
    s = str(kick or "").strip()
    if not s:
        return False
    if re.match(r"^tbd", s, re.I):
        return False
    return True


def _fallback_select(shortlist: list[dict[str, Any]], date_et: str | None = None) -> list[dict[str, Any]]:
    if not date_et:
        date_et = datetime.now(ET).strftime("%Y-%m-%d")
    # Paid-sub priority: win probability first, then edge (not pure long-term EV sort).
    ordered = sorted(
        shortlist,
        key=lambda c: (-float(c.get("model_p") or 0), -float(c.get("edge") or 0)),
    )
    out = []
    for c in ordered:
        if not _kickoff_ok(c.get("kickoff_et")):
            continue
        out.append(
            {
                "date": date_et,
                "sport": "icehockey_nhl",
                "league": "NHL",
                "home": c["home"],
                "away": c["away"],
                "match": c["match"],
                "kickoff_et": c["kickoff_et"],
                "market": c["market"],
                "selection_struct": c["selection_struct"],
                "pick_name": c["pick_name"],
                "odds_dec": c["odds_dec"],
                "odds_us": c["odds_us"],
                "model_edge": c["edge"],
                "model_p": c["model_p"],
                "rationale": (
                    f"Model edge {c['edge']*100:.1f}% at {c['odds_dec']:.2f}. "
                    f"Pace-adjusted team ratings priced margin/total "
                    f"{c['meta'].get('exp_margin')}/{c['meta'].get('exp_total')}."
                ),
            }
        )
        if len(out) >= MAX_PICKS:
            break
    return out


def _validate_against_shortlist(
    picks: list, shortlist: list[dict[str, Any]], date_et: str
) -> list[dict[str, Any]]:
    """Ensure brain cannot invent prices/teams — exact shortlist key only; force shortlist fields."""
    by_key = {}
    for c in shortlist:
        k = (c["home"], c["away"], c["market"], c["selection_struct"])
        by_key[k] = c

    out = []
    seen_match = set()
    for p in picks:
        if not isinstance(p, dict):
            continue
        home, away = p.get("home"), p.get("away")
        market = p.get("market")
        sel = p.get("selection_struct") or p.get("selection")
        key = (home, away, market, sel)
        c = by_key.get(key)
        if not c:
            # exact key only — never remap UNDER→OVER or invent a side
            continue
        if not _kickoff_ok(c.get("kickoff_et")):
            continue
        mk = (c["home"], c["away"])
        if mk in seen_match:
            continue
        seen_match.add(mk)
        out.append(
            {
                "date": date_et,
                "sport": "icehockey_nhl",
                "league": "NHL",
                "home": c["home"],
                "away": c["away"],
                "match": c["match"],
                "kickoff_et": c["kickoff_et"],
                "market": c["market"],
                "selection_struct": c["selection_struct"],
                "pick_name": c["pick_name"],
                "odds_dec": c["odds_dec"],
                "odds_us": c["odds_us"],
                "model_edge": c["edge"],
                "model_p": c["model_p"],
                "rationale": (p.get("rationale") or "").strip()
                or f"Model edge {c['edge']*100:.1f}% at {c['odds_dec']}.",
            }
        )
        if len(out) >= MAX_PICKS:
            break
    return out
