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

- Wallet paper : cash, positions, PnL, **frais** + **slippage**
- Données **Coinbase** (API publique, lecture seule)
- Stratégie **XGBoost v1** (RSI, MACD, volumes, …) + score de confiance
- **Journal de décision** (features + explication)
- **Backtest** historique Coinbase (PnL, win rate, max drawdown)
- **Kill switch** manuel + auto (perte / drawdown)
- Alertes **Telegram** optionnelles
- Dashboard Tailwind : bleu→orange→rouge (risque), vert/rouge (PnL), **PWA**

## Démarrage Docker (Coolify / local)

```bash
docker compose up --build
```

- Web : http://localhost:3100  
- Engine docs : http://localhost:8100/docs  
- Health : http://localhost:8100/health  

> Ports **3100 / 8100** pour ne pas collisionner avec Korymb (souvent sur 3000).

Variables optionnelles : voir `.env.example` (`TELEGRAM_BOT_TOKEN`, SMTP…).

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
| PUT | `/params` | Paramètres bot / modèle |
| PUT | `/alerts/params` | Alertes |
| POST | `/backtest` | Backtest `{symbol,start,end}` |
| POST | `/bot/tick` | Une décision immédiate |
| POST | `/bot/kill` `/bot/resume` | Kill switch |
| POST | `/account/reset` | Reset paper wallet |

## Sécurité

- Mode **paper** uniquement pour l’instant
- Pas de clés exchange en écriture
- Token Telegram via env / formulaire (masqué à la relecture)

## Suite possible

- Broker live (trade-only API)
- Stratégies Grid/DCA en modules séparés
- Sentiment LLM en feature (pas d’ordres LLM directs)
