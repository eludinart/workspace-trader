"""Signal foundations and feature stubs (all disabled by default)."""

from app.signals.flags import SignalFeatureFlags, default_flags
from app.signals.observation import SignalObservation, ConfidenceTier

__all__ = [
    "SignalFeatureFlags",
    "default_flags",
    "SignalObservation",
    "ConfidenceTier",
]
