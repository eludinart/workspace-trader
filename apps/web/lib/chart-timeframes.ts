export const CHART_TIMEFRAME_STORAGE_KEY = "wt-chart-timeframe-sec";

export type ChartTimeframe = {
  label: string;
  seconds: number;
};

/** Coinbase-supported granularities used by the engine. */
export const CHART_TIMEFRAMES: ChartTimeframe[] = [
  { label: "1m", seconds: 60 },
  { label: "5m", seconds: 300 },
  { label: "15m", seconds: 900 },
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 21600 },
  { label: "1d", seconds: 86400 },
];

export const DEFAULT_CHART_TIMEFRAME_SEC = 3600;

const VALID_SECONDS = new Set(CHART_TIMEFRAMES.map((t) => t.seconds));

export function normalizeChartTimeframeSec(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(n) && VALID_SECONDS.has(n)) return n;
  return DEFAULT_CHART_TIMEFRAME_SEC;
}

export function loadChartTimeframeSec(): number | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(CHART_TIMEFRAME_STORAGE_KEY);
    if (raw == null || raw === "") return null;
    const n = Number(raw);
    if (Number.isFinite(n) && VALID_SECONDS.has(n)) return n;
    return null;
  } catch {
    return null;
  }
}

export function saveChartTimeframeSec(seconds: number): void {
  if (typeof window === "undefined") return;
  if (!VALID_SECONDS.has(seconds)) return;
  try {
    localStorage.setItem(CHART_TIMEFRAME_STORAGE_KEY, String(seconds));
  } catch {
    // ignore quota / private mode
  }
}
