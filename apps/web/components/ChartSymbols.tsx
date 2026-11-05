"use client";

type ChartSymbolsProps = {
  symbols: string[];
  value: string;
  onChange: (symbol: string) => void;
  held?: Set<string>;
  disabled?: boolean;
};

/** Dropdown of universe tokens: held (actif) and flat (inactif). */
export function ChartSymbols({ symbols, value, onChange, held, disabled }: ChartSymbolsProps) {
  if (symbols.length === 0) return null;

  const heldSet = held ?? new Set<string>();
  const active = symbols.filter((s) => heldSet.has(s));
  const inactive = symbols.filter((s) => !heldSet.has(s));
  const options = [...active, ...inactive.filter((s) => !active.includes(s))];
  // Ensure current value is selectable even if briefly missing from universe.
  if (value && !options.includes(value)) {
    options.unshift(value);
  }

  return (
    <label className="flex min-w-[11rem] flex-col gap-0.5 text-[10px] text-muted sm:min-w-[14rem]">
      <span className="font-medium uppercase tracking-wide">Token du graphe</span>
      <select
        className="rounded-md border border-line bg-ink/60 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-riskBlue/60 disabled:cursor-not-allowed disabled:opacity-50"
        value={value}
        disabled={disabled}
        aria-label="Sélectionner un token de l'univers"
        onChange={(e) => {
          const next = e.target.value;
          if (next && next !== value) onChange(next);
        }}
      >
        {active.length > 0 ? (
          <optgroup label="Actifs (position ouverte)">
            {active.map((sym) => (
              <option key={`a-${sym}`} value={sym}>
                ● {sym.replace(/-USD$/i, "")} — {sym}
              </option>
            ))}
          </optgroup>
        ) : null}
        {inactive.length > 0 ? (
          <optgroup label="Inactifs (sans position)">
            {inactive.map((sym) => (
              <option key={`i-${sym}`} value={sym}>
                {sym.replace(/-USD$/i, "")} — {sym}
              </option>
            ))}
          </optgroup>
        ) : null}
        {active.length === 0 && inactive.length === 0
          ? options.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))
          : null}
      </select>
    </label>
  );
}
