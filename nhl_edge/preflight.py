"""Live-run credential preflight — fail once before Odds/ratings/composer spend."""
from __future__ import annotations

from nhl_edge.config import env


def missing_live_secrets() -> list[str]:
    """
    Required for a full live ship.
    Email only required when EMAIL_ENABLED is on (default off — Whop + Sheets only).
    Composer: Opus 5 primary (Anthropic) + GPT-5.6 Sol fallback (OpenAI).
    """
    miss: list[str] = []
    if not (env("ODDS_API_KEY") or env("ODDS_API_KEY_FALLBACK")):
        miss.append("ODDS_API_KEY")
    if not env("ANTHROPIC_API_KEY"):
        miss.append("ANTHROPIC_API_KEY")
    if not env("OPENAI_API_KEY"):
        miss.append("OPENAI_API_KEY")
    if not env("PERPLEXITY_API_KEY"):
        miss.append("PERPLEXITY_API_KEY")
    if not (env("WHOP_APP_KEY") or env("WHOP_API_KEY")):
        miss.append("WHOP_APP_KEY")
    if not env("WHOP_OWNER_ID"):
        miss.append("WHOP_OWNER_ID")
    if not env("WHOP_SPORTS_EXP"):
        miss.append("WHOP_SPORTS_EXP")
    if not env("GQ_SPORTS_WEBHOOK_URL"):
        miss.append("GQ_SPORTS_WEBHOOK_URL")
    if not env("GQ_SPORTS_WEBHOOK_TOKEN"):
        miss.append("GQ_SPORTS_WEBHOOK_TOKEN")
    email_on = env("EMAIL_ENABLED", "0") in ("1", "true", "True", "yes")
    if email_on:
        if not env("GMAIL_USER"):
            miss.append("GMAIL_USER")
        if not env("GMAIL_APP_PASS"):
            miss.append("GMAIL_APP_PASS")
    return miss
