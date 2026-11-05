"use client";

import { useState } from "react";
import type { UniversePayload } from "@/lib/engine";
import { actionLabel } from "@/lib/signal-fr";
import { money } from "@/lib/engine";

type UniversePanelProps = {
  universe: UniversePayload | null;
  held: Set<string>;
  onPick?: (symbol: string) => void;
  onRefresh?: () => void;
  busy?: boolean;
  defaultCollapsed?: boolean;
};

export function UniversePanel({
  universe,
  held,
  onPick,
  onRefresh,
  busy,
  defaultCollapsed = true,
}: UniversePanelProps) {
  const [open, setOpen] = useState(!defaultCollapsed);
  const rows = universe?.rows || [];
  const news = universe?.news_hits || [];
  const size = universe?.size ?? rows.length;
  const openN = held.size;

  return (
    <section className="card mb-3 py-2 md:col-span-12">
      <div className="flex w-full flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className="text-sm font-medium">Univers</span>
          <span className="text-[11px] text-muted">
            {size} paires · {openN} actif{openN === 1 ? "" : "s"} · max {universe?.max_open ?? "—"}
          </span>
          {news.length > 0 ? (
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-200">
              {news.length} boost actu
            </span>
          ) : null}
          <span className="text-xs text-muted">{open ? "Réduire ▴" : "Liste ▾"}</span>
        </button>
        <button className="btn-ghost text-xs" disabled={busy} onClick={() => onRefresh?.()}>
          Rafraîchir
        </button>
      </div>

      {open ? (
        <div className="mt-3">
          {news.length > 0 ? (
            <div className="mb-3 rounded-md border border-line/60 bg-ink/40 px-2 py-1.5 text-xs">
              <p className="mb-1 font-medium text-slate-200">Boost actu</p>
              <ul className="max-h-16 space-y-0.5 overflow-auto text-muted">
                {news.slice(0, 6).map((h) => (
                  <li key={`${h.symbol}-${h.title.slice(0, 24)}`}>
                    <button
                      type="button"
                      className="text-left hover:text-sky-200"
                      onClick={() => onPick?.(h.symbol)}
                    >
                      <span className="font-medium text-slate-300">
                        {h.symbol.replace(/-USD$/i, "")}
                      </span>
                      {" · "}
                      {h.title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="mb-2 text-xs text-muted">
              Aucun boost actu pour l’instant (titres sans ticker clair).
            </p>
          )}
          <div className="max-h-56 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-panel text-muted">
                <tr>
                  <th className="py-1 pr-2 font-medium">#</th>
                  <th className="py-1 pr-2 font-medium">Token</th>
                  <th className="py-1 pr-2 font-medium">Vol 24h</th>
                  <th className="py-1 pr-2 font-medium">Signal</th>
                  <th className="py-1 font-medium">Statut</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 60).map((r) => {
                  const isOpen = held.has(r.symbol);
                  return (
                    <tr
                      key={r.symbol}
                      className="cursor-pointer border-t border-line/40 hover:bg-ink/50"
                      onClick={() => onPick?.(r.symbol)}
                    >
                      <td className="py-1 pr-2 text-muted">{r.rank || "—"}</td>
                      <td className="py-1 pr-2 font-medium text-slate-200">
                        {r.symbol.replace(/-USD$/i, "")}
                        {r.from_news || r.source === "news" ? (
                          <span className="ml-1 text-[10px] text-amber-300">actu</span>
                        ) : null}
                      </td>
                      <td className="py-1 pr-2 text-muted">
                        {r.volume_24h ? money(r.volume_24h, 0) : "—"}
                      </td>
                      <td className="py-1 pr-2">
                        {r.signal?.action
                          ? `${actionLabel(r.signal.action)} ${Math.round((r.signal.confidence || 0) * 100)}%`
                          : "—"}
                      </td>
                      <td className="py-1">
                        {isOpen ? (
                          <span className="text-gain">actif</span>
                        ) : (
                          <span className="text-muted">inactif</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-3 text-muted">
                      Univers en chargement…
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
