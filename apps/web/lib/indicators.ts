/** Client-side OHLC indicators (no external deps). */

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = [];
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) {
      out.push(null);
      continue;
    }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    out.push(sum / period);
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period) return out;

  let sum = 0;
  for (let i = 0; i < period; i++) sum += values[i];
  let prev = sum / period;
  out[period - 1] = prev;

  const k = 2 / (period + 1);
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** RSI(14) with Wilder smoothing. */
export function rsi(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length <= period) return out;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) avgGain += diff;
    else avgLoss -= diff;
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

export type MacdSeries = {
  macd: (number | null)[];
  signal: (number | null)[];
  histogram: (number | null)[];
};

export function macd(
  closes: number[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9
): MacdSeries {
  const len = closes.length;
  const macdLine: (number | null)[] = new Array(len).fill(null);
  const signalLine: (number | null)[] = new Array(len).fill(null);
  const histogram: (number | null)[] = new Array(len).fill(null);

  const emaFast = ema(closes, fastPeriod);
  const emaSlow = ema(closes, slowPeriod);

  for (let i = 0; i < len; i++) {
    const f = emaFast[i];
    const s = emaSlow[i];
    if (f != null && s != null) macdLine[i] = f - s;
  }

  const firstMacd = macdLine.findIndex((v) => v != null);
  if (firstMacd === -1) {
    return { macd: macdLine, signal: signalLine, histogram };
  }

  const macdOnly: number[] = [];
  const macdIndex: number[] = [];
  for (let i = firstMacd; i < len; i++) {
    const v = macdLine[i];
    if (v == null) continue;
    macdOnly.push(v);
    macdIndex.push(i);
  }

  const signalEma = ema(macdOnly, signalPeriod);
  for (let j = 0; j < macdIndex.length; j++) {
    const idx = macdIndex[j];
    const sig = signalEma[j];
    if (sig == null) continue;
    signalLine[idx] = sig;
    const m = macdLine[idx];
    if (m != null) histogram[idx] = m - sig;
  }

  return { macd: macdLine, signal: signalLine, histogram };
}

export function lastDefined<T>(series: (T | null | undefined)[]): T | null {
  for (let i = series.length - 1; i >= 0; i--) {
    const v = series[i];
    if (v != null) return v;
  }
  return null;
}
