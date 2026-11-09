export type NewsCategories = {
  crypto?: number;
  macro?: number;
  tech?: number;
  world?: number;
};

export type NewsHeadline = {
  title: string;
  link: string;
  category: string;
  published?: string;
  score?: number;
  summary?: string;
};

export type NewsBrief = {
  as_of?: string;
  overall_score?: number;
  categories?: NewsCategories;
  headlines?: NewsHeadline[];
};

const CATEGORY_KEYS = ["crypto", "macro", "tech", "world"] as const;

function readScore(value: unknown): number | undefined {
  if (typeof value === "number" && !Number.isNaN(value)) return value;
  if (!value || typeof value !== "object") return undefined;
  const score = (value as Record<string, unknown>).score;
  if (typeof score === "number" && !Number.isNaN(score)) return score;
  return undefined;
}

function plainSnippet(raw: string, title: string): string | undefined {
  const text = raw
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
  if (!text || text.toLowerCase() === title.toLowerCase()) return undefined;
  if (text.length <= 220) return text;
  return `${text.slice(0, 217).trimEnd()}…`;
}

function parseHeadlineList(raw: unknown): NewsHeadline[] {
  if (!Array.isArray(raw)) return [];
  const parsed: NewsHeadline[] = [];
  for (const h of raw) {
    if (!h || typeof h !== "object") continue;
    const item = h as Record<string, unknown>;
    if (typeof item.title !== "string" || typeof item.link !== "string") continue;
    if (!item.title.trim() || !item.link.trim()) continue;
    const headline: NewsHeadline = {
      title: item.title,
      link: item.link,
      category: typeof item.category === "string" ? item.category : "general",
    };
    if (typeof item.published === "string") headline.published = item.published;
    if (typeof item.score === "number") headline.score = item.score;
    if (typeof item.summary === "string") {
      const snippet = plainSnippet(item.summary, item.title);
      if (snippet) headline.summary = snippet;
    }
    parsed.push(headline);
  }
  return parsed;
}

export function normalizeNewsBrief(raw: unknown): NewsBrief | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const categoriesRaw = o.categories ?? o.scores;
  let categories: NewsCategories | undefined;
  if (categoriesRaw && typeof categoriesRaw === "object") {
    const c = categoriesRaw as Record<string, unknown>;
    categories = {};
    for (const key of CATEGORY_KEYS) {
      const v = readScore(c[key]);
      if (v != null) categories[key] = v;
    }
  }
  // /news/brief uses `headlines`; /news list uses `items`.
  const headlines = parseHeadlineList(o.headlines ?? o.items);
  const overall =
    readScore(o.overall_score) ??
    readScore(o.overallScore) ??
    readScore(o.overall_bias) ??
    readScore(o.overallBias);
  return {
    as_of: typeof o.as_of === "string" ? o.as_of : typeof o.asOf === "string" ? o.asOf : undefined,
    overall_score: overall,
    categories,
    headlines,
  };
}

/** True when the brief has titles to show (or real category scores). */
export function isUsableNewsBrief(brief: NewsBrief | null | undefined): boolean {
  if (!brief) return false;
  if ((brief.headlines?.length ?? 0) > 0) return true;
  const cats = brief.categories;
  if (!cats) return false;
  return CATEGORY_KEYS.some((key) => {
    const v = cats[key];
    return typeof v === "number" && Number.isFinite(v) && v !== 0;
  });
}

/** Keep a populated brief when a later empty stream frame would wipe the UI. */
export function pickRicherNewsBrief(
  current: NewsBrief | null | undefined,
  incoming: NewsBrief | null | undefined,
): NewsBrief | null {
  const nextOk = isUsableNewsBrief(incoming);
  const curOk = isUsableNewsBrief(current);
  if (nextOk) return incoming ?? null;
  if (curOk) return current ?? null;
  return incoming ?? current ?? null;
}

export function newsFromStreamPayload(data: Record<string, unknown>): NewsBrief | null {
  if (data.news_brief != null) return normalizeNewsBrief(data.news_brief);
  if (data.news != null) return normalizeNewsBrief(data.news);
  if (
    data.overall_score != null ||
    data.overall_bias != null ||
    data.categories != null ||
    data.scores != null ||
    data.headlines != null ||
    data.items != null
  ) {
    return normalizeNewsBrief(data);
  }
  return null;
}

/** Map score to bullish / bearish / neutral chip styling. */
export function newsBias(score: number | undefined | null): {
  label: string;
  shortLabel: string;
  className: string;
} {
  if (score == null || Number.isNaN(score)) {
    return {
      label: "ACTU —",
      shortLabel: "ACTU",
      className: "bg-slate-600 text-slate-200",
    };
  }
  const normalized = Math.abs(score) <= 1.5 ? score : (score - 50) / 50;
  if (normalized > 0.12) {
    return { label: "ACTU HAUSSIER", shortLabel: "HAUSSE", className: "bg-gain text-white" };
  }
  if (normalized < -0.12) {
    return { label: "ACTU BAISSIER", shortLabel: "BAISSE", className: "bg-loss text-white" };
  }
  return { label: "ACTU NEUTRE", shortLabel: "NEUTRE", className: "bg-slate-500 text-slate-100" };
}

export function formatNewsScore(score: number | undefined): string {
  if (score == null || Number.isNaN(score)) return "—";
  if (Math.abs(score) <= 1.5) return (score >= 0 ? "+" : "") + score.toFixed(2);
  return score.toFixed(0);
}

export function categoryLabel(key: (typeof CATEGORY_KEYS)[number]): string {
  switch (key) {
    case "crypto":
      return "Crypto";
    case "macro":
      return "Macroéconomie";
    case "tech":
      return "Technologie";
    case "world":
      return "Monde";
    default: {
      const _exhaustive: never = key;
      return _exhaustive;
    }
  }
}

export function headlineCategoryLabel(category: string): string {
  switch (category) {
    case "crypto":
      return "Crypto";
    case "macro":
      return "Macroéconomie";
    case "tech":
      return "Technologie";
    case "world":
      return "Monde";
    case "general":
      return "Général";
    default:
      return category;
  }
}

export { CATEGORY_KEYS };
