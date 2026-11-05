"use client";

import {
  CHART_OVERLAY_TOGGLES,
  type ChartOverlayKey,
  type ChartOverlays,
} from "@/lib/chart-overlays";

type ChartTogglesProps = {
  overlays: ChartOverlays;
  onChange: (key: ChartOverlayKey, enabled: boolean) => void;
};

export function ChartToggles({ overlays, onChange }: ChartTogglesProps) {
  return (
    <div
      className="mb-2 flex flex-wrap gap-1.5"
      role="group"
      aria-label="Affichage des indicateurs du graphique"
    >
      {CHART_OVERLAY_TOGGLES.map(({ key, label, color }) => {
        const on = overlays[key];
        return (
          <label
            key={key}
            className={`inline-flex cursor-pointer select-none items-center gap-1.5 rounded-md border px-2 py-1 text-[10px] transition-colors sm:text-xs ${
              on
                ? "border-slate-500/80 bg-slate-800/80 text-slate-100"
                : "border-line bg-ink/40 text-muted hover:border-slate-600"
            }`}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={on}
              onChange={(e) => onChange(key, e.target.checked)}
            />
            {color ? (
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: color, opacity: on ? 1 : 0.35 }}
                aria-hidden
              />
            ) : (
              <span
                className={`h-2 w-2 shrink-0 rounded-sm border ${on ? "border-slate-400 bg-slate-500" : "border-line bg-transparent"}`}
                aria-hidden
              />
            )}
            <span>{label}</span>
          </label>
        );
      })}
    </div>
  );
}
