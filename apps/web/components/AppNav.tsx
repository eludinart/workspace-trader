"use client";

export type AppPage = "home" | "activity" | "orders" | "config";

const ITEMS: Array<{ id: AppPage; label: string; hint: string }> = [
  { id: "home", label: "Tableau de bord", hint: "Prix, compte, positions, signal, actu" },
  { id: "activity", label: "Activité", hint: "Fil d’activité en direct" },
  { id: "orders", label: "Exécutions", hint: "Achats et ventes paper" },
  { id: "config", label: "Configuration", hint: "Style, paramètres, alertes, backtest" },
];

type AppNavProps = {
  page: AppPage;
  onChange: (page: AppPage) => void;
};

export function AppNav({ page, onChange }: AppNavProps) {
  return (
    <nav
      className="mb-4 flex flex-wrap gap-1 rounded-xl border border-line bg-panel/80 p-1"
      aria-label="Navigation principale"
    >
      {ITEMS.map((item) => {
        const active = page === item.id;
        return (
          <button
            key={item.id}
            type="button"
            title={item.hint}
            onClick={() => onChange(item.id)}
            className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
              active
                ? "bg-riskBlue/25 text-sky-100"
                : "text-muted hover:bg-ink/50 hover:text-slate-200"
            }`}
          >
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
