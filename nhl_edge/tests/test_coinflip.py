from nhl_edge.edge import _is_coinflip, build_candidates


def test_coinflip_near_zero_margin():
    assert _is_coinflip(0.0) is True
    assert _is_coinflip(0.2) is True
    assert _is_coinflip(-0.2) is True


def test_coinflip_clear_side():
    assert _is_coinflip(0.6) is False
    assert _is_coinflip(-0.4) is False


def test_coinflip_blocks_sides_keeps_total():
    games = [
        {
            "home": "Boston Bruins",
            "away": "New York Rangers",
            "kickoff_et": "7:00 PM ET",
            "prices": {
                "home": "Boston Bruins",
                "away": "New York Rangers",
                "ml_home": 1.91,
                "ml_away": 1.91,
                "total_line": 6.5,
                "total_over": 1.91,
                "total_under": 1.91,
                "spread_line": -1.5,
                "spread_home": 1.91,
                "spread_away": 1.91,
                "commence_time": "2026-10-27T23:00:00Z",
            },
            "model": {
                "exp_margin": 0.1,
                "exp_total": 6.9,
                "p_home": 0.51,
                "p_away": 0.49,
                "p_over": 0.62,
                "p_under": 0.38,
                "p_home_cover": 0.51,
                "p_away_cover": 0.49,
            },
            "talent": {"notes": {}},
        }
    ]
    cands = build_candidates(games)
    assert cands, "the mispriced total must survive"
    assert all(c["market"] == "Total" for c in cands)
