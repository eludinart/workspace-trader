import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const cache = new Map<string, string>();
const MAX_CACHE = 800;
const MAX_TEXTS = 24;
const MAX_CHARS = 450;

function remember(source: string, translated: string) {
  if (cache.has(source)) cache.delete(source);
  cache.set(source, translated);
  while (cache.size > MAX_CACHE) {
    const oldest = cache.keys().next().value;
    if (oldest == null) break;
    cache.delete(oldest);
  }
}

function extractTranslation(payload: unknown): string | null {
  if (!Array.isArray(payload) || !Array.isArray(payload[0])) return null;
  const chunks: string[] = [];
  for (const part of payload[0]) {
    if (Array.isArray(part) && typeof part[0] === "string") chunks.push(part[0]);
  }
  const text = chunks.join("").trim();
  return text || null;
}

async function translateOnce(text: string): Promise<{ text: string; translated: boolean }> {
  const hit = cache.get(text);
  if (hit) return { text: hit, translated: true };
  try {
    const url = new URL("https://translate.googleapis.com/translate_a/single");
    url.searchParams.set("client", "gtx");
    url.searchParams.set("sl", "en");
    url.searchParams.set("tl", "fr");
    url.searchParams.set("dt", "t");
    url.searchParams.set("q", text);
    const res = await fetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
      headers: { "user-agent": "Mozilla/5.0" },
    });
    if (!res.ok) return { text, translated: false };
    const json: unknown = await res.json();
    const fr = extractTranslation(json);
    if (!fr) return { text, translated: false };
    remember(text, fr);
    return { text: fr, translated: true };
  } catch {
    return { text, translated: false };
  }
}

async function translateOne(text: string): Promise<{ text: string; translated: boolean }> {
  const first = await translateOnce(text);
  if (first.translated) return first;
  await new Promise((resolve) => setTimeout(resolve, 300));
  return translateOnce(text);
}

async function mapPool<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next;
      next += 1;
      out[index] = await fn(items[index]);
    }
  }
  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return out;
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ translations: [], translated: [] });
  }
  const raw = body && typeof body === "object" ? (body as { texts?: unknown }).texts : undefined;
  const texts = Array.isArray(raw)
    ? raw
        .filter((item): item is string => typeof item === "string")
        .slice(0, MAX_TEXTS)
        .map((item) => item.slice(0, MAX_CHARS))
    : [];
  if (texts.length === 0) {
    return NextResponse.json({ translations: [], translated: [] });
  }
  const results = await mapPool(texts, 4, translateOne);
  return NextResponse.json({
    translations: results.map((item) => item.text),
    translated: results.map((item) => item.translated),
  });
}
