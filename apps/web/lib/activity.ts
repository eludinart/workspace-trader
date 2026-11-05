import { actionLabel } from "@/lib/signal-fr";

export type ActivityKind = "tick" | "signal" | "order" | "skip" | "news" | "system";

export type ActivityEvent = {
  id: string;
  kind: ActivityKind;
  ts: number;
  symbol?: string;
  action?: string;
  title: string;
  text: string;
};

export function formatAgo(ts: number, now = Date.now()): string {
  const sec = Math.max(0, Math.floor((now - ts) / 1000));
  if (sec < 5) return "à l’instant";
  if (sec < 60) return `il y a ${sec} s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `il y a ${min} min`;
  const h = Math.floor(min / 60);
  return `il y a ${h} h`;
}

export function kindLabel(kind: ActivityKind): string {
  switch (kind) {
    case "tick":
      return "Tick";
    case "signal":
      return "Signal";
    case "order":
      return "Ordre";
    case "skip":
      return "Refus";
    case "news":
      return "Actu";
    case "system":
      return "Système";
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

export function parseEventTs(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const t = Date.parse(raw);
    if (!Number.isNaN(t)) return t;
  }
  return Date.now();
}

/** Merge newest-first, de-dupe by id, cap length. */
export function mergeActivity(
  prev: ActivityEvent[],
  incoming: ActivityEvent[],
  limit = 80
): ActivityEvent[] {
  const map = new Map<string, ActivityEvent>();
  for (const e of [...incoming, ...prev]) {
    if (!map.has(e.id)) map.set(e.id, e);
  }
  return Array.from(map.values())
    .sort((a, b) => b.ts - a.ts)
    .slice(0, limit);
}

export function decisionToActivity(d: {
  ts?: string;
  symbol?: string;
  action?: string;
  narrative?: string;
  reason?: string;
  order_placed?: boolean;
  confidence?: number;
}): ActivityEvent | null {
  const symbol = (d.symbol || "").toUpperCase() || "—";
  const ts = parseEventTs(d.ts);
  const narrative = (d.narrative || "").trim();
  if (!narrative && !d.action) return null;
  const placed = Boolean(d.order_placed);
  const action = (d.action || "").toLowerCase();
  const isSkip =
    !placed &&
    (action === "buy" ||
      action === "sell" ||
      /non suivi|ignoré|insuffisante|aucun ordre/i.test(narrative));
  const kind: ActivityKind = placed ? "order" : isSkip ? "skip" : "signal";
  const title =
    kind === "order"
      ? "Ordre exécuté"
      : kind === "skip"
        ? "Ordre non placé"
        : `Signal ${actionLabel(action)}`;
  const conf =
    typeof d.confidence === "number" && Number.isFinite(d.confidence)
      ? ` · conf. ${(d.confidence * 100).toFixed(0)} %`
      : "";
  return {
    id: `dec-${d.ts || ts}-${symbol}-${action}-${kind}`,
    kind,
    ts,
    symbol,
    action,
    title,
    text: narrative || `${actionLabel(action)} ${symbol}${conf}`,
  };
}

export function tradeToActivity(t: Record<string, unknown>): ActivityEvent | null {
  const id = String(t.id || "");
  if (!id) return null;
  const side = String(t.side || "").toLowerCase();
  const symbol = String(t.symbol || "—").toUpperCase();
  const note = String(t.note || "");
  const price = Number(t.price);
  const qty = Number(t.qty);
  const reason =
    note === "exit:tp"
      ? "prise de gain"
      : note === "exit:sl"
        ? "stop-loss"
        : note === "entry:signal"
          ? "signal IA"
          : note === "exit:signal"
            ? "sortie signal"
            : note || "paper";
  return {
    id: `trade-${id}`,
    kind: "order",
    ts: parseEventTs(t.ts),
    symbol,
    action: side,
    title: side === "buy" ? "Achat exécuté" : side === "sell" ? "Vente exécutée" : "Ordre",
    text: `${actionLabel(side)} ${symbol} · qté ${qty.toFixed(5)} @ ${price.toFixed(2)} · ${reason}`,
  };
}

export function priceTickEvent(symbol: string, price: number, rsi?: number): ActivityEvent {
  const short = symbol.replace(/-USD$/i, "");
  const rsiBit = typeof rsi === "number" && Number.isFinite(rsi) ? ` · RSI ${rsi.toFixed(0)}` : "";
  const ts = Date.now();
  return {
    id: `tick-${symbol}-${Math.floor(ts / 8000)}`,
    kind: "tick",
    ts,
    symbol,
    title: "Tick marché",
    text: `${short} ${price.toLocaleString("fr-FR", { maximumFractionDigits: 2 })} USD${rsiBit}`,
  };
}

export function mapServerActivity(raw: unknown): ActivityEvent[] {
  if (!Array.isArray(raw)) return [];
  const out: ActivityEvent[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const o = row as Record<string, unknown>;
    const kind = String(o.kind || "signal") as ActivityKind;
    const id = String(o.id || "");
    const text = String(o.text || "");
    if (!id || !text) continue;
    out.push({
      id,
      kind: ["tick", "signal", "order", "skip", "news", "system"].includes(kind) ? kind : "signal",
      ts: parseEventTs(o.ts),
      symbol: o.symbol ? String(o.symbol) : undefined,
      action: o.action ? String(o.action) : undefined,
      title: String(o.title || kindLabel(kind as ActivityKind)),
      text,
    });
  }
  return out;
}
