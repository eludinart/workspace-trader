# État lots signaux / risque (workspace-trader)

Dernière livraison : branche `feat/lots-8-signals-risk-depth` (après merge PR #1).

## Hard constraints

| Règle | État |
|-------|------|
| Stop / risque max **2% capital / trade** | Hard-codé `HARD_RISK_PER_TRADE_PCT=0.02` ; `BotParams.risk_per_trade_pct` plafonné ; clamp runtime |
| Pas Telegram | Web Push VAPID only |
| Pas de secrets dans le repo | VAPID private / tokens Coolify env only |

## Flags (défaut OFF)

| Flag / env | Comportement prod recommandé |
|------------|------------------------------|
| `SIGNAL_CONFLUENCE_ENABLED` | ON → calcul réel + **shadow** tant que OOS refuse ; enforce seulement si `/signals/oos` `activation_allowed` |
| `SIGNAL_MTF_ENABLED` | Idem (Daily/4h/15m dérivés des candles) |
| `SIGNAL_KELLY_ENABLED` | **Éval only** (`allow_sizing=False` en runtime) — affiche fraction / raison échantillon ; ne change pas les tailles |
| `SIGNAL_ONCHAIN_ENABLED` | Stubs labellisés `hypothesis` + sources documentées (`providers`) |
| `SIGNAL_SUPER_OPP_ENABLED` | Stub basis ; size_mult sous plafond 2% |
| `SIGNAL_PASSIVE_WAKE_ENABLED` | Veille déterministe ; journal `passive_wake` si seuil |
| `SIGNAL_POST_TRADE_AI_ENABLED` | Commentaire journal après vente ; **jamais** P&L/positions |
| `WEB_PUSH_ENABLED` | Opt-in UI + VAPID env ; clé privée hors git |

## Kelly sizing

- Actif en **évaluation** si `SIGNAL_KELLY_ENABLED=true` (ou `kelly_enabled` params).
- **Sizing réel** refusé tant que : échantillon &lt; 200 **ou** OOS non OK **ou** `allow_sizing=False` (défaut prod).
- Même si un jour déverrouillé : mult advisory × risk, puis clamp ≤ 2%.

## Coolify

- App UUID : `onx2blngujkokmltpugysuh4`
- Smoke : https://trader.eludein.art/api/engine/health
- Migration `005_trade_fill_meta_signals.sql` au démarrage engine (`run_migrations`)
- Vars à poser pour Kelly éval : `SIGNAL_KELLY_ENABLED=true` (pas de secret)
- VAPID : `NEXT_PUBLIC_VAPID_PUBLIC_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`

## Endpoints utiles

- `GET /signals/flags`
- `GET /signals/oos`
- `GET /signals/stubs` (flags, OOS, Kelly, providers, hard risk, order book)
