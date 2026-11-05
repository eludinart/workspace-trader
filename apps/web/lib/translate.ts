"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "wt-fr-v1";
const MAX_STORED = 400;

const memory = new Map<string, string>();
const inflight = new Map<string, Promise<string[]>>();

function loadStored(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as Record<string, string>;
  } catch {
    return {};
  }
}

function persist(source: string, translated: string) {
  if (typeof window === "undefined") return;
  try {
    const stored = loadStored();
    if (stored[source] === translated) return;
    stored[source] = translated;
    const keys = Object.keys(stored);
    if (keys.length > MAX_STORED) {
      for (const key of keys.slice(0, keys.length - MAX_STORED)) delete stored[key];
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  } catch {
    /* quota or private mode: memory cache still covers this session */
  }
}

function polish(source: string, french: string): string {
  if (!/\bcrypto\b/i.test(source)) return french;
  return french.replace(/cryptograph(?:ie|iques?)/gi, "crypto");
}

function recall(source: string): string | undefined {
  const hit = memory.get(source);
  const stored = hit ?? loadStored()[source];
  if (!stored) return undefined;
  const polished = polish(source, stored);
  if (!hit) memory.set(source, polished);
  return polished;
}

async function translateMissing(texts: string[]): Promise<string[]> {
  const missing: string[] = [];
  for (const text of texts) {
    if (!text || recall(text) || missing.includes(text)) continue;
    missing.push(text);
  }
  let pending = missing;
  for (let attempt = 0; attempt < 2 && pending.length > 0; attempt += 1) {
    try {
      const res = await fetch("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texts: pending }),
      });
      if (!res.ok) continue;
      const data = (await res.json()) as { translations?: unknown; translated?: unknown };
      const translations = Array.isArray(data.translations) ? data.translations : [];
      const flags = Array.isArray(data.translated) ? data.translated : [];
      const still: string[] = [];
      pending.forEach((source, index) => {
        const value = translations[index];
        const ok = flags[index] === true && typeof value === "string" && value.trim().length > 0;
        if (!ok || typeof value !== "string") {
          still.push(source);
          return;
        }
        const polished = polish(source, value);
        memory.set(source, polished);
        persist(source, polished);
      });
      pending = still;
    } catch {
      /* keep the original headline */
    }
  }
  return texts.map((text) => recall(text) ?? text);
}

export async function translateMany(texts: string[]): Promise<string[]> {
  const key = JSON.stringify(texts);
  const pending = inflight.get(key);
  if (pending) return pending;
  const job = translateMissing(texts).finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, job);
  return job;
}

/** Original strings first, then French when the cache or API answers. */
export function useFrenchTexts(texts: string[]): string[] {
  const key = JSON.stringify(texts);
  const [state, setState] = useState<{ key: string; texts: string[] }>({ key: "", texts: [] });

  useEffect(() => {
    const current = JSON.parse(key) as string[];
    let cancelled = false;
    setState({ key, texts: current.map((text) => recall(text) ?? text) });
    if (current.length === 0 || current.every((text) => !text || recall(text))) {
      return () => {
        cancelled = true;
      };
    }
    void translateMany(current).then((french) => {
      if (!cancelled) setState({ key, texts: french });
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  if (state.key !== key) return texts;
  return state.texts;
}
