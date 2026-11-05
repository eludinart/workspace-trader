"use client";

import { money, pnlClass } from "@/lib/engine";
import { actionLabel } from "@/lib/signal-fr";

type LivePulseProps = {
  symbol: string;
  price: number | null;
  equity: number;
  pnl: number;
  action: string | null;
  confidence: number;
  narrative: string | null;
  live: boolean;
  lastTickAt: number | null;
  flashKey: number;
};

export function LivePulse({
  symbol,
  price,
  equity,
  pnl,
  action,
  confidence,
  narrative,
  live,
  lastTickAt,
  flashKey,
}: LivePulseProps) {
  const age =
    lastTickAt != null ? Math.max(0, Math.floor((Date.now() - lastTickAt) / 1000)) : null;

  return (
    <section className="card mb-4 overflow-hidden">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label mb-1 flex items-center gap-2">
            Instantané
            {live ? (
              <span className="inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-gain">
                <span className="live-dot" aria-hidden />
                reçu
              </span>
            ) : (
              <span className="text-[10px] uppercase text-muted">hors flux</span>
            )}
          </p>
          <p className="text-sm text-muted">{symbol}</p>
          <p
            key={flashKey}
            className="price-flash mt-1 text-3xl font-semibold tabular-nums tracking-tight text-slate-50"
          >
            {price != null ? `${money(price)} $` : "—"}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:text-right">
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted">Valeur</p>
            <p className="font-medium tabular-nums">{money(equity)} $</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wide text-muted">P&L</p>
            <p className={`font-medium tabular-nums ${pnlClass(pnl)}`}>
              {pnl >= 0 ? "+" : ""}
              {money(pnl)} $
            </p>
          </div>
          <div className="col-span-2">
            <p className="text-[10px] uppercase tracking-wide text-muted">Dernière décision</p>
            <p className="font-semibold">
              {action ? actionLabel(action) : "—"}
              {confidence > 0 ? (
                <span className="ml-2 text-xs font-normal text-muted">
                  {(confidence * 100).toFixed(0)} %
                </span>
              ) : null}
            </p>
          </div>
        </div>
      </div>
      {narrative ? (
        <p className="mt-3 border-t border-line/60 pt-3 text-sm leading-relaxed text-slate-300">
          {narrative}
        </p>
      ) : (
        <p className="mt-3 border-t border-line/60 pt-3 text-sm text-muted">
          En attente d’une décision du modèle…
        </p>
      )}
      {age != null ? (
        <p className="mt-2 text-[11px] text-muted">
          Dernière mise à jour il y a {age < 5 ? "quelques secondes" : `${age} s`}
        </p>
      ) : null}
    </section>
  );
}
