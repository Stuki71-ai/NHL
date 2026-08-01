from nhl_edge.config import LEAGUE_GPG
from nhl_edge.model import model_game, score_matrix
from nhl_edge.ratings import build_game_talent, league_averages, match_team


def _flat_ratings() -> dict:
    """Two exactly-average teams."""
    mk = lambda name: {
        "team": name,
        "off_mult": 1.0,
        "def_mult": 1.0,
        "gp": 82,
        "league_gpg": LEAGUE_GPG,
        "blend_w_current": 1.0,
    }
    return {"teama": mk("Team A"), "teamb": mk("Team B")}


def test_score_matrix_normalized():
    mat = score_matrix(3.0, 2.8)
    assert abs(sum(sum(r) for r in mat) - 1.0) < 1e-9


def test_probs_sum_to_one():
    tal = build_game_talent("Team A", "Team B", _flat_ratings(), {"gpg": LEAGUE_GPG})
    mo = model_game(tal, 6.5, -1.5)
    assert abs(mo["p_home"] + mo["p_away"] - 1.0) < 1e-9
    assert abs(mo["p_over"] + mo["p_under"] - 1.0) < 1e-9
    assert abs(mo["p_home_cover"] + mo["p_away_cover"] - 1.0) < 1e-9


def test_hca_makes_even_matchup_favor_home():
    tal = build_game_talent("Team A", "Team B", _flat_ratings(), {"gpg": LEAGUE_GPG})
    mo = model_game(tal, None, None)
    assert mo["exp_margin"] > 0.15  # net home edge ~ +0.25 goals
    assert mo["p_home"] > 0.52


def test_b2b_penalises_margin():
    r = _flat_ratings()
    base = model_game(build_game_talent("Team A", "Team B", r, {"gpg": LEAGUE_GPG}), None, None)
    b2b = model_game(
        build_game_talent("Team A", "Team B", r, {"gpg": LEAGUE_GPG}, home_b2b=True),
        None,
        None,
    )
    assert b2b["exp_margin"] < base["exp_margin"] - 0.2


def test_better_offense_raises_total_and_margin():
    r = _flat_ratings()
    r["teama"] = dict(r["teama"], off_mult=1.15)
    good = model_game(build_game_talent("Team A", "Team B", r, {"gpg": LEAGUE_GPG}), 6.0, None)
    flat = model_game(
        build_game_talent("Team A", "Team B", _flat_ratings(), {"gpg": LEAGUE_GPG}), 6.0, None
    )
    assert good["exp_margin"] > flat["exp_margin"] + 0.3
    assert good["exp_total"] > flat["exp_total"] + 0.3
    assert good["p_over"] > flat["p_over"]


def test_puck_line_cover_favors_dog_plus_15():
    tal = build_game_talent("Team A", "Team B", _flat_ratings(), {"gpg": LEAGUE_GPG})
    mo = model_game(tal, None, -1.5)  # home laying 1.5 in a near-even game
    assert mo["p_away_cover"] > mo["p_home_cover"]  # +1.5 covers far more often


def test_match_team_fallback_is_league_average():
    t = match_team("Nonexistent Team", {})
    assert t["off_mult"] == 1.0 and t["def_mult"] == 1.0
    assert t["league_gpg"] == LEAGUE_GPG


def test_league_averages_empty_table():
    la = league_averages({})
    assert la["gpg"] == LEAGUE_GPG


def test_talent_notes_carry_mults_and_b2b():
    r = _flat_ratings()
    r["teama"] = dict(r["teama"], off_mult=1.1, def_mult=0.92)
    tal = build_game_talent("Team A", "Team B", r, {"gpg": LEAGUE_GPG}, away_b2b=True)
    n = tal["notes"]
    assert n["home_off_mult"] == 1.1
    assert n["home_def_mult"] == 0.92
    assert n["away_b2b"] is True
    assert n["home_b2b"] is False
