"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

const RANGE_OPTIONS: Array<{ id: PaperTimeRange; label: string; hint: string }> = [
  { id: "1h", label: "1h", hint: "Dernière heure" },
  { id: "6h", label: "6h", hint: "6 dernières heures" },
  { id: "24h", label: "24h", hint: "24 dernières heures" },
  { id: "1d", label: "1j", hint: "Dernier jour" },
  { id: "1w", label: "1 sem.", hint: "7 derniers jours" },
  { id: "1mo", label: "1 mois", hint: "30 derniers jours" },
  { id: "1y", label: "1 an", hint: "365 derniers jours" },
  { id: "session", label: "Session", hint: "Depuis le démarrage du moteur" },
  { id: "all", label: "Tout", hint: "Tout l’historique paper stocké" },
];

const VALID_RANGES = new Set<PaperTimeRange>(RANGE_OPTIONS.map((o) => o.id));

const STORAGE_KEY = "wt.paperChartRange";

const W = 740;
const H = 242;
const PAD = { top: 16, right: 44, bottom: 36, left: 52 };

const COLORS = {
  equity: "#38bdf8",
  cash: "#a78bfa",
  pnl: "#34d399",
  pnlNeg: "#f87171",
  positions: "#fbbf24",
  drawdown: "#fb7185",
  grid: "#1e293b",
  axis: "#64748b",
} as const;

function linePath(points: Array<{ x: number; y: number }>): string {
  if (!points.length) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
}

function fmtPct(v: number, digits = 2): string {
  return `${v.toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} %`;
}

function loadRange(): PaperTimeRange {
  if (typeof window === "undefined") return "1h";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw && VALID_RANGES.has(raw as PaperTimeRange)) return raw as PaperTimeRange;
  return "1h";
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

/** Visible X domain = selected preset window (not just min/max of samples). */
function rangeDomainMs(
  range: PaperTimeRange,
  sessionStartedAt: number | null,
  series: PaperSample[],
): { start: number; end: number } {
  const end = Date.now();
  const delta = rangeDeltaMs(range);
  if (delta != null) return { start: end - delta, end };
  if (range === "session") {
    const start = sessionStartedAt ?? series[0]?.t ?? end - 3600_000;
    return { start: Math.min(start, end - 60_000), end };
  }
  if (series.length >= 1) {
    const start = series[0].t;
    return { start, end: Math.max(end, series[series.length - 1].t, start + 60_000) };
  }
  return { start: end - 3600_000, end };
}

function formatAxisTime(t: number, spanMs: number): string {
  const opts: Intl.DateTimeFormatOptions =
    spanMs >= 60 * 24 * 3600_000
      ? { day: "2-digit", month: "short", year: "2-digit" }
      : spanMs >= 2 * 24 * 3600_000
        ? { day: "2-digit", month: "2-digit", hour: "2-digit" }
        : spanMs >= 3 * 3600_000
          ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }
          : { hour: "2-digit", minute: "2-digit", second: "2-digit" };
  return new Date(t).toLocaleString("fr-FR", opts);
}

/** Live paper session chart with selectable time scale. */
export function PaperSessionChart({
  equity,
  cash,
  positions,
  pnl,
  live,
  maxPoints = 400,
}: PaperSessionChartProps) {
  const [range, setRange] = useState<PaperTimeRange>("1h");
  const [series, setSeries] = useState<PaperSample[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sessionStartedAt, setSessionStartedAt] = useState<number | null>(null);
  const peakInView = useRef<number | null>(null);
  const lastPush = useRef(0);
  const lastSample = useRef<PaperSample | null>(null);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  useEffect(() => {
    setRange(loadRange());
  }, []);

  const applyHistory = useCallback(async (nextRange: PaperTimeRange) => {
    setLoadingHistory(true);
    setHistoryError(null);
    try {
      const res = await api.equityHistory(nextRange);
      if (res.session_started_at) {
        const ts = Date.parse(res.session_started_at);
        if (!Number.isNaN(ts)) setSessionStartedAt(ts);
      }
      const mapped: PaperSample[] = (res.points || []).map((p) => ({
        t: Date.parse(p.t) || Date.now(),
        equity: Number(p.equity) || 0,
        cash: Number(p.cash) || 0,
        positions: Number(p.positions) || 0,
        pnl: Number(p.pnl) || 0,
        drawdownPct: Number(p.drawdown_pct) || 0,
      }));
      peakInView.current = mapped.reduce(
        (m, s) => Math.max(m, s.equity),
        mapped[0]?.equity ?? 0,
      );
      lastSample.current = mapped[mapped.length - 1] ?? null;
      setSeries(mapped.length ? mapped : []);
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : "Historique indisponible");
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    void applyHistory(range);
  }, [range, applyHistory]);

  function changeRange(next: PaperTimeRange) {
    setRange(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }

  // Append live ticks into the active window.
  useEffect(() => {
    if (!Number.isFinite(equity) || !Number.isFinite(cash)) return;
    const now = Date.now();
    const prev = lastSample.current;
    const moved =
      !prev ||
      Math.abs(prev.equity - equity) >= 0.05 ||
      Math.abs(prev.cash - cash) >= 0.05 ||
      prev.positions !== positions;
    if (prev && !moved && now - lastPush.current < 1500) return;

    const cutoff = rangeCutoffMs(rangeRef.current, sessionStartedAt);
    if (cutoff != null && now < cutoff) return;

    lastPush.current = now;
    peakInView.current =
      peakInView.current == null ? equity : Math.max(peakInView.current, equity);
    const peak = peakInView.current > 0 ? peakInView.current : equity;
    const drawdownPct = peak > 0 ? Math.max(0, ((peak - equity) / peak) * 100) : 0;
    const sample: PaperSample = {
      t: now,
      equity,
      cash,
      positions: Math.max(0, positions),
      pnl,
      drawdownPct,
    };
    lastSample.current = sample;
    setSeries((s) => {
      const base = s.filter((p) => (cutoff == null ? true : p.t >= cutoff));
      const next = [...base, sample];
      return next.length > maxPoints ? next.slice(-maxPoints) : next;
    });
  }, [equity, cash, positions, pnl, maxPoints, sessionStartedAt]);

  const latest = series[series.length - 1];
  const chartPnl = latest?.pnl ?? pnl;
  const drawdownPct = latest?.drawdownPct ?? 0;

  const geometry = useMemo(() => {
    const domain = rangeDomainMs(range, sessionStartedAt, series);
    const span = Math.max(60_000, domain.end - domain.start);
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const moneyVals =
      series.length > 0
        ? series.flatMap((s) => [s.equity, s.cash, s.pnl])
        : [equity, cash, pnl];
    let minM = Math.min(...moneyVals);
    let maxM = Math.max(...moneyVals);
    if (maxM - minM < 1) {
      minM -= 1;
      maxM += 1;
    }
    const pad = (maxM - minM) * 0.08;
    minM -= pad;
    maxM += pad;

    const maxPos = Math.max(1, ...series.map((s) => s.positions), positions);
    const maxDd = Math.max(1, ...series.map((s) => s.drawdownPct), drawdownPct);

    const xAtTime = (t: number) => {
      const clamped = Math.min(domain.end, Math.max(domain.start, t));
      return PAD.left + ((clamped - domain.start) / span) * innerW;
    };
    const yMoney = (v: number) =>
      PAD.top + ((maxM - v) / (maxM - minM)) * innerH;
    const yPos = (v: number) =>
      PAD.top + ((maxPos - v) / maxPos) * innerH;
    const yDd = (v: number) => PAD.top + (v / maxDd) * innerH;

    const equityPts = series.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.equity) }));
    const cashPts = series.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.cash) }));
    const pnlPts = series.map((s) => ({ x: xAtTime(s.t), y: yMoney(s.pnl) }));
    const posPts = series.map((s) => ({ x: xAtTime(s.t), y: yPos(s.positions) }));
    const ddPts = series.map((s) => ({ x: xAtTime(s.t), y: yDd(s.drawdownPct) }));

    const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const v = maxM - f * (maxM - minM);
      return { y: yMoney(v), label: money(v, 0) };
    });
    const ddTicks = [0, 0.5, 1].map((f) => {
      const v = f * maxDd;
      return { y: yDd(v), label: fmtPct(v, f === 0 ? 0 : 1) };
    });

    const dataSpan = series.length
      ? series[series.length - 1].t - series[0].t
      : 0;
    const coverageNote =
      dataSpan > 0 && dataSpan < span * 0.35
        ? `Données depuis ${formatAxisTime(series[0].t, span)} (historique encore court)`
        : null;

    // Mid tick for long windows so the domain is obvious.
    const mid = domain.start + span / 2;
    const timeTicks = [
      { x: PAD.left, label: formatAxisTime(domain.start, span) },
      { x: PAD.left + innerW / 2, label: formatAxisTime(mid, span) },
      { x: PAD.left + innerW, label: formatAxisTime(domain.end, span) },
    ];

    return {
      equityPts,
      cashPts,
      pnlPts,
      posPts,
      ddPts,
      gridY,
      ddTicks,
      timeTicks,
      coverageNote,
      zeroY: yMoney(0),
      pnlPositive: chartPnl >= 0,
      pointCount: series.length,
      domainSpanMs: span,
      hasSeries: series.length >= 1,
    };
  }, [series, chartPnl, drawdownPct, range, sessionStartedAt, equity, cash, pnl, positions]);

  return (
    <section className="card">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium">Paper — session live</h2>
          <p className="text-xs text-muted">
            Equity, cash, positions, P&amp;L et drawdown — choisissez l’échelle de temps
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div
            className="flex flex-wrap gap-1 rounded-lg border border-line/70 bg-ink/40 p-1"
            role="group"
            aria-label="Échelle de temps du graphique paper"
          >
            {RANGE_OPTIONS.map((opt) => {
              const active = range === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  title={opt.hint}
                  disabled={loadingHistory}
                  onClick={() => changeRange(opt.id)}
                  className={
                    active
                      ? "rounded-md bg-sky-500/25 px-2.5 py-1 text-xs font-semibold text-sky-200"
                      : "rounded-md px-2.5 py-1 text-xs text-muted hover:bg-slate-700/50 hover:text-slate-200"
                  }
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          {live ? (
            <span className="rounded-full bg-gain/20 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-gain">
              Flux actif
            </span>
          ) : (
            <span className="rounded-full bg-slate-700/60 px-2.5 py-1 text-[10px] uppercase text-muted">
              Connexion…
            </span>
          )}
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Metric label="Equity" value={`${money(equity)} $`} />
        <Metric label="Cash" value={`${money(cash)} $`} />
        <Metric label="Positions" value={String(positions)} />
        <Metric
          label="P&L total"
          value={`${pnl >= 0 ? "+" : ""}${money(pnl)} $`}
          className={pnlClass(pnl)}
        />
        <Metric
          label="Drawdown"
          value={fmtPct(drawdownPct)}
          className={drawdownPct > 0.05 ? "text-loss" : ""}
        />
      </div>

      {historyError ? (
        <p className="mb-2 text-xs text-orange-300">{historyError}</p>
      ) : null}

      {loadingHistory && !geometry?.hasSeries ? (
        <div className="flex h-[242px] items-center justify-center rounded-lg border border-dashed border-line/60 text-sm text-muted">
          Chargement de l’échelle {range}…
        </div>
      ) : !geometry ? (
        <div className="flex h-[242px] items-center justify-center rounded-lg border border-dashed border-line/60 text-sm text-muted">
          Fenêtre indisponible.
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="h-auto w-full overflow-visible"
          role="img"
          aria-label={`Évolution paper sur ${range}`}
        >
          {geometry.gridY.map((g) => (
            <g key={`m-${g.y}`}>
              <line
                x1={PAD.left}
                x2={W - PAD.right}
                y1={g.y}
                y2={g.y}
                stroke={COLORS.grid}
                strokeWidth={1}
              />
              <text
                x={PAD.left - 6}
                y={g.y + 3}
                textAnchor="end"
                className="fill-slate-500"
                fontSize={9}
              >
                {g.label}
              </text>
            </g>
          ))}
          {geometry.ddTicks.map((g) => (
            <text
              key={`dd-${g.y}`}
              x={W - PAD.right + 6}
              y={g.y + 3}
              textAnchor="start"
              fill={COLORS.drawdown}
              fontSize={9}
              opacity={0.9}
            >
              {g.label}
            </text>
          ))}
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={geometry.zeroY}
            y2={geometry.zeroY}
            stroke={COLORS.axis}
            strokeDasharray="4 4"
            strokeWidth={1}
          />
          <path
            d={linePath(geometry.posPts)}
            fill="none"
            stroke={COLORS.positions}
            strokeWidth={1.5}
            strokeDasharray="3 3"
            opacity={0.75}
          />
          <path
            d={linePath(geometry.ddPts)}
            fill="none"
            stroke={COLORS.drawdown}
            strokeWidth={2}
            strokeDasharray="5 3"
          />
          <path d={linePath(geometry.cashPts)} fill="none" stroke={COLORS.cash} strokeWidth={1.75} />
          <path
            d={linePath(geometry.pnlPts)}
            fill="none"
            stroke={geometry.pnlPositive ? COLORS.pnl : COLORS.pnlNeg}
            strokeWidth={1.75}
          />
          <path
            d={linePath(geometry.equityPts)}
            fill="none"
            stroke={COLORS.equity}
            strokeWidth={2.25}
          />
          {geometry.timeTicks.map((tick, i) => (
            <text
              key={`t-${tick.x}-${tick.label}`}
              x={tick.x}
              y={H - 8}
              textAnchor={i === 0 ? "start" : i === geometry.timeTicks.length - 1 ? "end" : "middle"}
              className="fill-slate-400"
              fontSize={9}
            >
              {tick.label}
            </text>
          ))}
          <text
            x={W - PAD.right + 6}
            y={PAD.top - 4}
            textAnchor="start"
            fill={COLORS.drawdown}
            fontSize={8}
          >
            DD %
          </text>
        </svg>
      )}

      {geometry?.coverageNote ? (
        <p className="mt-1 text-[11px] text-amber-200/90">{geometry.coverageNote}</p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <ul className="flex flex-wrap gap-4">
          <Legend color={COLORS.equity} label="Equity" />
          <Legend color={COLORS.cash} label="Cash" />
          <Legend color={pnl >= 0 ? COLORS.pnl : COLORS.pnlNeg} label="P&L" />
          <Legend color={COLORS.positions} label="Positions" dashed />
          <Legend color={COLORS.drawdown} label="Drawdown % (axe droit)" dashed />
        </ul>
        <span>
          {loadingHistory ? "Maj…" : `${series.length} pts`} · fenêtre{" "}
          {RANGE_OPTIONS.find((o) => o.id === range)?.label ?? range}
        </span>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  className = "",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`text-base font-medium tabular-nums ${className}`}>{value}</p>
    </div>
  );
}

function Legend({
  color,
  label,
  dashed,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        className="inline-block h-0.5 w-4 rounded"
        style={{
          backgroundColor: dashed ? "transparent" : color,
          borderTop: dashed ? `2px dashed ${color}` : undefined,
        }}
      />
      {label}
    </li>
  );
}
