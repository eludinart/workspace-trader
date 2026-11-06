from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.fees import FeeTier, normalize_fee_tier, resolve_fee_bps
from app.trading_style import TradingStyle, normalize_style

DEFAULT_SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]


def coerce_symbols(value: Any) -> list[str]:
    """Accept a symbol list or a comma-separated string. Empty falls back to the default book."""
    if value is None:
        return list(DEFAULT_SYMBOLS)
    if isinstance(value, str):
        raw_parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_parts: list[Any] = []
        for item in value:
            if item is None:
                continue
            raw_parts.extend(str(item).split(","))
    else:
        raise ValueError("symbols must be a list or comma-separated string")

    out: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        sym = str(part).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out or list(DEFAULT_SYMBOLS)


def _as_fraction(value: Any) -> Any:
    """0.05 and 5 both mean 5%. Numbers above 1 are treated as percents."""
    if value is None or isinstance(value, bool):
        return value
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    if num > 1.0:
        return num / 100.0
    return num


class AlertParams(BaseModel):
    """Alert channels and thresholds (token/SMTP secrets preferably via env)."""

    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    telegram_bot_token: str = ""

    email_enabled: bool = False
    email_to: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    alert_on_trade: bool = True
    alert_on_pnl: bool = True
    alert_on_strong_signal: bool = True
    pnl_alert_threshold: float = Field(default=100.0, ge=0)
    strong_signal_threshold: float = Field(default=0.75, ge=0.5, le=1.0)


class BotParams(BaseModel):
    """Configurable bot and model parameters."""

    symbol: str = "BTC-USD"
    symbols: list[str] = Field(default_factory=lambda: list(DEFAULT_SYMBOLS))
    initial_balance: float = Field(default=10_000.0, gt=0)
    position_size_pct: float = Field(default=0.1, gt=0, le=1.0)
    decision_interval_sec: int = Field(default=30, ge=10, le=3600)
    rsi_period: int = Field(default=14, ge=2, le=100)
    rsi_oversold: float = Field(default=30.0, ge=0, le=50)
    rsi_overbought: float = Field(default=70.0, ge=50, le=100)
    macd_fast: int = Field(default=12, ge=2)
    macd_slow: int = Field(default=26, ge=3)
    macd_signal: int = Field(default=9, ge=2)
    volume_ma_period: int = Field(default=20, ge=2)
    trading_style: TradingStyle = "intermediaire"
    # Defaults match intermediaire; switching trading_style reapplies the preset.
    buy_threshold: float = Field(default=0.58, ge=0.2, le=1.0)
    sell_threshold: float = Field(default=0.50, ge=0.2, le=1.0)
    candle_granularity_sec: int = Field(default=3600, ge=60)
    candle_lookback: int = Field(default=300, ge=50, le=1000)
    auto_trade: bool = True
    # Execution twin (shadow): walk real Coinbase L2; fee_bps aliases taker.
    execution_twin_enabled: bool = True
    fee_tier: FeeTier = "us_intro"
    maker_fee_bps: float = Field(default=50.0, ge=0)
    taker_fee_bps: float = Field(default=90.0, ge=0)
    fee_bps: float = Field(default=90.0, ge=0)  # wallet fee = taker when twin on
    slippage_bps: float = Field(default=5.0, ge=0)  # legacy / fee_guard only when twin
    # Decision → fill latency (ms). Twin always re-fetches book after this delay.
    latency_ms: int = Field(default=250, ge=0, le=5000)
    latency_wall_clock: bool = True  # twin: sleep then re-quote L2
    order_impact_enabled: bool = False
    impact_bps_max: float = Field(default=5.0, ge=0, le=50)
    twin_min_fill_notional: float = Field(default=15.0, ge=1.0)
    # Signal feature stubs — all false; see app.signals.flags
    confluence_enabled: bool = False
    mtf_enabled: bool = False
    kelly_enabled: bool = False
    onchain_enabled: bool = False
    super_opp_enabled: bool = False
    passive_wake_enabled: bool = False
    post_trade_ai_enabled: bool = False
    web_push_enabled: bool = False
    kill_switch_enabled: bool = True
    max_daily_loss_pct: float = Field(default=5.0, ge=0.1, le=100)
    max_drawdown_pct: float = Field(default=15.0, ge=0.1, le=100)
    # TP above round-trip (~0.3%) but reachable; SL tighter to cut losers.
    take_profit_pct: float = Field(default=0.018, ge=0, le=1)
    stop_loss_pct: float = Field(default=0.009, ge=0, le=1)
    # Concentrate capital on fewer, better lines (~15% equity max).
    max_position_pct: float = Field(default=0.15, gt=0, le=1)
    # Hard ceiling 2% capital/trade (AI / Kelly cannot raise this).
    risk_per_trade_pct: float = Field(default=0.012, gt=0, le=0.02)
    # Paper order-book depth walk (lot 8); off keeps classic slip-only fills.
    order_book_depth_enabled: bool = False
    order_book_depth_levels: int = Field(default=5, ge=1, le=20)
    atr_stop_mult: float = Field(default=1.35, ge=1.0, le=4.0)
    reward_risk: float = Field(default=2.0, ge=1.0, le=5.0)
    min_hold_min: int = Field(default=3, ge=0, le=24 * 60)
    add_cooldown_min: int = Field(default=20, ge=0, le=24 * 60)
    max_open_symbols: int = Field(default=6, ge=1, le=30)
    capital_per_slot: float = Field(default=110.0, ge=20.0, le=500.0)
    max_open_hard_cap: int = Field(default=20, ge=5, le=50)
    max_portfolio_risk_r: float = Field(default=6.0, ge=1.0, le=30.0)
    universe_enabled: bool = True
    universe_size: int = Field(default=40, ge=10, le=80)
    universe_min_volume_usd: float = Field(default=2_000_000.0, ge=0)
    universe_refresh_hours: float = Field(default=6.0, ge=1.0, le=48.0)
    aggressive_sell_confidence: float = Field(default=0.75, ge=0.5, le=1.0)
    # XGB label window: horizon bars × move threshold (aligned with TP regime).
    label_horizon_bars: int = Field(default=3, ge=1, le=48)
    label_threshold_pct: float = Field(default=0.002, gt=0, le=0.2)
    label_path_aware: bool = True

    @field_validator("trading_style", mode="before")
    @classmethod
    def _clean_style(cls, value: Any) -> str:
        return normalize_style(value)

    @field_validator("fee_tier", mode="before")
    @classmethod
    def _clean_fee_tier(cls, value: Any) -> str:
        return normalize_fee_tier(value)

    @field_validator("symbol")
    @classmethod
    def _clean_symbol(cls, value: str) -> str:
        text = str(value).strip().upper()
        return text or "BTC-USD"

    @field_validator("symbols", mode="before")
    @classmethod
    def _clean_symbols(cls, value: Any) -> list[str]:
        return coerce_symbols(value)

    @field_validator("take_profit_pct", "stop_loss_pct", "max_position_pct", mode="before")
    @classmethod
    def _clean_fractions(cls, value: Any) -> Any:
        return _as_fraction(value)

    @model_validator(mode="after")
    def _sync_fee_tier(self) -> BotParams:
        """Presets overwrite maker/taker; fee_bps always tracks taker for wallet fills."""
        maker, taker = resolve_fee_bps(
            self.fee_tier,
            maker_fee_bps=self.maker_fee_bps,
            taker_fee_bps=self.taker_fee_bps,
        )
        self.maker_fee_bps = maker
        self.taker_fee_bps = taker
        self.fee_bps = taker
        return self
