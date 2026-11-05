"""French, human-readable account of one trading decision.

Machine fields (action, reason, features) stay in English for logs.
This text is what the dashboard shows live.
"""

from __future__ import annotations

import re
from typing import Any

_THRESHOLD_RE = re.compile(r"buy_th=([0-9]*\.?[0-9]+)\s+sell_th=([0-9]*\.?[0-9]+)")

_ACTION_FR = {"buy": "ACHAT", "sell": "VENTE", "hold": "ATTENTE"}

_SKIP_REASONS = {
    "cooldown": "Ordre ignoré : délai entre achats.",
    "budget": "Ordre ignoré : cash insuffisant.",
    "max_size": "Ordre ignoré : position déjà au plafond.",
    "max_symbols": "Ordre ignoré : nombre maximum de paires atteint.",
    "flat": "Vente ignorée : aucune position ouverte.",
}

_EXIT_LABELS = {
    "tp": "objectif de gain",
    "sl": "stop-loss",
    "kill": "kill switch",
}


def thresholds_from_detail(
    detail: str,
    buy_default: float,
    sell_default: float,
) -> tuple[float, float]:
    match = _THRESHOLD_RE.search(detail or "")
    if match is None:
        return buy_default, sell_default
    return float(match.group(1)), float(match.group(2))


def format_other_exits(exits: list[dict[str, Any]], focus_symbol: str) -> str:
    bits: list[str] = []
    for item in exits:
        symbol = str(item.get("symbol") or "")
        if not symbol or symbol == focus_symbol:
            continue
        reason = _EXIT_LABELS.get(str(item.get("reason") or ""), "sortie")
        bits.append(f"{symbol} ({reason})")
    if not bits:
        return ""
    return "Autres sorties : " + ", ".join(bits) + "."


def decision_narrative(
    *,
    symbol: str,
    signal_action: str,
    confidence: float,
    features: dict[str, float] | None,
    plan_side: str,
    plan_reason: str,
    order_placed: bool,
    buy_threshold: float,
    sell_threshold: float,
    base_buy_threshold: float,
    base_sell_threshold: float,
    rsi_oversold: float,
    rsi_overbought: float,
    auto_trade: bool,
    killed: bool,
    price: float | None = None,
) -> str:
    feats = features or {}
    conf = _confidence_clause(confidence, plan_reason)
    lead = _lead(
        symbol=symbol,
        signal_action=signal_action,
        plan_side=plan_side,
        plan_reason=plan_reason,
        order_placed=order_placed,
        killed=killed,
        conf=conf,
    )
    drivers = _drivers(
        feats,
        price=price,
        rsi_oversold=rsi_oversold,
        rsi_overbought=rsi_overbought,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
        base_buy_threshold=base_buy_threshold,
        base_sell_threshold=base_sell_threshold,
    )
    outcome = _outcome(
        signal_action=signal_action,
        confidence=confidence,
        plan_side=plan_side,
        plan_reason=plan_reason,
        order_placed=order_placed,
        auto_trade=auto_trade,
        killed=killed,
    )
    return " ".join(part for part in (lead, drivers, outcome) if part)


def _confidence_clause(confidence: float, plan_reason: str) -> str:
    if plan_reason in {"tp", "sl", "kill"} or confidence <= 0:
        return ""
    return f" (confiance {round(confidence * 100)} %)"


def _lead(
    *,
    symbol: str,
    signal_action: str,
    plan_side: str,
    plan_reason: str,
    order_placed: bool,
    killed: bool,
    conf: str,
) -> str:
    if plan_reason in {"tp", "sl"} or (plan_reason == "kill" and order_placed):
        return f"VENTE {symbol}{conf}."
    if killed and not order_placed:
        return f"ATTENTE {symbol}."
    if order_placed and plan_side in _ACTION_FR and plan_side != "hold":
        return f"{_ACTION_FR[plan_side]} {symbol}{conf}."
    if plan_side == "hold" and signal_action in {"buy", "sell"}:
        word = _ACTION_FR[signal_action]
        return f"Signal {word} {symbol}{conf}, non suivi."
    word = _ACTION_FR.get(signal_action, _ACTION_FR.get(plan_side, "ATTENTE"))
    return f"{word} {symbol}{conf}."


def _outcome(
    *,
    signal_action: str,
    confidence: float,
    plan_side: str,
    plan_reason: str,
    order_placed: bool,
    auto_trade: bool,
    killed: bool,
) -> str:
    if plan_reason == "tp" and order_placed:
        return "Objectif de gain atteint. Ordre paper exécuté."
    if plan_reason == "sl" and order_placed:
        return "Stop-loss atteint. Ordre paper exécuté."
    if plan_reason == "kill" or killed:
        if order_placed:
            return "Kill switch : position fermée."
        return "Kill switch actif : aucun ordre."
    skipped = _SKIP_REASONS.get(plan_reason)
    if skipped:
        return skipped
    if order_placed and plan_side == "buy":
        return "Ordre paper exécuté."
    if order_placed and plan_side == "sell":
        return "Ordre paper de vente exécuté."
    if not auto_trade and signal_action in {"buy", "sell"} and plan_side == "hold":
        return "Auto-trade désactivé : signal observé, pas d'ordre."
    if signal_action == "hold" or plan_reason == "hold" or plan_side == "hold":
        if signal_action == "hold" and confidence >= 0.6:
            return "Le modèle privilégie l'attente. Aucun ordre."
        return "Confiance insuffisante pour agir."
    return "Aucun ordre."


def _drivers(
    features: dict[str, float],
    *,
    price: float | None,
    rsi_oversold: float,
    rsi_overbought: float,
    buy_threshold: float,
    sell_threshold: float,
    base_buy_threshold: float,
    base_sell_threshold: float,
) -> str:
    parts: list[str] = []
    rsi = features.get("rsi")
    if rsi is not None:
        parts.append(_rsi_phrase(rsi, rsi_oversold, rsi_overbought))
    hist = features.get("macd_hist")
    if hist is not None:
        macd = _macd_phrase(hist, price)
        if macd:
            parts.append(macd)
    bias = features.get("news_bias")
    if bias is not None:
        parts.append(_news_phrase(bias))

    shifts: list[str] = []
    if abs(buy_threshold - base_buy_threshold) >= 0.005:
        verb = "abaissé" if buy_threshold < base_buy_threshold else "relevé"
        shifts.append(f"seuil d'achat {verb} à {_num(buy_threshold)}")
    if abs(sell_threshold - base_sell_threshold) >= 0.005:
        verb = "abaissé" if sell_threshold < base_sell_threshold else "relevé"
        shifts.append(f"seuil de vente {verb} à {_num(sell_threshold)}")

    if not parts and not shifts:
        return ""
    text = ", ".join(parts)
    if shifts:
        shifted = " ; ".join(shifts)
        text = f"{text} ; {shifted}" if text else shifted
    return text[0].upper() + text[1:] + "."


def _rsi_phrase(rsi: float, oversold: float, overbought: float) -> str:
    if rsi <= oversold:
        return f"RSI en survente ({_num(rsi)})"
    if rsi >= overbought:
        return f"RSI en surachat ({_num(rsi)})"
    if rsi < 45:
        return "RSI bas"
    if rsi > 55:
        return "RSI élevé"
    return "RSI neutre"


def _macd_phrase(hist: float, price: float | None) -> str | None:
    scale = price if price and price > 0 else 1.0
    if abs(hist) < scale * 0.00002:
        return None
    if hist > 0:
        return "MACD haussier"
    return "MACD baissier"


def _news_phrase(bias: float) -> str:
    if bias >= 0.15:
        return "biais actus haussier"
    if bias <= -0.15:
        return "biais actus baissier"
    if bias >= 0.03:
        return "biais actus légèrement positif"
    if bias <= -0.03:
        return "biais actus légèrement négatif"
    return "biais actus neutre"


def _num(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")
