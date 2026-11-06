-- Backfill buy fill meta only (deterministic from qty/price/fee).
-- Do NOT invent sell PnL / avg_entry_ref / cost_basis_closed.

UPDATE trades
SET invested_quote = qty * price + fee,
    meta_complete = true
WHERE lower(side) = 'buy'
  AND invested_quote IS NULL;
