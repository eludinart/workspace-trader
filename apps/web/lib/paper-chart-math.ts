/** Pure helpers for PaperSessionChart (domain fit + signed curve segments). */

export type PaperTimeRange =
  | "1h"
  | "6h"
  | "24h"
  | "1d"
  | "1w"
  | "1mo"
  | "1y"
  | "session"
  | "all";

export type TimedPoint = { t: number };

export function rangeDeltaMs(range: PaperTimeRange): number | null {
  if (range === "1h") return 3600_000;
  if (range === "6h") return 6 * 3600_000;
  if (range === "24h" || range === "1d") return 24 * 3600_000;
  if (range === "1w") return 7 * 24 * 3600_000;
  if (range === "1mo") return 30 * 24 * 3600_000;
  if (range === "1y") return 365 * 24 * 3600_000;
  return null;
}

/**
 * X domain for the selected range.
 * When stored samples only cover a short recent slice of a long window (e.g. 20 min
 * of data on a 6h range), snap start forward so the series fills the plot instead
 * of sitting compressed on the right edge.
 */
export function domainForRange(
  range: PaperTimeRange,
  sessionStartedAt: number | null,
  series: TimedPoint[],
  nowMs: number = Date.now(),
): { start: number; end: number } {
  const end = nowMs;
  const delta = rangeDeltaMs(range);
  let start: number;
  if (delta != null) {
    start = end - delta;
  } else if (range === "session") {
    start = sessionStartedAt ?? series[0]?.t ?? end - 3600_000;
    start = Math.min(start, end - 60_000);
  } else if (series.length) {
    start = Math.min(series[0].t, end - 60_000);
  } else {
    start = end - 3600_000;
  }

  if (!series.length) return { start, end };

  const dataStart = series[0].t;
  const dataEnd = series[series.length - 1].t;
  const windowSpan = Math.max(1, end - start);
  const dataSpan = Math.max(0, dataEnd - dataStart);

  // Single point: keep a readable local window around it.
  if (series.length === 1) {
    const half = Math.min(delta ?? 3600_000, 10 * 60_000) / 2;
    return {
      start: Math.max(start, dataStart - half),
      end: Math.max(end, dataStart + Math.max(30_000, half * 0.25)),
    };
  }

  // Sparse relative to the selected window → zoom to data (keep live end).
  const fillsWindow = dataSpan >= windowSpan * 0.45 && dataStart <= start + windowSpan * 0.12;
  if (!fillsWindow) {
    const pad = Math.max(20_000, dataSpan * 0.06);
    const fittedStart = dataStart - pad;
    // Never expand past the nominal range start (don't invent older empty time).
    return { start: Math.max(start, fittedStart), end };
  }

  return { start, end };
}

export type XY = { x: number; y: number };

/** Split a polyline into runs on either side of zero (yValue > 0 = gain). */
export function splitSignedSegments(
  points: XY[],
  baselineY: number,
  yValue: (p: XY) => number,
): { positive: XY[][]; negative: XY[][] } {
  const positive: XY[][] = [];
  const negative: XY[][] = [];
  if (points.length < 2) return { positive, negative };

  let current: XY[] = [points[0]];
  let sign = Math.sign(yValue(points[0]));
  if (sign === 0) sign = 1;

  const flush = () => {
    if (current.length < 2) {
      current = [];
      return;
    }
    if (sign >= 0) positive.push(current);
    else negative.push(current);
    current = [];
  };

  for (let i = 1; i < points.length; i += 1) {
    const prev = points[i - 1];
    const next = points[i];
    const prevV = yValue(prev);
    const nextV = yValue(next);
    let nextSign = Math.sign(nextV);
    if (nextSign === 0) nextSign = sign;

    // Crossing through zero (including exact-zero vertices).
    if (sign !== nextSign) {
      let cross: XY;
      if (prevV === 0) {
        cross = { x: prev.x, y: baselineY };
      } else if (nextV === 0) {
        cross = { x: next.x, y: baselineY };
      } else {
        const t = prevV / (prevV - nextV);
        cross = {
          x: prev.x + (next.x - prev.x) * t,
          y: baselineY,
        };
      }
      if (current[current.length - 1] !== cross) current.push(cross);
      flush();
      current = [cross];
      if (nextV !== 0) current.push(next);
      sign = nextSign;
    } else {
      current.push(next);
    }
  }
  flush();
  return { positive, negative };
}

/** Build open SVG path from points; omit non-finite / invalid coords. */
export function linePath(points: XY[]): string {
  const clean = points.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  if (!clean.length) return "";
  return clean
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(" ");
}

export function areaPath(points: XY[], baselineY: number): string {
  if (!points.length) return "";
  const line = linePath(points);
  if (!line) return "";
  const first = points[0];
  const last = points[points.length - 1];
  return `${line} L${last.x.toFixed(1)},${baselineY.toFixed(1)} L${first.x.toFixed(1)},${baselineY.toFixed(1)} Z`;
}

/**
 * Area fill on each side of break-even: gain where value >= breakEven, else loss.
 * Builds separate closed runs so discontinuous segments never reconnect across the other side.
 */
export function signedAreaPaths(
  points: XY[],
  values: number[],
  breakEvenY: number,
  breakEvenValue: number,
): { gain: string; loss: string } {
  if (points.length < 2 || points.length !== values.length) {
    return { gain: "", loss: "" };
  }

  const gainRuns: XY[][] = [];
  const lossRuns: XY[][] = [];
  let gainRun: XY[] = [];
  let lossRun: XY[] = [];

  const flushGain = () => {
    if (gainRun.length >= 2) gainRuns.push(gainRun);
    gainRun = [];
  };
  const flushLoss = () => {
    if (lossRun.length >= 2) lossRuns.push(lossRun);
    lossRun = [];
  };
  const append = (run: XY[], a: XY, b: XY) => {
    if (!run.length) run.push(a);
    else if (run[run.length - 1].x !== a.x || run[run.length - 1].y !== a.y) run.push(a);
    run.push(b);
  };

  for (let i = 1; i < points.length; i += 1) {
    const v0 = values[i - 1] - breakEvenValue;
    const v1 = values[i] - breakEvenValue;
    const p0 = points[i - 1];
    const p1 = points[i];

    if (v0 >= 0 && v1 >= 0) {
      flushLoss();
      append(gainRun, p0, p1);
    } else if (v0 <= 0 && v1 <= 0) {
      flushGain();
      append(lossRun, p0, p1);
    } else {
      const t = v0 / (v0 - v1);
      const cross: XY = {
        x: p0.x + (p1.x - p0.x) * t,
        y: breakEvenY,
      };
      if (v0 > 0) {
        append(gainRun, p0, cross);
        flushGain();
        append(lossRun, cross, p1);
      } else {
        append(lossRun, p0, cross);
        flushLoss();
        append(gainRun, cross, p1);
      }
    }
  }
  flushGain();
  flushLoss();

  return {
    gain: gainRuns.map((r) => areaPath(r, breakEvenY)).filter(Boolean).join(" "),
    loss: lossRuns.map((r) => areaPath(r, breakEvenY)).filter(Boolean).join(" "),
  };
}

/** Drop cash<=0 samples from a continuous path (avoids spikes off-chart). */
export function pathSkippingInvalid(
  points: XY[],
  valid: boolean[],
): string {
  if (points.length !== valid.length) return linePath(points);
  const parts: string[] = [];
  let chunk: XY[] = [];
  const flush = () => {
    if (chunk.length) {
      const d = linePath(chunk);
      if (d) parts.push(d);
      chunk = [];
    }
  };
  for (let i = 0; i < points.length; i += 1) {
    if (!valid[i]) {
      flush();
      continue;
    }
    chunk.push(points[i]);
  }
  flush();
  return parts.join(" ");
}
