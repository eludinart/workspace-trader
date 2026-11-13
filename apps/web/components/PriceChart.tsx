"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChartToggles } from "@/components/ChartToggles";
import {
  DEFAULT_CHART_OVERLAYS,
  loadChartOverlays,
  saveChartOverlays,
  type ChartOverlayKey,
  type ChartOverlays,
} from "@/lib/chart-overlays";
import { ema, macd, rsi, sma } from "@/lib/indicators";

export type Candle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};

export type ChartTrade = {
  id: string;
  side: "buy" | "sell";
  price: number;
  qty: number;
  ts: string;
  symbol?: string;
  fee?: number | null;
  realized_pnl?: number | null;
  note?: string | null;
  clamped?: boolean;
};

const W = 720;
const PAD_X = 10;

const COLORS = {
  sma20: "#60a5fa",
  sma50: "#fbbf24",
  ema12: "#c084fc",
  rsi: "#38bdf8",
  macd: "#22d3ee",
  signal: "#fb923c",
  histUp: "#16a34a",
  histDown: "#dc2626",
  buy: "#22c55e",
  sell: "#ef4444",
  guide: "#475569",
  grid: "#1e293b",
} as const;

function fmt(v: number | null, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

function linePath(
  values: (number | null)[],
  xAt: (i: number) => number,
  yAt: (v: number) => number
): string {
  let d = "";
  let move = false;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) {
      move = false;
      continue;
    }
    const x = xAt(i);
    const y = yAt(v);
    d += move ? ` L ${x} ${y}` : `M ${x} ${y}`;
    move = true;
  }
  return d;
}

function parseTrade(raw: Record<string, unknown>): ChartTrade | null {
  const sideRaw = String(raw.side ?? "").toLowerCase();
  if (sideRaw !== "buy" && sideRaw !== "sell") return null;
  const price = Number(raw.price);
  const qty = Number(raw.qty);
  const ts = String(raw.ts ?? "");
  if (!ts || !Number.isFinite(price) || !Number.isFinite(qty)) return null;
  const fee = raw.fee != null ? Number(raw.fee) : null;
  const realized = raw.realized_pnl != null ? Number(raw.realized_pnl) : null;
  return {
    id: String(raw.id ?? `${sideRaw}-${ts}-${price}`),
    side: sideRaw as "buy" | "sell",
    price,
    qty,
    ts,
    symbol: raw.symbol != null ? String(raw.symbol) : undefined,
    fee: fee != null && Number.isFinite(fee) ? fee : null,
    realized_pnl: realized != null && Number.isFinite(realized) ? realized : null,
    note: raw.note != null ? String(raw.note) : null,
  };
}

function timeMs(iso: string): number {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : NaN;
}

function nearestCandleIndex(timesMs: number[], targetMs: number): number {
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < timesMs.length; i++) {
    const diff = Math.abs(timesMs[i] - targetMs);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  }
  return best;
}

function tradeTitle(trade: ChartTrade): string {
  const when = new Date(trade.ts).toLocaleString("fr-FR");
  const side = trade.side === "buy" ? "ACHAT" : "VENTE";
  const bits = [
    `${side} · ${trade.qty.toFixed(5)} @ ${trade.price.toFixed(2)}`,
    when,
  ];
  if (trade.fee != null) bits.push(`frais ${trade.fee.toFixed(4)}`);
  if (trade.realized_pnl != null) bits.push(`P&L ${trade.realized_pnl.toFixed(2)}`);
  if (trade.note) bits.push(String(trade.note).slice(0, 48));
  if (trade.clamped) bits.push("(hors fenêtre → bord)");
  return bits.join(" · ");
}

function LegendChip({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color: string;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-line bg-ink/60 px-2 py-0.5 text-[10px] sm:text-xs">
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden />
      <span className="text-muted">{label}</span>
      <span className="font-medium tabular-nums text-slate-200">{value}</span>
    </span>
  );
}

function SubPaneLabel({ children }: { children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute left-2 top-1 text-[10px] font-medium uppercase tracking-wide text-muted">
      {children}
    </div>
  );
}

function TradeMarker({
  x,
  yPrice,
  trade,
  mainH,
  padY,
}: {
  x: number;
  yPrice: number;
  trade: ChartTrade;
  mainH: number;
  padY: number;
}) {
  const isBuy = trade.side === "buy";
  const color = isBuy ? COLORS.buy : COLORS.sell;
  const tickLen = 10;
  const tri = 5;
  const yEnd = isBuy
    ? Math.min(yPrice + tickLen, mainH - padY - 1)
    : Math.max(yPrice - tickLen, padY + 1);

  const triPoints = isBuy
    ? `${x},${yEnd - tri} ${x - tri},${yEnd + tri * 0.6} ${x + tri},${yEnd + tri * 0.6}`
    : `${x},${yEnd + tri} ${x - tri},${yEnd - tri * 0.6} ${x + tri},${yEnd - tri * 0.6}`;

  return (
    <g className="pointer-events-auto">
      <title>{tradeTitle(trade)}</title>
      <line x1={x} x2={x} y1={yPrice} y2={yEnd} stroke={color} strokeWidth="1.5" />
      <polygon points={triPoints} fill={color} stroke={color} strokeWidth="0.5" />
    </g>
  );
}

type PriceChartProps = {
  candles: Candle[];
  trades?: Array<Record<string, unknown>>;
  symbol?: string;
  loading?: boolean;
};

export function PriceChart({ candles, trades = [], symbol, loading = false }: PriceChartProps) {
  const [overlays, setOverlays] = useState<ChartOverlays>(DEFAULT_CHART_OVERLAYS);
  const [overlaysReady, setOverlaysReady] = useState(false);

  useEffect(() => {
    setOverlays(loadChartOverlays());
    setOverlaysReady(true);
  }, []);

  useEffect(() => {
    if (!overlaysReady) return;
    saveChartOverlays(overlays);
  }, [overlays, overlaysReady]);

  const setOverlay = (key: ChartOverlayKey, enabled: boolean) => {
    setOverlays((prev) => ({ ...prev, [key]: enabled }));
  };

  const normalizedTrades = useMemo(() => {
    const parsed = trades.map(parseTrade).filter((t): t is ChartTrade => t != null);
    if (!symbol) return parsed;
    return parsed.filter((t) => !t.symbol || t.symbol === symbol);
  }, [trades, symbol]);

  const chart = useMemo(() => {
    if (!candles.length) return null;

    const closes = candles.map((c) => c.close);
    const volumes = candles.map((c) => c.volume ?? 0);
    const candleTimesMs = candles.map((c) => timeMs(c.time));

    const sma20Full = sma(closes, 20);
    const sma50Full = sma(closes, 50);
    const ema12Full = ema(closes, 12);
    const rsiFull = rsi(closes, 14);
    const macdFull = macd(closes);

    const data = candles.slice(-80);
    const offset = candles.length - data.length;

    const slice = <T,>(arr: T[]) => arr.slice(offset);

    const sma20 = slice(sma20Full);
    const sma50 = slice(sma50Full);
    const ema12 = slice(ema12Full);
    const rsi14 = slice(rsiFull);
    const macdLine = slice(macdFull.macd);
    const macdSignal = slice(macdFull.signal);
    const macdHist = slice(macdFull.histogram);
    const vols = slice(volumes);

    const highs = data.map((c) => c.high);
    const lows = data.map((c) => c.low);
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const span = max - min || 1;

    const maxVol = Math.max(...vols, 1);

    const rsiVals = rsi14.filter((v): v is number => v != null);
    const rsiMin = rsiVals.length ? Math.min(...rsiVals, 20) : 0;
    const rsiMax = rsiVals.length ? Math.max(...rsiVals, 80) : 100;
    const rsiSpan = rsiMax - rsiMin || 1;

    const macdNums = [...macdLine, ...macdSignal, ...macdHist].filter(
      (v): v is number => v != null
    );
    const macdMin = macdNums.length ? Math.min(...macdNums, 0) : -1;
    const macdMax = macdNums.length ? Math.max(...macdNums, 0) : 1;
    const macdSpan = macdMax - macdMin || 1;

    const n = data.length;
    const slot = (plotW: number) => plotW / n;
    const xAt = (plotW: number) => (i: number) => PAD_X + i * slot(plotW) + slot(plotW) / 2;

    const tradeMarkers = normalizedTrades
      .map((trade) => {
        const tsMs = timeMs(trade.ts);
        if (!Number.isFinite(tsMs)) return null;
        const candleIdx = nearestCandleIndex(candleTimesMs, tsMs);
        if (candleIdx < offset) {
          return {
            trade: { ...trade, clamped: true },
            sliceIdx: 0,
          };
        }
        return {
          trade,
          sliceIdx: candleIdx - offset,
        };
      })
      .filter((m): m is { trade: ChartTrade; sliceIdx: number } => m != null);

    return {
      data,
      vols,
      sma20,
      sma50,
      ema12,
      rsi14,
      macdLine,
      macdSignal,
      macdHist,
      min,
      max,
      span,
      maxVol,
      rsiMin,
      rsiMax,
      rsiSpan,
      macdMin,
      macdMax,
      macdSpan,
      xAt,
      lastIdx: n - 1,
      tradeMarkers,
    };
  }, [candles, normalizedTrades]);

  if (!chart) {
    return (
      <div className="relative flex h-56 items-center justify-center text-sm text-muted">
        {loading ? "Chargement des bougies…" : "Pas encore de bougies"}
        {loading && (
          <div
            className="pointer-events-none absolute inset-0 rounded-lg bg-ink/40"
            aria-hidden
          />
        )}
      </div>
    );
  }

  const {
    data,
    vols,
    sma20,
    sma50,
    ema12,
    rsi14,
    macdLine,
    macdSignal,
    macdHist,
    min,
    max,
    span,
    maxVol,
    rsiMin,
    rsiSpan,
    macdMin,
    macdSpan,
    xAt,
    lastIdx,
    tradeMarkers,
  } = chart;

  const last = data[data.length - 1];
  const up = last.close >= last.open;

  const mainH = 200;
  const volH = 48;
  const rsiH = 56;
  const macdH = 56;
  const padY = 12;
  const plotW = W - PAD_X * 2;

  const yPrice = (price: number) => padY + (1 - (price - min) / span) * (mainH - padY * 2);
  const xMain = xAt(plotW);
  const bodyW = Math.max(2, Math.min(10, (plotW / data.length) * 0.65));

  const yVol = (v: number) => padY + (1 - v / maxVol) * (volH - padY * 2);
  const yRsi = (v: number) => padY + (1 - (v - rsiMin) / rsiSpan) * (rsiH - padY * 2);
  const yMacd = (v: number) => padY + (1 - (v - macdMin) / macdSpan) * (macdH - padY * 2);
  const yMacdZero = yMacd(0);

  const lastSma20 = sma20[lastIdx];
  const lastSma50 = sma50[lastIdx];
  const lastEma12 = ema12[lastIdx];
  const lastRsi = rsi14[lastIdx];
  const lastMacd = macdLine[lastIdx];
  const lastSignal = macdSignal[lastIdx];

  const rsi30 = yRsi(30);
  const rsi70 = yRsi(70);

  const showLegend =
    overlays.sma20 || overlays.sma50 || overlays.ema12 || overlays.rsi || overlays.macd;

  return (
    <div className={`relative w-full overflow-hidden ${loading ? "opacity-60" : ""}`}>
      {loading && (
        <div
          className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-ink/30"
          aria-live="polite"
          aria-busy="true"
        >
          <span className="rounded-md border border-line bg-panel px-3 py-1.5 text-xs text-muted">
            Mise à jour…
          </span>
        </div>
      )}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <ChartToggles overlays={overlays} onChange={setOverlay} />
        {overlays.trades ? (
          <span className="text-[10px] tabular-nums text-muted">
            {tradeMarkers.length}/{normalizedTrades.length} trades visibles
          </span>
        ) : null}
      </div>

      {showLegend && (
        <div className="mb-2 flex flex-wrap gap-1.5 sm:gap-2">
          {overlays.sma20 && (
            <LegendChip label="SMA20" value={fmt(lastSma20)} color={COLORS.sma20} />
          )}
          {overlays.sma50 && (
            <LegendChip label="SMA50" value={fmt(lastSma50)} color={COLORS.sma50} />
          )}
          {overlays.ema12 && (
            <LegendChip label="EMA12" value={fmt(lastEma12)} color={COLORS.ema12} />
          )}
          {overlays.rsi && (
            <LegendChip label="RSI" value={fmt(lastRsi, 1)} color={COLORS.rsi} />
          )}
          {overlays.macd && (
            <LegendChip
              label="MACD"
              value={`${fmt(lastMacd, 3)} / ${fmt(lastSignal, 3)}`}
              color={COLORS.macd}
            />
          )}
        </div>
      )}

      <svg
        viewBox={`0 0 ${W} ${mainH}`}
        className="h-36 w-full sm:h-44 md:h-52"
        role="img"
        aria-label="Bougies OHLC avec moyennes mobiles et trades"
      >
        <line
          x1={PAD_X}
          x2={W - PAD_X}
          y1={mainH - padY}
          y2={mainH - padY}
          stroke={COLORS.grid}
          strokeWidth="1"
        />
        {overlays.candles &&
          data.map((c, i) => {
            const x = xMain(i);
            const bull = c.close >= c.open;
            const color = bull ? COLORS.histUp : COLORS.histDown;
            const yHigh = yPrice(c.high);
            const yLow = yPrice(c.low);
            const yOpen = yPrice(c.open);
            const yClose = yPrice(c.close);
            const bodyTop = Math.min(yOpen, yClose);
            const bodyH = Math.max(1.5, Math.abs(yClose - yOpen));
            return (
              <g key={`${c.time}-${i}`}>
                <line x1={x} x2={x} y1={yHigh} y2={yLow} stroke={color} strokeWidth="1.2" />
                <rect
                  x={x - bodyW / 2}
                  y={bodyTop}
                  width={bodyW}
                  height={bodyH}
                  fill={color}
                  rx="0.5"
                />
              </g>
            );
          })}
        {overlays.sma20 && (
          <path
            d={linePath(sma20, xMain, yPrice)}
            fill="none"
            stroke={COLORS.sma20}
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
        )}
        {overlays.sma50 && (
          <path
            d={linePath(sma50, xMain, yPrice)}
            fill="none"
            stroke={COLORS.sma50}
            strokeWidth="1.2"
            strokeLinejoin="round"
          />
        )}
        {overlays.ema12 && (
          <path
            d={linePath(ema12, xMain, yPrice)}
            fill="none"
            stroke={COLORS.ema12}
            strokeWidth="1"
            strokeDasharray="3 2"
            strokeLinejoin="round"
            opacity={0.9}
          />
        )}
        {overlays.trades &&
          tradeMarkers.map(({ trade, sliceIdx }) => (
            <TradeMarker
              key={trade.id}
              x={xMain(sliceIdx)}
              yPrice={yPrice(trade.price)}
              trade={trade}
              mainH={mainH}
              padY={padY}
            />
          ))}
      </svg>

      {overlays.volume && (
        <div className="relative mt-1">
          <SubPaneLabel>Volume</SubPaneLabel>
          <svg viewBox={`0 0 ${W} ${volH}`} className="h-10 w-full sm:h-12" aria-hidden>
            {data.map((c, i) => {
              const v = vols[i];
              if (v <= 0) return null;
              const x = xMain(i);
              const bull = c.close >= c.open;
              const color = bull ? COLORS.histUp : COLORS.histDown;
              const barH = Math.max(1, (volH - padY * 2) * (v / maxVol));
              return (
                <rect
                  key={`vol-${i}`}
                  x={x - bodyW / 2}
                  y={volH - padY - barH}
                  width={bodyW}
                  height={barH}
                  fill={color}
                  opacity={0.75}
                  rx="0.5"
                />
              );
            })}
          </svg>
        </div>
      )}

      {overlays.rsi && (
        <div className="relative mt-0.5">
          <SubPaneLabel>RSI (14)</SubPaneLabel>
          <svg viewBox={`0 0 ${W} ${rsiH}`} className="h-12 w-full sm:h-14" aria-hidden>
            <line
              x1={PAD_X}
              x2={W - PAD_X}
              y1={rsi30}
              y2={rsi30}
              stroke={COLORS.guide}
              strokeDasharray="4 3"
              strokeWidth="1"
            />
            <line
              x1={PAD_X}
              x2={W - PAD_X}
              y1={rsi70}
              y2={rsi70}
              stroke={COLORS.guide}
              strokeDasharray="4 3"
              strokeWidth="1"
            />
            <path
              d={linePath(rsi14, xMain, yRsi)}
              fill="none"
              stroke={COLORS.rsi}
              strokeWidth="1.3"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      )}

      {overlays.macd && (
        <div className="relative mt-0.5">
          <SubPaneLabel>MACD</SubPaneLabel>
          <svg viewBox={`0 0 ${W} ${macdH}`} className="h-12 w-full sm:h-14" aria-hidden>
            <line
              x1={PAD_X}
              x2={W - PAD_X}
              y1={yMacdZero}
              y2={yMacdZero}
              stroke={COLORS.guide}
              strokeWidth="1"
            />
            {data.map((c, i) => {
              const h = macdHist[i];
              if (h == null) return null;
              const x = xMain(i);
              const y0 = yMacdZero;
              const y1 = yMacd(h);
              const top = Math.min(y0, y1);
              const height = Math.max(1, Math.abs(y1 - y0));
              const color = h >= 0 ? COLORS.histUp : COLORS.histDown;
              return (
                <rect
                  key={`hist-${i}`}
                  x={x - bodyW / 2}
                  y={top}
                  width={bodyW}
                  height={height}
                  fill={color}
                  opacity={0.65}
                />
              );
            })}
            <path
              d={linePath(macdLine, xMain, yMacd)}
              fill="none"
              stroke={COLORS.macd}
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
            <path
              d={linePath(macdSignal, xMain, yMacd)}
              fill="none"
              stroke={COLORS.signal}
              strokeWidth="1"
              strokeLinejoin="round"
            />
          </svg>
        </div>
      )}

      <div className="mt-1 flex flex-col gap-0.5 text-xs text-muted sm:flex-row sm:justify-between">
        <span>{min.toFixed(2)}</span>
        <span className={`text-center ${up ? "text-gain" : "text-loss"}`}>
          O {last.open.toFixed(2)} · H {last.high.toFixed(2)} · L {last.low.toFixed(2)} · C{" "}
          {last.close.toFixed(2)}
          {(last.volume ?? 0) > 0 ? ` · V ${last.volume!.toFixed(2)}` : ""}
        </span>
        <span className="text-right">{max.toFixed(2)}</span>
      </div>
    </div>
  );
}
