"use client";

export type ToastTone = "info" | "ok" | "warn" | "error";

export type ToastItem = {
  id: string;
  message: string;
  tone: ToastTone;
  undoLabel?: string;
  onUndo?: () => void;
};

const toneClass: Record<ToastTone, string> = {
  info: "border-slate-500/40 bg-slate-800 text-slate-100",
  ok: "border-gain/40 bg-gain/15 text-green-100",
  warn: "border-riskOrange/40 bg-orange-950/80 text-orange-100",
  error: "border-riskRed/50 bg-red-950/80 text-red-100",
};

export function PendingBanner({
  label,
  visible,
}: {
  label: string | null;
  visible: boolean;
}) {
  if (!visible || !label) return null;
  return (
    <div
      className="fixed inset-x-0 top-0 z-[60] border-b border-riskBlue/30 bg-ink/95 px-3 py-2 text-center text-xs font-medium text-slate-100 backdrop-blur md:px-6"
      role="status"
      aria-live="polite"
    >
      {label} — un seul clic à la fois, patientez…
    </div>
  );
}

export function ToastStack({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}) {
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[70] flex flex-col items-center gap-2 px-3 md:bottom-6">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex max-w-md items-center gap-3 rounded-xl border px-3 py-2 text-sm shadow-lg ${toneClass[t.tone]}`}
          role="status"
        >
          <span className="flex-1">{t.message}</span>
          {t.onUndo && t.undoLabel ? (
            <button
              type="button"
              className="rounded-md bg-white/10 px-2 py-1 text-xs font-semibold hover:bg-white/20"
              onClick={() => {
                t.onUndo?.();
                onDismiss(t.id);
              }}
            >
              {t.undoLabel}
            </button>
          ) : null}
          <button
            type="button"
            className="rounded-md px-1.5 text-xs opacity-70 hover:opacity-100"
            aria-label="Fermer"
            onClick={() => onDismiss(t.id)}
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

export const ACTION_LABELS: Record<string, string> = {
  tick: "Décision IA",
  loop: "Boucle bot",
  kill: "Arrêt d’urgence",
  resume: "Reprise",
  auto: "Trading auto",
  reset: "Réinitialisation",
  close: "Vente manuelle",
  "save-params": "Enregistrement params",
  "save-alerts": "Enregistrement alertes",
  "save-llm": "Enregistrement co-pilote LLM",
  "refresh-universe": "Rafraîchissement univers",
  backtest: "Backtest",
};
