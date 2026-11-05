"use client";

import { useEffect, useState } from "react";
import {
  ActivityEvent,
  formatAgo,
  kindLabel,
} from "@/lib/activity";
import { actionLabel } from "@/lib/signal-fr";

type ActivityFeedProps = {
  events: ActivityEvent[];
};

export function ActivityFeed({ events }: ActivityFeedProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <section className="card flex h-full min-h-[22rem] flex-col">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-medium">Fil d’activité</h2>
          <p className="text-xs text-muted">Ce que le bot fait, en direct et en français.</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-gain/40 bg-gain/10 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-gain">
          <span className="live-dot" aria-hidden />
          Live
        </span>
      </div>
      {events.length === 0 ? (
        <p className="text-sm text-muted">
          En attente du flux… Dès qu’un tick ou une décision arrive, elle s’affiche ici.
        </p>
      ) : (
        <ul className="activity-scroll flex-1 space-y-2 overflow-auto pr-1">
          {events.map((e, i) => (
            <li
              key={e.id}
              className={`activity-row rounded-lg border border-line/80 bg-ink/50 px-3 py-2 ${
                i === 0 ? "activity-row-new border-l-2 border-l-riskBlue" : ""
              }`}
            >
              <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
                <span className={`rounded px-1.5 py-0.5 font-semibold ${kindTone(e.kind)}`}>
                  {kindLabel(e.kind)}
                </span>
                {e.symbol ? <span>{e.symbol.replace(/-USD$/i, "")}</span> : null}
                {e.action ? (
                  <span className={actionTone(e.action)}>{actionLabel(e.action)}</span>
                ) : null}
                <span className="ml-auto normal-case tracking-normal">{formatAgo(e.ts, now)}</span>
              </div>
              <p className="mt-1 text-sm font-medium text-slate-100">{e.title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-slate-300">{e.text}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function kindTone(kind: ActivityEvent["kind"]): string {
  switch (kind) {
    case "order":
      return "bg-gain/20 text-gain";
    case "skip":
      return "bg-riskOrange/20 text-orange-200";
    case "tick":
      return "bg-riskBlue/20 text-sky-200";
    case "news":
      return "bg-slate-600/40 text-slate-200";
    case "system":
      return "bg-slate-700 text-slate-300";
    default:
      return "bg-slate-600/50 text-slate-200";
  }
}

function actionTone(action: string): string {
  const a = action.toLowerCase();
  if (a === "buy") return "text-gain";
  if (a === "sell") return "text-loss";
  return "text-slate-400";
}
