from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from app.config import AlertParams

logger = logging.getLogger(__name__)


class AlertService:
    def __init__(self) -> None:
        self.params = AlertParams()
        self._last_pnl_alert_level: float = 0.0
        self.history: list[dict[str, Any]] = []

    def update_params(self, patch: dict[str, Any]) -> AlertParams:
        data = self.params.model_dump()
        data.update({k: v for k, v in patch.items() if v is not None})
        self.params = AlertParams(**data)
        return self.params

    def _telegram_token(self) -> str:
        return self.params.telegram_bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")

    def notify(self, title: str, body: str, kind: str = "info") -> dict[str, Any]:
        results: dict[str, Any] = {"kind": kind, "title": title, "telegram": None, "email": None}
        text = f"{title}\n{body}"

        if self.params.telegram_enabled:
            results["telegram"] = self._send_telegram(text)
        if self.params.email_enabled:
            results["email"] = self._send_email(title, body)

        entry = {
            "title": title,
            "body": body,
            "kind": kind,
            "channels": results,
        }
        self.history.append(entry)
        self.history = self.history[-100:]
        return results

    def on_trade(self, symbol: str, side: str, qty: float, price: float) -> None:
        if not self.params.alert_on_trade:
            return
        self.notify(
            "Trade exécuté",
            f"{side.upper()} {qty:.6f} {symbol} @ {price:.2f}",
            kind="trade",
        )

    def on_strong_signal(self, symbol: str, action: str, confidence: float) -> None:
        if not self.params.alert_on_strong_signal:
            return
        if confidence < self.params.strong_signal_threshold:
            return
        if action == "hold":
            return
        self.notify(
            "Signal fort",
            f"{action.upper()} {symbol} (confiance {confidence:.0%})",
            kind="signal",
        )

    def on_pnl(self, total_pnl: float) -> None:
        if not self.params.alert_on_pnl:
            return
        threshold = self.params.pnl_alert_threshold
        if threshold <= 0:
            return
        # Alert when absolute PnL crosses successive multiples of threshold
        level = abs(total_pnl) // threshold
        if level > self._last_pnl_alert_level and abs(total_pnl) >= threshold:
            self._last_pnl_alert_level = level
            sign = "+" if total_pnl >= 0 else ""
            self.notify(
                "Seuil PnL atteint",
                f"PnL total = {sign}{total_pnl:.2f} USD (seuil {threshold})",
                kind="pnl",
            )

    def reset_pnl_tracker(self) -> None:
        self._last_pnl_alert_level = 0.0

    def _send_telegram(self, text: str) -> dict[str, Any]:
        token = self._telegram_token()
        chat_id = self.params.telegram_chat_id
        if not token or not chat_id:
            return {"ok": False, "error": "missing_token_or_chat_id"}
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                if resp.status_code >= 400:
                    return {"ok": False, "error": resp.text}
                return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram alert failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _send_email(self, subject: str, body: str) -> dict[str, Any]:
        host = self.params.smtp_host or os.getenv("SMTP_HOST", "")
        port = self.params.smtp_port or int(os.getenv("SMTP_PORT", "587"))
        user = self.params.smtp_user or os.getenv("SMTP_USER", "")
        password = self.params.smtp_password or os.getenv("SMTP_PASSWORD", "")
        to_addr = self.params.email_to
        from_addr = self.params.smtp_from or user or "bot@workspace-trader.local"

        if not host or not to_addr:
            return {"ok": False, "error": "missing_smtp_or_recipient"}

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg.set_content(body)

        try:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning("email alert failed: %s", exc)
            return {"ok": False, "error": str(exc)}
