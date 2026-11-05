from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd


class CoinbaseClient:
    """Read-only Coinbase Exchange public API (no API key)."""

    BASE_URL = "https://api.exchange.coinbase.com"

    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=timeout,
            headers={"User-Agent": "workspace-trader/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        resp = self._client.get(f"/products/{product_id}/ticker")
        resp.raise_for_status()
        data = resp.json()
        return {
            "symbol": product_id,
            "price": float(data["price"]),
            "bid": float(data.get("bid") or data["price"]),
            "ask": float(data.get("ask") or data["price"]),
            "volume": float(data.get("volume") or 0),
            "time": data.get("time"),
        }

    def get_products(self) -> list[dict[str, Any]]:
        resp = self._client.get("/products")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def get_volume_summary(self) -> list[dict[str, Any]]:
        resp = self._client.get("/products/volume-summary")
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []

    def _parse_candles(self, raw: list[list[Any]]) -> pd.DataFrame:
        if not raw:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            )
        rows = []
        for item in raw:
            ts = datetime.fromtimestamp(item[0], tz=timezone.utc)
            rows.append(
                {
                    "time": ts,
                    "low": float(item[1]),
                    "high": float(item[2]),
                    "open": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
            )
        df = pd.DataFrame(rows)
        return df.sort_values("time").drop_duplicates(subset=["time"]).reset_index(drop=True)

    def get_candles(
        self,
        product_id: str,
        granularity: int = 3600,
        limit: int = 300,
    ) -> pd.DataFrame:
        """Fetch latest OHLCV candles (max 300)."""
        limit = min(max(limit, 1), 300)
        resp = self._client.get(
            f"/products/{product_id}/candles",
            params={"granularity": granularity},
        )
        resp.raise_for_status()
        df = self._parse_candles(resp.json())
        if len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    def get_candles_range(
        self,
        product_id: str,
        start: datetime,
        end: datetime,
        granularity: int = 3600,
        max_candles: int = 5000,
    ) -> pd.DataFrame:
        """
        Paginate Coinbase candles between start and end (inclusive window).
        API returns at most 300 candles per request.
        """
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            raise ValueError("end must be after start")

        frames: list[pd.DataFrame] = []
        cursor_end = end
        fetched = 0

        while cursor_end > start and fetched < max_candles:
            window_start = max(
                start,
                datetime.fromtimestamp(
                    cursor_end.timestamp() - granularity * 300,
                    tz=timezone.utc,
                ),
            )
            resp = self._client.get(
                f"/products/{product_id}/candles",
                params={
                    "granularity": granularity,
                    "start": window_start.isoformat().replace("+00:00", "Z"),
                    "end": cursor_end.isoformat().replace("+00:00", "Z"),
                },
            )
            resp.raise_for_status()
            chunk = self._parse_candles(resp.json())
            if chunk.empty:
                break
            frames.append(chunk)
            fetched += len(chunk)
            oldest = chunk["time"].iloc[0]
            if oldest <= start:
                break
            # step before oldest to avoid overlap loop
            cursor_end = datetime.fromtimestamp(
                oldest.timestamp() - granularity,
                tz=timezone.utc,
            )

        if not frames:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            )

        df = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["time"])
            .sort_values("time")
        )
        mask = (df["time"] >= start) & (df["time"] <= end)
        df = df.loc[mask].reset_index(drop=True)
        if len(df) > max_candles:
            df = df.iloc[-max_candles:].reset_index(drop=True)
        return df

    def candles_as_dicts(
        self, product_id: str, granularity: int = 3600, limit: int = 300
    ) -> list[dict[str, Any]]:
        df = self.get_candles(product_id, granularity=granularity, limit=limit)
        return self._df_to_dicts(df)

    @staticmethod
    def _df_to_dicts(df: pd.DataFrame) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            out.append(
                {
                    "time": row["time"].isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
        return out
