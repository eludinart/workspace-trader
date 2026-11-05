"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { money, pnlClass, type Account } from "@/lib/engine";

type Position = Account["positions"][number];

type PositionsPanelProps = {
  positions: Position[];
  chartSymbol?: string;
  busy?: boolean;
  compact?: boolean;
  onPick?: (symbol: string) => void;
  onSell?: (symbol: string) => void;
  sellLabel?: (symbol: string) => string;
};

const SPARK_MAX = 48;

/** Adaptive decimals so FLOKI/PEPE don't render as 0,00. */
export function formatPrice(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  let digits = 2;
  if (abs > 0 && abs < 0.0001) digits = 8;
  else if (abs < 0.01) digits = 6;
  else if (abs < 1) digits = 4;
  else if (abs >= 1000) digits = 2;
  return n.toLocaleString("fr-FR", {
    minimumFractionDigits: Math.min(2, digits),
    maximumFractionDigits: digits,
  });
}

function formatQty(n: number): string {
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) {
    return `${(n / 1_000_000).toLocaleString("fr-FR", { maximumFractionDigits: 2 })}M`;
  }
  if (abs >= 10_000) return n.toLocaleString("fr-FR", { maximumFractionDigits: 0 });
  if (abs >= 1) return n.toLocaleString("fr-FR", { maximumFractionDigits: 4 });
  return n.toLocaleString("fr-FR", { maximumFractionDigits: 6 });
}

/** Net after fees if available; else gross unrealized. */
function netPnl(p: Position): number {
  if (typeof p.net_pnl === "number" && Number.isFinite(p.net_pnl)) return p.net_pnl;
  return p.unrealized_pnl;
}

function netPct(p: Position): number {
  if (typeof p.net_pnl_pct === "number" && Number.isFinite(p.net_pnl_pct)) return p.net_pnl_pct;
  if (!p.avg_entry || p.avg_entry <= 0) return 0;
  return ((p.mark_price - p.avg_entry) / p.avg_entry) * 100;
}

function isTrulyGreen(p: Position): boolean {
  if (typeof p.would_be_green === "boolean") return p.would_be_green;
  return netPnl(p) > 0;
}

function tone(p: Position): "gain" | "loss" | "flat" {
  const net = netPnl(p);
  if (isTrulyGreen(p) && net > 0.005) return "gain";
  if (net < -0.005) return "loss";
  // Gross positive but fees eat the edge → not a real win yet.
  if (p.unrealized_pnl > 0.005 && net <= 0.005) return "flat";
  return "flat";
}

function Sparkline({ points, positive }: { points: number[]; positive: boolean }) {
  if (points.length < 2) {
    return <div className="h-8 w-full rounded bg-ink/40" aria-hidden />;
  }
  const w = 120;
  const h = 32;
  const min = Math.min(...points, 0);
  const max = Math.max(...points, 0);
  const span = Math.max(0.01, max - min);
  const coords = points.map((v, i) => {
    const x = (i / (points.length - 1)) * (w - 2) + 1;
    const y = h - 2 - ((v - min) / span) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const zeroY = h - 2 - ((0 - min) / span) * (h - 4);
  const color = positive ? "#34d399" : "#f87171";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-8 w-full" aria-hidden>
      <line
        x1={0}
        x2={w}
        y1={zeroY}
        y2={zeroY}
        stroke="rgba(148,163,184,0.25)"
        strokeWidth={1}
        strokeDasharray="2 2"
      />
      <polyline
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
        points={coords.join(" ")}
      />
    </svg>
  );
}

function usePnlSparks(positions: Position[]): Record<string, number[]> {
  const [sparks, setSparks] = useState<Record<string, number[]>>({});
  const lastKey = useRef("");

  useEffect(() => {
    const stamp = positions
      .map((p) => `${p.symbol}:${netPnl(p).toFixed(4)}:${netPct(p).toFixed(4)}`)
      .join("|");
    if (stamp === lastKey.current) return;
    lastKey.current = stamp;

    setSparks((prev) => {
      const next: Record<string, number[]> = {};
      const open = new Set(positions.map((p) => p.symbol));
      for (const p of positions) {
        const hist = [...(prev[p.symbol] || []), netPct(p)].slice(-SPARK_MAX);
        next[p.symbol] = hist;
      }
      for (const sym of Object.keys(prev)) {
        if (!open.has(sym)) {
          /* dropped */
        }
      }
      return next;
    });
  }, [positions]);

  return sparks;
}

export function PositionsPanel({
  positions,
  chartSymbol,
  busy,
  compact,
  onPick,
  onSell,
  sellLabel,
}: PositionsPanelProps) {
  const sparks = usePnlSparks(positions);
  const sorted = useMemo(
    () => [...positions].sort((a, b) => netPnl(b) - netPnl(a)),
    [positions],
  );

  if (!sorted.length) {
    return (
      <p className="text-sm text-muted">{compact ? "Aucune" : "Aucune position ouverte"}</p>
    );
  }

  return (
    <ul className={compact ? "space-y-2" : "space-y-2.5"}>
      {sorted.map((p) => {
        const net = netPnl(p);
        const pct = netPct(p);
        const kind = tone(p);
        const fees = typeof p.fees_total === "number" ? p.fees_total : null;
        const gross = typeof p.gross_pnl === "number" ? p.gross_pnl : p.unrealized_pnl;
        const border =
          kind === "gain"
            ? "border-gain/40 bg-gain/5"
            : kind === "loss"
              ? "border-loss/40 bg-loss/5"
              : chartSymbol === p.symbol
                ? "border-riskBlue/50 bg-riskBlue/10"
                : "border-line bg-ink/20";
        const badge =
          kind === "gain"
            ? "bg-gain/20 text-gain"
            : kind === "loss"
              ? "bg-loss/20 text-loss"
              : "bg-amber-500/15 text-amber-200";
        const badgeText =
          kind === "gain"
            ? "Gagnante nette"
            : kind === "loss"
              ? "Perdante"
              : gross > 0
                ? "Sous les frais"
                : "Neutre";
        const spark = sparks[p.symbol] || [pct];

        return (
          <li key={p.symbol} className={`rounded-xl border p-2.5 transition-colors ${border}`}>
            <div className="flex items-start gap-2">
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                title="Voir sur le graphique"
                onClick={() => onPick?.(p.symbol)}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold tracking-tight">{p.symbol}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badge}`}
                    >
                      {badgeText}
                    </span>
                  </div>
                  <div className="text-right">
                    <p className={`text-sm font-semibold tabular-nums ${pnlClass(net)}`}>
                      {net >= 0 ? "+" : ""}
                      {money(net)} $ net
                    </p>
                    <p className={`text-xs tabular-nums ${pnlClass(pct)}`}>
                      {pct >= 0 ? "+" : ""}
                      {pct.toLocaleString("fr-FR", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}{" "}
                      %
                    </p>
                  </div>
                </div>

                <div className="mt-2 grid grid-cols-[1fr_auto] items-end gap-2">
                  <div>
                    <Sparkline points={spark} positive={net >= 0} />
                    <p className="mt-1 text-[11px] text-muted">
                      qté {formatQty(p.qty)} · entrée {formatPrice(p.avg_entry)} · cours{" "}
                      {formatPrice(p.mark_price)}
                    </p>
                    <p className="text-[11px] text-muted">
                      brut {gross >= 0 ? "+" : ""}
                      {money(gross)} $
                      {fees != null ? ` · frais ~${money(fees)} $` : ""}
                      {" · "}
                      {kind === "gain"
                        ? "sortie maintenant = encore gagnant"
                        : kind === "loss"
                          ? "sortie maintenant = perte"
                          : "pas encore au-dessus des frais"}
                    </p>
                  </div>
                  {!compact ? (
                    <p className="pb-8 text-right text-[11px] tabular-nums text-muted">
                      {money(p.market_value)} $
                    </p>
                  ) : null}
                </div>
              </button>

              {onSell ? (
                <button
                  type="button"
                  className="shrink-0 rounded-md border border-loss/40 px-2.5 py-1.5 text-xs font-semibold text-loss hover:bg-loss/10 disabled:opacity-50"
                  disabled={busy}
                  title={`Vendre toute la position ${p.symbol}`}
                  onClick={() => onSell(p.symbol)}
                >
                  {sellLabel?.(p.symbol) ?? "Vendre"}
                </button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
