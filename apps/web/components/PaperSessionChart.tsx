"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { api, money, pnlClass } from "@/lib/engine";

export type PaperSample = {
  t: number;
  equity: number;
  cash: number;
  positions: number;
  pnl: number;
  drawdownPct: number;
};

export type PaperTimeRange =
  | "1h"
  | "6h"
  | "24h"
  | "1d"
  | "1w"
  | "1mo"
  | "1y"
  | "session"
  | "all";

type PaperSessionChartProps = {
  equity: number;
  cash: number;
  positions: number;
  pnl: number;
  live?: boolean;
  maxPoints?: number;
};

type SeriesKey = "equity" | "cash" | "pnl" | "positions" | "drawdown";

const RANGE_OPTIONS: Array<{ id: PaperTimeRange; label: string; hint: string }> = [
  { id: "1h", label: "1h", hint: "Dernière heure" },
  { id: "6h", label: "6h", hint: "6 dernières heures" },
  { id: "24h", label: "24h", hint: "24 dernières heures" },
  { id: "1w", label: "1 sem.", hint: "7 derniers jours" },
  { id: "1mo", label: "1 mois", hint: "30 derniers jours" },
  { id: "1y", label: "1 an", hint: "365 derniers jours" },
  { id: "session", label: "Session", hint: "Depuis le démarrage du moteur" },
  { id: "all", label: "Tout", hint: "Tout l’historique paper stocké" },
];

const VALID_RANGES = new Set<PaperTimeRange>(RANGE_OPTIONS.map((o) => o.id));
const STORAGE_KEY = "wt.paperChartRange";
const LAYERS_KEY = "wt.paperChartLayers";

const W = 900;
const H = 320;
const PAD = { top: 28, right: 56, bottom: 40, left: 64 };

const COLORS = {
  equity: "#38bdf8",
  cash: "#c4b5fd",
  pnl: "#34d399",
  pnlNeg: "#f87171",
  positions: "#fbbf24",
  drawdown: "#fb7185",
  grid: "rgba(148,163,184,0.12)",
  axis: "#64748b",
  cross: "rgba(226,232,240,0.35)",
  panel: "#0b1220",
} as const;

const DEFAULT_LAYERS: Record<SeriesKey, boolean> = {
  equity: true,
  cash: true,
  pnl: false,
  positions: false,
  drawdown: true,
};

function loadRange(): PaperTimeRange {
  if (typeof window === "undefined") return "1h";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  // Legacy alias: 1j was duplicate of 24h.
  if (raw === "1d") return "24h";
  if (raw && VALID_RANGES.has(raw as PaperTimeRange)) return raw as PaperTimeRange;
  return "1h";
}

function loadLayers(): Record<SeriesKey, boolean> {
  if (typeof window === "undefined") return { ...DEFAULT_LAYERS };
  try {
    const raw = window.localStorage.getItem(LAYERS_KEY);
    if (!raw) return { ...DEFAULT_LAYERS };
    const parsed = JSON.parse(raw) as Partial<Record<SeriesKey, boolean>>;
    return { ...DEFAULT_LAYERS, ...parsed, equity: true };
  } catch {
    return { ...DEFAULT_LAYERS };
  }
}

function rangeDeltaMs(range: PaperTimeRange): number | null {
  if (range === "1h") return 3600_000;
  if (range === "6h") return 6 * 3600_000;
  if (range === "24h" || range === "1d") return 24 * 3600_000;
  if (range === "1w") return 7 * 24 * 3600_000;
  if (range === "1mo") return 30 * 24 * 3600_000;
  if (range === "1y") return 365 * 24 * 3600_000;
  return null;
}

function rangeCutoffMs(range: PaperTimeRange, sessionStartedAt: number | null): number | null {
  const delta = rangeDeltaMs(range);
  if (delta != null) return Date.now() - delta;
  if (range === "session") return sessionStartedAt;
  return null;
}

/** X domain always follows the selected time scale (so range buttons actually work). */
function visibleDomainMs(
  range: PaperTimeRange,
  sessionStartedAt: number | null,
  series: PaperSample[],
): { start: number; end: number; sparse: boolean } {
  const end = Date.now();
  const delta = rangeDeltaMs(range);
  let start: number;
  if (delta != null) {
    start = end - delta;
  } else if (range === "session") {
    start = sessionStartedAt ?? series[0]?.t ?? end - 3600_000;
    start = Math.min(start, end - 60_000);
  } else if (series.length) {
    start = series[0].t;
    if (end - start < 60_000) start = end - 60_000;
  } else {
    start = end - 3600_000;
  }

  const inWindow = series.filter((s) => s.t >= start && s.t <= end);
  const dataSpan =
    inWindow.length >= 2 ? inWindow[inWindow.length - 1].t - inWindow[0].t : 0;
  const sparse = inWindow.length < 2 || dataSpan < (end - start) * 0.25;
  return { start, end, sparse };
}

function formatAxisTime(t: number, spanMs: number): string {
  const opts: Intl.DateTimeFormatOptions =
    spanMs >= 60 * 24 * 3600_000
      ? { day: "2-digit", month: "short" }
      : spanMs >= 2 * 24 * 3600_000
        ? { day: "2-digit", month: "2-digit", hour: "2-digit" }
        : spanMs >= 3 * 3600_000
          ? { hour: "2-digit", minute: "2-digit" }
          : { hour: "2-digit", minute: "2-digit", second: "2-digit" };
  return new Date(t).toLocaleString("fr-FR", opts);
}

function fmtPct(v: number, digits = 2): string {
  return `${v.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} %`;
}

function niceMoneyTicks(min: number, max: number, count = 5): number[] {
  const span = Math.max(1e-6, max - min);
  const step = span / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(step));
  const niceStep =
    [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= step) ?? step;
  const niceMin = Math.floor(min / niceStep) * niceStep;
  const ticks: number[] = [];
  for (let v = niceMin; v <= max + niceStep * 0.01 && ticks.length < count + 2; v += niceStep) {
    ticks.push(v);
  }
  return ticks.length ? ticks : [min, max];
}

function linePath(points: Array<{ x: number; y: number }>): string {
  if (!points.length) return "";
  if (points.length === 1) return `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
  let d = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
  for (let i = 1; i < points.length; i += 1) {
    const p0 = points[i - 1];
    const p1 = points[i];
    const midX = (p0.x + p1.x) / 2;
    d += ` C${midX.toFixed(1)},${p0.y.toFixed(1)} ${midX.toFixed(1)},${p1.y.toFixed(1)} ${p1.x.toFixed(1)},${p1.y.toFixed(1)}`;
  }
  return d;
}

function areaPath(points: Array<{ x: number; y: number }>, baselineY: number): string {
  if (!points.length) return "";
  const line = linePath(points);
  const first = points[0];
  const last = points[points.length - 1];
  return `${line} L${last.x.toFixed(1)},${baselineY.toFixed(1)} L${first.x.toFixed(1)},${baselineY.toFixed(1)} Z`;
}

function nearestIndex(series: PaperSample[], t: number): number {
  if (!series.length) return -1;
  let best = 0;
  let bestDist = Math.abs(series[0].t - t);
  for (let i = 1; i < series.length; i += 1) {
    const dist = Math.abs(series[i].t - t);
    if (dist < bestDist) {
      best = i;
      bestDist = dist;
    }
  }
  return best;
}

/** Live paper session chart with selectable time scale. */
export function PaperSessionChart({
  equity,
  cash,
  positions,
  pnl,
  live,
  maxPoints = 480,
}: PaperSessionChartProps) {
  const [range, setRange] = useState<PaperTimeRange>("1h");
  const [layers, setLayers] = useState<Record<SeriesKey, boolean>>(DEFAULT_LAYERS);
  const [series, setSeries] = useState<PaperSample[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sessionStartedAt, setSessionStartedAt] = useState<number | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const peakInView = useRef<number | null>(null);
  const lastPush = useRef(0);
  const lastSample = useRef<PaperSample | null>(null);
  const rangeRef = useRef(range);
  const liveRef = useRef({ equity, cash, positions, pnl });
  const svgRef = useRef<SVGSVGElement | null>(null);
  rangeRef.current = range;
  liveRef.current = { equity, cash, positions, pnl };

  useEffect(() => {
    setRange(loadRange());
    setLayers(loadLayers());
  }, []);

  const pushLiveSample = useCallback(
    (force = false) => {
      const snap = liveRef.current;
      if (!Number.isFinite(snap.equity) || !Number.isFinite(snap.cash)) return;
      const now = Date.now();
      const prev = lastSample.current;
      const moved =
        !prev ||
        Math.abs(prev.equity - snap.equity) >= 0.05 ||
        Math.abs(prev.cash - snap.cash) >= 0.05 ||
        prev.positions !== snap.positions ||
        Math.abs(prev.pnl - snap.pnl) >= 0.05;
      if (!force && prev && !moved && now - lastPush.current < 2000) return;

      const cutoff = rangeCutoffMs(rangeRef.current, sessionStartedAt);
      if (cutoff != null && now < cutoff) return;

      lastPush.current = now;
      peakInView.current =
        peakInView.current == null ? snap.equity : Math.max(peakInView.current, snap.equity);
      const peak = peakInView.current > 0 ? peakInView.current : snap.equity;
      const drawdownPct = peak > 0 ? Math.max(0, ((peak - snap.equity) / peak) * 100) : 0;
      const sample: PaperSample = {
        t: now,
        equity: snap.equity,
        cash: snap.cash,
        positions: Math.max(0, snap.positions),
        pnl: snap.pnl,
        drawdownPct,
      };
      lastSample.current = sample;
      setSeries((s) => {
        const base = s.filter((p) => (cutoff == null ? true : p.t >= cutoff));
        const withoutDup =
          base.length && Math.abs(base[base.length - 1].t - sample.t) < 400
            ? base.slice(0, -1)
            : base;
        const next = [...withoutDup, sample];
        return next.length > maxPoints ? next.slice(-maxPoints) : next;
      });
    },
    [maxPoints, sessionStartedAt],
  );

  const applyHistory = useCallback(
    async (nextRange: PaperTimeRange, opts?: { replace?: boolean }) => {
      setLoadingHistory(true);
      setHistoryError(null);
      try {
        const res = await api.equityHistory(nextRange);
        let sessionTs: number | null = null;
        if (res.session_started_at) {
          const ts = Date.parse(res.session_started_at);
          if (!Number.isNaN(ts)) {
            sessionTs = ts;
            setSessionStartedAt(ts);
          }
        }
        const mapped: PaperSample[] = (res.points || []).map((p) => ({
          t: Date.parse(p.t) || Date.now(),
          equity: Number(p.equity) || 0,
          cash: Number(p.cash) || 0,
          positions: Number(p.positions) || 0,
          pnl: Number(p.pnl) || 0,
          drawdownPct: Number(p.drawdown_pct) || 0,
        }));
        const cutoff = rangeCutoffMs(nextRange, sessionTs ?? sessionStartedAt);
        setSeries((prev) => {
          const byBucket = new Map<number, PaperSample>();
          const put = (p: PaperSample) => {
            if (cutoff != null && p.t < cutoff) return;
            const key = Math.round(p.t / 2000);
            const existing = byBucket.get(key);
            if (!existing || p.t >= existing.t) byBucket.set(key, p);
          };
          for (const p of mapped) put(p);
          // Keep dense live samples inside the new window; drop out-of-range points.
          if (!opts?.replace) {
            for (const p of prev) put(p);
          }
          const next = [...byBucket.values()].sort((a, b) => a.t - b.t);
          const trimmed = next.length > maxPoints ? next.slice(-maxPoints) : next;
          peakInView.current = trimmed.reduce(
            (m, s) => Math.max(m, s.equity),
            trimmed[0]?.equity ?? liveRef.current.equity,
          );
          lastSample.current = trimmed[trimmed.length - 1] ?? null;
          return trimmed;
        });
      } catch (err) {
        setHistoryError(err instanceof Error ? err.message : "Historique indisponible");
      } finally {
        setLoadingHistory(false);
      }
    },
    [maxPoints, sessionStartedAt],
  );

  useEffect(() => {
    void applyHistory(range, { replace: true });
  }, [range]); // eslint-disable-line react-hooks/exhaustive-deps -- only on range change

  useEffect(() => {
    const timer = window.setInterval(() => {
      void applyHistory(rangeRef.current, { replace: false });
    }, 20_000);
    return () => window.clearInterval(timer);
  }, [applyHistory]);

  function changeRange(next: PaperTimeRange) {
    if (next === range) return;
    setHoverIdx(null);
    setSeries([]);
    lastSample.current = null;
    peakInView.current = null;
    setRange(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }

  function toggleLayer(key: SeriesKey) {
    if (key === "equity") return;
    setLayers((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      try {
        window.localStorage.setItem(LAYERS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  useEffect(() => {
    pushLiveSample(true);
    const timer = window.setInterval(() => pushLiveSample(false), 2000);
    return () => window.clearInterval(timer);
  }, [pushLiveSample]);

  useEffect(() => {
    pushLiveSample(true);
  }, [equity, cash, positions, pnl, pushLiveSample]);

  const latest = series[series.length - 1];
  const chartPnl = latest?.pnl ?? pnl;
  const drawdownPct = latest?.drawdownPct ?? 0;
  const active = hoverIdx != null ? series[hoverIdx] : latest;

  const geometry = useMemo(() => {
    const domain = visibleDomainMs(range, sessionStartedAt, series);
    const span = Math.max(60_000, domain.end - domain.start);
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const plotSeries = series.filter((s) => s.t >= domain.start - 1000 && s.t <= domain.end + 1000);

    // Left axis = portfolio dollars only (equity + optional cash). Never mix with ~0 P&L.
    const moneyVals: number[] = [];
    if (plotSeries.length) {
      for (const s of plotSeries) {
        moneyVals.push(s.equity);
        if (layers.cash) moneyVals.push(s.cash);
      }
    } else {
      moneyVals.push(equity, cash);
    }
    let minM = Math.min(...moneyVals);
    let maxM = Math.max(...moneyVals);
    const center = (minM + maxM) / 2;
    if (maxM - minM < Math.max(2, Math.abs(center) * 0.004)) {
      const padFlat = Math.max(2, Math.abs(center) * 0.01);
      minM = center - padFlat;
      maxM = center + padFlat;
    } else {
      const pad = (maxM - minM) * 0.12;
      minM -= pad;
      maxM += pad;
    }

    const maxPos = Math.max(1, ...plotSeries.map((s) => s.positions), positions, 1);
    const maxDd = Math.max(
      0.5,
      ...plotSeries.map((s) => s.drawdownPct),
      drawdownPct,
      0.5,
    );

    // Optional P&L band uses its own normalized overlay near the bottom third when enabled.
    const pnlVals = plotSeries.length ? plotSeries.map((s) => s.pnl) : [pnl];
    let minPnl = Math.min(0, ...pnlVals);
    let maxPnl = Math.max(0, ...pnlVals);
    if (maxPnl - minPnl < 1) {
      minPnl -= 1;
      maxPnl += 1;
    }

    const xAtTime = (t: number) => {
      const clamped = Math.min(domain.end, Math.max(domain.start, t));
      return PAD.left + ((clamped - domain.start) / span) * innerW;
    };
    const yMoney = (v: number) => PAD.top + ((maxM - v) / (maxM - minM)) * innerH;
    const yPos = (v: number) => PAD.top + ((maxPos - v) / maxPos) * innerH * 0.85 + innerH * 0.08;
    const yDd = (v: number) => PAD.top + (v / maxDd) * innerH;
    const yPnl = (v: number) => {
      const top = PAD.top + innerH * 0.55;
      const h = innerH * 0.4;
      return top + ((maxPnl - v) / (maxPnl - minPnl)) * h;
    };

    const equityPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.equity) }));
    const cashPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.cash) }));
    const pnlPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yPnl(s.pnl) }));
    const posPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yPos(s.positions) }));
    const ddPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yDd(s.drawdownPct) }));

    const moneyTicks = niceMoneyTicks(minM, maxM, 5).map((v) => ({
      y: yMoney(v),
      label: money(v, v >= 100 || v <= -100 ? 0 : 2),
      value: v,
    }));

    const ddTicks =
      layers.drawdown
        ? [0, 0.5, 1].map((f) => {
            const v = f * maxDd;
            return { y: yDd(v), label: fmtPct(v, v < 1 ? 1 : 0) };
          })
        : [];

    const timeTicks = [0, 0.5, 1].map((f) => {
      const t = domain.start + span * f;
      return {
        x: PAD.left + innerW * f,
        label: formatAxisTime(t, span),
      };
    });

    return {
      domain,
      span,
      innerW,
      innerH,
      equityPts,
      cashPts,
      pnlPts,
      posPts,
      ddPts,
      moneyTicks,
      ddTicks,
      timeTicks,
      baselineY: PAD.top + innerH,
      zeroPnlY: yPnl(0),
      xAtTime,
      yMoney,
      yPnl,
      yPos,
      yDd,
      pointCount: series.length,
      hasSeries: series.length >= 1,
      sparse: domain.sparse,
      pnlPositive: chartPnl >= 0,
    };
  }, [
    series,
    chartPnl,
    drawdownPct,
    range,
    sessionStartedAt,
    equity,
    cash,
    pnl,
    positions,
    layers.cash,
    layers.drawdown,
  ]);

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    if (!series.length || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const t =
      geometry.domain.start +
      ((x - PAD.left) / Math.max(1, geometry.innerW)) * geometry.span;
    const idx = nearestIndex(series, t);
    if (idx >= 0) setHoverIdx(idx);
  }

  const hoverSample = hoverIdx != null ? series[hoverIdx] : null;
  const hoverX =
    hoverSample != null ? geometry.xAtTime(hoverSample.t) : null;

  return (
    <section className="card overflow-hidden">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-medium tracking-tight">Paper — session live</h2>
            {live ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-gain/15 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gain">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gain" />
                Live
              </span>
            ) : (
              <span className="rounded-full bg-slate-700/60 px-2.5 py-0.5 text-[10px] uppercase text-muted">
                Connexion…
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            Courbe portfolio · survol pour le détail · calques à droite
          </p>
        </div>
        <div
          className="flex flex-wrap gap-1 rounded-xl border border-line/70 bg-ink/50 p-1"
          role="group"
          aria-label="Échelle de temps"
        >
          {RANGE_OPTIONS.map((opt) => {
            const activeRange = range === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                title={opt.hint}
                disabled={loadingHistory}
                onClick={() => changeRange(opt.id)}
                className={
                  activeRange
                    ? "rounded-lg bg-sky-500/30 px-2.5 py-1.5 text-xs font-semibold text-sky-100 shadow-sm shadow-sky-900/30"
                    : "rounded-lg px-2.5 py-1.5 text-xs text-muted hover:bg-slate-700/40 hover:text-slate-200"
                }
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Metric
          label="Equity"
          value={`${money(active?.equity ?? equity)} $`}
          accent={COLORS.equity}
          highlight={hoverSample != null}
        />
        <Metric
          label="Cash"
          value={`${money(active?.cash ?? cash)} $`}
          accent={COLORS.cash}
          highlight={hoverSample != null}
        />
        <Metric
          label="Positions"
          value={String(active?.positions ?? positions)}
          accent={COLORS.positions}
          highlight={hoverSample != null}
        />
        <Metric
          label="P&L"
          value={`${(active?.pnl ?? pnl) >= 0 ? "+" : ""}${money(active?.pnl ?? pnl)} $`}
          className={pnlClass(active?.pnl ?? pnl)}
          accent={(active?.pnl ?? pnl) >= 0 ? COLORS.pnl : COLORS.pnlNeg}
          highlight={hoverSample != null}
        />
        <Metric
          label="Drawdown"
          value={fmtPct(active?.drawdownPct ?? drawdownPct)}
          className={(active?.drawdownPct ?? drawdownPct) > 0.05 ? "text-loss" : ""}
          accent={COLORS.drawdown}
          highlight={hoverSample != null}
        />
      </div>

      {historyError ? (
        <p className="mb-2 text-xs text-orange-300">{historyError}</p>
      ) : null}

      <div className="relative rounded-xl border border-line/50 bg-gradient-to-b from-slate-950/80 to-ink/90 p-2 sm:p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5">
            {(
              [
                ["equity", "Equity", COLORS.equity],
                ["cash", "Cash", COLORS.cash],
                ["pnl", "P&L", chartPnl >= 0 ? COLORS.pnl : COLORS.pnlNeg],
                ["positions", "Positions", COLORS.positions],
                ["drawdown", "DD %", COLORS.drawdown],
              ] as Array<[SeriesKey, string, string]>
            ).map(([key, label, color]) => {
              const on = layers[key];
              const locked = key === "equity";
              return (
                <button
                  key={key}
                  type="button"
                  disabled={locked}
                  onClick={() => toggleLayer(key)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                    on
                      ? "border-white/15 bg-white/5 text-slate-100"
                      : "border-transparent text-muted opacity-55 hover:opacity-80"
                  }`}
                  title={locked ? "Toujours visible" : on ? "Masquer" : "Afficher"}
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: on ? color : "#475569" }}
                  />
                  {label}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] tabular-nums text-muted">
            {loadingHistory ? "Maj…" : `${series.length} pts`}
            {geometry.sparse ? " · historique encore court" : ""} ·{" "}
            {RANGE_OPTIONS.find((o) => o.id === range)?.label ?? range}
            {hoverSample
              ? ` · ${formatAxisTime(hoverSample.t, geometry.span)}`
              : ""}
          </p>
        </div>

        {loadingHistory && !geometry.hasSeries ? (
          <div className="flex h-[320px] items-center justify-center text-sm text-muted">
            Chargement de la courbe…
          </div>
        ) : (
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full touch-pan-y"
            role="img"
            aria-label={`Évolution paper sur ${range}`}
            onPointerMove={onPointerMove}
            onPointerLeave={() => setHoverIdx(null)}
          >
            <defs>
              <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.equity} stopOpacity="0.35" />
                <stop offset="100%" stopColor={COLORS.equity} stopOpacity="0.02" />
              </linearGradient>
              <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur stdDeviation="2.2" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>

            {geometry.moneyTicks.map((g) => (
              <g key={`m-${g.value}`}>
                <line
                  x1={PAD.left}
                  x2={W - PAD.right}
                  y1={g.y}
                  y2={g.y}
                  stroke={COLORS.grid}
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 8}
                  y={g.y + 3}
                  textAnchor="end"
                  fill="#94a3b8"
                  fontSize={10}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  {g.label}
                </text>
              </g>
            ))}

            {layers.drawdown
              ? geometry.ddTicks.map((g, i) => (
                  <text
                    key={`dd-${i}-${g.label}`}
                    x={W - PAD.right + 8}
                    y={g.y + 3}
                    textAnchor="start"
                    fill={COLORS.drawdown}
                    fontSize={10}
                    opacity={0.85}
                    fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  >
                    {g.label}
                  </text>
                ))
              : null}

            {layers.drawdown ? (
              <text
                x={W - PAD.right + 8}
                y={PAD.top - 10}
                textAnchor="start"
                fill={COLORS.drawdown}
                fontSize={9}
                opacity={0.7}
              >
                DD
              </text>
            ) : null}

            {layers.positions && geometry.pointCount > 0 ? (
              <path
                d={linePath(geometry.posPts)}
                fill="none"
                stroke={COLORS.positions}
                strokeWidth={1.5}
                strokeDasharray="4 4"
                opacity={0.7}
              />
            ) : null}

            {layers.drawdown && geometry.pointCount > 0 ? (
              <path
                d={linePath(geometry.ddPts)}
                fill="none"
                stroke={COLORS.drawdown}
                strokeWidth={1.75}
                strokeDasharray="5 3"
                opacity={0.85}
              />
            ) : null}

            {layers.pnl && geometry.pointCount > 0 ? (
              <>
                <line
                  x1={PAD.left}
                  x2={W - PAD.right}
                  y1={geometry.zeroPnlY}
                  y2={geometry.zeroPnlY}
                  stroke={COLORS.axis}
                  strokeDasharray="3 4"
                  strokeWidth={1}
                  opacity={0.5}
                />
                <path
                  d={linePath(geometry.pnlPts)}
                  fill="none"
                  stroke={geometry.pnlPositive ? COLORS.pnl : COLORS.pnlNeg}
                  strokeWidth={1.75}
                  opacity={0.9}
                />
              </>
            ) : null}

            {layers.cash && geometry.pointCount > 0 ? (
              <path
                d={linePath(geometry.cashPts)}
                fill="none"
                stroke={COLORS.cash}
                strokeWidth={2}
                opacity={0.9}
              />
            ) : null}

            {geometry.pointCount > 0 ? (
              <>
                <path
                  d={areaPath(geometry.equityPts, geometry.baselineY)}
                  fill="url(#equityFill)"
                />
                <path
                  d={linePath(geometry.equityPts)}
                  fill="none"
                  stroke={COLORS.equity}
                  strokeWidth={2.6}
                  filter="url(#softGlow)"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </>
            ) : null}

            {geometry.pointCount === 1 && geometry.equityPts[0] ? (
              <circle
                cx={geometry.equityPts[0].x}
                cy={geometry.equityPts[0].y}
                r={5}
                fill={COLORS.equity}
                stroke={COLORS.panel}
                strokeWidth={2}
              />
            ) : null}

            {geometry.equityPts.length > 0 ? (
              <g>
                <circle
                  cx={geometry.equityPts[geometry.equityPts.length - 1].x}
                  cy={geometry.equityPts[geometry.equityPts.length - 1].y}
                  r={4.5}
                  fill={COLORS.equity}
                  stroke={COLORS.panel}
                  strokeWidth={2}
                />
              </g>
            ) : null}

            {hoverX != null && hoverSample ? (
              <g pointerEvents="none">
                <line
                  x1={hoverX}
                  x2={hoverX}
                  y1={PAD.top}
                  y2={H - PAD.bottom}
                  stroke={COLORS.cross}
                  strokeWidth={1}
                />
                <circle
                  cx={hoverX}
                  cy={geometry.yMoney(hoverSample.equity)}
                  r={5}
                  fill={COLORS.equity}
                  stroke="#fff"
                  strokeWidth={1.5}
                />
                <rect
                  x={Math.min(hoverX + 12, W - PAD.right - 150)}
                  y={PAD.top + 4}
                  width={138}
                  height={74}
                  rx={8}
                  fill="rgba(15,23,42,0.92)"
                  stroke="rgba(148,163,184,0.25)"
                />
                <text
                  x={Math.min(hoverX + 12, W - PAD.right - 150) + 10}
                  y={PAD.top + 22}
                  fill="#e2e8f0"
                  fontSize={10}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  {formatAxisTime(hoverSample.t, geometry.span)}
                </text>
                <text
                  x={Math.min(hoverX + 12, W - PAD.right - 150) + 10}
                  y={PAD.top + 40}
                  fill={COLORS.equity}
                  fontSize={11}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  Eq {money(hoverSample.equity)} $
                </text>
                <text
                  x={Math.min(hoverX + 12, W - PAD.right - 150) + 10}
                  y={PAD.top + 56}
                  fill={COLORS.cash}
                  fontSize={11}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  Cash {money(hoverSample.cash)} $
                </text>
                <text
                  x={Math.min(hoverX + 12, W - PAD.right - 150) + 10}
                  y={PAD.top + 72}
                  fill={hoverSample.pnl >= 0 ? COLORS.pnl : COLORS.pnlNeg}
                  fontSize={11}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                >
                  P&L {hoverSample.pnl >= 0 ? "+" : ""}
                  {money(hoverSample.pnl)} $
                </text>
              </g>
            ) : null}

            {geometry.timeTicks.map((tick, i) => (
              <text
                key={`t-${tick.x}-${tick.label}`}
                x={tick.x}
                y={H - 12}
                textAnchor={i === 0 ? "start" : i === geometry.timeTicks.length - 1 ? "end" : "middle"}
                fill="#94a3b8"
                fontSize={10}
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                {tick.label}
              </text>
            ))}

            {/* Invisible hit area */}
            <rect
              x={PAD.left}
              y={PAD.top}
              width={geometry.innerW}
              height={geometry.innerH}
              fill="transparent"
            />
          </svg>
        )}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  className = "",
  accent,
  highlight,
}: {
  label: string;
  value: string;
  className?: string;
  accent?: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`rounded-lg border px-3 py-2 transition ${
        highlight ? "border-sky-500/30 bg-sky-500/5" : "border-line/60 bg-ink/40"
      }`}
    >
      <p className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted">
        {accent ? (
          <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: accent }} />
        ) : null}
        {label}
      </p>
      <p className={`mt-0.5 text-base font-semibold tabular-nums tracking-tight ${className}`}>
        {value}
      </p>
    </div>
  );
}
