# workspace-trader

Bot de **paper trading crypto IA** (simulation).  
Monorepo prêt pour **Coolify** via Docker Compose.

Architecture inspirée de frameworks modulaires type Hummingbot (connectors / strategies / risk) — **sans copie de code**.

## Architecture

```
┌──────────────────────────┐       HTTP        ┌──────────────────────────┐
│  apps/web (Next.js)      │ ────────────────► │  apps/engine (FastAPI)   │
│  Dashboard + API proxy   │                   │  Wallet / Market / IA    │
│  Orchestre               │                   │  Calcule & exécute paper │
└──────────────────────────┘                   └──────────────────────────┘
```

| Service | Rôle |
|---------|------|
| **web** | UI responsive (mobile essentiel / desktop enrichi), PWA, proxy `/api/engine/*` |
| **engine** | Wallet virtuel, Coinbase public, XGBoost, journal, backtest, kill switch, alertes |

## Fonctionnalités

- Wallet paper : cash, positions, PnL — **jumeau d’exécution L2** (carnet Coinbase réel + frais taker tier, défaut US 90 bps) ; legacy slip si `execution_twin_enabled=false`
- Historic paper : P&L réalisé net par vente / montant investi à l’achat (pas de P&L inventé sur données incomplètes)
- Données **Coinbase** (API publique, lecture seule)
- Stratégie **XGBoost v1** (RSI, MACD, volumes, …) + score de confiance
- **Journal de décision** (features + explication)
- **Backtest** historique Coinbase (PnL, win rate, max drawdown)
- **Kill switch** manuel + auto (perte / drawdown)
- Alertes **Web Push** PWA (VAPID env) — Telegram présent mais non recommandé
- Signaux 14–21 derrière flags **off** par défaut ; confluence/MTF branchés (shadow si OOS refuse) ; Kelly **éval-only** ; risque dur **2%/trade** (`docs/SIGNAL_LOTS_STATE.md`)
- Web Push VAPID (pas Telegram) + veille passive déterministe
- Carnet papier profondeur/latence optionnel (`order_book_depth_*`)
- Dashboard Tailwind : bleu→orange→rouge (risque), vert/rouge (PnL), **PWA** mobile enrichi

## Démarrage Docker (Coolify / local)

```bash
docker compose up --build
```

- Web : http://localhost:3100  
- Engine docs : http://localhost:8100/docs  
- Health : http://localhost:8100/health  

> Ports **3100 / 8100** pour ne pas collisionner avec Korymb (souvent sur 3000).

Variables optionnelles : voir `.env.example` (SMTP, VAPID, flags `SIGNAL_*` tous `false`).

## Dev local

### Engine

```bash
cd apps/engine
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Web

```bash
cd apps/web
npm install
# ENGINE_URL pointe vers le moteur local
set ENGINE_URL=http://127.0.0.1:8000
npm run dev
```

## API moteur (extraits)

| Méthode | Path | Description |
|---------|------|-------------|
| GET | `/account` | Solde, positions, PnL, risk_score |
| GET | `/market/candles` | Historique OHLCV |
| GET | `/signals` | Dernier signal IA |
| GET | `/journal` | Journal de décision |
| GET | `/trades` | Fills annotés (P&L / investi) |
| GET | `/signals/flags` `/signals/stubs` `/signals/oos` | Flags + stubs 14–21 + harness OOS |
| GET | `/fidelity` | Gate jumeau L2 (go_live_ready, friction, fills) |
| PUT | `/params` | Paramètres bot / modèle |
| PUT | `/alerts/params` | Alertes |
| POST | `/backtest` | Backtest `{symbol,start,end}` |
| POST | `/bot/tick` | Une décision immédiate |
| POST | `/bot/kill` `/bot/resume` | Kill switch |
| POST | `/account/reset` | Reset paper wallet |

## Sécurité

- Mode **paper** uniquement pour l’instant
- Pas de clés exchange en écriture
- VAPID : clé **publique** côté client, clé **privée** uniquement en secret env (jamais dans le repo)
- Flags signaux / Web Push / post-trade : **off** jusqu’à OK explicite

## Suite possible

- Broker live (trade-only API)
- Stratégies Grid/DCA en modules séparés
- Sentiment LLM en feature (pas d’ordres LLM directs)
