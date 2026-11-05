"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { money, pnlClass } from "@/lib/engine";

type LiveEquityProps = {
  equity: number;
  cash: number;
  pnl: number;
  live?: boolean;
};

/** Big live portfolio value + mini sparkline fed by SSE equity updates. */
export function LiveEquity({ equity, cash, pnl, live }: LiveEquityProps) {
  const [series, setSeries] = useState<number[]>([equity]);
  const prev = useRef(equity);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (!Number.isFinite(equity)) return;
    setSeries((s) => {
      const next = [...s, equity];
      return next.length > 48 ? next.slice(-48) : next;
    });
    const delta = equity - prev.current;
    if (Math.abs(delta) >= 0.01) {
      setFlash(delta > 0 ? "up" : "down");
      const t = window.setTimeout(() => setFlash(null), 700);
      prev.current = equity;
      return () => window.clearTimeout(t);
    }
    prev.current = equity;
  }, [equity]);

  const path = useMemo(() => {
    if (series.length < 2) return "";
    const min = Math.min(...series);
    const max = Math.max(...series);
    const span = Math.max(max - min, 0.01);
    const w = 120;
    const h = 28;
    return series
      .map((v, i) => {
        const x = (i / (series.length - 1)) * w;
        const y = h - ((v - min) / span) * h;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [series]);

  const flashClass =
    flash === "up" ? "text-gain" : flash === "down" ? "text-loss" : "text-slate-100";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <p className="label">Valeur du portefeuille</p>
        {live ? (
          <span className="rounded-full bg-gain/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gain">
            Live
          </span>
        ) : (
          <span className="rounded-full bg-slate-700/60 px-2 py-0.5 text-[10px] uppercase text-muted">
            …
          </span>
        )}
      </div>
      <p className={`text-2xl font-semibold tabular-nums transition-colors duration-300 ${flashClass}`}>
        {money(equity)} $
      </p>
      {path ? (
        <svg viewBox="0 0 120 28" className="h-7 w-full text-sky-300/80" aria-hidden>
          <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      ) : null}
      <p className={`text-sm ${pnlClass(pnl)}`}>
        P&amp;L {pnl >= 0 ? "+" : ""}
        {money(pnl)} $ · cash {money(cash)} $
      </p>
    </div>
  );
}
