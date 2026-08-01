from __future__ import annotations

from typing import Any

import requests

from nhl_edge.config import (
    GROK_NEWS_MODEL,
    GROK_NEWS_TIMEOUT,
    SONAR_MODEL,
    env,
)


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


_NEWS_ASK = (
    "You are researching tonight's NHL games for a betting desk. "
    "For EACH matchup below, report ONLY factual, current items with sources, "
    "anchored on morning-skate and beat-writer reporting: "
    "THE CONFIRMED STARTING GOALIES FIRST for both teams (name them; say UNCONFIRMED "
    "if not yet announced — and whether a BACKUP goalie is getting the start), "
    "then key players OUT / injured / suspended, and any late line-moving news. "
    "Late-breaking beat-writer reports on X count — prefer the freshest confirmed information. "
    "Additionally give ONE sharp, verifiable stat or fact per matchup a recreational "
    "bettor would find striking. "
    "If unknown, say UNKNOWN. No betting advice. Be concise.\n\n"
)


def grok_team_news(games: list[dict[str, Any]]) -> str:
    """Primary news engine: grok-4.5 with live web + X search (family-proven pattern).

    Raises on any failure so the caller can fall back to sonar-pro.
    """
    key = env("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    if not games:
        return "NO TEAM NEWS — empty slate"

    prompt = _NEWS_ASK + "\n".join(_game_lines(games))
    r = requests.post(
        "https://api.x.ai/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": GROK_NEWS_MODEL,
            "input": prompt,
            "tools": [{"type": "web_search"}, {"type": "x_search"}],
            "tool_choice": "required",
            "reasoning": {"effort": "high"},
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
            raise RuntimeError("grok returned text with 0 web+x searches")

    text = ""
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text"):
                    text += c.get("text") or ""
    text = text.strip()
    if not text:
        raise RuntimeError("empty grok news text")
    web_n = (sd or {}).get("web_search_calls")
    x_n = (sd or {}).get("x_search_calls")
    print(f"[news] grok ok web_searches={web_n} x_searches={x_n} chars={len(text)}")
    return text


def sonar_team_news(games: list[dict[str, Any]]) -> str:
    """Fallback news engine: sonar-pro (never plain sonar — operator order)."""
    key = env("PERPLEXITY_API_KEY")
    if not key:
        return "NO TEAM NEWS — PERPLEXITY_API_KEY missing"

    if not games:
        return "NO TEAM NEWS — empty slate"

    prompt = _NEWS_ASK + "\n".join(_game_lines(games))

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
                "messages": [
                    {
                        "role": "system",
                        "content": "Return tight factual NHL team news. Cite when possible. No fabrications.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        return text.strip() or "NO TEAM NEWS — empty sonar response"
    except Exception as e:
        return f"NO TEAM NEWS — sonar error: {e}"


def team_news(games: list[dict[str, Any]]) -> str:
    """Mandatory news brief: grok-4.5 web+X primary → sonar-pro fallback."""
    try:
        return grok_team_news(games)
    except Exception as e:
        print(f"[news] grok failed ({str(e)[:160]}) — falling back to sonar-pro")
        return sonar_team_news(games)
