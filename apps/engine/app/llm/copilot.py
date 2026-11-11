"""Live co-pilot: XGBoost proposes; LLM may approve / veto / resize / force_exit."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.llm.free_pool import (
    FreePoolResult,
    complete as free_pool_complete,
    configured_hops,
    pool_configured,
)

logger = logging.getLogger(__name__)

CopilotAction = Literal["approve", "veto", "resize", "force_exit"]

_SYSTEM = """Tu es le co-pilote paper-trading d'un bot crypto.
XGBoost a déjà proposé une action. Tu peux: approve, veto, resize (size_mult 0.25–1.0), force_exit (positions ouvertes seulement).
Règles:
- ne jamais augmenter le risque au-delà de la proposition;
- si confiance modèle ≥ 0.55 et pas de news très adverse: préférer approve, ou resize 0.5–0.75 plutôt que veto;
- veto seulement si edge clairement < ~½ friction A/R (~95 bps) OU news clairement adverse OU book illiquide;
- paper: laisser passer des setups moyens pour apprendre (évite le veto systématique);
répondre UNIQUEMENT en JSON compact:
{"action":"approve|veto|resize|force_exit","size_mult":1.0,"reason_fr":"…"}
reason_fr en français, max 200 caractères."""


@dataclass(frozen=True)
class CopilotDecision:
    action: CopilotAction
    size_mult: float
    reason_fr: str
    soft_fail: bool = False
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "size_mult": self.size_mult,
            "reason_fr": self.reason_fr,
            "soft_fail": self.soft_fail,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
        }


def _clamp_mult(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 1.0
    if n != n:  # NaN
        return 1.0
    return max(0.25, min(1.0, n))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    blob = (text or "").strip()
    if not blob:
        return None
    # Strip markdown fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", blob, re.DOTALL | re.IGNORECASE)
    if fence:
        blob = fence.group(1)
    else:
        start = blob.find("{")
        end = blob.rfind("}")
        if start >= 0 and end > start:
            blob = blob[start : end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_copilot_response(
    text: str,
    *,
    allow_force_exit: bool = False,
    meta: FreePoolResult | None = None,
) -> CopilotDecision:
    """Parse model output; invalid → soft-fail approve."""
    data = _extract_json_object(text)
    provider = meta.provider if meta else ""
    model = meta.model if meta else ""
    latency = int(meta.latency_ms) if meta else 0
    if not data:
        return CopilotDecision(
            action="approve",
            size_mult=1.0,
            reason_fr="Co-pilote indisponible (réponse invalide) — décision modèle seule.",
            soft_fail=True,
            provider=provider,
            model=model,
            latency_ms=latency,
            raw=(text or "")[:400],
        )
    action_raw = str(data.get("action") or "approve").strip().lower()
    allowed = {"approve", "veto", "resize", "force_exit"}
    if action_raw not in allowed:
        action_raw = "approve"
    if action_raw == "force_exit" and not allow_force_exit:
        action_raw = "veto"
    size_mult = _clamp_mult(data.get("size_mult", 1.0))
    if action_raw == "resize" and size_mult >= 0.999:
        action_raw = "approve"
        size_mult = 1.0
    if action_raw in {"approve", "veto", "force_exit"}:
        size_mult = 1.0 if action_raw != "resize" else size_mult
    reason = str(data.get("reason_fr") or data.get("reason") or "").strip()
    if not reason:
        reason = f"Co-pilote: {action_raw}"
    reason = reason[:240]
    return CopilotDecision(
        action=action_raw,  # type: ignore[arg-type]
        size_mult=size_mult,
        reason_fr=reason,
        soft_fail=False,
        provider=provider,
        model=model,
        latency_ms=latency,
        raw=(text or "")[:400],
    )


class CopilotService:
    def enabled(self, params_flag: bool) -> bool:
        env = str(os.getenv("COPILOT_ENABLED") or "").strip().lower()
        if env in {"0", "false", "off", "no"}:
            return False
        if env in {"1", "true", "on", "yes"}:
            return True
        # Param flag alone: only run when a free-pool key exists.
        return bool(params_flag) and pool_configured()

    def status_state(self, params_flag: bool) -> tuple[str, str]:
        """Return (state, detail_fr) for the system status board."""
        env = str(os.getenv("COPILOT_ENABLED") or "").strip().lower()
        if env in {"0", "false", "off", "no"}:
            want = False
        elif env in {"1", "true", "on", "yes"}:
            want = True
        else:
            want = bool(params_flag)
        hops = len(configured_hops())
        if not want:
            return "off", "OFF — co-pilote LLM désactivé (XGBoost seul)"
        if hops <= 0:
            return "warn", "ON sans clé LLM — soft-fail (chemin modèle inchangé)"
        if not self.enabled(params_flag):
            return "warn", "Flag ON mais pool indisponible"
        return "ok", f"ON · pool libre ({hops} hop(s)) · Groq→Gemini→OpenRouter"

    def review_entry(self, context: dict[str, Any]) -> CopilotDecision:
        return self._review(context, allow_force_exit=False, kind="entry")

    def review_position(self, context: dict[str, Any]) -> CopilotDecision:
        return self._review(context, allow_force_exit=True, kind="position")

    def _review(
        self,
        context: dict[str, Any],
        *,
        allow_force_exit: bool,
        kind: str,
    ) -> CopilotDecision:
        user = json.dumps({"kind": kind, **context}, ensure_ascii=False, default=str)[:3500]
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ]
        result = free_pool_complete(messages, max_tokens=180, temperature=0.15)
        if not result.ok:
            logger.warning(
                "copilot soft-fail kind=%s err=%s provider=%s model=%s",
                kind,
                result.error,
                result.provider,
                result.model,
            )
            detail = result.error or "erreur"
            if result.model:
                detail = f"{detail} · {result.provider or '?'}/{result.model}"
            return CopilotDecision(
                action="approve",
                size_mult=1.0,
                reason_fr=f"Co-pilote indisponible ({detail}) — décision modèle seule.",
                soft_fail=True,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms,
                raw="",
            )
        return parse_copilot_response(
            result.text,
            allow_force_exit=allow_force_exit,
            meta=result,
        )
