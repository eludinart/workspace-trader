/**
 * One-off asserts for paper-chart-math (no vitest in this package).
 * Run: node --experimental-strip-types apps/web/lib/paper-chart-math.test.mjs
 * Fallback: transpile via the duplicated checks below after importing .ts is unavailable.
 */

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(__dirname, "paper-chart-math.ts");

// Compile the tiny TS module with tsc if needed, else use npx tsx-less eval via typescript transpile.
function loadModule() {
  const require = createRequire(import.meta.url);
  let ts;
  try {
    ts = require("typescript");
  } catch {
    console.error("typescript package required");
    process.exit(1);
  }
  const code = fs.readFileSync(src, "utf8");
  const out = ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const tmp = path.join(os.tmpdir(), `paper-chart-math-${Date.now()}.mjs`);
  fs.writeFileSync(tmp, out);
  return import(pathToFileURL(tmp).href).finally(() => {
    try {
      fs.unlinkSync(tmp);
    } catch {
      /* ignore */
    }
  });
}

const { domainForRange, splitSignedSegments, pathSkippingInvalid, signedAreaPaths } =
  await loadModule();

const now = Date.parse("2026-11-06T13:29:00Z");
const hour = 3600_000;

// Sparse history must still honor the selected window (time-scale buttons).
{
  const series = [];
  for (let i = 0; i < 30; i += 1) {
    series.push({ t: now - 25 * 60_000 + i * 50_000 });
  }
  const d1h = domainForRange("1h", null, series, now);
  const d6h = domainForRange("6h", null, series, now);
  const d1w = domainForRange("1w", null, series, now);
  assert.equal(d1h.start, now - hour);
  assert.equal(d1h.end, now);
  assert.equal(d6h.start, now - 6 * hour);
  assert.equal(d6h.end, now);
  assert.equal(d1w.start, now - 7 * 24 * hour);
  assert.equal(d1w.end, now);
  assert.ok(d6h.end - d6h.start > d1h.end - d1h.start, "6h domain wider than 1h");
  assert.ok(d1w.end - d1w.start > d6h.end - d6h.start, "1w domain wider than 6h");
}

// Full 6h of data keeps nominal window.
{
  const series = [];
  for (let i = 0; i <= 60; i += 1) {
    series.push({ t: now - 6 * hour + i * 6 * 60_000 });
  }
  const domain = domainForRange("6h", null, series, now);
  assert.equal(domain.start, now - 6 * hour);
  assert.equal(domain.end, now);
}

// Session uses sessionStartedAt even when samples are shorter.
{
  const started = now - 3 * hour;
  const series = [{ t: now - 20 * 60_000 }, { t: now }];
  const domain = domainForRange("session", started, series, now);
  assert.equal(domain.start, started);
  assert.equal(domain.end, now);
}

// Invalid cash samples break the path instead of one continuous spike.
{
  const pts = [
    { x: 0, y: 10 },
    { x: 1, y: 10 },
    { x: 2, y: 500 },
    { x: 3, y: 10 },
  ];
  const d = pathSkippingInvalid(pts, [true, true, false, true]);
  assert.ok(d.includes("M0.0,10.0"));
  assert.ok(!d.includes("500"), "invalid point must be omitted");
  assert.ok(d.includes("M3.0,10.0"), "path resumes after gap");
}

// Zero-crossing splits gain/loss stroke segments.
{
  const points = [
    { x: 0, y: 40 },
    { x: 10, y: 50 },
    { x: 20, y: 60 },
  ];
  // baselineY=50; value = baselineY - y (higher equity → smaller screen y → positive).
  const { positive, negative } = splitSignedSegments(points, 50, (p) => 50 - p.y);
  assert.ok(positive.length >= 1, "expected a positive run");
  assert.ok(negative.length >= 1, "expected a negative run");
}

// Signed fills must not bridge across a loss gap.
{
  const points = [
    { x: 0, y: 40 },
    { x: 10, y: 40 },
    { x: 20, y: 60 },
    { x: 30, y: 60 },
    { x: 40, y: 40 },
  ];
  const values = [110, 110, 90, 90, 110];
  const { gain, loss } = signedAreaPaths(points, values, 50, 100);
  assert.ok(gain.includes("M"), "expected gain fill");
  assert.ok(loss.includes("M"), "expected loss fill");
  // Two separate gain islands → at least two path moves.
  assert.ok((gain.match(/M/g) || []).length >= 2, "gain runs must stay separate");
}

console.log("paper-chart-math.test.mjs: ok");
