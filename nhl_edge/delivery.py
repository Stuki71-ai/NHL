from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import requests

from nhl_edge.config import env


def build_email_html(picks: list[dict[str, Any]]) -> tuple[str, str]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    parts = datetime.now(ZoneInfo("Europe/Berlin"))
    date_str = parts.strftime("%d.%m.%Y")
    n = len(picks)
    noun = " Pick" if n == 1 else " Picks"
    subject = f"NHL EDGE | {date_str} | {n}{noun}"

    def esc(s: str) -> str:
        return (
            (s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    cards = []
    for i, p in enumerate(picks):
        dec = float(p.get("odds_dec") or 0)
        us = str(p.get("odds_us") or "")
        meta = f"{esc(p.get('match'))} — {esc(p.get('market'))}, {esc(p.get('league'))}"
        if p.get("kickoff_et"):
            meta += f" · {esc(p['kickoff_et'])}"
        last = i == n - 1
        cards.append(
            f'<div style="background:#ffffff;border-left:6px solid #111;border-radius:4px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.07);padding:16px 18px;margin:0 0 {0 if last else 16}px 0;">'
            f'<div style="margin-bottom:7px;"><span style="display:inline-block;background:#111;color:#fff;'
            f'font-size:11px;font-weight:bold;letter-spacing:.5px;padding:3px 11px;border-radius:12px;">PICK {i+1}</span>'
            f'<span style="font-size:17px;font-weight:bold;color:#111;margin-left:9px;">{esc(p.get("pick_name"))}</span></div>'
            f'<div style="font-size:13px;color:#8a8f98;margin-bottom:9px;">{meta}</div>'
            f'<div style="font-size:14px;margin-bottom:11px;"><span style="color:#16a34a;font-weight:bold;">{dec:.2f}</span> '
            f'<span style="color:#9aa0a6;">(dec)</span> &nbsp;&middot;&nbsp; <span style="color:#9aa0a6;">{esc(us)} (US)</span></div>'
            f'<div style="font-size:14px;color:#333;line-height:1.55;">{esc(p.get("rationale"))}</div></div>'
        )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;background:#f4f5f7;padding:20px 18px;">'
        + "".join(cards)
        + "</div>"
    )
    return subject, html


def send_email(subject: str, html: str) -> bool:
    # Default OFF: Whop + Sheets are the customer channels (operator 2026-07-30).
    if env("EMAIL_ENABLED", "0") not in ("1", "true", "True", "yes"):
        print("[delivery] email disabled (intentional skip)")
        return True  # not a failure — operator disabled
    user = env("GMAIL_USER")
    password = env("GMAIL_APP_PASS")
    to = env("GMAIL_TO") or "Stuki71.alert@gmail.com"
    if not user or not password:
        print("[delivery] GMAIL creds missing")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())
    print(f"[delivery] email sent → {to}")
    return True


def post_whop(picks: list[dict[str, Any]]) -> bool:
    key = env("WHOP_APP_KEY") or env("WHOP_API_KEY")
    owner = env("WHOP_OWNER_ID")
    exp = env("WHOP_SPORTS_EXP")
    if not key or not exp:
        print("[delivery] Whop creds missing")
        return False

    from datetime import datetime
    from zoneinfo import ZoneInfo

    date_str = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y")
    n = len(picks)
    noun = " Pick" if n == 1 else " Picks"
    # Same Whop room + brand as US EDGE
    title = f"US EDGE · {date_str} · {n}{noun}"
    blocks = []
    for i, p in enumerate(picks):
        dec = float(p.get("odds_dec") or 0)
        us = p.get("odds_us") or ""
        line1 = f"**PICK {i+1} — {p.get('pick_name')}**"
        line2 = f"{p.get('match')} — {p.get('league')}"
        if p.get("kickoff_et"):
            line2 += f" · {p['kickoff_et']}"
        line3 = f"**{dec:.2f}** (dec)" + (f" · {us} (US)" if us else "")
        line4 = p.get("rationale") or ""
        blocks.append("\n".join([line1, line2, line3, line4]))
    content = "\n".join(blocks)

    r = requests.post(
        "https://api.whop.com/api/v1/forum_posts",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "x-on-behalf-of": owner,
        },
        json={"experience_id": exp, "title": title, "content": content},
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"[delivery] Whop fail {r.status_code}: {r.text[:300]}")
        return False
    print("[delivery] Whop ok", r.json().get("id"))
    return True


def post_grader(picks: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Claim keys on shared Picks sheet. Returns (ok, accepted_claim_keys).

    Accepted keys are the family source of truth for US EDGE ↔ NHL EDGE dedupe.
    """
    token = env("GQ_SPORTS_WEBHOOK_TOKEN")
    url = env("GQ_SPORTS_WEBHOOK_URL")
    if not token or not url:
        print("[delivery] grader webhook missing")
        return False, []
    body = {
        "picks": [
            {
                "date": p["date"],
                "sport": p["sport"],
                "league": p["league"],
                "home": p["home"],
                "away": p["away"],
                "market": p["market"],
                "selection": p["selection_struct"],
                "odds_dec": p["odds_dec"],
                "odds_us": p["odds_us"],
            }
            for p in picks
        ]
    }
    r = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-gq-token": token},
        json=body,
        timeout=60,
    )
    print(f"[delivery] grader status={r.status_code} body={r.text[:200]}")
    if r.status_code >= 400:
        return False, []
    try:
        data = r.json() or {}
    except Exception:
        data = {}
    keys = data.get("keys") or []
    if not isinstance(keys, list):
        keys = []
    return True, [str(k) for k in keys]


def ntfy_no_picks() -> None:
    try:
        requests.post(
            "https://ntfy.sh/Stuki71-EDGE",
            headers={"Title": "NHL EDGE @ No picks for today"},
            data=b"",
            timeout=15,
        )
    except Exception:
        pass


def ntfy_critical(title: str, msg: str) -> None:
    # EDGE family: ONLY critical (urgent) or no-picks — both on Stuki71-EDGE.
    try:
        requests.post(
            "https://ntfy.sh/Stuki71-EDGE",
            headers={
                "Title": title,
                "Priority": "urgent",
                "Tags": "rotating_light,nhl-edge",
            },
            data=str(msg)[:500].encode(),
            timeout=15,
        )
    except Exception:
        pass
