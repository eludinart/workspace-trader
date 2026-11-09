"""Co-pilot JSON parse + soft-fail rules."""

from __future__ import annotations

from app.llm.copilot import parse_copilot_response
from app.llm.free_pool import FreePoolResult


def test_parse_approve() -> None:
    d = parse_copilot_response(
        '{"action":"approve","size_mult":1,"reason_fr":"Edge ok"}',
    )
    assert d.action == "approve"
    assert d.size_mult == 1.0
    assert "Edge" in d.reason_fr
    assert d.soft_fail is False


def test_parse_resize_clamped() -> None:
    d = parse_copilot_response(
        '{"action":"resize","size_mult":2.5,"reason_fr":"Trop gros"}',
    )
    # 2.5 → clamp 1.0 → treated as approve (no real resize)
    assert d.action == "approve"
    assert d.size_mult == 1.0


def test_parse_resize_half() -> None:
    d = parse_copilot_response(
        '{"action":"resize","size_mult":0.5,"reason_fr":"Réduire"}',
    )
    assert d.action == "resize"
    assert d.size_mult == 0.5


def test_force_exit_blocked_on_entry() -> None:
    d = parse_copilot_response(
        '{"action":"force_exit","size_mult":1,"reason_fr":"Sortir"}',
        allow_force_exit=False,
    )
    assert d.action == "veto"


def test_invalid_json_soft_fail() -> None:
    meta = FreePoolResult(
        text="not json",
        provider="groq",
        model="x",
        latency_ms=12,
        hop_id="groq-1",
        ok=True,
    )
    d = parse_copilot_response("bonjour", meta=meta)
    assert d.soft_fail is True
    assert d.action == "approve"
    assert d.size_mult == 1.0
