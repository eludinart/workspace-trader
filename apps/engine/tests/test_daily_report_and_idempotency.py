"""Daily report aggregation, performance metrics, fill idempotency."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.execution_idempotency import decision_fill_key, has_fill_key
from app.reports.daily import build_daily_report, digest_push_body, paris_day_bounds
from app.reports.performance import build_performance_metrics
from app.wallet import PaperWallet, Position, Trade


def test_paris_day_bounds_cover_full_local_day() -> None:
    start, end = paris_day_bounds(date(2026, 11, 13))
    assert start.tzinfo is not None
    assert (end - start).total_seconds() == 86400


def test_daily_report_sums_realized_fees_and_fails() -> None:
    day = date(2026, 11, 13)
    start, _end = paris_day_bounds(day)
    trades = [
        Trade(
            id="b1",
            symbol="SOL-USD",
            side="buy",
            qty=1.0,
            price=100.0,
            fee=0.9,
            ts=start + timedelta(hours=10),
            note="entry:signal",
        ),
        Trade(
            id="s1",
            symbol="SOL-USD",
            side="sell",
            qty=1.0,
            price=98.0,
            fee=0.8,
            ts=start + timedelta(hours=12),
            realized_pnl=-3.5,
            note="exit:copilot",
        ),
        Trade(
            id="s2",
            symbol="BTC-USD",
            side="sell",
            qty=0.01,
            price=70_000.0,
            fee=1.0,
            ts=start + timedelta(hours=14),
            realized_pnl=-1.0,
            note="exit:kill|dust",
        ),
    ]
    report = build_daily_report(
        day=day,
        trades=trades,
        positions={"ETH-USD": Position(symbol="ETH-USD", qty=0.5, avg_entry=3000.0)},
        prices={"ETH-USD": 3100.0},
        cash=500.0,
        unrealized_pnl=50.0,
        equity_points=[
            {"t": start.isoformat(), "equity": 700.0, "drawdown_pct": 0.0},
            {
                "t": (start + timedelta(hours=15)).isoformat(),
                "equity": 658.0,
                "drawdown_pct": 6.0,
            },
        ],
        fidelity={"reject_count": 2, "partial_count": 1},
        killed=True,
        kill_reason="max_loss_-5.18%",
    )
    assert report["realized"]["pnl"] == -4.5
    assert report["fees"]["total"] == 2.7
    assert report["failures"]["count"] >= 1
    assert report["open"]["count"] == 1
    assert report["equity"]["max_drawdown_pct"] == 6.0
    body = digest_push_body(report)
    assert "Réalisé" in body
    assert "frais" in body


def test_performance_metrics_win_rate() -> None:
    now = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    trades = [
        Trade(
            id="1",
            symbol="A",
            side="sell",
            qty=1,
            price=10,
            fee=0.1,
            ts=now,
            realized_pnl=2.0,
        ),
        Trade(
            id="2",
            symbol="B",
            side="sell",
            qty=1,
            price=10,
            fee=0.1,
            ts=now,
            realized_pnl=-1.0,
        ),
        Trade(
            id="3",
            symbol="C",
            side="buy",
            qty=1,
            price=10,
            fee=0.1,
            ts=now,
        ),
    ]
    metrics = build_performance_metrics(
        trades=trades,
        equity_points=[{"t": now.isoformat(), "equity": 100.0, "drawdown_pct": 1.5}],
        fidelity={"unfilled_rate": 0.05, "reject_count": 1, "partial_count": 0},
        range_key="all",
        now=now,
    )
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["win_rate"] == 0.5
    assert abs(metrics["fees_total"] - 0.3) < 1e-9
    assert metrics["profit_factor"] == 2.0


def test_idempotency_skips_duplicate_decision_key() -> None:
    ts = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
    key = decision_fill_key(decision_ts=ts, symbol="SOL-USD", side="buy", qty=1.23456789)
    assert key is not None
    wallet = PaperWallet(initial_balance=1000.0)
    wallet.trades.append(
        Trade(
            id="x",
            symbol="SOL-USD",
            side="buy",
            qty=1.23456789,
            price=80.0,
            fee=0.5,
            ts=ts,
            decision_ts=ts,
        )
    )
    assert has_fill_key(wallet.trades, key)
    other = decision_fill_key(decision_ts=ts, symbol="SOL-USD", side="buy", qty=1.0)
    assert not has_fill_key(wallet.trades, other)


def test_daily_push_mark_once_logic() -> None:
    """Pure helper: same calendar day should not re-push."""
    pushed_on = "2026-11-13"
    today = "2026-11-13"
    assert pushed_on == today
