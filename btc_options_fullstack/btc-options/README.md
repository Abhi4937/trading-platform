# BTC Options Chain — Full-Stack App

Delta Exchange BTC options with real-time Greeks, IV smile, IV vs RV charts.

## Quick Start

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add DELTA_API_KEY and DELTA_API_SECRET
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
# VITE_API_URL=http://localhost:8000/api/v1 (default)
npm run dev
```

App: http://localhost:3000

### 3. Docker (full stack)

```bash
cd docker
cp ../backend/.env.example ../backend/.env
# Edit ../backend/.env with your Delta API keys
docker compose up --build
```

## API Keys

Get Delta Exchange API keys at: https://www.delta.exchange/app/account/api-keys

- **No API key**: app runs in demo mode with synthetic Black-Scholes generated data
- **With API key**: live option chain data from Delta Exchange
- **Testnet**: set `DELTA_BASE_URL=https://testnet-api.delta.exchange` in .env

## Architecture

```
Frontend (React + Recharts)  ←HTTP→  FastAPI Backend  ←HTTPS→  Delta Exchange API
                                           ↕
                                      Redis Cache (15s TTL)
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/v1/expiries | Available BTC option expiry dates |
| GET | /api/v1/spot | Current BTC spot price |
| GET | /api/v1/options?expiry=YYYY-MM-DD | Full option chain with Greeks |
| GET | /api/v1/plot-data/premium | OHLCV candles for a contract |
| GET | /api/v1/plot-data/iv-smile | IV smile across strikes |
| GET | /api/v1/plot-data/iv-rv | Implied vs Realised vol timeseries |
| GET | /docs | Interactive OpenAPI docs |

## Phase Plan

**Phase 1 (MVP — done)**: Single provider, full chain + premium + IV charts  
**Phase 2**: TimescaleDB historical storage, real IV history, Kite/Nifty support  
**Phase 3**: Auth (JWT), multi-user, backtest integration, WebSocket streaming  
