"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { api, money, pnlClass } from "@/lib/engine";
import {
  areaPath,
  domainForRange,
  linePath,
  pathSkippingInvalid,
  rangeDeltaMs,
  signedAreaPaths,
  type PaperTimeRange,
  type XY,
} from "@/lib/paper-chart-math";

export type PaperSample = {
  t: number;
  equity: number;
  cash: number;
  positions: number;
  pnl: number;
  drawdownPct: number;
};

export type { PaperTimeRange };

type PaperSessionChartProps = {
  equity: number;
  cash: number;
  positions: number;
  pnl: number;
  /** Break-even equity (paper initial balance). Used for gain/loss coloring. */
  initialBalance?: number;
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
const LAYERS_KEY = "wt.paperChartLayers.v2";

const W = 900;
const H = 300;
const PAD = { top: 24, right: 20, bottom: 36, left: 58 };
const DISPLAY_CAP = 160;

const COLORS = {
  equity: "#38bdf8",
  cash: "#a78bfa",
  pnl: "#34d399",
  pnlNeg: "#f87171",
  gainFill: "rgba(52,211,153,0.22)",
  lossFill: "rgba(248,113,113,0.20)",
  breakEven: "rgba(148,163,184,0.55)",
  positions: "#fbbf24",
  drawdown: "#fb7185",
  grid: "rgba(148,163,184,0.10)",
  cross: "rgba(226,232,240,0.4)",
  panel: "#0b1220",
} as const;

/** Clean defaults: equity + cash only. DD/P&L/positions off (they made the chart unreadable). */
const DEFAULT_LAYERS: Record<SeriesKey, boolean> = {
  equity: true,
  cash: true,
  pnl: false,
  positions: false,
  drawdown: false,
};

function loadRange(): PaperTimeRange {
  if (typeof window === "undefined") return "1h";
  const raw = window.localStorage.getItem(STORAGE_KEY);
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

function rangeCutoffMs(range: PaperTimeRange, sessionStartedAt: number | null): number | null {
  const delta = rangeDeltaMs(range);
  if (delta != null) return Date.now() - delta;
  if (range === "session") return sessionStartedAt;
  return null;
}

function formatAxisTime(t: number, spanMs: number): string {
  const opts: Intl.DateTimeFormatOptions =
    spanMs >= 60 * 24 * 3600_000
      ? { day: "2-digit", month: "short" }
      : spanMs >= 2 * 24 * 3600_000
        ? { day: "2-digit", month: "2-digit", hour: "2-digit" }
        : spanMs >= 3 * 3600_000
          ? { day: "2-digit", hour: "2-digit", minute: "2-digit" }
          : { hour: "2-digit", minute: "2-digit" };
  return new Date(t).toLocaleString("fr-FR", opts);
}

function fmtPct(v: number, digits = 2): string {
  return `${v.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} %`;
}

function windowLabel(range: PaperTimeRange, spanMs: number): string {
  // Fixed ranges: always show the selected scale (never derive from fitted/data span).
  if (range !== "session" && range !== "all") {
    return RANGE_OPTIONS.find((o) => o.id === range)?.label ?? range;
  }
  const opt = RANGE_OPTIONS.find((o) => o.id === range)?.label ?? range;
  const hours = spanMs / 3600_000;
  if (hours < 1.5) return `${opt} (${Math.max(1, Math.round(spanMs / 60_000))} min)`;
  if (hours < 36) return `${opt} (${Math.max(1, Math.round(hours))} h)`;
  const days = hours / 24;
  if (days < 40) return `${opt} (${Math.round(days)} j)`;
  return opt;
}

/** Drop hydrate glitches that collapse the Y axis to 0. */
function sanitize(samples: PaperSample[]): PaperSample[] {
  return samples.filter((s) => {
    if (!Number.isFinite(s.t) || !Number.isFinite(s.equity)) return false;
    if (s.equity <= 0) return false;
    // API used to record cash=0 / pnl=0 ghosts with no book.
    if (s.cash === 0 && s.positions === 0 && s.equity > 10) return false;
    return true;
  });
}

/** Evenly downsample for SVG (keeps first/last). */
function downsample(samples: PaperSample[], maxOut: number): PaperSample[] {
  if (samples.length <= maxOut || maxOut < 3) return samples;
  const out: PaperSample[] = [samples[0]];
  const step = (samples.length - 1) / (maxOut - 1);
  for (let i = 1; i < maxOut - 1; i += 1) {
    out.push(samples[Math.round(i * step)]);
  }
  out.push(samples[samples.length - 1]);
  return out;
}

function niceMoneyTicks(min: number, max: number, count = 4): number[] {
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

/** Split polyline by signed scalar series (gain ≥ 0 / loss < 0), inserting baseline crosses. */
function strokeBySign(
  points: XY[],
  values: number[],
  zeroY: number,
): { positive: XY[][]; negative: XY[][] } {
  const positive: XY[][] = [];
  const negative: XY[][] = [];
  if (points.length < 2 || points.length !== values.length) {
    return { positive, negative };
  }
  let current: XY[] = [points[0]];
  let sign = Math.sign(values[0]) || 1;
  const flush = () => {
    if (current.length >= 2) {
      if (sign >= 0) positive.push(current);
      else negative.push(current);
    }
    current = [];
  };
  for (let i = 1; i < points.length; i += 1) {
    const prevV = values[i - 1];
    const nextV = values[i];
    let nextSign = Math.sign(nextV);
    if (nextSign === 0) nextSign = sign;
    if (sign !== nextSign) {
      let crossX: number;
      if (prevV === 0) crossX = points[i - 1].x;
      else if (nextV === 0) crossX = points[i].x;
      else {
        const t = prevV / (prevV - nextV);
        crossX = points[i - 1].x + (points[i].x - points[i - 1].x) * t;
      }
      current.push({ x: crossX, y: zeroY });
      flush();
      current = [{ x: crossX, y: zeroY }];
      if (nextV !== 0) current.push(points[i]);
      sign = nextSign;
    } else {
      current.push(points[i]);
    }
  }
  flush();
  return { positive, negative };
}

export function PaperSessionChart({
  equity,
  cash,
  positions,
  pnl,
  initialBalance,
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

  const breakEven =
    Number.isFinite(initialBalance) && (initialBalance ?? 0) > 0
      ? Number(initialBalance)
      : null;
  const clipId = useId().replace(/:/g, "");

  useEffect(() => {
    setRange(loadRange());
    setLayers(loadLayers());
  }, []);

  const pushLiveSample = useCallback(
    (force = false) => {
      const snap = liveRef.current;
      if (!Number.isFinite(snap.equity) || snap.equity <= 0) return;
      const now = Date.now();
      const prev = lastSample.current;
      const moved =
        !prev ||
        Math.abs(prev.equity - snap.equity) >= 0.05 ||
        Math.abs(prev.cash - snap.cash) >= 0.05 ||
        prev.positions !== snap.positions;
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
        const next = sanitize([...withoutDup, sample]);
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
        const mapped = sanitize(
          (res.points || []).map((p) => ({
            t: Date.parse(p.t) || Date.now(),
            equity: Number(p.equity) || 0,
            cash: Number(p.cash) || 0,
            positions: Number(p.positions) || 0,
            pnl: Number(p.pnl) || 0,
            drawdownPct: Number(p.drawdown_pct) || 0,
          })),
        );
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
          if (!opts?.replace) {
            for (const p of prev) put(p);
          }
          const next = sanitize([...byBucket.values()].sort((a, b) => a.t - b.t));
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
    // Intentionally only when range changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void applyHistory(rangeRef.current, { replace: false });
    }, 25_000);
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

  const domain = useMemo(
    () => domainForRange(range, sessionStartedAt, series),
    [range, sessionStartedAt, series],
  );
  const span = Math.max(60_000, domain.end - domain.start);

  const plotRaw = useMemo(() => {
    return series.filter((s) => s.t >= domain.start - 500 && s.t <= domain.end + 500);
  }, [series, domain.start, domain.end]);

  const plotSeries = useMemo(() => downsample(plotRaw, DISPLAY_CAP), [plotRaw]);

  const latest = series[series.length - 1];
  const chartPnl = latest?.pnl ?? pnl;
  const drawdownPct = latest?.drawdownPct ?? 0;
  const active = hoverIdx != null ? plotSeries[hoverIdx] ?? latest : latest;

  const geometry = useMemo(() => {
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;

    const moneyVals: number[] = [];
    if (plotSeries.length) {
      for (const s of plotSeries) {
        moneyVals.push(s.equity);
        // Only scale against plausible cash — zeros collapse the axis / shoot lines off-chart.
        if (layers.cash && s.cash > 0 && s.cash <= s.equity * 1.05) moneyVals.push(s.cash);
      }
    } else {
      moneyVals.push(equity);
      if (cash > 0) moneyVals.push(cash);
    }
    if (breakEven != null && breakEven > 0) moneyVals.push(breakEven);

    let minM = Math.min(...moneyVals);
    let maxM = Math.max(...moneyVals);
    const mid = (minM + maxM) / 2;
    if (maxM - minM < Math.max(1, Math.abs(mid) * 0.008)) {
      const padFlat = Math.max(3, Math.abs(mid) * 0.015);
      minM = mid - padFlat;
      maxM = mid + padFlat;
    } else {
      const pad = (maxM - minM) * 0.1;
      minM -= pad;
      maxM += pad;
    }

    const maxPos = Math.max(1, ...plotSeries.map((s) => s.positions), positions, 1);
    const maxDd = Math.max(0.25, ...plotSeries.map((s) => s.drawdownPct), drawdownPct, 0.25);

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
    const yPos = (v: number) => PAD.top + ((maxPos - v) / maxPos) * innerH;
    const yDd = (v: number) => PAD.top + (v / maxDd) * innerH;
    const yPnl = (v: number) => PAD.top + ((maxPnl - v) / (maxPnl - minPnl)) * innerH;

    const equityPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.equity) }));
    const equityVals = plotSeries.map((s) => s.equity);
    const cashPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.cash) }));
    const cashValid = plotSeries.map(
      (s) => Number.isFinite(s.cash) && s.cash > 0 && s.cash <= Math.max(s.equity, 1) * 1.05,
    );
    const pnlPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yPnl(s.pnl) }));
    const posPts = plotSeries.map((s) => ({ x: xAtTime(s.t), y: yPos(s.positions) }));
    const ddPts = plotSeries.map((s) => ({
      x: xAtTime(s.t),
      y: yDd(Math.min(s.drawdownPct, maxDd)),
    }));

    const breakEvenY = breakEven != null ? yMoney(breakEven) : null;
    const signedFill =
      breakEven != null && breakEvenY != null && equityPts.length > 1
        ? signedAreaPaths(equityPts, equityVals, breakEvenY, breakEven)
        : { gain: "", loss: "" };

    const equityStroke =
      breakEven != null && breakEvenY != null && equityPts.length > 1
        ? strokeBySign(
            equityPts,
            equityVals.map((v) => v - breakEven),
            breakEvenY,
          )
        : { positive: equityPts.length ? [equityPts] : [], negative: [] as XY[][] };

    const pnlStroke =
      pnlPts.length > 1
        ? strokeBySign(
            pnlPts,
            plotSeries.map((s) => s.pnl),
            yPnl(0),
          )
        : { positive: [] as XY[][], negative: [] as XY[][] };

    const moneyTicks = niceMoneyTicks(minM, maxM, 4).map((v) => ({
      y: yMoney(v),
      label: money(v, Math.abs(v) >= 100 ? 0 : 2),
      value: v,
    }));

    const timeTicks = [0, 0.5, 1].map((f) => {
      const t = domain.start + span * f;
      return { x: PAD.left + innerW * f, label: formatAxisTime(t, span) };
    });

    const dataSpan =
      plotRaw.length >= 2 ? plotRaw[plotRaw.length - 1].t - plotRaw[0].t : 0;

    return {
      equityPts,
      cashPts,
      cashPath: pathSkippingInvalid(cashPts, cashValid),
      pnlPts,
      posPts,
      ddPts,
      moneyTicks,
      timeTicks,
      baselineY: PAD.top + innerH,
      breakEvenY,
      signedFill,
      equityStroke,
      pnlStroke,
      pnlZeroY: yPnl(0),
      innerW,
      innerH,
      xAtTime,
      yMoney,
      pointCount: plotSeries.length,
      rawCount: plotRaw.length,
      hasSeries: plotSeries.length >= 1,
      sparse: plotRaw.length < 3 || dataSpan < span * 0.25,
      pnlPositive: chartPnl >= 0,
    };
  }, [
    plotSeries,
    plotRaw,
    chartPnl,
    drawdownPct,
    domain,
    span,
    equity,
    cash,
    pnl,
    positions,
    layers.cash,
    breakEven,
  ]);

  function onPointerMove(e: ReactPointerEvent<SVGSVGElement>) {
    if (!plotSeries.length || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const t = domain.start + ((x - PAD.left) / Math.max(1, geometry.innerW)) * span;
    const idx = nearestIndex(plotSeries, t);
    if (idx >= 0) setHoverIdx(idx);
  }

  const hoverSample = hoverIdx != null ? plotSeries[hoverIdx] : null;
  const hoverX = hoverSample != null ? geometry.xAtTime(hoverSample.t) : null;
  const activePnl = active?.pnl ?? pnl;
  const inGain = breakEven != null ? (active?.equity ?? equity) >= breakEven : activePnl >= 0;

  return (
    <section className="card overflow-hidden">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
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
            {geometry.hasSeries ? (
              <span
                className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                  inGain ? "bg-gain/15 text-gain" : "bg-loss/15 text-loss"
                }`}
              >
                {inGain ? "En gain" : "En perte"}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-xs text-muted">
            Fenêtre <span className="text-slate-300">{windowLabel(range, span)}</span>
            {" · "}
            equity {layers.cash ? "+ cash" : ""}
            {breakEven != null ? ` · seuil ${money(breakEven)} $` : ""}
            {geometry.sparse ? " · données encore courtes sur cette échelle" : ""}
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
                disabled={loadingHistory && activeRange}
                onClick={() => changeRange(opt.id)}
                className={
                  activeRange
                    ? "rounded-lg bg-sky-500/30 px-2.5 py-1.5 text-xs font-semibold text-sky-100"
                    : "rounded-lg px-2.5 py-1.5 text-xs text-muted hover:bg-slate-700/40 hover:text-slate-200"
                }
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
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

      {historyError ? <p className="mb-2 text-xs text-orange-300">{historyError}</p> : null}

      <div className="relative rounded-xl border border-line/50 bg-gradient-to-b from-slate-950/70 to-ink/90 p-2 sm:p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-1.5">
            {(
              [
                ["equity", "Equity", COLORS.equity],
                ["cash", "Cash", COLORS.cash],
                ["pnl", "P&L", chartPnl >= 0 ? COLORS.pnl : COLORS.pnlNeg],
                ["positions", "Pos.", COLORS.positions],
                ["drawdown", "DD %", COLORS.drawdown],
              ] as Array<[SeriesKey, string, string]>
            ).map(([key, label, color]) => {
              const on = layers[key];
              return (
                <button
                  key={key}
                  type="button"
                  disabled={key === "equity"}
                  onClick={() => toggleLayer(key)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition ${
                    on
                      ? "border-white/15 bg-white/5 text-slate-100"
                      : "border-transparent text-muted opacity-50 hover:opacity-80"
                  }`}
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
            {loadingHistory ? "Maj…" : `${geometry.rawCount} pts`}
            {hoverSample ? ` · ${formatAxisTime(hoverSample.t, span)}` : ""}
          </p>
        </div>

        {loadingHistory && !geometry.hasSeries ? (
          <div className="flex h-[300px] items-center justify-center text-sm text-muted">
            Chargement…
          </div>
        ) : (
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full"
            role="img"
            aria-label={`Paper ${range}`}
            onPointerMove={onPointerMove}
            onPointerLeave={() => setHoverIdx(null)}
          >
            <defs>
              <clipPath id={`paper-plot-clip-${clipId}`}>
                <rect
                  x={PAD.left}
                  y={PAD.top}
                  width={geometry.innerW}
                  height={geometry.innerH}
                />
              </clipPath>
              <linearGradient id={`eqFill-${clipId}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLORS.equity} stopOpacity="0.18" />
                <stop offset="100%" stopColor={COLORS.equity} stopOpacity="0.02" />
              </linearGradient>
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

            <g clipPath={`url(#paper-plot-clip-${clipId})`}>
              {layers.positions && geometry.pointCount > 1 ? (
                <path
                  d={linePath(geometry.posPts)}
                  fill="none"
                  stroke={COLORS.positions}
                  strokeWidth={1.25}
                  strokeDasharray="4 3"
                  opacity={0.65}
                />
              ) : null}

              {layers.drawdown && geometry.pointCount > 1 ? (
                <path
                  d={linePath(geometry.ddPts)}
                  fill="none"
                  stroke={COLORS.drawdown}
                  strokeWidth={1.25}
                  strokeDasharray="5 3"
                  opacity={0.75}
                />
              ) : null}

              {layers.pnl && geometry.pointCount > 1 ? (
                <>
                  <line
                    x1={PAD.left}
                    x2={W - PAD.right}
                    y1={geometry.pnlZeroY}
                    y2={geometry.pnlZeroY}
                    stroke={COLORS.breakEven}
                    strokeWidth={1}
                    strokeDasharray="3 3"
                    opacity={0.7}
                  />
                  {geometry.pnlStroke.positive.map((seg, i) => (
                    <path
                      key={`pnl-pos-${i}`}
                      d={linePath(seg)}
                      fill="none"
                      stroke={COLORS.pnl}
                      strokeWidth={1.6}
                      opacity={0.9}
                    />
                  ))}
                  {geometry.pnlStroke.negative.map((seg, i) => (
                    <path
                      key={`pnl-neg-${i}`}
                      d={linePath(seg)}
                      fill="none"
                      stroke={COLORS.pnlNeg}
                      strokeWidth={1.6}
                      opacity={0.9}
                    />
                  ))}
                </>
              ) : null}

              {layers.cash && geometry.pointCount > 0 && geometry.cashPath ? (
                <path
                  d={geometry.cashPath}
                  fill="none"
                  stroke={COLORS.cash}
                  strokeWidth={1.75}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ) : null}

              {geometry.pointCount > 0 ? (
                <>
                  {geometry.pointCount > 1 && breakEven != null && geometry.signedFill.gain ? (
                    <path d={geometry.signedFill.gain} fill={COLORS.gainFill} />
                  ) : null}
                  {geometry.pointCount > 1 && breakEven != null && geometry.signedFill.loss ? (
                    <path d={geometry.signedFill.loss} fill={COLORS.lossFill} />
                  ) : null}
                  {geometry.pointCount > 1 && breakEven == null ? (
                    <path
                      d={areaPath(geometry.equityPts, geometry.baselineY)}
                      fill={`url(#eqFill-${clipId})`}
                    />
                  ) : null}

                  {breakEven != null && geometry.breakEvenY != null ? (
                    <>
                      <line
                        x1={PAD.left}
                        x2={W - PAD.right}
                        y1={geometry.breakEvenY}
                        y2={geometry.breakEvenY}
                        stroke={COLORS.breakEven}
                        strokeWidth={1.25}
                        strokeDasharray="5 4"
                      />
                      <text
                        x={W - PAD.right - 4}
                        y={geometry.breakEvenY - 4}
                        textAnchor="end"
                        fill="#94a3b8"
                        fontSize={9}
                        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                      >
                        seuil
                      </text>
                    </>
                  ) : null}

                  {breakEven != null ? (
                    <>
                      {geometry.equityStroke.positive.map((seg, i) => (
                        <path
                          key={`eq-pos-${i}`}
                          d={linePath(seg)}
                          fill="none"
                          stroke={COLORS.pnl}
                          strokeWidth={2.4}
                          strokeLinejoin="round"
                          strokeLinecap="round"
                        />
                      ))}
                      {geometry.equityStroke.negative.map((seg, i) => (
                        <path
                          key={`eq-neg-${i}`}
                          d={linePath(seg)}
                          fill="none"
                          stroke={COLORS.pnlNeg}
                          strokeWidth={2.4}
                          strokeLinejoin="round"
                          strokeLinecap="round"
                        />
                      ))}
                    </>
                  ) : (
                    <path
                      d={linePath(geometry.equityPts)}
                      fill="none"
                      stroke={COLORS.equity}
                      strokeWidth={2.4}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                    />
                  )}
                  <circle
                    cx={geometry.equityPts[geometry.equityPts.length - 1].x}
                    cy={geometry.equityPts[geometry.equityPts.length - 1].y}
                    r={4}
                    fill={inGain ? COLORS.pnl : COLORS.pnlNeg}
                    stroke={COLORS.panel}
                    strokeWidth={2}
                  />
                </>
              ) : null}
            </g>

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
                  r={4.5}
                  fill={
                    breakEven != null && hoverSample.equity < breakEven
                      ? COLORS.pnlNeg
                      : COLORS.pnl
                  }
                  stroke="#fff"
                  strokeWidth={1.5}
                />
              </g>
            ) : null}

            {geometry.timeTicks.map((tick, i) => (
              <text
                key={`t-${i}-${tick.label}`}
                x={tick.x}
                y={H - 10}
                textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
                fill="#94a3b8"
                fontSize={10}
                fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
              >
                {tick.label}
              </text>
            ))}

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
