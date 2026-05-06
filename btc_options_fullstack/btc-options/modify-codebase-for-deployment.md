# Claude Code Prompt: Modify Codebase for Oracle Cloud Deployment

## Context

I have an existing BTC options historical backtesting platform with:
- **Frontend:** React + Vite + TypeScript
- **Backend:** Python FastAPI serving data from Parquet files via DuckDB
- **Data:** 18GB of pre-computed Parquet files (option chain snapshots with mark price, IV, Greeks per strike per timestamp)

Currently everything runs locally on my Windows/WSL machine. I'm deploying to an **Oracle Cloud ARM instance** (Ubuntu 22.04, 4 cores, 24GB RAM, 200GB disk) where Nginx will serve the frontend and proxy API calls to FastAPI.

**Read my entire codebase first** before making any changes. Understand the project structure, how the frontend calls the backend, where file paths are referenced, and how data flows.

## Changes Needed

### 1. Frontend API URLs — Remove localhost

Search the entire frontend codebase for any hardcoded backend URLs and replace them with relative paths.

**Find patterns like:**
```javascript
// Any of these patterns:
fetch('http://localhost:8000/...')
fetch('http://127.0.0.1:8000/...')
fetch(`http://localhost:8000/...`)
axios.get('http://localhost:8000/...')
axios.post('http://localhost:8000/...')
const API_URL = 'http://localhost:8000'
const BASE_URL = 'http://localhost:8000'
baseURL: 'http://localhost:8000'
```

**Replace with relative paths:**
```javascript
// All API calls should use relative /api/ prefix:
fetch('/api/...')
axios.get('/api/...')
const API_URL = '/api'
const BASE_URL = '/api'
baseURL: '/api'
```

**Important:** If the backend routes don't have an `/api` prefix, don't add it to the fetch calls yet — just remove the `http://localhost:8000` part. We'll handle the prefix mapping in Nginx config.

For example, if the frontend currently calls `http://localhost:8000/chain-snapshot` and the FastAPI route is `@app.get("/chain-snapshot")`, then change the frontend to call `/api/chain-snapshot`. Nginx will strip `/api` and proxy to FastAPI at `/chain-snapshot`.

BUT — if the FastAPI routes already include `/api` (like `@app.get("/api/chain-snapshot")`), then just change to `/api/chain-snapshot` without Nginx stripping anything. Read the actual route definitions to determine which case applies.

### 2. Vite Proxy Config — For Local Development

Update `vite.config.ts` (or `vite.config.js`) so local development still works after the URL changes:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // If FastAPI routes DON'T have /api prefix, uncomment this:
        // rewrite: (path) => path.replace(/^\/api/, ''),
      }
    }
  }
})
```

**Read the existing vite.config file first** — it may already have plugins, settings, or proxy config. Don't overwrite existing config, just add/modify the proxy section.

### 3. Backend File Paths — Use Environment Variable

Search the entire backend codebase for hardcoded file paths to Parquet data.

**Find patterns like:**
```python
# Windows paths:
"C:/Users/..."
"C:\\Users\\..."

# WSL paths:
"/mnt/c/Users/..."
"/mnt/d/..."

# Relative paths that assume a specific working directory:
"./data/..."
"../data/..."
"data/..."

# Any path containing the data directory:
read_parquet('some/specific/path/...')
```

**Replace with a configurable DATA_DIR:**

Create or update a config file:
```python
# config.py (create if doesn't exist, or add to existing config)
import os

# Data directory - configurable via environment variable
# Default: /opt/trading/data (production on Oracle Cloud)
# Override: set DATA_DIR env var for local development
DATA_DIR = os.environ.get('DATA_DIR', '/opt/trading/data')

# Sub-directories
CHAIN_SNAPSHOTS_DIR = os.path.join(DATA_DIR, 'chain_snapshots')
TICKER_SPREADS_DIR = os.path.join(DATA_DIR, 'ticker_spreads')
L2_DEPTH_DIR = os.path.join(DATA_DIR, 'l2_depth')
MODELS_DIR = os.path.join(DATA_DIR, 'models')
```

Then update all Parquet query paths:
```python
# BEFORE:
duckdb.sql("SELECT * FROM read_parquet('C:/Users/Abhishek/Trading/data/chain_snapshots/*.parquet')")

# AFTER:
from config import CHAIN_SNAPSHOTS_DIR
duckdb.sql(f"SELECT * FROM read_parquet('{CHAIN_SNAPSHOTS_DIR}/*.parquet')")
```

**Search thoroughly** — check every .py file for path strings. Common places:
- Main app file (main.py / app.py)
- Route handlers
- Data loading utilities
- Any helper/utility modules

### 4. CORS — Simplify for Production

In production, Nginx serves both frontend and API from the same origin (same IP, same port 80). So CORS is not needed. But keep it configurable so local development still works.

**Find the CORS config** (usually in main.py or app.py):
```python
# Something like:
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", ...],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Replace with environment-aware config:**
```python
import os
from fastapi.middleware.cors import CORSMiddleware

# CORS only needed in development (Vite runs on different port)
# In production, Nginx serves everything from same origin
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'production')

if ENVIRONMENT == 'development':
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

### 5. Backend Host Binding

Check how the FastAPI server is started. It might be bound to localhost only:

```python
# BEFORE (only accessible from same machine):
uvicorn.run(app, host="127.0.0.1", port=8000)

# AFTER (accessible from Nginx proxy):
# Keep 127.0.0.1 — Nginx proxies locally, no need to expose to internet
# This is actually correct for production. Don't change to 0.0.0.0
```

If there's a `uvicorn` command in a script or Dockerfile, make sure it uses `127.0.0.1` (not `0.0.0.0`) since Nginx handles external traffic.

### 6. Create .env.example

Create a `.env.example` file in the project root showing all configurable variables:

```bash
# .env.example — copy to .env and fill in values

# Environment: 'development' or 'production'
ENVIRONMENT=development

# Path to Parquet data directory
# Local (Windows): C:/Users/YourName/Trading/data
# Local (WSL): /mnt/c/Users/YourName/Trading/data
# Production (Oracle Cloud): /opt/trading/data
DATA_DIR=/opt/trading/data

# Backend server
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000

# Delta Exchange API (for data collectors — not needed for backtester)
# DELTA_API_KEY=your_key_here
# DELTA_API_SECRET=your_secret_here
```

### 7. Create Nginx Config

Create `deployment/nginx.conf` in the project:

```nginx
server {
    listen 80;
    server_name _;

    # Frontend — serve React build output
    root /opt/trading/frontend/dist;
    index index.html;

    # SPA fallback — all non-file routes serve index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API proxy — forward to FastAPI backend
    # READ THE ACTUAL FASTAPI ROUTES to determine if rewrite is needed
    location /api/ {
        # If FastAPI routes DON'T have /api prefix (e.g., @app.get("/chain-snapshot")):
        rewrite ^/api/(.*) /$1 break;
        
        # If FastAPI routes DO have /api prefix (e.g., @app.get("/api/chain-snapshot")):
        # Comment out the rewrite line above and use proxy_pass directly
        
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Increase timeouts for large DuckDB queries
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

**IMPORTANT:** Read the FastAPI route definitions to determine whether the `rewrite` line is needed. If routes already have `/api` prefix, remove the rewrite.

### 8. Create systemd Service Files

Create `deployment/trading-backend.service`:
```ini
[Unit]
Description=Trading Platform FastAPI Backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/trading/backend
Environment="PATH=/opt/trading/backend/venv/bin"
Environment="DATA_DIR=/opt/trading/data"
Environment="ENVIRONMENT=production"
ExecStart=/opt/trading/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**NOTE:** The `main:app` in ExecStart assumes your FastAPI app is in `main.py` with the app instance called `app`. Read the actual backend entry point and adjust accordingly (could be `app:app`, `server:app`, etc.).

Create `deployment/trading-collector.service`:
```ini
[Unit]
Description=Trading Data Collectors
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/trading/collectors
Environment="PATH=/opt/trading/collectors/venv/bin"
Environment="DATA_DIR=/opt/trading/data"
ExecStart=/opt/trading/collectors/venv/bin/python run_collector.py --mode continuous --data-dir /opt/trading/data
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 9. Create Deploy Script

Create `deployment/deploy.sh`:
```bash
#!/bin/bash
set -e

echo "=== Trading Platform Deploy Script ==="
echo "Target: Oracle Cloud ARM (Ubuntu 22.04)"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# --- System Setup (run once) ---
setup_system() {
    echo -e "${GREEN}[1/6] System packages...${NC}"
    sudo apt update && sudo apt upgrade -y
    sudo apt install -y python3 python3-pip python3-venv nginx git tmux htop curl wget

    # Node.js 20 LTS
    if ! command -v node &> /dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt install -y nodejs
    fi

    # Project directories
    sudo mkdir -p /opt/trading/{frontend,backend,collectors,data}
    sudo mkdir -p /opt/trading/data/{chain_snapshots,ticker_spreads,l2_depth,models}
    sudo chown -R ubuntu:ubuntu /opt/trading
}

# --- Backend Setup ---
setup_backend() {
    echo -e "${GREEN}[2/6] Backend setup...${NC}"
    cd /opt/trading/backend
    
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    deactivate
    
    # Install systemd service
    sudo cp /opt/trading/deployment/trading-backend.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable trading-backend
}

# --- Frontend Build ---
build_frontend() {
    echo -e "${GREEN}[3/6] Frontend build...${NC}"
    cd /opt/trading/frontend
    npm install
    npm run build
}

# --- Nginx Config ---
setup_nginx() {
    echo -e "${GREEN}[4/6] Nginx config...${NC}"
    sudo cp /opt/trading/deployment/nginx.conf /etc/nginx/sites-available/trading
    sudo ln -sf /etc/nginx/sites-available/trading /etc/nginx/sites-enabled/trading
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t && sudo systemctl restart nginx
}

# --- Firewall ---
setup_firewall() {
    echo -e "${GREEN}[5/6] Firewall rules...${NC}"
    # Check if rules already exist
    if ! sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null; then
        sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
        sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
        sudo netfilter-persistent save
    fi
}

# --- Start Services ---
start_services() {
    echo -e "${GREEN}[6/6] Starting services...${NC}"
    sudo systemctl restart trading-backend
    sudo systemctl restart nginx
    
    echo ""
    echo -e "${GREEN}=== Deployment Complete ===${NC}"
    echo -e "Frontend: http://$(curl -s ifconfig.me)"
    echo -e "Backend:  http://$(curl -s ifconfig.me)/api/"
    echo ""
    echo -e "${YELLOW}Services status:${NC}"
    sudo systemctl status trading-backend --no-pager -l
    echo ""
    sudo systemctl status nginx --no-pager -l
}

# --- Quick Redeploy (after code changes) ---
redeploy() {
    echo -e "${GREEN}Quick redeploy...${NC}"
    
    # Rebuild frontend
    cd /opt/trading/frontend
    npm install
    npm run build
    
    # Restart backend
    cd /opt/trading/backend
    source venv/bin/activate
    pip install -r requirements.txt
    deactivate
    sudo systemctl restart trading-backend
    
    echo -e "${GREEN}Redeploy complete!${NC}"
}

# --- Main ---
case "${1:-full}" in
    full)
        setup_system
        setup_backend
        build_frontend
        setup_nginx
        setup_firewall
        start_services
        ;;
    redeploy)
        redeploy
        ;;
    backend)
        setup_backend
        sudo systemctl restart trading-backend
        ;;
    frontend)
        build_frontend
        ;;
    *)
        echo "Usage: ./deploy.sh [full|redeploy|backend|frontend]"
        ;;
esac
```

### 10. Create .gitignore Updates

Add to `.gitignore` (create if doesn't exist):
```
# Environment
.env
*.env.local

# Data (don't commit 18GB of Parquet)
data/
*.parquet

# Python
__pycache__/
*.pyc
venv/
.venv/

# Node
node_modules/
dist/

# OS
.DS_Store
Thumbs.db
```

## What NOT to Change

- Don't restructure the React components or backend route logic
- Don't change DuckDB query logic (just the file paths)
- Don't change the strategy builder, option chain, or P&L chart functionality
- Don't add new dependencies unless absolutely needed
- Don't change how the frontend state management works
- Keep all existing functionality working in local development mode

## Verification Checklist

After making all changes, verify:

1. **Local development still works:**
   ```bash
   # Terminal 1:
   cd backend
   DATA_DIR=./data ENVIRONMENT=development uvicorn main:app --port 8000
   
   # Terminal 2:
   cd frontend
   npm run dev
   
   # Open localhost:5173 — everything should work as before
   ```

2. **No hardcoded localhost in frontend:**
   ```bash
   grep -r "localhost:8000\|127.0.0.1:8000" frontend/src/
   # Should return nothing
   ```

3. **No hardcoded paths in backend:**
   ```bash
   grep -r "C:\\\\Users\|/mnt/c/\|C:/Users" backend/
   # Should return nothing
   ```

4. **Build works:**
   ```bash
   cd frontend
   npm run build
   # Should create dist/ folder without errors
   ```

5. **All deployment files created:**
   ```
   deployment/
   ├── nginx.conf
   ├── trading-backend.service
   ├── trading-collector.service
   └── deploy.sh
   ```

## Summary of Changes

```
Files MODIFIED:
├── vite.config.ts          — Add /api proxy for dev
├── config.py (backend)     — Add DATA_DIR env variable
├── main.py (backend)       — CORS conditional on ENVIRONMENT
├── *.py (backend)          — Replace hardcoded paths with DATA_DIR
├── *.tsx/*.ts (frontend)   — Replace localhost:8000 with /api
└── .gitignore              — Add data/, venv/, dist/

Files CREATED:
├── .env.example
└── deployment/
    ├── nginx.conf
    ├── trading-backend.service
    ├── trading-collector.service
    └── deploy.sh
```

Start by reading the full project structure, then make changes in order (1 through 10). Test locally after changes before deploying.
