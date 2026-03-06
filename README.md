# BTC Options Chain — Full-Stack Trading Platform

A full-stack web application for viewing **real-time BTC options data** from Delta Exchange. It computes Black-Scholes Greeks server-side, visualises the IV smile, IV vs Realised Volatility, and premium OHLCV charts — all in a live, auto-refreshing dashboard.

Built with **FastAPI** (Python) on the backend, **React + TypeScript** (Vite) on the frontend, **Redis** for caching, and fully containerised with **Docker**.

---

## Architecture

### System Overview

![Architecture Diagram](<architecture%20diagram/architecture%20diagram.png>)

### Light Mode

![Architecture Diagram Light](<architecture%20diagram/architecture%20diagram_light.png>)

```
Frontend (React + Recharts)  ←HTTP→  FastAPI Backend  ←HTTPS→  Delta Exchange API
                                           ↕
                                      Redis Cache (15s TTL)
```

---

## Features

- Real-time BTC option chain with live Greeks (Delta, Gamma, Theta, Vega, IV)
- IV Smile chart across strikes for a selected expiry
- IV vs Realised Volatility timeseries chart
- Premium OHLCV candlestick chart per contract
- Demo mode (no API key needed) — synthetic Black-Scholes data
- 15-second Redis cache to avoid rate limits
- Fully Dockerised for one-command deployment

---

## Quick Start

### 1. Backend

```bash
cd btc_options_fullstack/btc-options/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env: add DELTA_API_KEY and DELTA_API_SECRET
uvicorn app.main:app --reload --port 8000
```

- Interactive API docs (Swagger): http://localhost:8000/docs
- Alternative API docs (ReDoc): http://localhost:8000/redoc

### 2. Frontend

```bash
cd btc_options_fullstack/btc-options/frontend
npm install
cp .env.example .env.local
npm run dev
```

App: http://localhost:3000

### 3. Docker (full stack)

```bash
cd btc_options_fullstack/btc-options/docker
cp ../backend/.env.example ../backend/.env
# Edit ../backend/.env with your Delta API keys
docker compose up --build
```

---

## API Keys

Get Delta Exchange API keys at: https://www.delta.exchange/app/account/api-keys

| Mode | Description |
|------|-------------|
| No API key | Demo mode — synthetic Black-Scholes generated data |
| With API key | Live option chain data from Delta Exchange |
| Testnet | Set `DELTA_BASE_URL=https://testnet-api.delta.exchange` in `.env` |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/expiries` | Available BTC option expiry dates |
| GET | `/api/v1/spot` | Current BTC spot price |
| GET | `/api/v1/options?expiry=YYYY-MM-DD` | Full option chain with Greeks |
| GET | `/api/v1/plot-data/premium` | OHLCV candles for a contract |
| GET | `/api/v1/plot-data/iv-smile` | IV smile across strikes |
| GET | `/api/v1/plot-data/iv-rv` | Implied vs Realised vol timeseries |
| GET | `/docs` | Interactive OpenAPI docs (Swagger UI) |
| GET | `/redoc` | Interactive OpenAPI docs (ReDoc) |
| GET | `/health` | Liveness check |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI, Python, Black-Scholes (scipy) |
| Frontend | React, TypeScript, Vite, Recharts |
| Cache | Redis (15s TTL) |
| Infrastructure | Docker, Nginx |
| Data Source | Delta Exchange REST API |

---

## Roadmap

**Phase 1 (MVP — done)**: Single provider, full chain + premium + IV charts
**Phase 2**: TimescaleDB historical storage, real IV history, Kite/Nifty support
**Phase 3**: Auth (JWT), multi-user, backtest integration, WebSocket streaming
