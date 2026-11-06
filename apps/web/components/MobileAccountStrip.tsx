"use client";

import { Account, money, pnlClass } from "@/lib/engine";

type MobileAccountStripProps = {
  account: Account | null;
};

/** Compact mobile-only strip: exposition / réalisé / non réalisé / soldes. */
export function MobileAccountStrip({ account }: MobileAccountStripProps) {
  const exposure = account?.exposure_quote ?? account?.positions?.reduce(
    (s, p) => s + (p.market_value || 0),
    0,
  ) ?? 0;
  const realized = account?.realized_pnl ?? 0;
  const unrealized = account?.unrealized_pnl ?? 0;
  const cash = account?.cash ?? 0;
  const equity = account?.equity ?? 0;
  const markTs = account?.marks_as_of;
  const openN = account?.open_count ?? account?.positions?.length ?? 0;

  return (
    <section className="card col-span-2 space-y-2 md:hidden">
      <p className="label">Compte paper (mobile)</p>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted">Exposition</p>
          <p className="font-medium">{money(exposure)}</p>
          <p className="text-[10px] text-muted">
            {openN} pos. · quote
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted">Soldes</p>
          <p className="font-medium">{money(cash)} cash</p>
          <p className="text-[10px] text-muted">equity {money(equity)}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted">P&L réalisé</p>
          <p className={`font-medium ${pnlClass(realized)}`}>
            {realized > 0 ? "+" : ""}
            {money(realized)}
          </p>
          <p className="text-[10px] text-muted">clôturé</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted">P&L non réalisé</p>
          <p className={`font-medium ${pnlClass(unrealized)}`}>
            {unrealized > 0 ? "+" : ""}
            {money(unrealized)}
          </p>
          <p className="text-[10px] text-muted">
            mark
            {markTs ? ` · ${new Date(markTs).toLocaleTimeString("fr-FR")}` : ""}
          </p>
        </div>
      </div>
    </section>
  );
}
