"""Universe quality filters: volume floor, deny-list, coarse tick rejection."""

from __future__ import annotations

from app.universe import UniverseService, tick_size_bps


class _FakeMarket:
    def get_products(self):
        return [
            {
                "id": "BTC-USD",
                "status": "online",
                "trading_disabled": False,
                "base_currency": "BTC",
                "quote_currency": "USD",
                "quote_increment": "0.01",
            },
            {
                "id": "MOG-USD",
                "status": "online",
                "trading_disabled": False,
                "base_currency": "MOG",
                "quote_currency": "USD",
                "quote_increment": "0.00000001",
            },
            {
                "id": "LINK-USD",
                "status": "online",
                "trading_disabled": False,
                "base_currency": "LINK",
                "quote_currency": "USD",
                "quote_increment": "0.001",
            },
            {
                "id": "NOICE-USD",
                "status": "online",
                "trading_disabled": False,
                "base_currency": "NOICE",
                "quote_currency": "USD",
                "quote_increment": "0.00000001",
            },
        ]

    def get_volume_summary(self):
        return [
            {"id": "BTC-USD", "spot_volume_24hour": 500_000_000},
            {"id": "MOG-USD", "spot_volume_24hour": 80_000_000},  # volume ok, tick toxic
            {"id": "LINK-USD", "spot_volume_24hour": 40_000_000},
            {"id": "NOICE-USD", "spot_volume_24hour": 25_000_000},  # deny base
        ]


def test_tick_size_bps_flags_mog_style_ticks() -> None:
    # MOG ~1e-7 with 1e-8 quote increment → 1000 bps per tick
    assert tick_size_bps(1e-8, 1e-7) > 40
    assert tick_size_bps(0.01, 100_000.0) < 1


def test_universe_excludes_deny_and_coarse_tick() -> None:
    uni = UniverseService(
        market=_FakeMarket(),  # type: ignore[arg-type]
        size=20,
        min_volume_usd=15_000_000,
        max_tick_bps=40.0,
    )
    symbols = uni.refresh(force=True)
    assert "BTC-USD" in symbols
    assert "LINK-USD" in symbols
    assert "MOG-USD" not in symbols
    assert "NOICE-USD" not in symbols
    assert uni.quote_increments.get("BTC-USD") == 0.01


def test_news_boost_cannot_inject_denied_symbol() -> None:
    uni = UniverseService(
        market=_FakeMarket(),  # type: ignore[arg-type]
        size=20,
        min_volume_usd=15_000_000,
        max_tick_bps=40.0,
    )
    uni.refresh(force=True)
    from datetime import datetime, timedelta, timezone

    from app.universe import _NewsBoost

    uni._news_boosts["MOG-USD"] = _NewsBoost(
        symbol="MOG-USD",
        score=0.9,
        title="MOG moon",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    uni._merge_symbols()
    assert "MOG-USD" not in uni.symbols
    assert "MOG-USD" not in uni._news_boosts
