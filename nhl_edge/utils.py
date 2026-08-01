from __future__ import annotations

import math
import re
import sys
import unicodedata
from typing import Any


def norm_team(s: str) -> str:
    s = (s or "").strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def team_eq(a: str, b: str) -> bool:
    na, nb = norm_team(a), norm_team(b)
    return bool(na) and na == nb


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def american_from_decimal(dec: float) -> str:
    if dec is None or dec <= 1:
        return ""
    if dec >= 2.0:
        return f"+{int(round((dec - 1) * 100))}"
    return str(int(round(-100 / (dec - 1))))


def safe_print(*args: Any, **kwargs: Any) -> None:
    """Print that never crashes Windows cp1252 consoles on curly dashes / unicode."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        parts = []
        for a in args:
            s = "" if a is None else str(a)
            parts.append(s.encode(enc, errors="replace").decode(enc, errors="replace"))
        sep = kwargs.get("sep", " ")
        print(sep.join(parts), **{k: v for k, v in kwargs.items() if k != "sep"})


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default
