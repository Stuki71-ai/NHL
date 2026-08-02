from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from nhl_edge.config import (
    GROK_NEWS_MODEL,
    GROK_NEWS_TIMEOUT,
    SONAR_MODEL,
    env,
)

# NIGHT EDGE 3-layer news architecture (operator order 2026-08-01, "place on all EDGE
# products"). Every game is researched individually and IN PARALLEL - no game is ever
# skipped, and a thin answer for one game can never blank the rest of the slate:
#   [1] sonar-pro PER GAME (recency=day) with a per-game quality gate
#   [2] Serper.dev per-game fallback fills any game layer 1 left empty
#   [3] ONE bounded grok-4.5 X-INTEL call on top (X-first, cited handles, fail-silent)
# Layer 3 failing NEVER blocks the slate; layers 1+2 guarantee a web baseline.

_SPORT = "NHL"

_SONAR_SYS = (
    "You are an NHL team news researcher. Report, anchored on morning-skate "
    "and beat-writer reporting: "
    "1) THE CONFIRMED STARTING GOALIES FIRST for both teams (name them; say "
    "UNCONFIRMED if not announced, and whether a BACKUP is getting the start), "
    "2) Key players OUT / injured / suspended, "
    "3) Notable line changes. "
    "Also give ONE sharp, verifiable stat or fact a recreational bettor "
    "would find striking. Be specific with names and reasons. "
    "If no information found, say so clearly. No betting advice."
)

_X_ANCHOR = (
    "CONFIRMED STARTING GOALIES FIRST (name them; note a backup getting "
    "the start), line combinations, injuries and suspensions"
)

# Per-game quality gate (NIGHT pattern): reject stubs and "found nothing" boilerplate.
_MIN_CHARS = 100
_BOILER = (
    "no information",
    "no data",
    "could not find",
    "search results contain no",
)

_SONAR_GAME_TIMEOUT = 120  # per game, parallel - guards a hung connection, not accuracy
_SERPER_TIMEOUT = 30


def _game_lines(games: list[dict[str, Any]]) -> list[str]:
    lines = []
    for g in games[:12]:
        home, away = g.get("home"), g.get("away")
        tags = []
        if g.get("home_b2b"):
            tags.append(f"{home} on 2nd night of back-to-back")
        if g.get("away_b2b"):
            tags.append(f"{away} on 2nd night of back-to-back")
        suffix = f" | {'; '.join(tags)}" if tags else ""
        lines.append(f"- {away} @ {home}{suffix}")
    return lines


def _label(g: dict[str, Any]) -> str:
    return f"{g.get('away')} @ {g.get('home')}"


def _game_context(g: dict[str, Any]) -> str:
    tags = []
    if g.get("home_b2b"):
        tags.append(f"{g.get('home')} on 2nd night of back-to-back")
    if g.get("away_b2b"):
        tags.append(f"{g.get('away')} on 2nd night of back-to-back")
    return "; ".join(tags)


def sonar_game_news(g: dict[str, Any]) -> str:
    """Layer 1: one sonar-pro call for THIS game (never plain sonar - operator order).

    Returns "" when the call fails or the per-game quality gate rejects the answer.
    """
    key = env("PERPLEXITY_API_KEY")
    if not key:
        return ""
    ctx = _game_context(g)
    user = (
        f"Team news, injuries and lineup updates for {_label(g)} ({_SPORT}) playing today"
        + (f" | {ctx}" if ctx else "")
        + ". Include confirmed absences, doubtful players, and the freshest "
        "beat-writer reports for BOTH teams."
    )
    try:
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": SONAR_MODEL,
                "temperature": 0.1,
                "search_recency_filter": "day",
                "messages": [
                    {"role": "system", "content": _SONAR_SYS},
                    {"role": "user", "content": user},
                ],
            },
            timeout=_SONAR_GAME_TIMEOUT,
        )
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""
    low = text.lower()
    if len(text) <= _MIN_CHARS or any(b in low for b in _BOILER):
        return ""
    return text


def serper_game_news(g: dict[str, Any]) -> str:
    """Layer 2: Serper.dev web snippets for THIS game (deterministic fallback)."""
    key = env("SERPER_KEY")
    if not key:
        return ""
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={
                "q": f"{g.get('away')} {g.get('home')} {_SPORT} team news injuries lineup today",
                "num": 8,
            },
            timeout=_SERPER_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        organic = data.get("organic") or []
        if not organic:
            return ""
        snippets = [
            f"- {o.get('title') or ''}: {o.get('snippet') or ''}" for o in organic[:6]
        ]
        kg = (data.get("knowledgeGraph") or {}).get("description")
        if kg:
            snippets.insert(0, f"OVERVIEW: {kg}")
        return "[Via web search]\n" + "\n".join(snippets)
    except Exception:
        return ""


def _research_game(g: dict[str, Any]) -> str:
    text = (
        sonar_game_news(g)
        or serper_game_news(g)
        or "No team news found from any source."
    )
    ctx = _game_context(g)
    header = f"### {_label(g)}" + (f" | {ctx}" if ctx else "")
    return f"{header}\n{text}\n"


def grok_x_intel(games: list[dict[str, Any]]) -> str:
    """Layer 3: ONE bounded grok-4.5 live X search across the slate (NIGHT pattern).

    Raises on any failure - the caller appends fail-silently so the slate never blocks.
    """
    key = env("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    prompt = (
        "Search X (and the web only when X is thin) for TEAM NEWS on today's "
        f"{_SPORT} games:\n" + "\n".join(_game_lines(games)) +
        "\n\nFor EACH game report ONLY intel sourced from X posts: "
        f"{_X_ANCHOR}, and beat-writer / team-account reports. "
        "Cite the X handle and how recent the post is. "
        "If nothing credible is found for a game, write exactly 'no X intel'. "
        "Plain text, grouped per game, max ~120 words per game."
    )
    r = requests.post(
        "https://api.x.ai/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": GROK_NEWS_MODEL,
            "input": prompt,
            "tools": [{"type": "web_search"}, {"type": "x_search"}],
            "tool_choice": "required",
            "reasoning": {"effort": "high"},
            "max_turns": 12,
        },
        timeout=GROK_NEWS_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()

    # Zero-search guard (US EDGE pattern): text without any live search is memory, not news.
    usage = data.get("usage") or {}
    sd = usage.get("server_side_tool_usage_details") or None
    if sd is not None:
        web = sd.get("web_search_calls") or 0
        x = sd.get("x_search_calls") or 0
        if web == 0 and x == 0:
            raise RuntimeError("grok X-INTEL returned text with 0 web+x searches")

    text = ""
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    text += c.get("text") or ""
    if not text and isinstance(data.get("output_text"), str):
        text = data["output_text"]
    text = text.strip()
    if not text:
        raise RuntimeError("empty grok X-INTEL text")
    web_n = (sd or {}).get("web_search_calls")
    x_n = (sd or {}).get("x_search_calls")
    print(f"[news] grok-x ok web_searches={web_n} x_searches={x_n} chars={len(text)}")
    return text


def team_news(games: list[dict[str, Any]]) -> str:
    """Mandatory news brief: per-game sonar-pro + Serper baseline, then X-INTEL on top."""
    if not games:
        return "NO TEAM NEWS - empty slate"
    games = games[:12]

    with ThreadPoolExecutor(max_workers=min(8, len(games))) as ex:
        parts = list(ex.map(_research_game, games))

    serper_used = sum(1 for p in parts if "[Via web search]" in p)
    empty = sum(1 for p in parts if "No team news found from any source." in p)
    brief = "".join(parts).strip()
    print(
        f"[news] per-game baseline games={len(games)} "
        f"sonar={len(games) - serper_used - empty} serper={serper_used} "
        f"empty={empty} chars={len(brief)}"
    )

    try:
        x_text = grok_x_intel(games)
        brief += (
            "\n\n### X-INTEL (grok-4.5 live X search - beat writers, lineup leaks)\n"
            + x_text
        )
    except Exception as e:
        print(f"[news] grok X-INTEL unavailable ({str(e)[:140]})")
        brief += "\n\n### X-INTEL (grok-4.5 live X search)\n[unavailable this run]"
    return brief
