"""Trading style presets: prudent | intermediaire | agressif."""

from __future__ import annotations

from typing import Any, Literal

TradingStyle = Literal["prudent", "intermediaire", "agressif"]

STYLE_PRESETS: dict[TradingStyle, dict[str, Any]] = {
    "prudent": {
        "buy_threshold": 0.64,
        "sell_threshold": 0.52,
        # SL/TP above ~2.1% RT friction (intro taker 90bps ×2 + slip).
        "take_profit_pct": 0.05,
        "stop_loss_pct": 0.025,
        "risk_per_trade_pct": 0.008,
        "max_position_pct": 0.12,
        "position_size_pct": 0.08,
        "min_hold_min": 8,
        "max_open_symbols": 3,
        "capital_per_slot": 180.0,
        "atr_stop_mult": 1.6,
        "reward_risk": 2.0,
        "max_portfolio_risk_r": 4.0,
        "universe_size": 25,
        "universe_min_volume_usd": 75_000_000.0,
        "add_cooldown_min": 30,
        "aggressive_sell_confidence": 0.72,
        "decision_interval_sec": 30,
        "label_horizon_bars": 4,
        "label_threshold_pct": 0.05,
        "label_path_aware": True,
    },
    "intermediaire": {
        # Slightly lower so paper can open more often under intro-taker friction.
        "buy_threshold": 0.58,
        "sell_threshold": 0.50,
        "take_profit_pct": 0.045,
        "stop_loss_pct": 0.022,
        "risk_per_trade_pct": 0.01,
        "max_position_pct": 0.15,
        "position_size_pct": 0.10,
        "min_hold_min": 5,
        "max_open_symbols": 4,
        "capital_per_slot": 140.0,
        "atr_stop_mult": 1.45,
        "reward_risk": 2.0,
        "max_portfolio_risk_r": 5.0,
        "universe_size": 30,
        "universe_min_volume_usd": 50_000_000.0,
        "add_cooldown_min": 25,
        "aggressive_sell_confidence": 0.70,
        "decision_interval_sec": 25,
        "label_horizon_bars": 4,
        "label_threshold_pct": 0.045,
        "label_path_aware": True,
    },
    "agressif": {
        "buy_threshold": 0.58,
        "sell_threshold": 0.51,
        "take_profit_pct": 0.04,
        "stop_loss_pct": 0.02,
        "risk_per_trade_pct": 0.015,
        "max_position_pct": 0.18,
        "position_size_pct": 0.12,
        "min_hold_min": 5,
        "max_open_symbols": 5,
        "capital_per_slot": 120.0,
        "atr_stop_mult": 1.3,
        "reward_risk": 2.0,
        "max_portfolio_risk_r": 6.0,
        "universe_size": 40,
        "universe_min_volume_usd": 35_000_000.0,
        "add_cooldown_min": 15,
        "aggressive_sell_confidence": 0.65,
        "decision_interval_sec": 20,
        "label_horizon_bars": 6,
        "label_threshold_pct": 0.04,
        "label_path_aware": True,
    },
}


def normalize_style(value: Any) -> TradingStyle:
    # Guard: JSON null / NaN / numeric junk from bad form coercion → default.
    if value is None or isinstance(value, bool):
        return "intermediaire"
    if isinstance(value, (int, float)):
        return "intermediaire"
    raw = str(value or "intermediaire").strip().lower()
    if raw in {"", "nan", "none", "null"}:
        return "intermediaire"
    aliases = {
        "prudent": "prudent",
        "safe": "prudent",
        "conservateur": "prudent",
        "intermediaire": "intermediaire",
        "intermédiaire": "intermediaire",
        "medium": "intermediaire",
        "normal": "intermediaire",
        "agressif": "agressif",
        "aggressive": "agressif",
        "aggro": "agressif",
    }
    style = aliases.get(raw, "intermediaire")
    if style not in STYLE_PRESETS:
        return "intermediaire"
    return style  # type: ignore[return-value]


def apply_style_preset(data: dict[str, Any], style: TradingStyle) -> dict[str, Any]:
    """Merge style preset into a BotParams dict (style wins for controlled keys)."""
    out = dict(data)
    out["trading_style"] = style
    out.update(STYLE_PRESETS[style])
    return out


def scan_gates(style: TradingStyle) -> dict[str, float]:
    """Numeric gates used by the universe entry scanner."""
    if style == "prudent":
        return {
            "rsi_extra": 3.0,
            "sell_cap": 0.55,
            "min_buy_p_rsi": 0.25,
            "min_buy_p_edge": 0.48,
            "edge_gap": 0.12,
            "macd_rsi_lo": 38.0,
            "macd_rsi_hi": 50.0,
            "min_buy_p_macd": 0.32,
            "news_score": 0.35,
            "news_rsi_cap": 55.0,
            "allow_macd": 0.0,
            "allow_weak_prob": 0.0,
        }
    if style == "agressif":
        return {
            "rsi_extra": 10.0,
            "sell_cap": 0.75,
            "min_buy_p_rsi": 0.12,
            "min_buy_p_edge": 0.28,
            "edge_gap": 0.04,
            "macd_rsi_lo": 30.0,
            "macd_rsi_hi": 62.0,
            "min_buy_p_macd": 0.18,
            "news_score": 0.15,
            "news_rsi_cap": 70.0,
            "allow_macd": 1.0,
            "allow_weak_prob": 1.0,
        }
    return {
        "rsi_extra": 5.0,
        "sell_cap": 0.65,
        "min_buy_p_rsi": 0.18,
        "min_buy_p_edge": 0.40,
        "edge_gap": 0.08,
        "macd_rsi_lo": 35.0,
        "macd_rsi_hi": 55.0,
        "min_buy_p_macd": 0.25,
        "news_score": 0.25,
        "news_rsi_cap": 60.0,
        "allow_macd": 1.0,
        "allow_weak_prob": 0.0,
    }
