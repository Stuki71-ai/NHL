"""EDGE family policy markers (US EDGE lead)."""
from pathlib import Path

import nhl_edge.pipeline as pipeline


ROOT = Path(__file__).resolve().parents[2]


def test_standing_order_in_pipeline_source():
    src = (ROOT / "nhl_edge" / "pipeline.py").read_text(encoding="utf-8")
    assert "solely responsible for autonomous resolution" in src
    assert "production-critical" in src
    assert "DELIVERY_ATTEMPTS" in src


def test_delivery_retries_config():
    assert pipeline.DELIVERY_ATTEMPTS >= 3


def test_ops_doc_has_standing_order():
    ops = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "solely responsible for autonomous resolution" in ops
    assert "ALL THREE" in ops


def test_mackey_brand_in_brain_and_ops():
    brain = (ROOT / "nhl_edge" / "brain.py").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    for src in (brain, ops):
        assert "Vic Mackey" in src
        assert "ONE verifiable fact" in src
        assert "damn, I didn't know that" in src or "damn I didn't know that" in src
        assert "punter jargon" in src
        assert "selling factors" in src
