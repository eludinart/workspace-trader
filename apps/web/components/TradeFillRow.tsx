"use client";

import { money } from "@/lib/engine";
import { actionLabel } from "@/lib/signal-fr";
import {
  AnnotatedTrade,
  formatPct,
  formatSignedMoney,
  tradeToneClass,
} from "@/lib/trade-display";

type TradeFillRowProps = {
  trade: AnnotatedTrade;
  hot?: boolean;
  dense?: boolean;
};

export function TradeFillRow({ trade: t, hot = false, dense = false }: TradeFillRowProps) {
  const side = String(t.side || "").toLowerCase();
  const sym = String(t.symbol || "—");
  const ts = t.ts ? new Date(String(t.ts)).toLocaleString("fr-FR") : "—";
  const kind = String(t.display_kind || "");
  const tone = String(t.tone || "muted");
  const amount = typeof t.display_amount === "number" ? t.display_amount : null;
  const pct = formatPct(typeof t.display_pct === "number" ? t.display_pct : null);
  const badge = t.badge_label ? String(t.badge_label) : null;

  return (
    <li
      className={`rounded-md border px-2 py-1.5 transition-colors ${
        hot ? "border-riskBlue/70 bg-riskBlue/15" : "border-line/50 bg-ink/30"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span
          className={`font-semibold ${
            side === "buy" ? "text-gain" : side === "sell" ? "text-loss" : ""
          }`}
        >
          {actionLabel(side)} {sym.replace(/-USD$/i, "")}
        </span>
        <span className="text-[10px] text-muted">{dense ? ts.split(" ").pop() : ts}</span>
      </div>
      <div className="mt-0.5 flex flex-wrap items-center justify-between gap-1 text-xs text-slate-300">
        <span>
          qté {Number(t.qty || 0).toFixed(5)} @ {money(Number(t.price || 0))}
          {typeof t.fee === "number" && t.fee > 0 ? (
            <span className="text-muted"> · frais {money(t.fee)}</span>
          ) : null}
        </span>
        {badge ? (
          <span
            className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
              tone === "gain"
                ? "border-gain/40 bg-gain/10 text-gain"
                : tone === "loss"
                  ? "border-loss/40 bg-loss/10 text-loss"
                  : "border-line/60 bg-ink/40 text-muted"
            }`}
          >
            {badge}
          </span>
        ) : null}
      </div>
      <div className={`mt-1 text-xs ${tradeToneClass(tone)}`}>
        {kind === "incomplete" || amount === null ? (
          <span>{String(t.amount_label || "Non calculable — données incomplètes")}</span>
        ) : kind === "invested" ? (
          <span>
            {String(t.amount_label || "Montant investi")} : {money(amount)}
          </span>
        ) : (
          <span>
            {String(t.amount_label || "P&L réalisé net")} : {formatSignedMoney(amount)}
            {pct ? ` (${pct})` : ""}
          </span>
        )}
      </div>
      {kind === "realized" && t.reference_price != null ? (
        <p className="mt-0.5 text-[10px] text-muted">
          {String(t.reference_label || "Réf.")} {money(Number(t.reference_price))}
          {t.reference_nature ? ` · ${String(t.reference_nature)}` : ""}
          {t.reference_ts
            ? ` · ${new Date(String(t.reference_ts)).toLocaleString("fr-FR")}`
            : ""}
        </p>
      ) : null}
      {typeof t.latency_ms === "number" && t.latency_ms > 0 ? (
        <p className="mt-0.5 text-[10px] text-muted">
          latence {t.latency_ms} ms
          {t.decision_price != null ? ` · décision ${money(Number(t.decision_price))}` : ""}
        </p>
      ) : null}
      {!dense && t.note ? (
        <p className="mt-0.5 text-[10px] text-muted">{String(t.note)}</p>
      ) : null}
    </li>
  );
}
