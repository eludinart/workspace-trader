"use client";

import {
  CATEGORY_KEYS,
  NewsBrief,
  categoryLabel,
  formatNewsScore,
  headlineCategoryLabel,
  newsBias,
} from "@/lib/news";
import { useFrenchTexts } from "@/lib/translate";

type NewsPanelProps = {
  brief: NewsBrief | null;
  loading: boolean;
  compact?: boolean;
};

function ScoreBar({ label, score }: { label: string; score: number | undefined }) {
  const v = score ?? 0;
  const normalized = Math.abs(v) <= 1.5 ? (v + 1) / 2 : v / 100;
  const pct = Math.min(100, Math.max(0, normalized * 100));
  const tone =
    v > (Math.abs(v) <= 1.5 ? 0.12 : 55)
      ? "bg-gain"
      : v < (Math.abs(v) <= 1.5 ? -0.12 : 45)
        ? "bg-loss"
        : "bg-slate-500";

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[11px]">
        <span className="text-muted">{label}</span>
        <span className="font-medium tabular-nums">{formatNewsScore(score)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-ink">
        <div className={`h-full rounded-full transition-all ${tone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function NewsPanel({ brief, loading, compact = false }: NewsPanelProps) {
  const bias = newsBias(brief?.overall_score);
  const headlines = (brief?.headlines || []).slice(0, compact ? 3 : 8);
  const sources = headlines.flatMap((h) => (h.summary ? [h.title, h.summary] : [h.title]));
  const french = useFrenchTexts(sources);
  const frenchBySource = new Map<string, string>();
  sources.forEach((source, index) => {
    frenchBySource.set(source, french[index] ?? source);
  });
  const showFrench = sources.some((source) => {
    const value = frenchBySource.get(source);
    return value != null && value !== source;
  });

  if (loading && !brief) {
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-medium">Actualités</h2>
          <span className="rounded-full bg-slate-600 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-300">
            en attente
          </span>
        </div>
        <p className="text-sm text-muted">Chargement des actualités…</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Actualités</h2>
        <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${bias.className}`}>
          {compact ? bias.shortLabel : bias.label}
        </span>
      </div>

      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="text-muted">Biais global</span>
        <span className="font-semibold tabular-nums">{formatNewsScore(brief?.overall_score)}</span>
      </div>

      {!compact && (
        <div className="grid grid-cols-2 gap-3">
          {CATEGORY_KEYS.map((key) => (
            <ScoreBar
              key={key}
              label={categoryLabel(key)}
              score={brief?.categories?.[key]}
            />
          ))}
        </div>
      )}

      {compact && (
        <div className="flex flex-wrap gap-1.5">
          {CATEGORY_KEYS.map((key) => (
            <span
              key={key}
              className="rounded-md border border-line bg-ink/80 px-2 py-0.5 text-[10px] text-muted"
            >
              {categoryLabel(key)}{" "}
              <span className="font-medium text-slate-200">{formatNewsScore(brief?.categories?.[key])}</span>
            </span>
          ))}
        </div>
      )}

      <div>
        <p className="label mb-2 flex items-center gap-2">
          Titres
          {showFrench ? (
            <span className="rounded bg-ink px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-muted">
              FR
            </span>
          ) : null}
        </p>
        {headlines.length === 0 ? (
          <p className="text-sm text-muted">Aucun titre pour le moment</p>
        ) : (
          <ul className={`space-y-2 ${compact ? "text-xs" : "text-sm"}`}>
            {headlines.map((h, i) => (
              <li key={`${h.link}-${i}`} className="border-b border-line/50 pb-2 last:border-0">
                <a
                  href={h.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-slate-100 hover:text-riskBlue line-clamp-2"
                >
                  {frenchBySource.get(h.title) ?? h.title}
                </a>
                {h.summary ? (
                  <p className={`mt-0.5 text-muted ${compact ? "line-clamp-2" : "line-clamp-3"}`}>
                    {frenchBySource.get(h.summary) ?? h.summary}
                  </p>
                ) : null}
                <p className="mt-0.5 text-[11px] text-muted">
                  {headlineCategoryLabel(h.category)}
                  {h.score != null ? ` · ${formatNewsScore(h.score)}` : ""}
                  {h.published ? ` · ${new Date(h.published).toLocaleString("fr-FR")}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>

      {brief?.as_of && (
        <p className="text-[10px] text-muted">
          Maj {new Date(brief.as_of).toLocaleString("fr-FR")}
        </p>
      )}
    </div>
  );
}
