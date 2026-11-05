from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from app.config import BotParams
from app.news.models import NewsBrief
from app.strategy.features import FEATURE_COLUMNS, build_feature_frame, label_forward_returns

# Positive news eases the buy threshold and tightens the sell threshold, by at most 4pp.
_NEWS_THRESHOLD_SHIFT = 0.04


@dataclass
class SignalResult:
    action: str  # buy | sell | hold
    confidence: float
    probs: dict[str, float]
    features: dict[str, float]
    model_ready: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "probs": self.probs,
            "features": self.features,
            "model_ready": self.model_ready,
            "detail": self.detail,
        }


class XGBoostStrategy:
    """v1 XGBoost classifier on technical features (retrained from recent candles).

    News scores are fused only at decision time. They are not training columns:
    the candle history has no matching news series, so a constant news column
    would not teach the model anything.
    """

    CLASS_MAP = {-1: "sell", 0: "hold", 1: "buy"}

    def __init__(self) -> None:
        self.model: XGBClassifier | None = None
        self.last_train_rows: int = 0
        self.last_accuracy: float | None = None

    def train(self, candles: pd.DataFrame, params: BotParams) -> dict[str, Any]:
        featured = build_feature_frame(candles, params)
        horizon = int(getattr(params, "label_horizon_bars", 3) or 3)
        threshold = float(getattr(params, "label_threshold_pct", 0.002) or 0.002)
        path_aware = bool(getattr(params, "label_path_aware", True))
        labels = label_forward_returns(
            featured,
            horizon=horizon,
            threshold=threshold,
            path_aware=path_aware,
        )
        data = featured[FEATURE_COLUMNS].copy()
        data["label"] = labels
        data = data.dropna()
        # Drop rows whose forward window is incomplete.
        trim = max(horizon, 1)
        data = data.iloc[:-trim] if len(data) > trim else data

        if len(data) < 80 or data["label"].nunique() < 2:
            self.model = None
            self.last_train_rows = len(data)
            self.last_accuracy = None
            return {
                "ok": False,
                "rows": len(data),
                "reason": "insufficient_data_or_classes",
            }

        X = data[FEATURE_COLUMNS].to_numpy(dtype=float)
        y = data["label"].to_numpy()
        # Always train 3-way sell/hold/buy so XGBoost never sees a missing
        # class index after the train/val split (binary path broke on {1,2}).
        label_to_idx = {-1: 0, 0: 1, 1: 2}
        classes = [-1, 0, 1]
        y_idx = np.array([label_to_idx.get(int(v), 1) for v in y], dtype=int)

        split = max(1, int(len(X) * 0.8))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y_idx[:split], y_idx[split:]
        X_train, y_train = self._ensure_all_classes(X_train, y_train)

        model = XGBClassifier(
            n_estimators=80,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            n_jobs=2,
            random_state=42,
        )

        try:
            model.fit(X_train, y_train)
        except Exception as exc:  # noqa: BLE001
            self.model = None
            self.last_train_rows = len(data)
            self.last_accuracy = None
            return {"ok": False, "rows": len(data), "reason": f"fit_failed:{exc}"}

        preds = model.predict(X_val) if len(X_val) else y_train
        truth = y_val if len(X_val) else y_train
        accuracy = float((preds == truth).mean()) if len(truth) else None

        self.model = model
        self._classes = classes
        self.last_train_rows = len(data)
        self.last_accuracy = accuracy
        return {
            "ok": True,
            "rows": len(data),
            "classes": classes,
            "accuracy": accuracy,
        }

    @staticmethod
    def _ensure_all_classes(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pad one duplicate row per missing class so multi:softprob can fit."""
        present = set(int(v) for v in np.unique(y).tolist())
        if len(X) == 0:
            return X, y
        xs = [X]
        ys = [y]
        donor = X[0]
        for cls in (0, 1, 2):
            if cls in present:
                continue
            xs.append(donor.reshape(1, -1))
            ys.append(np.array([cls], dtype=int))
            present.add(cls)
        return np.vstack(xs), np.concatenate(ys)

    def predict(
        self,
        candles: pd.DataFrame,
        params: BotParams,
        news: NewsBrief | None = None,
    ) -> SignalResult:
        featured = build_feature_frame(candles, params)
        row = featured.iloc[-1]
        feature_vals = {
            col: float(row[col]) if pd.notna(row[col]) else 0.0 for col in FEATURE_COLUMNS
        }
        feature_vals, buy_th, sell_th = self._fuse_news(feature_vals, params, news)

        # Heuristic fallback if model not ready
        if self.model is None or any(pd.isna(row[c]) for c in FEATURE_COLUMNS):
            action, conf, detail = self._heuristic(row, params)
            conf = self._nudge_heuristic_confidence(action, conf, feature_vals)
            return SignalResult(
                action=action,
                confidence=conf,
                probs={"buy": 0.0, "hold": 0.0, "sell": 0.0, action: conf},
                features=feature_vals,
                model_ready=False,
                detail=detail + self._news_detail(feature_vals, buy_th, sell_th),
            )

        X = np.array([[feature_vals[c] for c in FEATURE_COLUMNS]])
        proba = self.model.predict_proba(X)[0]
        classes = getattr(self, "_classes", list(range(len(proba))))
        prob_map = {self.CLASS_MAP.get(int(c), str(c)): float(p) for c, p in zip(classes, proba)}
        for key in ("buy", "hold", "sell"):
            prob_map.setdefault(key, 0.0)

        buy_p = prob_map["buy"]
        sell_p = prob_map["sell"]
        hold_p = prob_map["hold"]

        action = "hold"
        confidence = hold_p
        # Hard thresholds only — no soft micro bias (that churned fee-lossy exits).
        if buy_p >= buy_th and buy_p >= sell_p:
            action = "buy"
            confidence = buy_p
        elif sell_p >= sell_th and sell_p > buy_p:
            action = "sell"
            confidence = sell_p

        # Blend RSI extremes as soft guardrails
        rsi = feature_vals.get("rsi", 50.0)
        if action == "buy" and rsi > params.rsi_overbought:
            action = "hold"
            confidence = hold_p
        if action == "sell" and rsi < params.rsi_oversold:
            action = "hold"
            confidence = hold_p

        return SignalResult(
            action=action,
            confidence=float(confidence),
            probs=prob_map,
            features=feature_vals,
            model_ready=True,
            detail=(
                f"xgb rows={self.last_train_rows} acc={self.last_accuracy}"
                + self._news_detail(feature_vals, buy_th, sell_th)
            ),
        )

    def feature_importances(self) -> dict[str, float]:
        if self.model is None:
            return {c: 1.0 / len(FEATURE_COLUMNS) for c in FEATURE_COLUMNS}
        importances = getattr(self.model, "feature_importances_", None)
        if importances is None:
            return {c: 0.0 for c in FEATURE_COLUMNS}
        return {c: float(v) for c, v in zip(FEATURE_COLUMNS, importances)}

    def top_features(self, features: dict[str, float], n: int = 5) -> list[dict[str, Any]]:
        imp = self.feature_importances()
        scored: list[dict[str, Any]] = []
        for name, value in features.items():
            importance = float(imp.get(name, 0.0))
            scored.append(
                {
                    "name": name,
                    "value": float(value),
                    "importance": importance,
                    "score": abs(float(value)) * (importance + 1e-6),
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:n]

    def explain(self, signal: SignalResult) -> str:
        technical = {
            name: value
            for name, value in signal.features.items()
            if not name.startswith("news_")
        }
        tops = self.top_features(technical, n=3)
        bits = [f"{t['name']}={t['value']:.4f} (w={t['importance']:.3f})" for t in tops]
        mode = "xgb" if signal.model_ready else "heuristic"
        news_bit = ""
        if "news_bias" in signal.features:
            bias = signal.features["news_bias"]
            if bias >= 0.15:
                label = "bullish"
            elif bias <= -0.15:
                label = "bearish"
            else:
                label = "neutral"
            news_bit = (
                f" | news {label} bias={bias:+.2f}"
                f" crypto={signal.features.get('news_crypto', 0.0):+.2f}"
                f" macro={signal.features.get('news_macro', 0.0):+.2f}"
                f" tech={signal.features.get('news_tech', 0.0):+.2f}"
                f" world={signal.features.get('news_world', 0.0):+.2f}"
                " — news features are decision-time only (not XGBoost inputs);"
                " a positive bias lowers the buy threshold and raises the sell"
                " threshold by up to 4 percentage points"
            )
        return (
            f"{signal.action.upper()} conf={signal.confidence:.0%} [{mode}] — "
            + "; ".join(bits)
            + news_bit
            + (f" | {signal.detail}" if signal.detail else "")
        )

    def _fuse_news(
        self,
        feature_vals: dict[str, float],
        params: BotParams,
        news: NewsBrief | None,
    ) -> tuple[dict[str, float], float, float]:
        if news is None:
            return feature_vals, params.buy_threshold, params.sell_threshold
        merged = {**feature_vals, **news.feature_vector()}
        bias = max(-1.0, min(1.0, merged["news_bias"]))
        shift = bias * _NEWS_THRESHOLD_SHIFT
        # News may ease/tighten thresholds ±shift, never below 0.45 for entries.
        buy_th = min(0.90, max(0.45, params.buy_threshold - shift))
        sell_th = min(0.90, max(0.45, params.sell_threshold + shift))
        return merged, buy_th, sell_th

    def _nudge_heuristic_confidence(
        self,
        action: str,
        confidence: float,
        features: dict[str, float],
    ) -> float:
        """Heuristic mode has no probability threshold, so bias nudges confidence slightly."""
        bias = features.get("news_bias")
        if bias is None:
            return confidence
        if action == "buy":
            confidence += max(0.0, bias) * 0.05
        elif action == "sell":
            confidence += max(0.0, -bias) * 0.05
        return float(min(0.9, max(0.0, confidence)))

    def _news_detail(
        self,
        features: dict[str, float],
        buy_th: float,
        sell_th: float,
    ) -> str:
        if "news_bias" not in features:
            return ""
        return (
            f" | news_bias={features['news_bias']:+.2f}"
            f" buy_th={buy_th:.2f} sell_th={sell_th:.2f}"
        )

    def _heuristic(self, row: pd.Series, params: BotParams) -> tuple[str, float, str]:
        rsi = float(row["rsi"]) if pd.notna(row.get("rsi")) else 50.0
        macd_hist = float(row["macd_hist"]) if pd.notna(row.get("macd_hist")) else 0.0
        if rsi <= params.rsi_oversold and macd_hist >= 0:
            return "buy", 0.6, "heuristic_rsi_oversold"
        if rsi >= params.rsi_overbought and macd_hist <= 0:
            return "sell", 0.6, "heuristic_rsi_overbought"
        return "hold", 0.5, "heuristic_hold"
