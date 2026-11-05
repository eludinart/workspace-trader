const STORAGE_KEY = "wt-chart-symbol";

export function loadChartSymbol(allowed: string[]): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const sym = raw.trim().toUpperCase();
    if (allowed.length > 0 && !allowed.includes(sym)) return null;
    return sym || null;
  } catch {
    return null;
  }
}

export function saveChartSymbol(symbol: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, symbol.trim().toUpperCase());
  } catch {
    // ignore quota / private mode
  }
}
