"""Daily paper trading digest (Europe/Paris calendar day)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.wallet import Trade

PARIS = ZoneInfo("Europe/Paris")


def parse_paris_date(raw: str | None) -> date:
    if not raw or not str(raw).strip():
        return datetime.now(PARIS).date()
    return date.fromisoformat(str(raw).strip()[:10])


def paris_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=PARIS)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _as_utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _in_window(ts: datetime | None, start: datetime, end: datetime) -> bool:
    u = _as_utc(ts)
    if u is None:
        return False
    return start <= u < end


def _fail_note(note: str) -> bool:
    n = (note or "").lower()
    keys = ("reject", "fail", "unfilled", "insufficient_depth", "dust", "duplicate_skip")
    return any(k in n for k in keys)


def build_daily_report(
    *,
    day: date,
    trades: list[Trade],
    positions: dict[str, Any],
    prices: dict[str, float],
    cash: float,
    unrealized_pnl: float,
    equity_points: list[dict[str, Any]],
    fidelity: dict[str, Any] | None,
    killed: bool,
    kill_reason: str | None,
    journal_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    start, end = paris_day_bounds(day)
    day_trades = [t for t in trades if _in_window(t.ts, start, end)]
    buys = [t for t in day_trades if t.side == "buy"]
    sells = [t for t in day_trades if t.side == "sell"]

    realized = sum(float(t.realized_pnl) for t in sells)
    fees = sum(float(t.fee) for t in day_trades)
    symbols = sorted({t.symbol for t in day_trades if t.symbol})

    fail_examples: list[dict[str, Any]] = []
    fail_count = 0
    for t in day_trades:
        if _fail_note(t.note):
            fail_count += 1
            if len(fail_examples) < 8:
                fail_examples.append(
                    {
                        "ts": t.ts.isoformat(),
                        "symbol": t.symbol,
                        "side": t.side,
                        "note": t.note,
                        "fee": float(t.fee),
                    }
                )

    twin_rejects = int((fidelity or {}).get("reject_count") or 0)
    twin_partials = int((fidelity or {}).get("partial_count") or 0)

    journal_fails: list[dict[str, Any]] = []
    for entry in journal_entries or []:
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else None
        except ValueError:
            ts = None
        if not _in_window(ts, start, end):
            continue
        note = str(entry.get("note") or entry.get("explanation") or "")
        action = str(entry.get("action") or "")
        if _fail_note(note) or action in {"error", "reject"}:
            fail_count += 1
            if len(journal_fails) < 5:
                journal_fails.append(
                    {
                        "ts": ts.isoformat() if ts else None,
                        "symbol": entry.get("symbol"),
                        "action": action,
                        "note": note[:120],
                    }
                )

    open_rows: list[dict[str, Any]] = []
    for sym, pos in positions.items():
        qty = float(getattr(pos, "qty", 0) or 0)
        avg = float(getattr(pos, "avg_entry", 0) or 0)
        mark = float(prices.get(sym, avg) or avg)
        open_rows.append(
            {
                "symbol": sym,
                "qty": qty,
                "avg_entry": avg,
                "mark": mark,
                "unrealized_pnl": (mark - avg) * qty if qty else 0.0,
            }
        )

    eq_day = []
    for p in equity_points:
        if not isinstance(p, dict):
            continue
        try:
            ts = datetime.fromisoformat(str(p.get("t")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if _in_window(ts, start, end):
            eq_day.append(p)

    equity_start = float(eq_day[0]["equity"]) if eq_day else None
    equity_end = float(eq_day[-1]["equity"]) if eq_day else None
    max_dd = 0.0
    for p in eq_day:
        try:
            max_dd = max(max_dd, float(p.get("drawdown_pct") or 0))
        except (TypeError, ValueError):
            continue

    return {
        "date": day.isoformat(),
        "timezone": "Europe/Paris",
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "realized": {
            "pnl": realized,
            "sells": len(sells),
            "buys": len(buys),
        },
        "open": {
            "unrealized_pnl": float(unrealized_pnl),
            "cash": float(cash),
            "positions": open_rows,
            "count": len(open_rows),
        },
        "fees": {"total": fees, "trade_count": len(day_trades)},
        "failures": {
            "count": fail_count,
            "twin_rejects": twin_rejects,
            "twin_partials": twin_partials,
            "examples": fail_examples,
            "journal": journal_fails,
        },
        "activity": {
            "symbols": symbols,
            "trade_count": len(day_trades),
            "killed": bool(killed),
            "kill_reason": kill_reason,
        },
        "equity": {
            "start": equity_start,
            "end": equity_end,
            "max_drawdown_pct": max_dd,
            "samples": len(eq_day),
        },
    }


def digest_push_body(report: dict[str, Any]) -> str:
    realized = float(report.get("realized", {}).get("pnl") or 0)
    fees = float(report.get("fees", {}).get("total") or 0)
    fails = int(report.get("failures", {}).get("count") or 0)
    open_n = int(report.get("open", {}).get("count") or 0)
    sign = "+" if realized >= 0 else ""
    return (
        f"Réalisé {sign}{realized:.2f}$ · frais {fees:.2f}$ · "
        f"{open_n} pos. · échecs {fails}"
    )
