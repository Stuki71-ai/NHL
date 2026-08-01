from nhl_edge.brain import SYS, _kickoff_ok, _validate_against_shortlist


def test_composer_rules_match_us_edge_bar():
    """Subscriber brand bar — Vic Mackey / QoQ / no secrets / no punter jargon."""
    assert "quality over quantity" in SYS
    assert "Vic Mackey" in SYS
    assert "0 is allowed" in SYS or "[]" in SYS
    assert "max 1 pick per match" in SYS.lower()
    assert "damn, I didn't know that" in SYS or "damn-I-didn't-know-that" in SYS
    assert "selection strategy secrets" in SYS or "strategy secrets" in SYS
    assert "punter jargon" in SYS.lower()
    assert "paid subscribers" in SYS
    assert "DO NOT repeat yourself" in SYS or "do not repeat yourself" in SYS.lower()
    # Paid-sub priority: hit rate > long-horizon EV
    assert "win probability" in SYS
    assert "long-term" in SYS or "long-horizon" in SYS
    # EDGE family rationale length (NIGHT rules, 80–100 word band)
    assert "80–100" in SYS or "80-100" in SYS
    assert "HARD" in SYS
    assert "STARTS ON SUBSTANCE" in SYS or "START ON SUBSTANCE" in SYS
    # strategy-leak bans stay in place; glanceable fan data is allowed
    assert "Corsi" in SYS and "xG" in SYS and "Poisson" in SYS
    assert "recreational bettor" in SYS.lower() or "at a glance" in SYS.lower()
    # NHL-specific: composer is the goalie availability veto
    assert "AVAILABILITY VETO" in SYS
    assert "GOALIES" in SYS or "goalie" in SYS.lower()


def test_composer_models_opus_then_gpt_sol():
    from pathlib import Path

    from nhl_edge import config

    assert config.COMPOSER_PRIMARY_MODEL == "claude-opus-5"
    assert config.COMPOSER_PRIMARY_EFFORT == "max"
    assert config.COMPOSER_ATTEMPTS_PRIMARY == 3
    assert config.COMPOSER_FALLBACK_MODEL == "gpt-5.6-sol"
    assert config.COMPOSER_FALLBACK_EFFORT == "high"
    brain = (Path(__file__).resolve().parents[1] / "brain.py").read_text(encoding="utf-8")
    assert "_call_anthropic" in brain
    assert "_call_openai" in brain
    assert "COMPOSER_PRIMARY_MODEL" in brain
    assert "COMPOSER_FALLBACK_MODEL" in brain
    assert "api.anthropic.com/v1/messages" in brain
    assert "api.openai.com/v1/responses" in brain
    # Official Opus 5: adaptive thinking explicit; effort max; no sampling params
    assert '"type": "adaptive"' in brain or "'type': 'adaptive'" in brain
    assert "thinking" in brain
    assert "output_config" in brain
    assert "64000" in brain
    # Payload must not set sampling params (Opus 5 400); word may appear in comments only
    anth_fn = brain.split("def _call_anthropic")[1].split("def _call_openai")[0]
    assert '"temperature"' not in anth_fn
    assert "'temperature'" not in anth_fn
    assert '"top_p"' not in anth_fn


def _shortlist_item(**overrides):
    base = {
        "home": "Milwaukee Brewers",
        "away": "Colorado Rockies",
        "match": "Colorado Rockies @ Milwaukee Brewers",
        "market": "Total",
        "selection_struct": "OVER 7.5",
        "pick_name": "Over 7.5",
        "odds_dec": 2.02,
        "odds_us": "+102",
        "edge": 0.27,
        "model_p": 0.63,
        "kickoff_et": "2:11 PM ET",
    }
    base.update(overrides)
    return base


def test_kickoff_ok():
    assert _kickoff_ok("2:11 PM ET") is True
    assert _kickoff_ok("") is False
    assert _kickoff_ok("TBD") is False
    assert _kickoff_ok(None) is False


def test_validate_exact_selection_only():
    sl = [_shortlist_item()]
    # composer invents UNDER — must reject (not remap to OVER)
    bad = [
        {
            "home": "Milwaukee Brewers",
            "away": "Colorado Rockies",
            "market": "Total",
            "selection_struct": "UNDER 7.5",
            "rationale": "nope",
        }
    ]
    assert _validate_against_shortlist(bad, sl, "2026-07-26") == []

    good = [
        {
            "home": "Milwaukee Brewers",
            "away": "Colorado Rockies",
            "market": "Total",
            "selection_struct": "OVER 7.5",
            "rationale": "real",
        }
    ]
    out = _validate_against_shortlist(good, sl, "2026-07-26")
    assert len(out) == 1
    assert out[0]["odds_dec"] == 2.02
    assert out[0]["selection_struct"] == "OVER 7.5"


def test_validate_rejects_tbd_kickoff():
    sl = [_shortlist_item(kickoff_et="TBD")]
    picks = [
        {
            "home": "Milwaukee Brewers",
            "away": "Colorado Rockies",
            "market": "Total",
            "selection_struct": "OVER 7.5",
            "rationale": "x",
        }
    ]
    assert _validate_against_shortlist(picks, sl, "2026-07-26") == []
