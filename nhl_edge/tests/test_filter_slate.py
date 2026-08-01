from datetime import datetime, timezone

from nhl_edge.slate import filter_slate


def _ev(iso: str, home: str = "A", away: str = "B") -> dict:
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": iso,
        "bookmakers": [],
    }


def test_live_includes_all_remaining_same_et_day():
    # Fixed "now": 2026-07-27 16:45 ET = 20:45 UTC (EDT)
    now = datetime(2026, 7, 27, 20, 45, 0, tzinfo=timezone.utc)
    events = [
        _ev("2026-07-27T18:00:00Z", "Early", "Gone"),  # 14:00 ET — already started
        _ev("2026-07-27T22:10:00Z", "Phillies", "Mets"),  # 18:10 ET — still open
        _ev("2026-07-28T02:40:00Z", "Dodgers", "Giants"),  # 22:40 ET same day — late
        _ev("2026-07-28T17:00:00Z", "Tomorrow", "Skip"),  # next ET day
    ]
    out = filter_slate(events, min_minutes=15, now_utc=now)
    homes = {e["home_team"] for e in out}
    assert homes == {"Phillies", "Dodgers"}
    assert "Early" not in homes
    assert "Tomorrow" not in homes


def test_live_no_hours_ahead_cap_drops_late_game():
    """Regression: old hours_ahead=20 must not exclude late same-day games from early slot."""
    # Weekend early slot 11:45 ET = 15:45 UTC
    now = datetime(2026, 7, 25, 15, 45, 0, tzinfo=timezone.utc)  # Sat
    events = [
        _ev("2026-07-26T02:10:00Z", "LateNight", "West"),  # 22:10 ET Sat
        _ev("2026-07-25T19:00:00Z", "Afternoon", "East"),  # 15:00 ET Sat
    ]
    out = filter_slate(events, min_minutes=15, now_utc=now)
    homes = {e["home_team"] for e in out}
    assert "LateNight" in homes
    assert "Afternoon" in homes


def test_historical_full_day():
    events = [
        _ev("2026-07-26T17:00:00Z"),
        _ev("2026-07-26T23:00:00Z"),
        _ev("2026-07-27T17:00:00Z"),
    ]
    out = filter_slate(events, date_et="2026-07-26")
    assert len(out) == 2
