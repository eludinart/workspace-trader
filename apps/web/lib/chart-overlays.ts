export const CHART_OVERLAY_STORAGE_KEY = "wt-chart-overlays";

export type ChartOverlayKey =
  | "candles"
  | "sma20"
  | "sma50"
  | "ema12"
  | "volume"
  | "rsi"
  | "macd"
  | "trades";

export type ChartOverlays = Record<ChartOverlayKey, boolean>;

export const DEFAULT_CHART_OVERLAYS: ChartOverlays = {
  candles: true,
  sma20: true,
  sma50: true,
  ema12: true,
  volume: true,
  rsi: true,
  macd: true,
  trades: true,
};

export type ChartOverlayToggle = {
  key: ChartOverlayKey;
  label: string;
  color?: string;
};

export const CHART_OVERLAY_TOGGLES: ChartOverlayToggle[] = [
  { key: "candles", label: "Bougies" },
  { key: "sma20", label: "SMA20", color: "#60a5fa" },
  { key: "sma50", label: "SMA50", color: "#fbbf24" },
  { key: "ema12", label: "EMA12", color: "#c084fc" },
  { key: "volume", label: "Volume", color: "#16a34a" },
  { key: "rsi", label: "RSI", color: "#38bdf8" },
  { key: "macd", label: "MACD", color: "#22d3ee" },
  { key: "trades", label: "Ordres", color: "#4ade80" },
];

export function loadChartOverlays(): ChartOverlays {
  if (typeof window === "undefined") return { ...DEFAULT_CHART_OVERLAYS };
  try {
    const raw = localStorage.getItem(CHART_OVERLAY_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CHART_OVERLAYS };
    const parsed = JSON.parse(raw) as Partial<ChartOverlays>;
    return { ...DEFAULT_CHART_OVERLAYS, ...parsed };
  } catch {
    return { ...DEFAULT_CHART_OVERLAYS };
  }
}

export function saveChartOverlays(overlays: ChartOverlays): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(CHART_OVERLAY_STORAGE_KEY, JSON.stringify(overlays));
  } catch {
    // ignore quota / private mode
  }
}
