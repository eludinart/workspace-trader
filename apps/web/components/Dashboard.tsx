"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ChartSymbols } from "@/components/ChartSymbols";
import { ChartTimeframes } from "@/components/ChartTimeframes";
import { ActivityFeed } from "@/components/ActivityFeed";
import { AppNav, type AppPage } from "@/components/AppNav";
import { LiveEquity } from "@/components/LiveEquity";
import { LiveOrders } from "@/components/LiveOrders";
import { MobileAccountStrip } from "@/components/MobileAccountStrip";
import { TradeFillRow } from "@/components/TradeFillRow";
import { WebPushOptIn } from "@/components/WebPushOptIn";
import type { AnnotatedTrade } from "@/lib/trade-display";
import { NewsPanel } from "@/components/NewsPanel";
import { PaperSessionChart } from "@/components/PaperSessionChart";
import { PositionsPanel } from "@/components/PositionsPanel";
import { SignalText } from "@/components/SignalText";
import { Candle, PriceChart } from "@/components/PriceChart";
import {
  ACTION_LABELS,
  PendingBanner,
  ToastStack,
  type ToastItem,
  type ToastTone,
} from "@/components/ActionFeedback";
import { SystemStatus } from "@/components/SystemStatus";
import { UniversePanel } from "@/components/UniversePanel";
import {
  mergeActivity,
  tradeToActivity,
  decisionToActivity,
  type ActivityEvent,
} from "@/lib/activity";
import {
  DEFAULT_CHART_TIMEFRAME_SEC,
  loadChartTimeframeSec,
  normalizeChartTimeframeSec,
  saveChartTimeframeSec,
} from "@/lib/chart-timeframes";
import {
  loadChartSymbol,
  saveChartSymbol,
} from "@/lib/chart-symbol";
import {
  Account,
  api,
  fetchNewsBrief,
  money,
  pnlClass,
  riskTone,
  type SystemStatusPayload,
  type UniversePayload,
} from "@/lib/engine";
import {
  NewsBrief,
  isUsableNewsBrief,
  newsBias,
  newsFromStreamPayload,
  pickRicherNewsBrief,
} from "@/lib/news";
import { actionLabel } from "@/lib/signal-fr";

function mapApiCandle(c: {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}): Candle {
  return {
    time: c.time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume ?? 0,
  };
}

type DecisionLine = {
  ts: string;
  symbol: string;
  action?: string;
  narrative: string;
  reason?: string;
};

type SignalPayload = {
  latest: {
    action: string;
    confidence: number;
    probs: Record<string, number>;
    features: Record<string, number>;
    model_ready: boolean;
  } | null;
  explanation: string | null;
  narrative?: string | null;
  decisions?: DecisionLine[];
  history: Array<{ ts: string; price: number; signal: { action: string; confidence: number } }>;
};

export function Dashboard() {
  const [account, setAccount] = useState<Account | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [signals, setSignals] = useState<SignalPayload | null>(null);
  const [trades, setTrades] = useState<Array<Record<string, unknown>>>([]);
  const [journal, setJournal] = useState<Array<Record<string, unknown>>>([]);
  const [params, setParams] = useState<Record<string, unknown>>({});
  const [alerts, setAlerts] = useState<Record<string, unknown>>({});
  const [llm, setLlm] = useState<Record<string, unknown>>({});
  const [backtest, setBacktest] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const actionLockRef = useRef(false);
  const toastTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [live, setLive] = useState(false);
  const [streamPhase, setStreamPhase] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [btStart, setBtStart] = useState(() => {
    const d = new Date();
    d.setUTCDate(d.getUTCDate() - 30);
    return d.toISOString().slice(0, 10);
  });
  const [btEnd, setBtEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [newsBrief, setNewsBrief] = useState<NewsBrief | null>(null);
  const [newsLoading, setNewsLoading] = useState(true);
  const [candleGranularitySec, setCandleGranularitySec] = useState(DEFAULT_CHART_TIMEFRAME_SEC);
  const [chartSymbol, setChartSymbol] = useState("BTC-USD");
  const [chartsLoading, setChartsLoading] = useState(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatusPayload | null>(null);
  const [systemStatusLoading, setSystemStatusLoading] = useState(true);
  const [universe, setUniverse] = useState<UniversePayload | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [page, setPage] = useState<AppPage>("home");
  const newsFromStreamRef = useRef(false);
  const chartSymbolRef = useRef(chartSymbol);
  chartSymbolRef.current = chartSymbol;

  const fetchCandles = useCallback(async (symbol?: string) => {
    const sym = symbol || chartSymbolRef.current;
    const cndl = await api.candles(120, sym);
    setCandles(cndl.candles.map(mapApiCandle));
    if (cndl.symbol) setChartSymbol(cndl.symbol);
  }, []);

  const refreshCore = useCallback(async () => {
    const [acc, sig, tr, jr, pr, sys, uni] = await Promise.all([
      api.account(),
      api.signals(),
      api.trades(),
      api.journal(),
      api.params(),
      api.systemStatus().catch(() => null),
      api.universe().catch(() => null),
    ]);
    setAccount(acc);
    setSignals(sig);
    setTrades(tr.trades || []);
    setJournal(jr.entries || []);
    setParams(pr);
    if (uni) setUniverse(uni);
    const fromTrades = (tr.trades || [])
      .map((t: Record<string, unknown>) => tradeToActivity(t))
      .filter(Boolean) as ActivityEvent[];
    const fromDec = ((sig.decisions || []) as DecisionLine[])
      .map((d) =>
        decisionToActivity({
          ts: d.ts,
          symbol: d.symbol,
          action: d.action,
          narrative: d.narrative,
          reason: d.reason,
        })
      )
      .filter(Boolean) as ActivityEvent[];
    setActivity((prev) => mergeActivity(prev, [...fromTrades, ...fromDec]));
    if (sys) {
      setSystemStatus(sys);
      setSystemStatusLoading(false);
    }
    return { acc, pr };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [{ acc, pr }, al, llmParams, lastBt] = await Promise.all([
        refreshCore(),
        api.alertParams(),
        api.llmParams().catch(() => ({})),
        api.lastBacktest().catch(() => null),
      ]);
      setAlerts(al);
      setLlm(llmParams);
      if (lastBt && lastBt.ok) setBacktest(lastBt);
      if (Object.prototype.hasOwnProperty.call(pr, "candle_granularity_sec")) {
        setCandleGranularitySec(normalizeChartTimeframeSec(pr.candle_granularity_sec));
      }
      const book = chartSymbolOptions(pr, acc);
      const preferred =
        chartSymbolRef.current && book.includes(chartSymbolRef.current)
          ? chartSymbolRef.current
          : loadChartSymbol(book) || book[0] || String(pr.symbol || "BTC-USD");
      setChartSymbol(preferred);
      const cndl = await api.candles(120, preferred);
      setCandles(cndl.candles.map(mapApiCandle));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erreur de chargement");
    }
  }, [refreshCore]);

  const changeChartTimeframe = useCallback(
    async (seconds: number) => {
      if (seconds === candleGranularitySec) return;
      setChartsLoading(true);
      setCandleGranularitySec(seconds);
      saveChartTimeframeSec(seconds);
      try {
        await api.putParams({ candle_granularity_sec: seconds });
        setParams((prev) => ({ ...prev, candle_granularity_sec: seconds }));
        await fetchCandles(chartSymbolRef.current);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Intervalle graphique échoué");
        try {
          const pr = await api.params();
          setParams(pr);
          setCandleGranularitySec(normalizeChartTimeframeSec(pr.candle_granularity_sec));
        } catch {
          // keep optimistic UI; user can refresh
        }
      } finally {
        setChartsLoading(false);
      }
    },
    [candleGranularitySec, fetchCandles]
  );

  const changeChartSymbol = useCallback(
    async (symbol: string) => {
      if (symbol === chartSymbolRef.current) return;
      setChartsLoading(true);
      setChartSymbol(symbol);
      saveChartSymbol(symbol);
      try {
        await fetchCandles(symbol);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Chargement du graphique échoué");
      } finally {
        setChartsLoading(false);
      }
    },
    [fetchCandles]
  );

  useEffect(() => {
    const stored = loadChartTimeframeSec();
    if (stored != null) setCandleGranularitySec(stored);
  }, []);

  useEffect(() => {
    return () => {
      for (const timer of Object.values(toastTimersRef.current)) clearTimeout(timer);
      toastTimersRef.current = {};
    };
  }, []);

  useEffect(() => {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - 14);
    setBtEnd(end.toISOString().slice(0, 10));
    setBtStart(start.toISOString().slice(0, 10));
    void refresh();
    void fetchNewsBrief().then(({ brief, loading }) => {
      setNewsBrief((prev) => pickRicherNewsBrief(prev, brief));
      setNewsLoading(loading && !isUsableNewsBrief(brief));
    });
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      void fetchNewsBrief().then(({ brief, loading }) => {
        if (cancelled) return;
        setNewsBrief((prev) => pickRicherNewsBrief(prev, brief));
        if (isUsableNewsBrief(brief)) setNewsLoading(false);
        else if (!newsFromStreamRef.current) setNewsLoading(loading);
      });
    };
    // First poll soon after mount in case the stream sent an empty news shell.
    const boot = setTimeout(poll, 2500);
    const timer = setInterval(() => {
      if (!newsFromStreamRef.current) poll();
    }, 60_000);
    return () => {
      cancelled = true;
      clearTimeout(boot);
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const clearReconnect = () => {
      if (reconnectTimer != null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const applyStreamMessage = (msg: MessageEvent<string>) => {
      try {
        const data = JSON.parse(msg.data) as {
          type?: string;
          account?: Account;
          signal?: SignalPayload;
          trades?: Array<Record<string, unknown>>;
          journal?: Array<Record<string, unknown>>;
          price?: number;
          news?: unknown;
          news_brief?: unknown;
          universe?: { symbols?: string[]; size?: number; news_hits?: UniversePayload["news_hits"]; updated_at?: string | null };
        };
        if (data.type === "heartbeat") return;
        const streamNews = newsFromStreamPayload(data as Record<string, unknown>);
        if (streamNews) {
          // Never let an empty early snapshot wipe a good poll, and keep polling
          // until we have actually received usable headlines from the stream.
          if (isUsableNewsBrief(streamNews)) {
            newsFromStreamRef.current = true;
            setNewsBrief(streamNews);
            setNewsLoading(false);
          } else {
            setNewsBrief((prev) => pickRicherNewsBrief(prev, streamNews));
          }
        }
        if (data.account) {
          setAccount(data.account);
          if (typeof data.account.auto_trade === "boolean") {
            setParams((prev) => ({ ...prev, auto_trade: data.account!.auto_trade }));
          }
          if (data.account.symbols?.length) {
            setParams((prev) => ({ ...prev, symbols: data.account!.symbols }));
          }
        }
        if (data.universe) {
          setUniverse((prev) => ({
            ...(prev || {}),
            ...data.universe,
            symbols: data.universe?.symbols || prev?.symbols,
            news_hits: data.universe?.news_hits || prev?.news_hits,
          }));
        }
        if (data.signal) setSignals(data.signal);
        if (data.trades) {
          setTrades(data.trades);
          const incoming = data.trades
            .map((t) => tradeToActivity(t))
            .filter(Boolean) as ActivityEvent[];
          if (incoming.length) {
            setActivity((prev) => mergeActivity(prev, incoming));
          }
        }
        if (data.journal) setJournal(data.journal);
        if (data.signal?.decisions) {
          const decs = (data.signal.decisions as DecisionLine[])
            .map((d) =>
              decisionToActivity({
                ts: d.ts,
                symbol: d.symbol,
                action: d.action,
                narrative: d.narrative,
                reason: d.reason,
                order_placed: undefined,
              })
            )
            .filter(Boolean) as ActivityEvent[];
          if (decs.length) setActivity((prev) => mergeActivity(prev, decs));
        }
        const focus = chartSymbolRef.current;
        const priced =
          (data.account?.prices && typeof data.account.prices[focus] === "number"
            ? data.account.prices[focus]
            : undefined) ??
          (typeof data.price === "number" &&
          (!data.account?.symbol || data.account.symbol === focus || data.account.active_symbol === focus)
            ? data.price
            : undefined);
        if (typeof priced === "number") {
          setCandles((prev) => {
            if (!prev.length) return prev;
            const next = [...prev];
            const last = { ...next[next.length - 1] };
            last.close = priced;
            last.high = Math.max(last.high, priced);
            last.low = Math.min(last.low, priced);
            next[next.length - 1] = last;
            return next;
          });
        }
        setLive(true);
        setStreamPhase("live");
        setError(null);
      } catch {
        // ignore malformed frames
      }
    };

    const connect = () => {
      if (cancelled) return;
      clearReconnect();
      if (es) {
        es.onopen = null;
        es.onerror = null;
        es.onmessage = null;
        es.close();
        es = null;
      }
      setStreamPhase(attempt === 0 ? "connecting" : "reconnecting");
      setLive(false);
      const next = new EventSource("/api/engine/stream");
      es = next;
      next.onopen = () => {
        if (cancelled || es !== next) return;
        attempt = 0;
        setLive(true);
        setStreamPhase("live");
      };
      next.onmessage = (msg) => {
        if (cancelled || es !== next) return;
        applyStreamMessage(msg);
      };
      next.onerror = () => {
        if (cancelled || es !== next) return;
        setLive(false);
        setStreamPhase("reconnecting");
        next.close();
        if (es === next) es = null;
        attempt += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt - 1, 4));
        clearReconnect();
        reconnectTimer = setTimeout(connect, delay);
      };
    };

    connect();
    const candleTimer = setInterval(() => {
      void fetchCandles();
    }, 60000);
    const healthTimer = setInterval(() => {
      if (cancelled) return;
      void api
        .systemStatus()
        .then((sys) => {
          if (cancelled) return;
          setSystemStatus(sys);
          setSystemStatusLoading(false);
          setAccount((prev) =>
            prev
              ? {
                  ...prev,
                  bot_running: Boolean(sys.bot_running ?? prev.bot_running),
                  killed: Boolean(sys.killed ?? prev.killed),
                  auto_trade:
                    typeof sys.auto_trade === "boolean" ? sys.auto_trade : prev.auto_trade,
                }
              : prev
          );
          if (typeof sys.auto_trade === "boolean") {
            setParams((prev) => ({ ...prev, auto_trade: sys.auto_trade }));
          }
        })
        .catch(() => {
          void api
            .health()
            .then((h) => {
              if (cancelled) return;
              setAccount((prev) =>
                prev
                  ? {
                      ...prev,
                      bot_running: Boolean(h.bot_running ?? prev.bot_running),
                      killed: Boolean(h.killed ?? prev.killed),
                      auto_trade:
                        typeof h.auto_trade === "boolean" ? h.auto_trade : prev.auto_trade,
                    }
                  : prev
              );
            })
            .catch(() => {
              /* stream reconnect handles transport issues */
            });
        });
    }, 20_000);

    return () => {
      cancelled = true;
      clearReconnect();
      clearInterval(candleTimer);
      clearInterval(healthTimer);
      if (es) {
        es.onopen = null;
        es.onerror = null;
        es.onmessage = null;
        es.close();
      }
    };
  }, [fetchCandles]);

  function dismissToast(id: string) {
    const timer = toastTimersRef.current[id];
    if (timer) {
      clearTimeout(timer);
      delete toastTimersRef.current[id];
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  function pushToast(
    message: string,
    tone: ToastTone = "info",
    extra?: { undoLabel?: string; onUndo?: () => void; ttlMs?: number },
  ) {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const item: ToastItem = {
      id,
      message,
      tone,
      undoLabel: extra?.undoLabel,
      onUndo: extra?.onUndo,
    };
    setToasts((prev) => [...prev.slice(-3), item]);
    const ttl = extra?.ttlMs ?? (extra?.onUndo ? 6_000 : 3_500);
    toastTimersRef.current[id] = setTimeout(() => dismissToast(id), ttl);
  }

  async function run(
    actionId: string,
    action: () => Promise<unknown>,
    opts?: {
      confirm?: string;
      optimistic?: () => void;
      successMessage?: string;
      undo?: { label: string; run: () => Promise<void> | void };
    },
  ) {
    if (actionLockRef.current) return;
    if (opts?.confirm && !window.confirm(opts.confirm)) return;
    actionLockRef.current = true;
    setBusy(true);
    setPendingAction(actionId);
    setError(null);
    opts?.optimistic?.();
    const nice = ACTION_LABELS[actionId] || actionId;
    try {
      await action();
      // Fast path after click: skip candles/backtest so the UI unlocks sooner.
      await refreshCore();
      setError(null);
      if (opts?.undo) {
        pushToast(opts.successMessage || `${nice} — fait.`, "ok", {
          undoLabel: opts.undo.label,
          onUndo: () => {
            void (async () => {
              try {
                await opts.undo!.run();
                await refreshCore();
                pushToast("Annulé.", "info");
              } catch (e) {
                pushToast(e instanceof Error ? e.message : "Annulation échouée", "error");
              }
            })();
          },
          ttlMs: 6_000,
        });
      } else {
        pushToast(opts?.successMessage || `${nice} — fait.`, "ok");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Action échouée";
      setError(msg);
      pushToast(msg, "error");
      try {
        await refreshCore();
      } catch {
        /* keep original error */
      }
    } finally {
      actionLockRef.current = false;
      setPendingAction(null);
      setBusy(false);
    }
  }

  function sellPosition(symbol: string) {
    void run(
      `close-${symbol}`,
      async () => {
        const res = await api.closePosition(symbol);
        if (res.account) setAccount(res.account);
      },
      {
        confirm: `Vendre toute la position ${symbol} au marché paper ?`,
        successMessage: `Vente ${symbol} demandée.`,
        optimistic: () => {
          setAccount((prev) => {
            if (!prev) return prev;
            const positions = prev.positions.filter((p) => p.symbol !== symbol);
            return {
              ...prev,
              positions,
              open_count: positions.length,
            };
          });
        },
      },
    );
  }

  function actionLabelBusy(id: string, idle: string) {
    return pendingAction === id ? "…" : idle;
  }

  async function saveParams(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    // Never coerce these to Number() — "agressif" must stay a string (Number→NaN→JSON null breaks API/stream).
    const stringKeys = new Set([
      "trading_style",
      "symbol",
      "watchlist",
      "symbols",
      "telegram_chat_id",
      "telegram_bot_token",
      "email_to",
      "smtp_host",
      "smtp_user",
      "smtp_password",
      "smtp_from",
    ]);
    const body: Record<string, unknown> = {};
    for (const [k, v] of fd.entries()) {
      const raw = String(v);
      if (v === "on") {
        body[k] = true;
        continue;
      }
      if (raw.trim() === "") continue;
      if (stringKeys.has(k)) {
        body[k] = raw;
        continue;
      }
      if (!Number.isNaN(Number(raw))) body[k] = Number(raw);
      else body[k] = raw;
    }
    body.auto_trade = fd.get("auto_trade") === "on";
    body.kill_switch_enabled = fd.get("kill_switch_enabled") === "on";
    if (typeof body.trading_style === "string") {
      body.trading_style = body.trading_style.trim().toLowerCase();
    }

    const watchlistRaw = String(fd.get("watchlist") || "").trim();
    if (watchlistRaw) {
      body.watchlist = watchlistRaw.includes(",")
        ? watchlistRaw.split(",").map((s) => s.trim()).filter(Boolean)
        : watchlistRaw;
    }
    for (const key of [
      "take_profit_pct",
      "stop_loss_pct",
      "max_position_pct",
      "news_weight",
      "add_cooldown_min",
      "aggressive_sell_confidence",
    ] as const) {
      const raw = fd.get(key);
      if (raw == null || String(raw).trim() === "") continue;
      const n = Number(raw);
      if (!Number.isNaN(n)) body[key] = n;
    }

    await run("save-params", () => api.putParams(body), {
      successMessage: "Paramètres enregistrés.",
    });
  }

  async function saveAlerts(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const body: Record<string, unknown> = {
      telegram_enabled: fd.get("telegram_enabled") === "on",
      telegram_chat_id: String(fd.get("telegram_chat_id") || ""),
      telegram_bot_token: String(fd.get("telegram_bot_token") || ""),
      alert_on_trade: fd.get("alert_on_trade") === "on",
      alert_on_pnl: fd.get("alert_on_pnl") === "on",
      alert_on_strong_signal: fd.get("alert_on_strong_signal") === "on",
      pnl_alert_threshold: Number(fd.get("pnl_alert_threshold") || 100),
      strong_signal_threshold: Number(fd.get("strong_signal_threshold") || 0.75),
    };
    await run("save-alerts", () => api.putAlerts(body), {
      successMessage: "Alertes enregistrées.",
    });
  }

  async function saveLlm(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const body: Record<string, unknown> = {
      groq_api_key: String(fd.get("groq_api_key") || ""),
      groq_api_key_2: String(fd.get("groq_api_key_2") || ""),
      gemini_api_key: String(fd.get("gemini_api_key") || ""),
      openrouter_api_key: String(fd.get("openrouter_api_key") || ""),
      groq_model: String(fd.get("groq_model") || ""),
      gemini_model: String(fd.get("gemini_model") || ""),
      openrouter_free_model: String(fd.get("openrouter_free_model") || ""),
      timeout_ms: Number(fd.get("timeout_ms") || 2500),
      free_pool: String(fd.get("free_pool") || "auto"),
    };
    const enableCopilot = fd.get("copilot_enabled") === "on";
    await run(
      "save-llm",
      async () => {
        const saved = await api.putLlmParams(body);
        setLlm(saved);
        const nextParams = await api.putParams({ copilot_enabled: enableCopilot });
        setParams(nextParams);
      },
      { successMessage: "Co-pilote LLM enregistré." },
    );
  }

  async function runBacktest(e: FormEvent) {
    e.preventDefault();
    await run(
      "backtest",
      async () => {
        const res = await api.backtest({
          symbol: String(params.symbol || "BTC-USD"),
          start: new Date(`${btStart}T00:00:00Z`).toISOString(),
          end: new Date(`${btEnd}T23:59:59Z`).toISOString(),
          initial_balance: Number(account?.initial_balance || params.initial_balance || 800),
        });
        setBacktest(res);
      },
      { successMessage: "Backtest terminé." },
    );
  }

  const risk = riskTone(account?.risk_score ?? 0);
  const pnl = account?.total_pnl ?? 0;
  const botRunning = Boolean(account?.bot_running);
  const newsChip = newsBias(newsBrief?.overall_score);
  const showNewsWeight = Object.prototype.hasOwnProperty.call(params, "news_weight");
  const watchlistDefault = paramDisplay(params, ["watchlist", "symbols"]);
  const autoTradeOn =
    typeof account?.auto_trade === "boolean"
      ? account.auto_trade
      : Boolean(params.auto_trade);

  const pendingBannerLabel = pendingAction
    ? pendingAction.startsWith("close-")
      ? ACTION_LABELS.close
      : ACTION_LABELS[pendingAction] || pendingAction
    : null;

  return (
    <div className={`mx-auto max-w-7xl px-3 py-4 md:px-6 md:py-6 ${busy ? "pt-12" : ""}`}>
      <PendingBanner visible={busy} label={pendingBannerLabel} />
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] text-muted">Trading simulé</p>
          <h1 className="text-2xl font-semibold md:text-3xl">workspace-trader</h1>
          <p className="text-sm text-muted">
            {account?.symbol || "…"} · {account?.last_price ? money(account.last_price) : "—"} USD
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              live
                ? "bg-gain text-white"
                : streamPhase === "reconnecting"
                  ? "bg-riskOrange text-white"
                  : "bg-slate-600 text-slate-200"
            }`}
          >
            {live ? "FLUX ACTIF" : streamPhase === "reconnecting" ? "RECONNEXION…" : "FLUX COUPÉ"}
          </span>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${newsChip.className}`}>
            {newsLoading && !newsBrief ? "ACTU …" : newsChip.label}
          </span>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold text-white ${risk.className}`}>
            Risque {risk.label}
          </span>
          {account?.killed ? (
            <span className="rounded-full bg-riskRed px-3 py-1 text-xs font-semibold">URGENCE</span>
          ) : account?.bot_running ? (
            <span className="rounded-full bg-gain px-3 py-1 text-xs font-semibold">BOT ACTIF</span>
          ) : (
            <span className="rounded-full bg-slate-600 px-3 py-1 text-xs font-semibold">BOT ARRÊTÉ</span>
          )}
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              autoTradeOn ? "bg-gain text-white" : "bg-riskOrange text-white"
            }`}
            title={
              autoTradeOn
                ? "Les ordres paper peuvent être exécutés automatiquement"
                : "La boucle tourne mais aucun ordre n’est placé (trading auto off)"
            }
          >
            {autoTradeOn ? "ORDRES AUTO" : "ORDRES COUPÉS"}
          </span>
        </div>
      </header>

      <AppNav page={page} onChange={setPage} />

      {!autoTradeOn && !account?.killed && (
        <div className="mb-4 rounded-lg border border-riskOrange/40 bg-riskOrange/10 px-3 py-2 text-sm text-orange-200">
          Trading automatique désactivé — le bot peut analyser le marché (BOT ACTIF) mais{" "}
          <strong>n’ouvre ni ne ferme aucun ordre</strong>. Va dans Configuration, ou clique Reprendre
          après une urgence.
        </div>
      )}

      {!live && (
        <div className="mb-4 rounded-lg border border-riskOrange/40 bg-riskOrange/10 px-3 py-2 text-sm text-orange-200">
          {streamPhase === "reconnecting"
            ? "Flux coupé — reconnexion automatique en cours. Le bot continue côté moteur si celui-ci tourne encore."
            : "Connexion au moteur en cours… Si ça dure, vérifiez que l’engine écoute sur le port 8100."}
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-riskOrange/40 bg-riskOrange/10 px-3 py-2 text-sm text-orange-200">
          {error}
        </div>
      )}

      {page === "home" ? (
      <>
      <SystemStatus
        status={systemStatus}
        streamLive={live}
        streamPhase={streamPhase}
        loading={systemStatusLoading}
        defaultCollapsed
      />

      <div className="md:hidden">
        <UniversePanel
          universe={universe}
          held={heldSymbols(account)}
          busy={busy}
          defaultCollapsed
          onPick={(sym) => void changeChartSymbol(sym)}
          onRefresh={() =>
            void run("refresh-universe", async () => {
              const uni = await api.refreshUniverse();
              setUniverse(uni);
              if (uni.symbols?.length) {
                setParams((prev) => ({ ...prev, symbols: uni.symbols }));
              }
            })
          }
        />
      </div>

      {/* Mobile essentials */}
      <section className="grid grid-cols-2 gap-3 md:hidden">
        <div className="col-span-2 flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${newsChip.className}`}>
            {newsLoading && !newsBrief ? "ACTU …" : newsChip.label}
          </span>
          <span className="text-xs text-muted">
            Biais {newsBrief?.overall_score != null ? newsBrief.overall_score.toFixed(2) : "—"}
          </span>
        </div>
        <MobileAccountStrip account={account} />
        <div className="col-span-2">
          <PaperSessionChart
            equity={account?.equity ?? 0}
            cash={account?.cash ?? 0}
            positions={account?.open_count ?? account?.positions?.length ?? 0}
            pnl={pnl}
            initialBalance={account?.initial_balance}
            live={live}
          />
        </div>
        <div className="card col-span-2">
          <LiveEquity
            equity={account?.equity ?? 0}
            cash={account?.cash ?? 0}
            pnl={pnl}
            live={live}
          />
        </div>
        <div className="card">
          <p className="label">Liquidités</p>
          <p className="text-lg font-medium">{money(account?.cash ?? 0)}</p>
        </div>
        <div className="card">
          <p className="label">Perte max</p>
          <p className="text-lg font-medium">{money(account?.drawdown_pct ?? 0, 1)}%</p>
        </div>
        <div className="card col-span-2">
          <p className="label">Positions</p>
          <PositionsPanel
            positions={account?.positions || []}
            chartSymbol={chartSymbol}
            busy={busy}
            compact
            onPick={(sym) => void changeChartSymbol(sym)}
            onSell={sellPosition}
            sellLabel={(sym) => actionLabelBusy(`close-${sym}`, "Vendre")}
          />
        </div>
        <div className="card col-span-2">
          <p className="label">Dernier signal</p>
          <p className="text-lg font-semibold">{actionLabel(signals?.latest?.action)}</p>
          <p className="text-sm text-muted">
            confiance {((signals?.latest?.confidence || 0) * 100).toFixed(0)}%
          </p>
          <SignalText text={signals?.explanation} className="mt-1 text-xs leading-relaxed text-muted" />
          <DecisionJournal items={signals?.decisions || []} />
        </div>
        <div className="card col-span-2">
          <NewsPanel brief={newsBrief} loading={newsLoading} compact />
        </div>
        <div
          className={`col-span-2 grid grid-cols-2 gap-2 ${busy ? "pointer-events-none opacity-80" : ""}`}
          aria-busy={busy}
        >
          <button
            className="btn-primary"
            disabled={busy}
            title="Force une décision IA immédiate (un tick paper)."
            onClick={() =>
              void run("tick", () => api.tick(), { successMessage: "Décision IA lancée." })
            }
          >
            {actionLabelBusy("tick", "Décider")}
          </button>
          <button
            className={botRunning ? "btn-ghost" : "btn-primary"}
            disabled={busy}
            title={
              botRunning
                ? "Arrête la boucle de décisions ; positions et cash conservés."
                : "Démarre la boucle automatique de décisions paper."
            }
            onClick={() =>
              void run("loop", () => (botRunning ? api.stop() : api.start()), {
                successMessage: botRunning ? "Boucle arrêtée." : "Boucle démarrée.",
                optimistic: () =>
                  setAccount((prev) =>
                    prev ? { ...prev, bot_running: !botRunning } : prev,
                  ),
              })
            }
          >
            {actionLabelBusy("loop", botRunning ? "Arrêter" : "Démarrer")}
          </button>
          <button
            className="btn-danger"
            disabled={busy}
            title="Kill switch : bloque les nouveaux ordres paper."
            onClick={() =>
              void run("kill", () => api.kill(), {
                confirm: "Activer l’arrêt d’urgence ? Aucun nouvel ordre paper ne sera placé.",
                successMessage: "Arrêt d’urgence actif.",
                optimistic: () =>
                  setAccount((prev) =>
                    prev ? { ...prev, killed: true, auto_trade: false } : prev,
                  ),
              })
            }
          >
            {actionLabelBusy("kill", "Urgence")}
          </button>
          <button
            className="btn-ok col-span-2"
            disabled={busy}
            title="Reprend après un arrêt ou une urgence."
            onClick={() =>
              void run("resume", () => api.resume(), {
                successMessage: "Trading repris.",
                optimistic: () =>
                  setAccount((prev) =>
                    prev ? { ...prev, killed: false, kill_reason: null, bot_running: true } : prev,
                  ),
              })
            }
          >
            {actionLabelBusy("resume", "Reprendre")}
          </button>
        </div>
      </section>

      {/* Desktop home */}
      <div className="mt-4 hidden gap-4 md:grid md:grid-cols-12">
        <div className="md:col-span-12">
          <PaperSessionChart
            equity={account?.equity ?? 0}
            cash={account?.cash ?? 0}
            positions={account?.open_count ?? account?.positions?.length ?? 0}
            pnl={pnl}
            initialBalance={account?.initial_balance}
            live={live}
          />
        </div>

        <UniversePanel
          universe={universe}
          held={heldSymbols(account)}
          busy={busy}
          defaultCollapsed
          onPick={(sym) => void changeChartSymbol(sym)}
          onRefresh={() =>
            void run("refresh-universe", async () => {
              const uni = await api.refreshUniverse();
              setUniverse(uni);
              if (uni.symbols?.length) {
                setParams((prev) => ({ ...prev, symbols: uni.symbols }));
              }
            })
          }
        />

        <section className="card md:col-span-8">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 className="text-lg font-medium">Prix</h2>
              <p className="text-xs text-muted">{chartSymbol}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <ChartSymbols
                symbols={chartSymbolOptions(params, account, universe)}
                value={chartSymbol}
                held={heldSymbols(account)}
                onChange={(sym) => void changeChartSymbol(sym)}
                disabled={busy || chartsLoading}
              />
              <ChartTimeframes
                value={candleGranularitySec}
                onChange={(sec) => void changeChartTimeframe(sec)}
                disabled={busy || chartsLoading}
              />
              <button className="btn-ghost" disabled={busy} onClick={() => void refresh()}>
                Actualiser
              </button>
            </div>
          </div>
          <PriceChart
            candles={candles}
            trades={trades.filter((t) => String(t.symbol || "") === chartSymbol)}
            symbol={chartSymbol}
            loading={chartsLoading}
          />
        </section>

        <section className="card md:col-span-4 space-y-3">
          <h2 className="text-lg font-medium">Compte</h2>
          <LiveEquity
            equity={account?.equity ?? 0}
            cash={account?.cash ?? 0}
            pnl={pnl}
            live={live}
          />
          <Stat
            label="Slots / univers"
            value={`${account?.open_count ?? account?.positions?.length ?? 0} / ${account?.max_open_symbols ?? "—"} · ${account?.universe_size ?? universe?.size ?? "—"} paires`}
            hint="Positions ouvertes, plafond dynamique (~10 à 800 $), taille de l’univers scanné."
          />
          <Stat
            label="Liquidités"
            value={`${money(account?.cash ?? 0)} $`}
            hint="Argent libre, pas encore investi."
          />
          <Stat
            label="P&L total"
            value={`${pnl >= 0 ? "+" : ""}${money(pnl)} $`}
            className={pnlClass(pnl)}
            hint="Gain ou perte depuis le solde initial (réalisé + non réalisé)."
          />
          <Stat
            label="Réalisé"
            value={`${money(account?.realized_pnl ?? 0)} $`}
            className={pnlClass(account?.realized_pnl ?? 0)}
            hint="Gains/pertes des ventes déjà fermées. Reste à 0 tant qu’il n’y a pas eu de vente."
          />
          <Stat
            label="Non réalisé"
            value={`${money(account?.unrealized_pnl ?? 0)} $`}
            className={pnlClass(account?.unrealized_pnl ?? 0)}
            hint="Papier : variation latente des positions encore ouvertes."
          />
          <Stat
            label="Perte max"
            value={`${money(account?.drawdown_pct ?? 0, 1)} %`}
            hint="Baisse maximale depuis le plus haut du compte (drawdown)."
          />
          <div className={`space-y-3 pt-2 ${busy ? "opacity-80" : ""}`} aria-busy={busy}>
            <div>
              <p className="label mb-1.5">Pilotage</p>
              <div className={`grid grid-cols-2 gap-2 ${busy ? "pointer-events-none" : ""}`}>
                <button
                  className="btn-primary"
                  disabled={busy}
                  title="Force une décision IA immédiate (un tick paper)."
                  onClick={() =>
                    void run("tick", () => api.tick(), {
                      successMessage: "Décision IA lancée.",
                    })
                  }
                >
                  {actionLabelBusy("tick", "Décider (IA)")}
                </button>
                <button
                  className={botRunning ? "btn-ghost" : "btn-primary"}
                  disabled={busy}
                  title={
                    botRunning
                      ? "Arrête la boucle de décisions ; positions et cash conservés."
                      : "Démarre la boucle automatique de décisions paper."
                  }
                  onClick={() =>
                    void run("loop", () => (botRunning ? api.stop() : api.start()), {
                      successMessage: botRunning ? "Boucle arrêtée." : "Boucle démarrée.",
                      optimistic: () =>
                        setAccount((prev) =>
                          prev ? { ...prev, bot_running: !botRunning } : prev,
                        ),
                    })
                  }
                >
                  {actionLabelBusy("loop", botRunning ? "Arrêter" : "Démarrer")}
                </button>
              </div>
            </div>

            <div>
              <p className="label mb-1.5">Sécurité</p>
              <div className={`grid grid-cols-2 gap-2 ${busy ? "pointer-events-none" : ""}`}>
                <button
                  className="btn-danger"
                  disabled={busy}
                  title="Kill switch : bloque les nouveaux ordres paper."
                  onClick={() =>
                    void run("kill", () => api.kill(), {
                      confirm:
                        "Activer l’arrêt d’urgence ? Aucun nouvel ordre paper ne sera placé.",
                      successMessage: "Arrêt d’urgence actif.",
                      optimistic: () =>
                        setAccount((prev) =>
                          prev ? { ...prev, killed: true, auto_trade: false } : prev,
                        ),
                    })
                  }
                >
                  {actionLabelBusy("kill", "Arrêt d’urgence")}
                </button>
                <button
                  className="btn-ok"
                  disabled={busy}
                  title="Reprend après un arrêt ou une urgence."
                  onClick={() =>
                    void run("resume", () => api.resume(), {
                      successMessage: "Trading repris.",
                      optimistic: () =>
                        setAccount((prev) =>
                          prev
                            ? { ...prev, killed: false, kill_reason: null, bot_running: true }
                            : prev,
                        ),
                    })
                  }
                >
                  {actionLabelBusy("resume", "Reprendre")}
                </button>
              </div>
            </div>

            <div>
              <p className="label mb-1.5">Compte</p>
              <div className={`grid grid-cols-2 gap-2 ${busy ? "pointer-events-none" : ""}`}>
                <button
                  className={autoTradeOn ? "btn-ghost" : "btn-primary"}
                  disabled={busy}
                  title={
                    autoTradeOn
                      ? "Désactive les ordres automatiques (pas de nouveaux trades)."
                      : "Autorise les ordres paper automatiques."
                  }
                  onClick={() => {
                    const next = !autoTradeOn;
                    void run("auto", () => api.putParams({ auto_trade: next }), {
                      successMessage: next ? "Ordres auto activés." : "Ordres auto coupés.",
                      optimistic: () => {
                        setAccount((prev) => (prev ? { ...prev, auto_trade: next } : prev));
                        setParams((prev) => ({ ...prev, auto_trade: next }));
                      },
                      undo: {
                        label: "Annuler",
                        run: async () => {
                          await api.putParams({ auto_trade: !next });
                          setAccount((prev) => (prev ? { ...prev, auto_trade: !next } : prev));
                          setParams((prev) => ({ ...prev, auto_trade: !next }));
                        },
                      },
                    });
                  }}
                >
                  {actionLabelBusy("auto", autoTradeOn ? "Couper auto" : "Activer auto")}
                </button>
                <button
                  className="btn-ghost"
                  disabled={busy}
                  title="Remet cash, positions, trades et historique chart au solde initial."
                  onClick={() =>
                    void run("reset", () => api.reset(), {
                      confirm:
                        "Réinitialiser le compte paper ? Cash, positions et historique seront remis au solde initial. Cette action est irréversible.",
                      successMessage: "Compte paper réinitialisé.",
                    })
                  }
                >
                  {actionLabelBusy("reset", "Réinitialiser")}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section className="card md:col-span-4">
          <h2 className="mb-2 text-lg font-medium">Positions</h2>
          <p className="mb-3 text-xs text-muted">
            « Gagnante nette » = encore positif si tu vends maintenant après frais + slippage.
            Rouge = perdante. Ambre = brut positif mais sous les frais.
          </p>
          <PositionsPanel
            positions={account?.positions || []}
            chartSymbol={chartSymbol}
            busy={busy}
            onPick={(sym) => void changeChartSymbol(sym)}
            onSell={sellPosition}
            sellLabel={(sym) => actionLabelBusy(`close-${sym}`, "Vendre")}
          />
        </section>

        <section className="card md:col-span-4">
          <h2 className="mb-2 text-lg font-medium">Signal IA</h2>
          <p className="text-2xl font-semibold">{actionLabel(signals?.latest?.action)}</p>
          <p className="text-sm text-muted">
            Confiance {((signals?.latest?.confidence || 0) * 100).toFixed(1)}% ·{" "}
            {signals?.latest?.model_ready ? "XGBoost" : "Heuristique"}
          </p>
          <SignalText text={signals?.explanation} className="mt-2 space-y-1 text-xs leading-relaxed text-slate-300" />
          <DecisionJournal items={signals?.decisions || []} />
          <div className="mt-3 space-y-1 text-xs text-muted">
            {(signals?.history || []).slice(0, 5).map((h) => (
              <div key={h.ts} className="flex justify-between gap-2">
                <span>{new Date(h.ts).toLocaleString("fr-FR")}</span>
                <span>{actionLabel(h.signal?.action)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="card md:col-span-4">
          <NewsPanel brief={newsBrief} loading={newsLoading} />
        </section>
      </div>
      </>
      ) : null}

      {page === "activity" ? (
        <div className="mt-2">
          <ActivityFeed events={activity} />
        </div>
      ) : null}

      {page === "orders" ? (
        <div className="mt-2 grid gap-4 md:grid-cols-2">
          <LiveOrders trades={trades as AnnotatedTrade[]} />
          <section className="card">
            <h2 className="mb-2 text-lg font-medium">Historique paper</h2>
            <p className="mb-2 text-xs text-muted">
              Vente = P&L net · achat = investi · legacy vente : P&L enregistré sans % / réf. d’entrée.
            </p>
            <ul className="max-h-[28rem] space-y-2 overflow-auto text-sm">
              {trades.length === 0 ? (
                <li className="text-muted">Aucun fill</li>
              ) : (
                trades.map((t) => (
                  <TradeFillRow key={String(t.id)} trade={t as AnnotatedTrade} />
                ))
              )}
            </ul>
          </section>
        </div>
      ) : null}

      {page === "config" ? (
      <div className="mt-2 grid gap-4 md:grid-cols-12">
        <section className="card md:col-span-6">
          <h2 className="mb-3 text-lg font-medium">Paramètres bot / modèle</h2>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => void saveParams(e)}>
            <label className="col-span-2 flex flex-col gap-1 text-xs">
              <span className="font-medium text-slate-300">Style de trading</span>
              <select
                name="trading_style"
                className="rounded-md border border-line bg-ink/60 px-2 py-1.5 text-sm text-slate-100"
                defaultValue={String(params.trading_style || "intermediaire")}
                key={`style-${String(params.trading_style || "intermediaire")}`}
              >
                <option value="prudent">Prudent — moins de trades, filtres stricts</option>
                <option value="intermediaire">Intermédiaire — équilibre</option>
                <option value="agressif">Agressif — plus d’entrées, TP plus proches</option>
              </select>
              <span className="text-[10px] text-muted">
                Change seuils, TP/SL, hold, taille, slots et scanner. Paper only.
              </span>
            </label>
            <div className="col-span-2 flex flex-wrap gap-2">
              {(["prudent", "intermediaire", "agressif"] as const).map((style) => (
                <button
                  key={style}
                  type="button"
                  className={
                    String(params.trading_style || "intermediaire") === style
                      ? "btn-primary text-xs"
                      : "btn-ghost text-xs"
                  }
                  disabled={busy}
                  onClick={() =>
                    void run(
                      `style-${style}`,
                      async () => {
                        await api.putParams({ trading_style: style });
                      },
                      {
                        successMessage:
                          style === "prudent"
                            ? "Style prudent."
                            : style === "agressif"
                              ? "Style agressif."
                              : "Style intermédiaire.",
                      },
                    )
                  }
                >
                  {pendingAction === `style-${style}`
                    ? "…"
                    : style === "prudent"
                      ? "Prudent"
                      : style === "agressif"
                        ? "Agressif"
                        : "Intermédiaire"}
                </button>
              ))}
            </div>
            <Field name="symbol" label="Paire" defaultValue={String(params.symbol || "BTC-USD")} />
            <Field name="initial_balance" label="Solde initial" type="number" step="1" defaultValue={String(params.initial_balance ?? 10000)} />
            <Field name="position_size_pct" label="Taille de position %" type="number" step="0.01" defaultValue={String(params.position_size_pct ?? 0.1)} />
            <Field name="decision_interval_sec" label="Fréquence (s)" type="number" step="1" defaultValue={String(params.decision_interval_sec ?? 30)} />
            <Field name="rsi_oversold" label="RSI survente" type="number" step="1" defaultValue={String(params.rsi_oversold ?? 30)} />
            <Field name="rsi_overbought" label="RSI surachat" type="number" step="1" defaultValue={String(params.rsi_overbought ?? 70)} />
            <Field name="buy_threshold" label="Seuil d’achat" type="number" step="0.01" defaultValue={String(params.buy_threshold ?? 0.58)} />
            <Field name="sell_threshold" label="Seuil de vente" type="number" step="0.01" defaultValue={String(params.sell_threshold ?? 0.5)} />
            <Field name="fee_bps" label="Frais (bps)" type="number" step="1" defaultValue={String(params.fee_bps ?? 10)} />
            <Field name="slippage_bps" label="Glissement (bps)" type="number" step="1" defaultValue={String(params.slippage_bps ?? 5)} />
            <Field name="max_daily_loss_pct" label="Perte max journalière %" type="number" step="0.1" defaultValue={String(params.max_daily_loss_pct ?? 5)} />
            <Field name="max_drawdown_pct" label="Perte max cumulée %" type="number" step="0.1" defaultValue={String(params.max_drawdown_pct ?? 15)} />
            <Field
              name="watchlist"
              label="Liste de paires"
              defaultValue={watchlistDefault}
              placeholder="BTC-USD, ETH-USD"
            />
            <Field
              name="take_profit_pct"
              label="Objectif de gain (ex. 0.008 = 0,8 %)"
              type="number"
              step="0.001"
              defaultValue={optionalParamString(params, "take_profit_pct", "0.008")}
            />
            <Field
              name="stop_loss_pct"
              label="Stop-loss (ex. 0.012 = 1,2 %)"
              type="number"
              step="0.001"
              defaultValue={optionalParamString(params, "stop_loss_pct", "0.012")}
            />
            <Field
              name="add_cooldown_min"
              label="Délai entre achats (min)"
              type="number"
              step="1"
              defaultValue={optionalParamString(params, "add_cooldown_min", "3")}
            />
            <Field
              name="max_position_pct"
              label="Position max %"
              type="number"
              step="0.01"
              defaultValue={optionalParamString(params, "max_position_pct", String(params.position_size_pct ?? 0.1))}
            />
            {showNewsWeight && (
              <Field
                name="news_weight"
                label="Poids actualités"
                type="number"
                step="0.01"
                defaultValue={optionalParamString(params, "news_weight", "0.15")}
              />
            )}
            <label
              className={`flex items-center gap-2 rounded-md border px-2 py-1.5 text-sm ${
                autoTradeOn ? "border-gain/40 bg-gain/10" : "border-riskOrange/50 bg-riskOrange/10"
              }`}
            >
              <input type="checkbox" name="auto_trade" defaultChecked={autoTradeOn} key={`auto-${autoTradeOn}`} />
              Trading automatique {autoTradeOn ? "(actif)" : "(coupé — aucun ordre)"}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="kill_switch_enabled" defaultChecked={params.kill_switch_enabled !== false} /> Arrêt d&apos;urgence auto
            </label>
            <button className="btn-primary col-span-2" disabled={busy} type="submit">
              Enregistrer paramètres
            </button>
          </form>
        </section>

        <section className="card md:col-span-6 space-y-4">
          <div>
            <h2 className="mb-3 text-lg font-medium">Alertes Web Push (recommandé)</h2>
            <WebPushOptIn />
          </div>
          <div>
          <h2 className="mb-3 text-lg font-medium">Alertes Telegram</h2>
          <p className="mb-2 text-xs text-muted">
            Conservé pour compatibilité — ne pas activer (préférence Eric : Web Push uniquement).
          </p>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => void saveAlerts(e)}>
            <label className="col-span-2 flex items-center gap-2 text-sm">
              <input type="checkbox" name="telegram_enabled" defaultChecked={false} disabled /> Activer Telegram (désactivé)
            </label>
            <Field name="telegram_chat_id" label="ID du chat" defaultValue={String(alerts.telegram_chat_id || "")} />
            <Field name="telegram_bot_token" label="Jeton du bot" defaultValue={String(alerts.telegram_bot_token || "")} />
            <Field name="pnl_alert_threshold" label="Seuil P&L $" type="number" defaultValue={String(alerts.pnl_alert_threshold ?? 100)} />
            <Field name="strong_signal_threshold" label="Seuil signal fort" type="number" step="0.01" defaultValue={String(alerts.strong_signal_threshold ?? 0.75)} />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="alert_on_trade" defaultChecked={alerts.alert_on_trade !== false} /> Sur chaque ordre</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="alert_on_pnl" defaultChecked={alerts.alert_on_pnl !== false} /> Sur le P&L</label>
            <label className="col-span-2 flex items-center gap-2 text-sm"><input type="checkbox" name="alert_on_strong_signal" defaultChecked={alerts.alert_on_strong_signal !== false} /> Sur signal fort</label>
            <button className="btn-primary col-span-2" disabled={busy} type="submit">Enregistrer alertes</button>
          </form>
          </div>
          <div>
            <h2 className="mb-3 text-lg font-medium">Co-pilote LLM</h2>
            <p className="mb-2 text-xs text-muted">
              Clés saisies ici priment sur Coolify. Les champs secrets affichent *** s&apos;ils sont déjà enregistrés — laisser *** pour ne pas les écraser.
              {systemStatus?.features?.find((f) => f.id === "copilot")
                ? ` Statut : ${String(systemStatus.features.find((f) => f.id === "copilot")?.detail || "")}`
                : ""}
            </p>
            <form className="grid grid-cols-2 gap-3" onSubmit={(e) => void saveLlm(e)} key={`llm-${String(llm.groq_api_key || "")}-${String(params.copilot_enabled)}`}>
              <label className="col-span-2 flex items-center gap-2 text-sm">
                <input type="checkbox" name="copilot_enabled" defaultChecked={params.copilot_enabled === true} /> Activer le co-pilote
              </label>
              <Field name="groq_api_key" label="Groq API key" type="password" defaultValue={String(llm.groq_api_key || "")} />
              <Field name="groq_api_key_2" label="Groq API key 2" type="password" defaultValue={String(llm.groq_api_key_2 || "")} />
              <Field name="gemini_api_key" label="Gemini API key" type="password" defaultValue={String(llm.gemini_api_key || "")} />
              <Field name="openrouter_api_key" label="OpenRouter API key" type="password" defaultValue={String(llm.openrouter_api_key || "")} />
              <Field name="groq_model" label="Modèle Groq" defaultValue={String(llm.groq_model || "")} />
              <Field name="gemini_model" label="Modèle Gemini" defaultValue={String(llm.gemini_model || "")} />
              <Field name="openrouter_free_model" label="Modèle OpenRouter :free" defaultValue={String(llm.openrouter_free_model || "")} />
              <Field name="timeout_ms" label="Timeout ms" type="number" defaultValue={String(llm.timeout_ms ?? 2500)} />
              <div>
                <label className="label" htmlFor="free_pool">Pool libre</label>
                <select className="field" id="free_pool" name="free_pool" defaultValue={String(llm.free_pool || "auto")}>
                  <option value="auto">auto</option>
                  <option value="on">on</option>
                  <option value="off">off</option>
                </select>
              </div>
              <button className="btn-primary col-span-2" disabled={busy} type="submit">
                Enregistrer co-pilote LLM
              </button>
            </form>
          </div>
        </section>

        <section className="card md:col-span-5">
          <h2 className="mb-3 text-lg font-medium">Backtest</h2>
          <form className="grid grid-cols-2 gap-3" onSubmit={(e) => void runBacktest(e)}>
            <div>
              <label className="label">Début</label>
              <input className="field" type="date" value={btStart} onChange={(e) => setBtStart(e.target.value)} />
            </div>
            <div>
              <label className="label">Fin</label>
              <input className="field" type="date" value={btEnd} onChange={(e) => setBtEnd(e.target.value)} />
            </div>
            <button className="btn-primary col-span-2" disabled={busy} type="submit">
              {busy ? "Backtest en cours…" : "Lancer backtest"}
            </button>
          </form>
          <p className="mt-2 text-xs text-muted">
            Paper only · solde de départ = capital paper ({money(Number(account?.initial_balance || 800))} $)
          </p>
        </section>

        <section className="card md:col-span-7">
          <h2 className="mb-2 text-lg font-medium">Journal de décision</h2>
          <ul className="max-h-80 space-y-3 overflow-auto text-sm">
            {journal.length === 0 && <li className="text-muted">Aucune décision enregistrée</li>}
            {journal.map((j) => {
              const kind = decisionKind(String(j.action), String(j.narrative || j.explanation || ""));
              return (
                <li
                  key={String(j.id)}
                  className={`rounded-lg border border-line border-l-2 p-3 ${decisionBorder(kind)}`}
                >
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <DecisionFlag kind={kind} />
                      <span className="font-medium">{String(j.symbol)}</span>
                    </div>
                    <span className="text-muted">{new Date(String(j.ts)).toLocaleString("fr-FR")}</span>
                  </div>
                  <p>
                    prix {money(Number(j.price))} · qté {Number(j.qty).toFixed(6)} · confiance{" "}
                    {(Number(j.confidence) * 100).toFixed(0)}%
                  </p>
                  {j.narrative ? (
                    <p className="mt-1 text-sm leading-relaxed text-slate-200">{String(j.narrative)}</p>
                  ) : (
                    <SignalText text={String(j.explanation || "")} className="mt-1 text-xs leading-relaxed text-slate-300" />
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        <section className="card md:col-span-12" aria-live="polite">
          <h2 className="mb-3 text-lg font-medium">Résultats backtest</h2>
          {busy && !backtest ? (
            <p className="text-sm text-muted">Calcul en cours sur l’historique Coinbase (paper)…</p>
          ) : null}
          {!backtest || backtest.ok === false ? (
            <p className="text-sm text-muted">
              {backtest && backtest.ok === false
                ? `Échec : ${String(backtest.error || "backtest")}`
                : "Aucun résultat encore. Choisissez les dates puis lancez un backtest — les chiffres s’affichent ici."}
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">Cash final</p>
                <p className="text-lg font-medium tabular-nums">
                  {money(
                    Number(
                      backtest.cash != null
                        ? backtest.cash
                        : Number(backtest.unrealized_pnl || 0) === 0
                          ? backtest.final_equity
                          : 0,
                    ),
                  )}{" "}
                  $
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">Equity</p>
                <p className="text-lg font-medium tabular-nums">
                  {money(Number(backtest.final_equity || 0))} $
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">Trades</p>
                <p className="text-lg font-medium tabular-nums">{String(backtest.trade_count ?? "—")}</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">Win rate</p>
                <p className="text-lg font-medium tabular-nums">
                  {(Number(backtest.win_rate || 0) * 100).toFixed(1)}%
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">Drawdown max</p>
                <p className="text-lg font-medium tabular-nums">
                  {(Number(backtest.max_drawdown || 0) * 100).toFixed(2)}%
                </p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-muted">P&L</p>
                <p className={`text-lg font-medium tabular-nums ${pnlClass(Number(backtest.total_pnl || 0))}`}>
                  {money(Number(backtest.total_pnl || 0))} $
                </p>
              </div>
            </div>
          )}
          {backtest && backtest.ok !== false ? (
            <p className="mt-3 text-xs text-muted">
              {String(backtest.symbol || "—")} · {String(backtest.trading_style || "agressif")} · départ{" "}
              {money(Number(backtest.initial_balance || 800))} $
              {backtest.closed_roundtrips != null
                ? ` · round-trips ${String(backtest.closed_roundtrips)} (${String(backtest.wins)}W / ${String(backtest.losses)}L)`
                : ""}
              {backtest.start && backtest.end
                ? ` · ${String(backtest.start).slice(0, 10)} → ${String(backtest.end).slice(0, 10)}`
                : ""}
            </p>
          ) : null}
        </section>
      </div>
      ) : null}

      <footer className="mt-6 text-center text-xs text-muted">
        Mode simulation · frais + glissement · arrêt d’urgence · architecture inspirée Hummingbot (pas de copie de code)
      </footer>
    </div>
  );
}

function DecisionJournal({ items }: { items: DecisionLine[] }) {
  return (
    <div className="mt-3 border-t border-line/60 pt-3">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Journal des décisions</p>
      {items.length === 0 ? (
        <p className="text-sm text-muted">En attente d’une décision. Cliquez sur « Décider (IA) » ou démarrez le bot.</p>
      ) : (
        <ul className="max-h-64 space-y-2 overflow-auto text-sm leading-relaxed">
          {items.map((item, index) => {
            const kind = decisionKind(item.action, item.narrative);
            return (
              <li
                key={`${item.ts}-${item.symbol}-${index}`}
                className={`rounded-lg border border-line border-l-2 px-3 py-2 ${decisionBorder(kind)}`}
              >
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <DecisionFlag kind={kind} />
                  <span className="font-medium text-slate-100">{formatDecisionTime(item.ts)}</span>
                  {item.symbol ? <span className="text-xs text-muted">{item.symbol}</span> : null}
                </div>
                <p className="text-slate-300">{item.narrative}</p>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function formatDecisionTime(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

type DecisionKind = "buy" | "sell" | "hold";

function decisionKind(action: string | undefined, narrative?: string): DecisionKind {
  const a = (action || "").toLowerCase();
  if (a === "buy" || a === "sell" || a === "hold") return a;
  const text = (narrative || "").toLowerCase();
  if (text.includes("achat") || text.includes("acheter")) return "buy";
  if (text.includes("vente") || text.includes("vendre")) return "sell";
  return "hold";
}

function decisionBorder(kind: DecisionKind): string {
  if (kind === "buy") return "border-l-gain";
  if (kind === "sell") return "border-l-loss";
  return "border-l-slate-500";
}

function DecisionFlag({ kind }: { kind: DecisionKind }) {
  if (kind === "buy") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gain/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-gain">
        <span className="h-2 w-2 rounded-full bg-gain" aria-hidden />
        Achat
      </span>
    );
  }
  if (kind === "sell") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-loss/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-loss">
        <span className="h-2 w-2 rounded-full bg-loss" aria-hidden />
        Vente
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-500/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-300">
      <span className="h-2 w-2 rounded-full bg-slate-400" aria-hidden />
      Attente
    </span>
  );
}

function Stat({
  label,
  value,
  className = "",
  hint,
}: {
  label: string;
  value: string;
  className?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/50 pb-1" title={hint}>
      <span className="text-xs uppercase tracking-wide text-muted">
        {label}
        {hint ? <span className="ml-1 text-[10px] normal-case tracking-normal text-slate-500">(?)</span> : null}
      </span>
      <span className={`font-medium ${className}`}>{value}</span>
    </div>
  );
}

function Field({
  name,
  label,
  type = "text",
  step,
  defaultValue,
  placeholder,
}: {
  name: string;
  label: string;
  type?: string;
  step?: string;
  defaultValue?: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="label" htmlFor={name}>
        {label}
      </label>
      <input
        className="field"
        id={name}
        name={name}
        type={type}
        step={step}
        defaultValue={defaultValue}
        placeholder={placeholder}
      />
    </div>
  );
}

function paramDisplay(params: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const v = params[key];
    if (v == null) continue;
    if (Array.isArray(v)) return v.map(String).join(", ");
    return String(v);
  }
  return "";
}

function optionalParamString(params: Record<string, unknown>, key: string, fallback: string): string {
  if (!Object.prototype.hasOwnProperty.call(params, key)) return fallback;
  const v = params[key];
  if (v == null || v === "") return fallback;
  return String(v);
}

function chartSymbolOptions(
  params: Record<string, unknown>,
  account: Account | null,
  universe?: UniversePayload | null
): string[] {
  const out: string[] = [];
  const add = (value: unknown) => {
    if (value == null) return;
    const parts = Array.isArray(value) ? value : String(value).split(",");
    for (const part of parts) {
      const sym = String(part).trim().toUpperCase();
      if (sym && !out.includes(sym)) out.push(sym);
    }
  };
  add(universe?.symbols);
  add((universe?.rows || []).map((r) => r.symbol));
  add(params.symbols);
  add(params.watchlist);
  add(params.symbol);
  add(account?.symbols);
  add(account?.symbol);
  for (const pos of account?.positions || []) add(pos.symbol);
  return out.length ? out : ["BTC-USD"];
}

function heldSymbols(account: Account | null): Set<string> {
  return new Set((account?.positions || []).map((p) => p.symbol.toUpperCase()));
}
