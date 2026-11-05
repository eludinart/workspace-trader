const ACTION_FR: Record<string, string> = {
  buy: "ACHETER",
  sell: "VENDRE",
  hold: "ATTENDRE",
};

const TONE_FR: Record<string, string> = {
  bullish: "haussier",
  bearish: "baissier",
  neutral: "neutre",
};

const NOTE_FR: Record<string, string> = {
  "Take-profit hit": "objectif de gain atteint",
  "Stop-loss hit": "stop-loss atteint",
  "Kill switch flatten": "arrêt d’urgence : positions fermées",
  "Manual close": "fermeture manuelle",
  "Forced exit": "sortie forcée",
  "exit:manual": "sortie : manuelle",
  heuristic_rsi_oversold: "heuristique : RSI en survente",
  heuristic_rsi_overbought: "heuristique : RSI en surachat",
  heuristic_hold: "heuristique : pas de signal",
  "exit:tp": "sortie : objectif de gain",
  "exit:sl": "sortie : stop-loss",
  "exit:kill": "sortie : kill switch",
  "exit:signal": "sortie : signal",
  "entry:signal": "entrée : signal",
};

const NEWS_PROSE =
  "Les scores d’actualité servent seulement au moment de la décision (ils n’entrent pas dans XGBoost) : un biais positif abaisse le seuil d’achat et relève le seuil de vente, jusqu’à 4 points de pourcentage.";

const NEWS_PROSE_EN =
  /news features are decision-time only \(not XGBoost inputs\); a positive bias lowers the buy threshold and raises the sell threshold by up to 4 percentage points/gi;

const NEWS_BIAS =
  /news (bullish|bearish|neutral) bias=([+-]?\d+(?:\.\d+)?) crypto=([+-]?\d+(?:\.\d+)?) macro=([+-]?\d+(?:\.\d+)?) tech=([+-]?\d+(?:\.\d+)?) world=([+-]?\d+(?:\.\d+)?)/gi;

export function actionLabel(action: string | null | undefined): string {
  if (!action) return "—";
  return ACTION_FR[action.toLowerCase()] ?? action.toUpperCase();
}

/** Rewrite the engine's English signal sentence. Unknown text is left as-is. */
export function formatSignalExplanation(raw: string | null | undefined): string {
  if (!raw) return "";
  let text = raw.trim();
  if (!text) return "";

  text = text.replace(NEWS_PROSE_EN, NEWS_PROSE);
  text = text.replace(NEWS_BIAS, (_match, tone: string, bias: string, crypto: string, macro: string, tech: string, world: string) => {
    const label = TONE_FR[tone.toLowerCase()] ?? tone;
    return `actus : biais ${label} ${bias} · crypto ${crypto} · macro ${macro} · tech ${tech} · monde ${world}`;
  });
  text = text.replace(/\bxgb rows=(\d+) acc=([0-9.]+)/gi, (_match, rows: string, acc: string) => {
    const value = Number(acc);
    const pct = Number.isFinite(value) && value <= 1 ? `${Math.round(value * 100)} %` : acc;
    return `XGBoost : ${rows} lignes, précision ${pct}`;
  });
  text = text.replace(
    /news_bias=([+-]?\d+(?:\.\d+)?)\s+buy_th=([0-9.]+)\s+sell_th=([0-9.]+)/gi,
    "biais actus $1 · seuil achat $2 · seuil vente $3",
  );
  text = text.replace(/\(w=([0-9.]+)\)/g, "(poids $1)");
  text = text.replace(
    /^(BUY|SELL|HOLD)\s+conf=(\d+)%\s+\[(xgb|heuristic)\]/i,
    (_match, action: string, conf: string, mode: string) => {
      const word = ACTION_FR[action.toLowerCase()] ?? action;
      const modeFr = mode.toLowerCase() === "xgb" ? "XGBoost" : "heuristique";
      return `${word} · confiance ${conf} % [${modeFr}]`;
    },
  );
  for (const [english, french] of Object.entries(NOTE_FR)) {
    text = text.split(english).join(french);
  }
  return text;
}

export function splitSignalExplanation(raw: string | null | undefined): { lead: string; note: string } {
  const french = formatSignalExplanation(raw);
  const marker = "Les scores d’actualité";
  const index = french.indexOf(marker);
  if (index <= 0) return { lead: french, note: "" };
  const lead = french.slice(0, index).replace(/[\s|—-]+$/u, "");
  const rest = french.slice(index).trim();
  const pipe = rest.indexOf(" | ");
  if (pipe === -1) return { lead, note: rest };
  const note = rest.slice(0, pipe).trim();
  const tail = rest.slice(pipe + 3).trim();
  return { lead: tail ? `${lead} | ${tail}` : lead, note };
}
