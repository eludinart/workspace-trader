import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";

const SERVER_NAME = "trader";
const REQUEST_TIMEOUT_MS = 60_000;

const paramsInput = {
  symbol: z.string().optional(),
  trading_style: z.string().optional(),
  initial_balance: z.number().positive().optional(),
  position_size_pct: z.number().gt(0).lte(1).optional(),
  decision_interval_sec: z.number().int().gte(10).lte(3600).optional(),
  rsi_period: z.number().int().gte(2).lte(100).optional(),
  rsi_oversold: z.number().gte(0).lte(50).optional(),
  rsi_overbought: z.number().gte(50).lte(100).optional(),
  macd_fast: z.number().int().gte(2).optional(),
  macd_slow: z.number().int().gte(3).optional(),
  macd_signal: z.number().int().gte(2).optional(),
  volume_ma_period: z.number().int().gte(2).optional(),
  buy_threshold: z.number().gte(0.2).lte(1).optional(),
  sell_threshold: z.number().gte(0.2).lte(1).optional(),
  candle_granularity_sec: z.number().int().gte(60).optional(),
  candle_lookback: z.number().int().gte(50).lte(1000).optional(),
  auto_trade: z.boolean().optional(),
  fee_bps: z.number().gte(0).optional(),
  slippage_bps: z.number().gte(0).optional(),
  kill_switch_enabled: z.boolean().optional(),
  max_daily_loss_pct: z.number().gte(0.1).lte(100).optional(),
  max_drawdown_pct: z.number().gte(0.1).lte(100).optional(),
  symbols: z.array(z.string()).optional(),
  watchlist: z.array(z.string()).optional(),
  take_profit_pct: z.number().gte(0).lte(1).optional(),
  stop_loss_pct: z.number().gte(0).lte(1).optional(),
  max_position_pct: z.number().gt(0).lte(1).optional(),
  add_cooldown_min: z.number().int().gte(0).lte(1440).optional(),
  max_open_symbols: z.number().int().gte(1).lte(30).optional(),
  capital_per_slot: z.number().gte(20).lte(500).optional(),
  max_open_hard_cap: z.number().int().gte(5).lte(50).optional(),
  max_portfolio_risk_r: z.number().gte(1).lte(30).optional(),
  universe_enabled: z.boolean().optional(),
  universe_size: z.number().int().gte(10).lte(80).optional(),
  universe_min_volume_usd: z.number().gte(0).optional(),
  universe_refresh_hours: z.number().gte(1).lte(48).optional(),
  risk_per_trade_pct: z.number().gt(0).lte(0.05).optional(),
  atr_stop_mult: z.number().gte(1).lte(4).optional(),
  reward_risk: z.number().gte(1).lte(5).optional(),
  min_hold_min: z.number().int().gte(0).lte(1440).optional(),
  aggressive_sell_confidence: z.number().gte(0.5).lte(1).optional(),
};

function errorMessage(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const secret = process.env.TRADER_CONTROL_TOKEN?.trim();
  if (secret && message.includes(secret)) {
    return message.split(secret).join("[redacted]");
  }
  return message;
}

function textResult(payload: unknown, isError = false): CallToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    ...(isError ? { isError: true } : {}),
  };
}

function resolveBaseUrl(): string {
  const raw = process.env.TRADER_BASE_URL?.trim();
  if (!raw) {
    throw new Error("TRADER_BASE_URL is not set");
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("TRADER_BASE_URL is not a valid URL");
  }
  if (parsed.protocol !== "https:") {
    throw new Error("TRADER_BASE_URL must use https");
  }
  if (parsed.username || parsed.password) {
    throw new Error("TRADER_BASE_URL must not embed credentials");
  }
  return raw.replace(/\/+$/, "");
}

function requireToken(): string {
  const token = process.env.TRADER_CONTROL_TOKEN?.trim();
  if (!token) {
    throw new Error("TRADER_CONTROL_TOKEN is not set");
  }
  return token;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { raw: text };
  }
}

type ControlMethod = "GET" | "PUT" | "POST";

async function controlCall(
  method: ControlMethod,
  path: string,
  body?: unknown,
): Promise<CallToolResult> {
  let url: string;
  let bearer: string;
  try {
    url = `${resolveBaseUrl()}${path}`;
    bearer = requireToken();
  } catch (err) {
    return textResult({ error: errorMessage(err) }, true);
  }

  try {
    const response = await fetch(url, {
      method,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${bearer}`,
        "X-Control-Actor": "cursor",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const parsed = await readBody(response);
    if (!response.ok) {
      return textResult({ status: response.status, body: parsed }, true);
    }
    return textResult(parsed);
  } catch (err) {
    return textResult({ error: errorMessage(err) }, true);
  }
}

function registerTools(server: McpServer): void {
  const readOnly = {
    readOnlyHint: true,
    destructiveHint: false,
    openWorldHint: true,
  } as const;
  const mutate = {
    readOnlyHint: false,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: true,
  } as const;

  server.registerTool(
    "get_status",
    {
      title: "Paper status",
      description:
        "GET /control/status. Returns paper mode, lease owner and heartbeat, kill flag, cash, and positions (symbol, qty, avg_entry).",
      annotations: readOnly,
    },
    async () => controlCall("GET", "/control/status"),
  );

  server.registerTool(
    "get_params",
    {
      title: "Bot params",
      description:
        "GET /control/params. Current bot params. Secret-like values are masked by the engine.",
      annotations: readOnly,
    },
    async () => controlCall("GET", "/control/params"),
  );

  server.registerTool(
    "set_params",
    {
      title: "Update bot params",
      description:
        "PUT /control/params. Partial paper-strategy update. Omitted fields stay unchanged. watchlist is an alias for symbols. The engine validates with BotParams (422 on invalid) and audits the diff. Empty arguments still call the API.",
      inputSchema: paramsInput,
      annotations: mutate,
    },
    async (patch) => controlCall("PUT", "/control/params", patch),
  );

  server.registerTool(
    "kill",
    {
      title: "Kill switch",
      description:
        "POST /control/kill. Engages the paper kill switch and sets auto_trade to false. Optional reason is stored on the account. Does not resume trading.",
      inputSchema: {
        reason: z.string().min(1).max(200).optional(),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async ({ reason }) =>
      controlCall(
        "POST",
        "/control/kill",
        reason !== undefined ? { reason } : undefined,
      ),
  );

  server.registerTool(
    "resume",
    {
      title: "Resume from kill",
      description:
        "POST /control/resume. Clears the kill flag. Does not set auto_trade back to true; call set_params if trading should run again.",
      annotations: mutate,
    },
    async () => controlCall("POST", "/control/resume"),
  );

  server.registerTool(
    "tick",
    {
      title: "Forced paper tick",
      description:
        "POST /control/tick. Asks the engine for one paper decision. The API process does not hold the lease, so a 409 refusal is expected until a trader process owns it.",
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async () => controlCall("POST", "/control/tick"),
  );
}

async function main(): Promise<void> {
  const server = new McpServer(
    { name: SERVER_NAME, version: "1.0.0" },
    {
      instructions:
        "Paper control plane for the trader engine. Tools call HTTPS /control/* with the bearer token. They never run SQL or shell, and they never place live orders.",
    },
  );
  registerTools(server);
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err: unknown) => {
  process.stderr.write(`${errorMessage(err)}\n`);
  process.exit(1);
});
