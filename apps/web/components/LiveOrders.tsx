"use client";

import { useEffect, useRef, useState } from "react";
import { money } from "@/lib/engine";
import { actionLabel } from "@/lib/signal-fr";

type LiveOrdersProps = {
  trades: Array<Record<string, unknown>>;
};

/** Newest paper fills with flash on arrival (type, token, price, qty, time). */
export function LiveOrders({ trades }: LiveOrdersProps) {
  const [flashId, setFlashId] = useState<string | null>(null);
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!trades.length) return;
    const newest = String(trades[0]?.id || "");
    if (!newest) return;
    if (seen.current.has(newest)) return;
    // Seed on first load without flash spam.
    if (seen.current.size === 0) {
      for (const t of trades) {
        const id = String(t.id || "");
        if (id) seen.current.add(id);
      }
      return;
    }
    seen.current.add(newest);
    setFlashId(newest);
    const timer = window.setTimeout(() => setFlashId(null), 2500);
    return () => window.clearTimeout(timer);
  }, [trades]);

  return (
    <section className="card md:col-span-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Exécutions paper</h2>
        <span className="rounded-full border border-gain/40 bg-gain/10 px-2 py-0.5 text-[10px] font-semibold uppercase text-gain">
          Live
        </span>
      </div>
      <p className="mb-2 text-xs text-muted">Achats / ventes simulés — pas ton compte Coinbase.</p>
      <ul className="max-h-64 space-y-1.5 overflow-auto text-sm">
        {trades.length === 0 ? (
          <li className="text-muted">Aucun fill pour l’instant…</li>
        ) : (
          trades.map((t) => {
            const id = String(t.id || "");
            const side = String(t.side || "").toLowerCase();
            const sym = String(t.symbol || "—");
            const ts = t.ts ? new Date(String(t.ts)).toLocaleTimeString("fr-FR") : "—";
            const hot = id === flashId;
            return (
              <li
                key={id}
                className={`rounded-md border px-2 py-1.5 transition-colors ${
                  hot
                    ? "border-riskBlue/70 bg-riskBlue/15"
                    : "border-line/50 bg-ink/30"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-1">
                  <span
                    className={`font-semibold ${
                      side === "buy" ? "text-gain" : side === "sell" ? "text-loss" : ""
                    }`}
                  >
                    {actionLabel(side)} {sym.replace(/-USD$/i, "")}
                  </span>
                  <span className="text-[10px] text-muted">{ts}</span>
                </div>
                <div className="mt-0.5 flex justify-between text-xs text-slate-300">
                  <span>
                    qté {Number(t.qty).toFixed(5)} @ {money(Number(t.price))}
                  </span>
                  <span className="text-muted">{String(t.note || "paper")}</span>
                </div>
              </li>
            );
          })
        )}
      </ul>
    </section>
  );
}
