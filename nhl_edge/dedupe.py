"""Same-day + cross-product dedupe (US EDGE + NHL EDGE share GQ Sports Picks sheet).

Local multi-cron: last_sent.json
Family shared: claim keys on Picks tab (same norm as US EDGE Generate & Format / grader).
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests

# Same spreadsheet + Picks gid as US EDGE / GQ Sports Grader
SHARED_PICKS_SHEET_ID = "1EFhrqp09H94gAlUqLvUoV8RtdeBTSamd9LDgAE5uuFk"
SHARED_PICKS_GID = "1397205216"
SHARED_KEYS_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHARED_PICKS_SHEET_ID}/gviz/tq"
    f"?tqx=out:csv&gid={SHARED_PICKS_GID}&tq=select%20A"
)


def gnorm(s: Any) -> str:
    """US EDGE _gnorm — must stay byte-faithful for cross-product keys."""
    t = unicodedata.normalize("NFD", str(s or ""))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", t.lower()).strip()


def claim_key(p: dict[str, Any]) -> str:
    """US EDGE _gkey / grader Build Rows key."""
    sel = p.get("selection_struct") or p.get("selection") or ""
    return "_".join(
        [
            str(p.get("date") or "").strip(),
            gnorm(p.get("sport") or "icehockey_nhl"),
            gnorm(p.get("home")),
            gnorm(p.get("away")),
            gnorm(p.get("market")),
            gnorm(sel),
        ]
    )


def pick_key(p: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Local last_sent identity (readable tuple)."""
    return (
        str(p.get("date") or ""),
        str(p.get("home") or ""),
        str(p.get("away") or ""),
        str(p.get("market") or ""),
        str(p.get("selection_struct") or p.get("selection") or ""),
    )


def key_str(k: tuple[str, ...]) -> str:
    return "||".join(k)


def load_sent_keys(out_dir: Path, date_et: str) -> set[tuple[str, str, str, str, str]]:
    """Keys already delivered by this NHL host today (last_sent + run JSON)."""
    sent: set[tuple[str, str, str, str, str]] = set()
    last = out_dir / "last_sent.json"
    if last.is_file():
        try:
            data = json.loads(last.read_text(encoding="utf-8"))
            if str(data.get("date_et") or "") == date_et:
                for row in data.get("keys") or []:
                    if isinstance(row, (list, tuple)) and len(row) == 5:
                        sent.add(tuple(str(x) for x in row))  # type: ignore[arg-type]
        except Exception as e:
            print(f"[dedupe] last_sent read failed: {e}")

    run_path = out_dir / f"run_{date_et}.json"
    if run_path.is_file():
        try:
            data = json.loads(run_path.read_text(encoding="utf-8"))
            for row in data.get("delivered_keys") or []:
                if isinstance(row, (list, tuple)) and len(row) == 5:
                    sent.add(tuple(str(x) for x in row))  # type: ignore[arg-type]
        except Exception as e:
            print(f"[dedupe] run json read failed: {e}")
    return sent


def load_shared_claim_keys(timeout: int = 20) -> set[str]:
    """All claim keys already on the shared Picks sheet (US EDGE + NHL EDGE + any claim)."""
    r = requests.get(SHARED_KEYS_URL, timeout=timeout)
    r.raise_for_status()
    text = r.text or ""
    keys: set[str] = set()
    # gviz CSV may quote fields
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        k = (row[0] or "").strip().strip('"')
        if not k or k.lower() == "key":
            continue
        keys.add(k)
    return keys


def drop_already_sent(
    picks: list[dict[str, Any]],
    sent: set[tuple[str, str, str, str, str]],
    shared_claim_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop picks already on local last_sent OR shared family sheet claim keys."""
    shared = shared_claim_keys or set()
    fresh, dups = [], []
    for p in picks:
        if pick_key(p) in sent or claim_key(p) in shared:
            dups.append(p)
        else:
            fresh.append(p)
    return fresh, dups


def filter_to_claim_keys(
    picks: list[dict[str, Any]], accepted: set[str]
) -> list[dict[str, Any]]:
    """Keep only picks whose claim_key was accepted by the grader (post-claim filter)."""
    if not accepted:
        return []
    return [p for p in picks if claim_key(p) in accepted]


def record_sent(
    out_dir: Path,
    date_et: str,
    picks: list[dict[str, Any]],
    prior: set[tuple[str, str, str, str, str]] | None = None,
) -> list[list[str]]:
    """Merge new pick keys into last_sent.json. Returns full key list as JSON-ready rows."""
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = set(prior or load_sent_keys(out_dir, date_et))
    for p in picks:
        keys.add(pick_key(p))
    rows = [list(k) for k in sorted(keys)]
    (out_dir / "last_sent.json").write_text(
        json.dumps({"date_et": date_et, "keys": rows}, indent=2),
        encoding="utf-8",
    )
    return rows
