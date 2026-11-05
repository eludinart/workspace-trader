from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import BotParams


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def build_feature_frame(df: pd.DataFrame, params: BotParams) -> pd.DataFrame:
    """Add technical indicators used as model features."""
    out = df.copy()
    close = out["close"]
    volume = out["volume"]

    out["return_1"] = close.pct_change()
    out["return_5"] = close.pct_change(5)
    out["rsi"] = _rsi(close, params.rsi_period)

    ema_fast = _ema(close, params.macd_fast)
    ema_slow = _ema(close, params.macd_slow)
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = _ema(out["macd"], params.macd_signal)
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    out["volume_ma"] = volume.rolling(params.volume_ma_period).mean()
    out["volume_ratio"] = volume / out["volume_ma"].replace(0, np.nan)

    out["sma_20"] = close.rolling(20).mean()
    out["sma_50"] = close.rolling(50).mean()
    out["price_sma20_ratio"] = close / out["sma_20"].replace(0, np.nan)
    out["sma_spread"] = (out["sma_20"] - out["sma_50"]) / close.replace(0, np.nan)

    high = out["high"]
    low = out["low"]
    out["volatility_20"] = out["return_1"].rolling(20).std()
    out["hl_range"] = (high - low) / close.replace(0, np.nan)

    return out


FEATURE_COLUMNS = [
    "return_1",
    "return_5",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "volume_ratio",
    "price_sma20_ratio",
    "sma_spread",
    "volatility_20",
    "hl_range",
]


def label_forward_returns(
    df: pd.DataFrame,
    horizon: int = 3,
    threshold: float = 0.002,
    *,
    path_aware: bool = True,
) -> pd.Series:
    """
    Label for supervised training over the next `horizon` bars:
      1 = buy  — upside reaches +threshold
      0 = hold
     -1 = sell — downside reaches -threshold

    When path_aware=True (default), use the forward high/low path so a TP-sized
    spike inside the window counts even if the close snaps back. If both sides
    hit, the side that moves farther (or buy on a tie) wins.
    Close-to-close labeling remains available with path_aware=False.
    """
    close = df["close"]
    labels = pd.Series(0, index=df.index, dtype=int)
    if horizon < 1 or threshold <= 0:
        return labels

    if not path_aware or "high" not in df.columns or "low" not in df.columns:
        fwd = close.shift(-horizon) / close - 1.0
        labels[fwd > threshold] = 1
        labels[fwd < -threshold] = -1
        return labels

    # Forward extrema excluding the current bar (decision at close of t).
    fut_high = (
        df["high"]
        .iloc[::-1]
        .rolling(horizon, min_periods=horizon)
        .max()
        .iloc[::-1]
        .shift(-1)
    )
    fut_low = (
        df["low"]
        .iloc[::-1]
        .rolling(horizon, min_periods=horizon)
        .min()
        .iloc[::-1]
        .shift(-1)
    )
    up = fut_high / close - 1.0
    down = fut_low / close - 1.0
    hit_up = up >= threshold
    hit_down = down <= -threshold
    both = hit_up & hit_down
    only_up = hit_up & ~hit_down
    only_down = hit_down & ~hit_up
    labels[only_up] = 1
    labels[only_down] = -1
    # Conflict: prefer the larger absolute excursion; buy on exact tie.
    if both.any():
        prefer_buy = up.abs() >= down.abs()
        labels[both & prefer_buy] = 1
        labels[both & ~prefer_buy] = -1
    return labels
