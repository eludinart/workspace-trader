import { NewsBrief, isUsableNewsBrief, normalizeNewsBrief } from "@/lib/news";

export type FeatureState = "ok" | "warn" | "off" | "error";

export type SystemStatusPayload = {
  overall: FeatureState;
  checked_at?: string;
  features: Array<{
    id: string;
    label: string;
    state: FeatureState;
    detail: string;
  }>;
  bot_running?: boolean;
  auto_trade?: boolean;
  killed?: boolean;
};

export type Account = {
  initial_balance: number;
  cash: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  exposure_quote?: number;
  marks_as_of?: string;
  positions: Array<{
    symbol: string;
    qty: number;
    avg_entry: number;
    mark_price: number;
    market_value: number;
    unrealized_pnl: number;
    cost_basis?: number;
    mark_ts?: string;
    gross_pnl?: number;
    net_pnl?: number;
    net_pnl_pct?: number;
    fees_total?: number;
    would_be_green?: boolean;
  }>;
  fee_bps?: number;
  slippage_bps?: number;
  latency_ms?: number;
  order_impact_enabled?: boolean;
  signal_flags?: Record<string, boolean>;
  symbol: string;
  symbols?: string[];
  active_symbol?: string;
  prices?: Record<string, number>;
  last_price: number | null;
  bot_running: boolean;
  auto_trade?: boolean;
  killed: boolean;
  kill_reason: string | null;
  drawdown_pct: number;
  risk_score: number;
  peak_equity: number;
  last_tick_at: string | null;
  last_error: string | null;
  trade_count: number;
  max_open_symbols?: number;
  open_count?: number;
  universe_size?: number;
};

export type UniverseRow = {
  symbol: string;
  base?: string;
  volume_24h?: number;
  rank?: number;
  source?: string;
  from_news?: boolean;
  signal?: { action?: string; confidence?: number; ts?: string } | null;
  news_score?: number;
  news_title?: string;
};

export type UniversePayload = {
  updated_at?: string | null;
  size?: number;
  symbols?: string[];
  rows?: UniverseRow[];
  news_hits?: Array<{ symbol: string; score: number; title: string }>;
  max_open?: number;
  equity?: number;
  open_positions?: string[];
  error?: string | null;
};

async function engineFetch(path: string, init?: RequestInit) {
  const res = await fetch(`/api/engine${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () =>
    engineFetch("/health") as Promise<{
      status?: string;
      bot_running?: boolean;
      auto_trade?: boolean;
      killed?: boolean;
      mode?: string;
    }>,
  systemStatus: () => engineFetch("/system/status") as Promise<SystemStatusPayload>,
  account: () => engineFetch("/account") as Promise<Account>,
  equityHistory: (
    range: "1h" | "6h" | "24h" | "1d" | "1w" | "1mo" | "1y" | "session" | "all" = "1h",
  ) =>
    engineFetch(`/account/equity-history?range=${encodeURIComponent(range)}`) as Promise<{
      range: string;
      session_started_at?: string;
      count: number;
      total_stored: number;
      points: Array<{
        t: string;
        equity: number;
        cash: number;
        positions: number;
        pnl: number;
        drawdown_pct: number;
      }>;
    }>,
  candles: (limit = 120, symbol?: string) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (symbol) qs.set("symbol", symbol);
    return engineFetch(`/market/candles?${qs.toString()}`) as Promise<{
      symbol: string;
      candles: Array<{ time: string; open: number; high: number; low: number; close: number; volume: number }>;
    }>;
  },
  signals: () => engineFetch("/signals"),
  trades: () => engineFetch("/trades?limit=30"),
  journal: () => engineFetch("/journal?limit=40"),
  params: () => engineFetch("/params"),
  universe: () => engineFetch("/universe") as Promise<UniversePayload>,
  refreshUniverse: () =>
    engineFetch("/universe/refresh", { method: "POST", body: "{}" }) as Promise<UniversePayload>,
  alertParams: () => engineFetch("/alerts/params"),
  llmParams: () => engineFetch("/llm/params"),
  putParams: (body: Record<string, unknown>) =>
    engineFetch("/params", { method: "PUT", body: JSON.stringify(body) }),
  putAlerts: (body: Record<string, unknown>) =>
    engineFetch("/alerts/params", { method: "PUT", body: JSON.stringify(body) }),
  putLlmParams: (body: Record<string, unknown>) =>
    engineFetch("/llm/params", { method: "PUT", body: JSON.stringify(body) }),
  tick: () => engineFetch("/bot/tick", { method: "POST", body: "{}" }),
  start: () => engineFetch("/bot/start", { method: "POST", body: "{}" }),
  stop: () => engineFetch("/bot/stop", { method: "POST", body: "{}" }),
  kill: () => engineFetch("/bot/kill", { method: "POST", body: JSON.stringify({ reason: "manual" }) }),
  resume: () => engineFetch("/bot/resume", { method: "POST", body: "{}" }),
  reset: (initial_balance?: number) =>
    engineFetch("/account/reset", {
      method: "POST",
      body: JSON.stringify(initial_balance ? { initial_balance } : {}),
    }),
  closePosition: (symbol: string) =>
    engineFetch("/positions/close", {
      method: "POST",
      body: JSON.stringify({ symbol }),
    }) as Promise<{
      ok: boolean;
      symbol: string;
      closed?: boolean;
      queued?: boolean;
      reason?: string;
      account?: Account;
      trade?: Record<string, unknown> | null;
    }>,
  backtest: (body: Record<string, unknown>) =>
    engineFetch("/backtest", { method: "POST", body: JSON.stringify(body) }),
  lastBacktest: () => engineFetch("/backtest/last"),
};

export type NewsFetchResult = {
  brief: NewsBrief | null;
  loading: boolean;
};

/** Tries /news/brief then /news; 404 → loading placeholder for forward-compatible UI. */
export async function fetchNewsBrief(): Promise<NewsFetchResult> {
  for (const path of ["/news/brief", "/news"]) {
    const res = await fetch(`/api/engine${path}`, { cache: "no-store" });
    if (res.status === 404) continue;
    if (!res.ok) continue;
    const json: unknown = await res.json();
    const brief = normalizeNewsBrief(json);
    // Ignore empty shells (e.g. /news before first RSS pull, or brief with 0 titles).
    if (isUsableNewsBrief(brief)) return { brief, loading: false };
  }
  return { brief: null, loading: true };
}

export function pnlClass(v: number) {
  if (v > 0) return "text-gain";
  if (v < 0) return "text-loss";
  return "text-slate-300";
}

export function riskTone(score: number) {
  if (score < 0.33) return { label: "Faible", className: "bg-riskBlue" };
  if (score < 0.66) return { label: "Modéré", className: "bg-riskOrange" };
  return { label: "Élevé", className: "bg-riskRed" };
}

export function money(n: number, digits = 2) {
  return n.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
