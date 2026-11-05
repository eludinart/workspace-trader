"use client";

import {
  CHART_TIMEFRAMES,
  type ChartTimeframe,
} from "@/lib/chart-timeframes";

type ChartTimeframesProps = {
  value: number;
  onChange: (seconds: number) => void;
  disabled?: boolean;
};

export function ChartTimeframes({ value, onChange, disabled }: ChartTimeframesProps) {
  return (
    <div
      className="flex flex-wrap gap-1"
      role="group"
      aria-label="Intervalle du graphique"
    >
      {CHART_TIMEFRAMES.map((tf: ChartTimeframe) => {
        const active = value === tf.seconds;
        return (
          <button
            key={tf.seconds}
            type="button"
            disabled={disabled}
            aria-pressed={active}
            onClick={() => {
              if (tf.seconds !== value) onChange(tf.seconds);
            }}
            className={`rounded-md border px-2 py-0.5 text-[10px] font-medium transition-colors sm:px-2.5 sm:py-1 sm:text-xs ${
              active
                ? "border-gain/60 bg-gain/20 text-gain"
                : "border-line bg-ink/40 text-muted hover:border-slate-600 hover:text-slate-200"
            } disabled:cursor-not-allowed disabled:opacity-50`}
          >
            {tf.label}
          </button>
        );
      })}
    </div>
  );
}
