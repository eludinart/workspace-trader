"""Out-of-sample harness — metrics only; activation_allowed stays false until validated."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class OosSplit:
    train_size: int
    holdout_size: int
    train_ratio: float


@dataclass
class OosReport:
    split: OosSplit
    train_metrics: dict[str, float] = field(default_factory=dict)
    holdout_metrics: dict[str, float] = field(default_factory=dict)
    activation_allowed: bool = False
    activation_block_reason: str = "OOS thresholds not validated — activation refused"

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": {
                "train_size": self.split.train_size,
                "holdout_size": self.split.holdout_size,
                "train_ratio": self.split.train_ratio,
            },
            "train_metrics": self.train_metrics,
            "holdout_metrics": self.holdout_metrics,
            "activation_allowed": self.activation_allowed,
            "activation_block_reason": self.activation_block_reason,
        }


def split_indices(n: int, train_ratio: float = 0.7) -> OosSplit:
    if n < 2:
        return OosSplit(train_size=max(0, n), holdout_size=0, train_ratio=train_ratio)
    ratio = min(0.95, max(0.5, float(train_ratio)))
    train = max(1, int(n * ratio))
    if train >= n:
        train = n - 1
    return OosSplit(train_size=train, holdout_size=n - train, train_ratio=ratio)


def summarize_pnls(pnls: Sequence[float]) -> dict[str, float]:
    if not pnls:
        return {"n": 0.0, "sum": 0.0, "win_rate": 0.0, "avg": 0.0}
    wins = sum(1 for p in pnls if p > 0)
    total = float(sum(pnls))
    return {
        "n": float(len(pnls)),
        "sum": total,
        "win_rate": wins / len(pnls),
        "avg": total / len(pnls),
    }


def evaluate_oos(
    trade_pnls: Sequence[float],
    *,
    train_ratio: float = 0.7,
    min_holdout: int = 30,
    min_holdout_win_rate: float = 0.55,
    min_holdout_avg: float = 0.0,
) -> OosReport:
    """Compute train/holdout metrics.

    ``activation_allowed`` is True only when holdout size, win-rate and avg PnL
    clear thresholds. Callers still choose shadow vs enforce for confluence/MTF.
    Kelly sizing has a separate, stricter gate (sample + this OOS).
    """
    split = split_indices(len(trade_pnls), train_ratio)
    train = list(trade_pnls[: split.train_size])
    hold = list(trade_pnls[split.train_size :])
    report = OosReport(
        split=split,
        train_metrics=summarize_pnls(train),
        holdout_metrics=summarize_pnls(hold),
        activation_allowed=False,
    )
    reasons: list[str] = []
    if split.holdout_size < min_holdout:
        reasons.append(
            f"holdout n={split.holdout_size} < min_holdout={min_holdout}"
        )
    wr = float(report.holdout_metrics.get("win_rate") or 0.0)
    avg = float(report.holdout_metrics.get("avg") or 0.0)
    if split.holdout_size > 0 and wr < min_holdout_win_rate:
        reasons.append(f"holdout win_rate={wr:.2f} < {min_holdout_win_rate:.2f}")
    if split.holdout_size > 0 and avg < min_holdout_avg:
        reasons.append(f"holdout avg={avg:.4f} < {min_holdout_avg:.4f}")
    if reasons:
        report.activation_block_reason = "; ".join(reasons)
        report.activation_allowed = False
    else:
        report.activation_allowed = True
        report.activation_block_reason = ""
    return report
