"use client";

import { useCallback, useEffect, useState } from "react";
import { api, pnlClass, type DailyReport, type PerformanceMetrics } from "@/lib/engine";

function money(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)} $`;
}

function shiftDate(iso: string, deltaDays: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + deltaDays);
  return dt.toISOString().slice(0, 10);
}

export function DailyDigestCard() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [range, setRange] = useState<"session" | "7d" | "30d" | "all">("session");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async (day: string | null, perfRange: typeof range) => {
    setLoading(true);
    setError(null);
    try {
      const [daily, perf] = await Promise.all([
        api.dailyReport(day || undefined),
        api.performanceMetrics(perfRange),
      ]);
      setReport(daily);
      setDate(daily.date);
      setMetrics(perf);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chargement bilan échoué");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(null, "session");
  }, [refresh]);

  return (
    <section className="card mb-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Bilan du jour</h2>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button
            type="button"
            className="btn-secondary text-xs"
            disabled={!date || loading}
            onClick={() => date && void refresh(shiftDate(date, -1), range)}
          >
            Jour précédent
          </button>
          <button
            type="button"
            className="btn-secondary text-xs"
            disabled={loading}
            onClick={() => void refresh(null, range)}
          >
            Aujourd’hui
          </button>
          <span className="text-muted">{date || "—"} · Europe/Paris</span>
        </div>
      </div>

      {error ? <p className="mb-2 text-sm text-loss">{error}</p> : null}
      {loading && !report ? <p className="text-sm text-muted">Chargement…</p> : null}

      {report ? (
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Réalisé"
            value={money(report.realized.pnl)}
            className={pnlClass(report.realized.pnl)}
            hint={`${report.realized.sells} ventes · ${report.realized.buys} achats`}
          />
          <Stat
            label="Ouvert"
            value={money(report.open.unrealized_pnl)}
            className={pnlClass(report.open.unrealized_pnl)}
            hint={`${report.open.count} pos. · cash ${report.open.cash.toFixed(2)} $`}
          />
          <Stat
            label="Frais"
            value={`${report.fees.total.toFixed(2)} $`}
            hint={`${report.fees.trade_count} fills`}
          />
          <Stat
            label="Échecs"
            value={String(report.failures.count)}
            className={report.failures.count > 0 ? "text-loss" : undefined}
            hint={`rejects ${report.failures.twin_rejects} · partiels ${report.failures.twin_partials}`}
          />
        </div>
      ) : null}

      {report?.activity.killed ? (
        <p className="mt-2 text-xs text-loss">
          Kill switch : {report.activity.kill_reason || "actif"}
        </p>
      ) : null}

      <div className="mt-4 border-t border-line/50 pt-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-slate-200">Métriques perf</h3>
          <select
            className="rounded-md border border-line bg-ink/60 px-2 py-1 text-xs"
            value={range}
            onChange={(e) => {
              const next = e.target.value as typeof range;
              setRange(next);
              void refresh(date, next);
            }}
          >
            <option value="session">Session</option>
            <option value="7d">7 jours</option>
            <option value="30d">30 jours</option>
            <option value="all">Tout</option>
          </select>
        </div>
        {metrics ? (
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6 text-xs">
            <Mini label="Win rate" value={`${(metrics.win_rate * 100).toFixed(0)}%`} />
            <Mini label="W/L" value={`${metrics.wins}/${metrics.losses}`} />
            <Mini
              label="Net réalisé"
              value={money(metrics.net_realized_pnl)}
              className={pnlClass(metrics.net_realized_pnl)}
            />
            <Mini label="Frais" value={`${metrics.fees_total.toFixed(2)} $`} />
            <Mini label="Max DD" value={`${metrics.max_drawdown_pct.toFixed(2)}%`} />
            <Mini
              label="Profit factor"
              value={
                metrics.profit_factor_infinite
                  ? "∞"
                  : metrics.profit_factor == null
                    ? "—"
                    : metrics.profit_factor.toFixed(2)
              }
            />
          </div>
        ) : null}
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <div className="rounded-lg border border-line/50 bg-ink/40 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-muted">{label}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${className || ""}`}>{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

function Mini({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className="rounded-md border border-line/40 px-2 py-1.5">
      <p className="text-[10px] text-muted">{label}</p>
      <p className={`font-medium tabular-nums ${className || "text-slate-200"}`}>{value}</p>
    </div>
  );
}
