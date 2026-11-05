"use client";

import { useState } from "react";
import type { FeatureState, SystemStatusPayload } from "@/lib/engine";

type FeatureStatus = SystemStatusPayload["features"][number];

const STATE_STYLE: Record<FeatureState, string> = {
  ok: "border-gain/40 bg-gain/10 text-gain",
  warn: "border-riskOrange/50 bg-riskOrange/10 text-orange-200",
  off: "border-slate-600 bg-slate-800/60 text-slate-300",
  error: "border-loss/50 bg-loss/10 text-loss",
};

const STATE_DOT: Record<FeatureState, string> = {
  ok: "bg-gain",
  warn: "bg-riskOrange",
  off: "bg-slate-500",
  error: "bg-loss",
};

const STATE_WORD: Record<FeatureState, string> = {
  ok: "OK",
  warn: "ATTENTION",
  off: "OFF",
  error: "ERREUR",
};

type SystemStatusProps = {
  status: SystemStatusPayload | null;
  streamLive: boolean;
  streamPhase: "connecting" | "live" | "reconnecting";
  loading?: boolean;
  /** Start collapsed as a thin banner (dashboard home). */
  defaultCollapsed?: boolean;
};

export function SystemStatus({
  status,
  streamLive,
  streamPhase,
  loading,
  defaultCollapsed = true,
}: SystemStatusProps) {
  const [open, setOpen] = useState(!defaultCollapsed);
  const features = [...(status?.features || [])];
  const streamFeature: FeatureStatus = streamLive
    ? {
        id: "stream",
        label: "Flux temps réel",
        state: "ok",
        detail: "SSE connecté — mises à jour live",
      }
    : streamPhase === "reconnecting"
      ? {
          id: "stream",
          label: "Flux temps réel",
          state: "warn",
          detail: "Reconnexion automatique en cours",
        }
      : {
          id: "stream",
          label: "Flux temps réel",
          state: "error",
          detail: "Pas de flux — l’affichage peut être figé",
        };

  if (!features.some((f) => f.id === "stream")) {
    features.unshift(streamFeature);
  } else {
    const idx = features.findIndex((f) => f.id === "stream");
    features[idx] = streamFeature;
  }

  const overall = worstState([
    status?.overall || "off",
    streamFeature.state,
    ...features.map((f) => f.state),
  ]);
  const okN = features.filter((f) => f.state === "ok").length;

  return (
    <section className="card mb-3 py-2">
      <button
        type="button"
        className="flex w-full flex-wrap items-center justify-between gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-sm font-medium">État des fonctions</span>
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${STATE_STYLE[overall]}`}>
            {STATE_WORD[overall]}
            {loading ? "…" : ""}
          </span>
          <span className="truncate text-[11px] text-muted">
            {okN}/{features.length || "—"} OK
          </span>
        </div>
        <span className="text-xs text-muted">{open ? "Réduire ▴" : "Détails ▾"}</span>
      </button>
      {open ? (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <li
              key={f.id}
              className={`rounded-lg border px-3 py-2 ${STATE_STYLE[f.state]}`}
              title={f.detail}
            >
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide">
                <span className={`h-2 w-2 rounded-full ${STATE_DOT[f.state]}`} aria-hidden />
                {f.label}
                <span className="ml-auto opacity-80">{STATE_WORD[f.state]}</span>
              </div>
              <p className="mt-1 text-xs font-normal normal-case leading-snug tracking-normal text-slate-200/90">
                {f.detail}
              </p>
            </li>
          ))}
          {features.length === 0 && (
            <li className="text-sm text-muted sm:col-span-2 lg:col-span-3">
              Chargement de l’état système…
            </li>
          )}
        </ul>
      ) : null}
    </section>
  );
}

function worstState(states: FeatureState[]): FeatureState {
  if (states.includes("error")) return "error";
  if (states.includes("warn")) return "warn";
  if (states.includes("off")) return "off";
  return "ok";
}
