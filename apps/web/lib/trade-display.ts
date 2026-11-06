/** Display helpers for annotated paper trades (engine annotate_trade_for_display). */

export type TradeTone = "gain" | "loss" | "neutral" | "muted";

export type AnnotatedTrade = Record<string, unknown> & {
  id?: string;
  side?: string;
  symbol?: string;
  qty?: number;
  price?: number;
  fee?: number;
  ts?: string;
  note?: string;
  display_kind?: "invested" | "realized" | "incomplete" | string;
  display_amount?: number | null;
  display_pct?: number | null;
  badge?: string | null;
  badge_label?: string | null;
  amount_label?: string | null;
  reference_label?: string | null;
  reference_price?: number | null;
  reference_nature?: string | null;
  reference_ts?: string | null;
  tone?: TradeTone | string;
  latency_ms?: number | null;
  decision_price?: number | null;
  avg_entry_ref?: number | null;
};

export function tradeToneClass(tone: string | undefined): string {
  switch (tone) {
    case "gain":
      return "text-gain";
    case "loss":
      return "text-loss";
    case "muted":
      return "text-muted";
    case "neutral":
      return "text-slate-300";
    default: {
      const _exhaustive: string = String(tone || "");
      void _exhaustive;
      return "text-slate-300";
    }
  }
}

export function formatSignedMoney(n: number, digits = 2): string {
  const abs = Math.abs(n).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  if (n > 0) return `+${abs}`;
  if (n < 0) return `−${abs}`;
  return abs;
}

export function formatPct(n: number | null | undefined): string | null {
  if (typeof n !== "number" || !Number.isFinite(n)) return null;
  const abs = Math.abs(n).toFixed(2);
  if (n > 0) return `+${abs}%`;
  if (n < 0) return `−${abs}%`;
  return `${abs}%`;
}
