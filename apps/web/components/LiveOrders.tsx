"use client";

import { useEffect, useRef, useState } from "react";
import { TradeFillRow } from "@/components/TradeFillRow";
import type { AnnotatedTrade } from "@/lib/trade-display";

type LiveOrdersProps = {
  trades: AnnotatedTrade[];
};

/** Newest paper fills with flash on arrival (P&L / investi annotés). */
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
      <p className="mb-2 text-xs text-muted">
        Achats = montant investi · ventes = P&L réalisé net (frais inclus, slip dans le fill).
      </p>
      <ul className="max-h-64 space-y-1.5 overflow-auto text-sm">
        {trades.length === 0 ? (
          <li className="text-muted">Aucun fill pour l’instant…</li>
        ) : (
          trades.map((t) => {
            const id = String(t.id || "");
            return <TradeFillRow key={id} trade={t} hot={id === flashId} dense />;
          })
        )}
      </ul>
    </section>
  );
}
