/**
 * Run: node apps/web/lib/news.test.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(__dirname, "news.ts");

function loadModule() {
  const require = createRequire(import.meta.url);
  const ts = require("typescript");
  const code = fs.readFileSync(src, "utf8");
  const out = ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const tmp = path.join(os.tmpdir(), `news-${Date.now()}.mjs`);
  fs.writeFileSync(tmp, out);
  return import(pathToFileURL(tmp).href).finally(() => {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* ignore */
    }
  });
}

const { normalizeNewsBrief, isUsableNewsBrief, pickRicherNewsBrief, newsFromStreamPayload } =
  await loadModule();

// /news/brief shape
{
  const brief = normalizeNewsBrief({
    overall_bias: -0.07,
    scores: { crypto: { score: -0.1, count: 2 }, macro: { score: 0, count: 1 } },
    headlines: [{ title: "Bitcoin rises", link: "https://x.test/a", category: "crypto", score: 0.4 }],
  });
  assert.equal(brief.overall_score, -0.07);
  assert.equal(brief.categories.crypto, -0.1);
  assert.equal(brief.headlines.length, 1);
  assert.equal(isUsableNewsBrief(brief), true);
}

// /news items shape must map to headlines
{
  const brief = normalizeNewsBrief({
    as_of: "2026-11-09T00:00:00Z",
    items: [
      { title: "ETF outflow", link: "https://x.test/b", category: "crypto", score: -0.5 },
      { title: "bad", link: 123, category: "crypto" },
    ],
  });
  assert.equal(brief.headlines.length, 1);
  assert.equal(brief.headlines[0].title, "ETF outflow");
  assert.equal(isUsableNewsBrief(brief), true);
}

// Empty payload is not usable
{
  const empty = normalizeNewsBrief({ overall_bias: 0, scores: {}, headlines: [] });
  assert.equal(isUsableNewsBrief(empty), false);
  assert.equal(isUsableNewsBrief(null), false);
}

// Prefer rich brief over empty stream overwrite
{
  const rich = normalizeNewsBrief({
    overall_bias: -0.2,
    headlines: [{ title: "Hack", link: "https://x.test/c", category: "crypto" }],
  });
  const empty = normalizeNewsBrief({ overall_bias: 0, headlines: [] });
  assert.equal(pickRicherNewsBrief(rich, empty), rich);
  assert.equal(pickRicherNewsBrief(null, rich), rich);
}

// Stream wrapper
{
  const fromStream = newsFromStreamPayload({
    type: "decision",
    news: {
      overall_bias: 0.3,
      headlines: [{ title: "Rally", link: "https://x.test/d", category: "macro" }],
    },
  });
  assert.equal(isUsableNewsBrief(fromStream), true);
}

console.log("news.test.mjs: ok");
