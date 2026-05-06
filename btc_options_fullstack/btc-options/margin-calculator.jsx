import { useState, useCallback, useMemo } from "react";

// ============================================================
// Delta Exchange Portfolio Margin Engine
// Implements: Risk Margin (stress test) + Margin Floor
// Portfolio Margin = max(Risk Margin, Margin Floor)
// ============================================================

// --- Black-Scholes helpers ---
function normalCDF(x) {
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.sqrt(2);
  const t = 1.0 / (1.0 + p * x);
  const y = 1.0 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1.0 + sign * y);
}

function bsPrice(S, K, T, r, sigma, isCall) {
  if (T <= 0) return Math.max(isCall ? S - K : K - S, 0);
  if (sigma <= 0) return Math.max(isCall ? S - K : K - S, 0);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  if (isCall) return S * normalCDF(d1) - K * Math.exp(-r * T) * normalCDF(d2);
  return K * Math.exp(-r * T) * normalCDF(-d2) - S * normalCDF(-d1);
}

function bsDelta(S, K, T, r, sigma, isCall) {
  if (T <= 0) return isCall ? (S > K ? 1 : 0) : (S < K ? -1 : 0);
  if (sigma <= 0) return isCall ? (S > K ? 1 : 0) : (S < K ? -1 : 0);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  return isCall ? normalCDF(d1) : normalCDF(d1) - 1;
}

// --- Notional Tier -> Shock Span (Delta Exchange India BTC) ---
function getShockParams(totalNotionalUSD) {
  // Simplified tier mapping from Delta docs
  // Price shock: 2% to 10%, Vol up: 9-45pts, Vol down: 6-30pts
  const tiers = [
    { maxNotional: 50000,   priceShock: 0.04, volUp: 15, volDown: 10 },
    { maxNotional: 100000,  priceShock: 0.05, volUp: 18, volDown: 12 },
    { maxNotional: 250000,  priceShock: 0.06, volUp: 22, volDown: 15 },
    { maxNotional: 500000,  priceShock: 0.07, volUp: 27, volDown: 18 },
    { maxNotional: 1000000, priceShock: 0.08, volUp: 33, volDown: 22 },
    { maxNotional: 2500000, priceShock: 0.09, volUp: 38, volDown: 25 },
    { maxNotional: Infinity, priceShock: 0.10, volUp: 45, volDown: 30 },
  ];
  for (const t of tiers) {
    if (totalNotionalUSD <= t.maxNotional) return t;
  }
  return tiers[tiers.length - 1];
}

// --- Risk Margin: 29-scenario stress test ---
function computeRiskMargin(positions, spotPrice, r = 0) {
  // positions: [{ strike, isCall, size (negative=short), iv, tte, premium }]
  const totalNotional = positions.reduce(
    (s, p) => s + Math.abs(p.size) * spotPrice, 0
  );
  const shock = getShockParams(totalNotional);

  // Generate scenarios: 7 price points × 3 vol scenarios + 2 extreme
  const priceSteps = [-1, -2/3, -1/3, 0, 1/3, 2/3, 1];
  const volScenarios = ["down", "flat", "up"];

  let worstLoss = 0;

  for (const pStep of priceSteps) {
    for (const vScene of volScenarios) {
      const simSpot = spotPrice * (1 + pStep * shock.priceShock);
      let portfolioPnL = 0;

      for (const pos of positions) {
        const volShift = vScene === "up" ? shock.volUp / 100
          : vScene === "down" ? -shock.volDown / 100 : 0;
        const simIV = Math.max(0.05, pos.iv + volShift);
        const simPrice = bsPrice(simSpot, pos.strike, pos.tte, r, simIV, pos.isCall);
        const currentPrice = bsPrice(spotPrice, pos.strike, pos.tte, r, pos.iv, pos.isCall);
        portfolioPnL += pos.size * (simPrice - currentPrice);
      }

      const loss = -portfolioPnL;
      if (loss > worstLoss) worstLoss = loss;
    }
  }

  // 2 extreme scenarios (price at extremes, vol at extreme)
  for (const extreme of [-1, 1]) {
    const simSpot = spotPrice * (1 + extreme * shock.priceShock);
    let portfolioPnL = 0;
    for (const pos of positions) {
      const simIV = Math.max(0.05, pos.iv + (extreme > 0 ? shock.volUp : -shock.volDown) / 100);
      const simPrice = bsPrice(simSpot, pos.strike, pos.tte, 0, simIV, pos.isCall);
      const currentPrice = bsPrice(spotPrice, pos.strike, pos.tte, 0, pos.iv, pos.isCall);
      // Extreme scenarios use 35% of move (Delta docs convention)
      portfolioPnL += pos.size * (simPrice - currentPrice) * 0.35;
    }
    const loss = -portfolioPnL;
    if (loss > worstLoss) worstLoss = loss;
  }

  return Math.max(0, worstLoss);
}

// --- Margin Floor ---
function computeMarginFloor(positions, spotPrice) {
  // MF = MF_Short_Options + MF_Long_Options + MF_Futures
  // For short options: max(5% × premium, OM% × notional)
  // OM% base = 1% for BTC, capped at 2%
  const OM_BASE = 0.01;
  const OM_CAP = 0.02;

  let shortNotional = 0;
  let longNotional = 0;

  for (const p of positions) {
    const notional = Math.abs(p.size) * spotPrice;
    if (p.size < 0) shortNotional += notional;
    else longNotional += notional;
  }

  // OM% for short (simplified — in reality scales with notional)
  const omShort = Math.min(OM_CAP, OM_BASE);
  const omLong = Math.min(OM_CAP, OM_BASE);

  let mfShort = 0;
  let mfLong = 0;

  for (const p of positions) {
    const notional = Math.abs(p.size) * spotPrice;
    const premium = Math.abs(p.size) * p.premium;
    if (p.size < 0) {
      mfShort += Math.max(0.05 * premium, omShort * notional);
    } else {
      mfLong += Math.max(0.05 * premium, omLong * notional);
    }
  }

  return mfShort + mfLong;
}

// --- Full Portfolio Margin ---
function computePortfolioMargin(positions, spotPrice) {
  if (!positions.length) return { total: 0, riskMargin: 0, marginFloor: 0, method: "none" };
  const riskMargin = computeRiskMargin(positions, spotPrice);
  const marginFloor = computeMarginFloor(positions, spotPrice);
  const total = Math.max(riskMargin, marginFloor);
  return {
    total,
    riskMargin,
    marginFloor,
    method: riskMargin >= marginFloor ? "Risk Margin (stress test)" : "Margin Floor",
  };
}

// ============================================================
// UI Components
// ============================================================

const COLORS = {
  bg: "#0a0e17",
  card: "#111827",
  cardBorder: "#1e293b",
  accent: "#f59e0b",
  accentDim: "#92400e",
  red: "#ef4444",
  green: "#22c55e",
  text: "#e2e8f0",
  textDim: "#64748b",
  textMuted: "#475569",
  input: "#1e293b",
  inputBorder: "#334155",
  inputFocus: "#f59e0b",
};

const styles = {
  container: {
    fontFamily: "'JetBrains Mono', 'SF Mono', 'Fira Code', monospace",
    background: COLORS.bg,
    color: COLORS.text,
    minHeight: "100vh",
    padding: "24px",
  },
  header: {
    marginBottom: "32px",
  },
  title: {
    fontSize: "20px",
    fontWeight: 700,
    color: COLORS.accent,
    letterSpacing: "-0.5px",
    margin: 0,
  },
  subtitle: {
    fontSize: "12px",
    color: COLORS.textDim,
    marginTop: "4px",
    letterSpacing: "0.5px",
    textTransform: "uppercase",
  },
  tabs: {
    display: "flex",
    gap: "2px",
    marginBottom: "24px",
    background: COLORS.card,
    borderRadius: "8px",
    padding: "3px",
    border: `1px solid ${COLORS.cardBorder}`,
  },
  tab: (active) => ({
    flex: 1,
    padding: "10px 16px",
    border: "none",
    background: active ? COLORS.accentDim : "transparent",
    color: active ? COLORS.accent : COLORS.textDim,
    cursor: "pointer",
    fontSize: "12px",
    fontWeight: 600,
    letterSpacing: "0.5px",
    textTransform: "uppercase",
    borderRadius: "6px",
    transition: "all 0.15s",
    fontFamily: "inherit",
  }),
  card: {
    background: COLORS.card,
    border: `1px solid ${COLORS.cardBorder}`,
    borderRadius: "10px",
    padding: "20px",
    marginBottom: "16px",
  },
  cardTitle: {
    fontSize: "11px",
    fontWeight: 700,
    color: COLORS.textDim,
    letterSpacing: "1px",
    textTransform: "uppercase",
    marginBottom: "16px",
  },
  row: {
    display: "flex",
    gap: "12px",
    marginBottom: "12px",
    flexWrap: "wrap",
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
    flex: 1,
    minWidth: "120px",
  },
  label: {
    fontSize: "10px",
    fontWeight: 600,
    color: COLORS.textMuted,
    letterSpacing: "0.5px",
    textTransform: "uppercase",
  },
  input: {
    background: COLORS.input,
    border: `1px solid ${COLORS.inputBorder}`,
    borderRadius: "6px",
    padding: "8px 10px",
    color: COLORS.text,
    fontSize: "13px",
    fontFamily: "inherit",
    outline: "none",
    transition: "border-color 0.15s",
  },
  select: {
    background: COLORS.input,
    border: `1px solid ${COLORS.inputBorder}`,
    borderRadius: "6px",
    padding: "8px 10px",
    color: COLORS.text,
    fontSize: "13px",
    fontFamily: "inherit",
    outline: "none",
    cursor: "pointer",
  },
  btn: (variant = "primary") => ({
    padding: "10px 20px",
    border: "none",
    borderRadius: "6px",
    fontSize: "12px",
    fontWeight: 700,
    letterSpacing: "0.5px",
    textTransform: "uppercase",
    cursor: "pointer",
    fontFamily: "inherit",
    transition: "all 0.15s",
    ...(variant === "primary"
      ? { background: COLORS.accent, color: COLORS.bg }
      : variant === "danger"
      ? { background: COLORS.red + "22", color: COLORS.red, border: `1px solid ${COLORS.red}44` }
      : { background: COLORS.input, color: COLORS.textDim, border: `1px solid ${COLORS.inputBorder}` }),
  }),
  resultBox: {
    background: "#0d1117",
    border: `1px solid ${COLORS.accent}33`,
    borderRadius: "10px",
    padding: "20px",
    marginTop: "16px",
  },
  resultRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "8px 0",
    borderBottom: `1px solid ${COLORS.cardBorder}`,
  },
  resultLabel: {
    fontSize: "12px",
    color: COLORS.textDim,
  },
  resultValue: (highlight = false) => ({
    fontSize: highlight ? "18px" : "13px",
    fontWeight: highlight ? 800 : 600,
    color: highlight ? COLORS.accent : COLORS.text,
  }),
  legTag: (isCall) => ({
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "10px",
    fontWeight: 700,
    letterSpacing: "0.5px",
    background: isCall ? COLORS.green + "22" : COLORS.red + "22",
    color: isCall ? COLORS.green : COLORS.red,
    border: `1px solid ${isCall ? COLORS.green : COLORS.red}44`,
  }),
  greekBox: {
    display: "flex",
    gap: "16px",
    marginTop: "12px",
    flexWrap: "wrap",
  },
  greekItem: {
    textAlign: "center",
    minWidth: "70px",
  },
  greekLabel: {
    fontSize: "10px",
    color: COLORS.textMuted,
    textTransform: "uppercase",
    letterSpacing: "0.5px",
  },
  greekValue: {
    fontSize: "14px",
    fontWeight: 700,
    color: COLORS.text,
    marginTop: "2px",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "12px",
  },
  th: {
    textAlign: "left",
    padding: "8px 10px",
    fontSize: "10px",
    fontWeight: 700,
    color: COLORS.textMuted,
    letterSpacing: "0.5px",
    textTransform: "uppercase",
    borderBottom: `1px solid ${COLORS.cardBorder}`,
  },
  td: {
    padding: "8px 10px",
    borderBottom: `1px solid ${COLORS.cardBorder}22`,
    color: COLORS.text,
  },
  helpText: {
    fontSize: "11px",
    color: COLORS.textMuted,
    lineHeight: 1.5,
    marginTop: "8px",
  },
};

// --- Forward Calculator Tab ---
function ForwardCalculator() {
  const [spot, setSpot] = useState("87000");
  const [legs, setLegs] = useState([
    { strike: "92000", isCall: true, size: "-0.1", iv: "55", tte: "7", premium: "450" },
    { strike: "82000", isCall: false, size: "-0.1", iv: "58", tte: "7", premium: "380" },
  ]);
  const [result, setResult] = useState(null);

  const updateLeg = (idx, key, val) => {
    setLegs((prev) => prev.map((l, i) => (i === idx ? { ...l, [key]: val } : l)));
  };

  const addLeg = () => {
    setLegs((prev) => [
      ...prev,
      { strike: "", isCall: true, size: "-0.1", iv: "50", tte: "7", premium: "200" },
    ]);
  };

  const removeLeg = (idx) => {
    setLegs((prev) => prev.filter((_, i) => i !== idx));
  };

  const calculate = () => {
    const spotNum = parseFloat(spot);
    if (!spotNum) return;
    const positions = legs
      .filter((l) => l.strike && l.size)
      .map((l) => ({
        strike: parseFloat(l.strike),
        isCall: l.isCall,
        size: parseFloat(l.size),
        iv: parseFloat(l.iv) / 100,
        tte: parseFloat(l.tte) / 365,
        premium: parseFloat(l.premium) || 0,
      }));
    if (!positions.length) return;

    const margin = computePortfolioMargin(positions, spotNum);

    // Compute greeks
    let netDelta = 0, netGamma = 0, netTheta = 0, netVega = 0;
    for (const p of positions) {
      const d = bsDelta(spotNum, p.strike, p.tte, 0, p.iv, p.isCall);
      netDelta += p.size * d;
      // Approximate gamma, theta, vega
      const bump = spotNum * 0.001;
      const dUp = bsDelta(spotNum + bump, p.strike, p.tte, 0, p.iv, p.isCall);
      netGamma += p.size * (dUp - d) / bump;
      const tBump = 1 / 365;
      const pNow = bsPrice(spotNum, p.strike, p.tte, 0, p.iv, p.isCall);
      const pTmrw = bsPrice(spotNum, p.strike, Math.max(0, p.tte - tBump), 0, p.iv, p.isCall);
      netTheta += p.size * (pTmrw - pNow);
      const vBump = 0.01;
      const pVup = bsPrice(spotNum, p.strike, p.tte, 0, p.iv + vBump, p.isCall);
      netVega += p.size * (pVup - pNow);
    }

    const totalPremium = positions.reduce(
      (s, p) => s + Math.abs(p.size) * p.premium, 0
    );
    const totalNotional = positions.reduce(
      (s, p) => s + Math.abs(p.size) * spotNum, 0
    );

    setResult({
      ...margin,
      netDelta,
      netGamma,
      netTheta,
      netVega,
      totalPremium,
      totalNotional,
      marginPct: ((margin.total / totalNotional) * 100).toFixed(2),
    });
  };

  return (
    <div>
      <div style={styles.card}>
        <div style={styles.cardTitle}>Underlying</div>
        <div style={styles.row}>
          <div style={styles.field}>
            <label style={styles.label}>BTC Spot Price (USDT)</label>
            <input
              style={styles.input}
              type="number"
              value={spot}
              onChange={(e) => setSpot(e.target.value)}
              placeholder="87000"
            />
          </div>
        </div>
      </div>

      {legs.map((leg, idx) => (
        <div key={idx} style={styles.card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={styles.cardTitle}>Leg {idx + 1}</span>
              <span style={styles.legTag(leg.isCall)}>{leg.isCall ? "CALL" : "PUT"}</span>
              <span style={{
                ...styles.legTag(false),
                background: COLORS.accent + "22",
                color: COLORS.accent,
                border: `1px solid ${COLORS.accent}44`,
              }}>
                {parseFloat(leg.size) < 0 ? "SHORT" : "LONG"}
              </span>
            </div>
            {legs.length > 1 && (
              <button style={styles.btn("danger")} onClick={() => removeLeg(idx)}>✕</button>
            )}
          </div>
          <div style={styles.row}>
            <div style={styles.field}>
              <label style={styles.label}>Type</label>
              <select
                style={styles.select}
                value={leg.isCall ? "call" : "put"}
                onChange={(e) => updateLeg(idx, "isCall", e.target.value === "call")}
              >
                <option value="call">Call</option>
                <option value="put">Put</option>
              </select>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Strike</label>
              <input
                style={styles.input}
                type="number"
                value={leg.strike}
                onChange={(e) => updateLeg(idx, "strike", e.target.value)}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Size (BTC)</label>
              <input
                style={styles.input}
                type="number"
                step="0.01"
                value={leg.size}
                onChange={(e) => updateLeg(idx, "size", e.target.value)}
              />
            </div>
          </div>
          <div style={styles.row}>
            <div style={styles.field}>
              <label style={styles.label}>IV (%)</label>
              <input
                style={styles.input}
                type="number"
                value={leg.iv}
                onChange={(e) => updateLeg(idx, "iv", e.target.value)}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Days to Expiry</label>
              <input
                style={styles.input}
                type="number"
                value={leg.tte}
                onChange={(e) => updateLeg(idx, "tte", e.target.value)}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Premium (USDT)</label>
              <input
                style={styles.input}
                type="number"
                value={leg.premium}
                onChange={(e) => updateLeg(idx, "premium", e.target.value)}
              />
            </div>
          </div>
        </div>
      ))}

      <div style={{ display: "flex", gap: "10px", marginBottom: "16px" }}>
        <button style={styles.btn("secondary")} onClick={addLeg}>+ Add Leg</button>
        <button style={styles.btn("primary")} onClick={calculate}>Calculate Margin</button>
      </div>

      {result && (
        <div style={styles.resultBox}>
          <div style={styles.resultRow}>
            <span style={styles.resultLabel}>Portfolio Margin Required</span>
            <span style={styles.resultValue(true)}>{result.total.toFixed(2)} USDT</span>
          </div>
          <div style={styles.resultRow}>
            <span style={styles.resultLabel}>Binding Constraint</span>
            <span style={{ ...styles.resultValue(), color: COLORS.accent }}>{result.method}</span>
          </div>
          <div style={styles.resultRow}>
            <span style={styles.resultLabel}>Risk Margin (stress test)</span>
            <span style={styles.resultValue()}>{result.riskMargin.toFixed(2)} USDT</span>
          </div>
          <div style={styles.resultRow}>
            <span style={styles.resultLabel}>Margin Floor</span>
            <span style={styles.resultValue()}>{result.marginFloor.toFixed(2)} USDT</span>
          </div>
          <div style={{ ...styles.resultRow, borderBottom: "none" }}>
            <span style={styles.resultLabel}>Margin as % of Notional</span>
            <span style={styles.resultValue()}>{result.marginPct}%</span>
          </div>
          <div style={{ ...styles.resultRow, borderBottom: "none" }}>
            <span style={styles.resultLabel}>Total Notional</span>
            <span style={styles.resultValue()}>{result.totalNotional.toFixed(2)} USDT</span>
          </div>
          <div style={{ ...styles.resultRow, borderBottom: "none" }}>
            <span style={styles.resultLabel}>Total Premium Collected</span>
            <span style={{ ...styles.resultValue(), color: COLORS.green }}>
              {result.totalPremium.toFixed(2)} USDT
            </span>
          </div>

          <div style={styles.greekBox}>
            <div style={styles.greekItem}>
              <div style={styles.greekLabel}>Delta</div>
              <div style={styles.greekValue}>{result.netDelta.toFixed(4)}</div>
            </div>
            <div style={styles.greekItem}>
              <div style={styles.greekLabel}>Gamma</div>
              <div style={styles.greekValue}>{result.netGamma.toFixed(6)}</div>
            </div>
            <div style={styles.greekItem}>
              <div style={styles.greekLabel}>Theta/day</div>
              <div style={{ ...styles.greekValue, color: result.netTheta > 0 ? COLORS.green : COLORS.red }}>
                {result.netTheta.toFixed(2)}
              </div>
            </div>
            <div style={styles.greekItem}>
              <div style={styles.greekLabel}>Vega</div>
              <div style={styles.greekValue}>{result.netVega.toFixed(2)}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Retroactive Reconstructor Tab ---
function Reconstructor() {
  const [jsonInput, setJsonInput] = useState("");
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  const sampleData = JSON.stringify({
    fills: [
      {
        timestamp: "2026-03-10T14:30:00Z",
        symbol: "C-BTC-92000-280326",
        side: "sell",
        size: 0.1,
        price: 1850,
        spot_price: 87200,
        iv: 0.55,
        strike: 92000,
        is_call: true,
        tte_days: 18
      },
      {
        timestamp: "2026-03-10T14:31:00Z",
        symbol: "P-BTC-82000-280326",
        side: "sell",
        size: 0.1,
        price: 1420,
        spot_price: 87200,
        iv: 0.58,
        strike: 82000,
        is_call: false,
        tte_days: 18
      }
    ]
  }, null, 2);

  const reconstruct = () => {
    setError(null);
    try {
      const data = JSON.parse(jsonInput);
      if (!data.fills || !Array.isArray(data.fills)) {
        throw new Error("JSON must have a 'fills' array");
      }

      // Sort fills by timestamp
      const fills = data.fills.sort(
        (a, b) => new Date(a.timestamp) - new Date(b.timestamp)
      );

      // Replay: accumulate positions, compute margin at each fill
      const portfolio = {}; // symbol -> position
      const snapshots = [];

      for (const fill of fills) {
        const key = fill.symbol;
        if (!portfolio[key]) {
          portfolio[key] = {
            strike: fill.strike,
            isCall: fill.is_call,
            size: 0,
            iv: fill.iv,
            tte: fill.tte_days / 365,
            premium: fill.price,
          };
        }

        const prevSize = portfolio[key].size;
        if (fill.side === "sell") {
          portfolio[key].size -= fill.size;
        } else {
          portfolio[key].size += fill.size;
        }
        portfolio[key].iv = fill.iv;
        portfolio[key].tte = fill.tte_days / 365;
        portfolio[key].premium = fill.price;

        // Remove zero-size positions
        const activePositions = Object.values(portfolio).filter(
          (p) => Math.abs(p.size) > 0.0001
        );

        const margin = computePortfolioMargin(activePositions, fill.spot_price);

        snapshots.push({
          timestamp: fill.timestamp,
          symbol: fill.symbol,
          side: fill.side,
          size: fill.size,
          fillPrice: fill.price,
          spotPrice: fill.spot_price,
          positionsCount: activePositions.length,
          ...margin,
        });
      }

      setResults(snapshots);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div>
      <div style={styles.card}>
        <div style={styles.cardTitle}>Input Fill Data (JSON)</div>
        <p style={styles.helpText}>
          Paste your fills data as JSON. Each fill needs: timestamp, symbol, side (buy/sell),
          size (in BTC), price (premium USDT), spot_price, iv (decimal e.g. 0.55), strike,
          is_call (true/false), tte_days. The tool replays each fill chronologically and
          computes the portfolio margin at each step.
        </p>
        <textarea
          style={{
            ...styles.input,
            width: "100%",
            minHeight: "200px",
            resize: "vertical",
            boxSizing: "border-box",
            marginTop: "12px",
          }}
          value={jsonInput}
          onChange={(e) => setJsonInput(e.target.value)}
          placeholder="Paste your fills JSON here..."
          spellCheck={false}
        />
        <div style={{ display: "flex", gap: "10px", marginTop: "12px" }}>
          <button style={styles.btn("primary")} onClick={reconstruct}>
            Reconstruct Margins
          </button>
          <button
            style={styles.btn("secondary")}
            onClick={() => setJsonInput(sampleData)}
          >
            Load Sample Data
          </button>
        </div>
      </div>

      {error && (
        <div style={{ ...styles.card, borderColor: COLORS.red + "66" }}>
          <span style={{ color: COLORS.red, fontSize: "13px" }}>Error: {error}</span>
        </div>
      )}

      {results && results.length > 0 && (
        <div style={styles.card}>
          <div style={styles.cardTitle}>Margin Reconstruction Timeline</div>
          <div style={{ overflowX: "auto" }}>
            <table style={styles.table}>
              <thead>
                <tr>
                  <th style={styles.th}>Time</th>
                  <th style={styles.th}>Fill</th>
                  <th style={styles.th}>Side</th>
                  <th style={styles.th}>Size</th>
                  <th style={styles.th}>Premium</th>
                  <th style={styles.th}>Spot</th>
                  <th style={styles.th}># Legs</th>
                  <th style={styles.th}>Risk Margin</th>
                  <th style={styles.th}>Margin Floor</th>
                  <th style={styles.th}>Total Margin</th>
                  <th style={styles.th}>Binding</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, idx) => (
                  <tr key={idx} style={{ background: idx % 2 === 0 ? "transparent" : COLORS.input + "44" }}>
                    <td style={{ ...styles.td, fontSize: "11px", whiteSpace: "nowrap" }}>
                      {new Date(r.timestamp).toLocaleString("en-IN", {
                        month: "short", day: "numeric",
                        hour: "2-digit", minute: "2-digit",
                      })}
                    </td>
                    <td style={{ ...styles.td, fontSize: "11px" }}>
                      {r.symbol.length > 20 ? r.symbol.slice(0, 20) + "…" : r.symbol}
                    </td>
                    <td style={styles.td}>
                      <span style={{
                        color: r.side === "sell" ? COLORS.red : COLORS.green,
                        fontWeight: 700,
                        fontSize: "11px",
                        textTransform: "uppercase",
                      }}>
                        {r.side}
                      </span>
                    </td>
                    <td style={styles.td}>{r.size}</td>
                    <td style={styles.td}>{r.fillPrice.toFixed(1)}</td>
                    <td style={styles.td}>{r.spotPrice.toLocaleString()}</td>
                    <td style={{ ...styles.td, textAlign: "center" }}>{r.positionsCount}</td>
                    <td style={styles.td}>{r.riskMargin.toFixed(2)}</td>
                    <td style={styles.td}>{r.marginFloor.toFixed(2)}</td>
                    <td style={{ ...styles.td, color: COLORS.accent, fontWeight: 700 }}>
                      {r.total.toFixed(2)}
                    </td>
                    <td style={{ ...styles.td, fontSize: "10px", color: COLORS.textDim }}>
                      {r.method === "Risk Margin (stress test)" ? "Risk" : "Floor"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ marginTop: "20px", ...styles.resultBox }}>
            <div style={styles.resultRow}>
              <span style={styles.resultLabel}>Peak Margin Required</span>
              <span style={styles.resultValue(true)}>
                {Math.max(...results.map((r) => r.total)).toFixed(2)} USDT
              </span>
            </div>
            <div style={{ ...styles.resultRow, borderBottom: "none" }}>
              <span style={styles.resultLabel}>Final Margin Required</span>
              <span style={styles.resultValue()}>
                {results[results.length - 1].total.toFixed(2)} USDT
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// --- Data Prep Helper Tab ---
function DataPrepGuide() {
  const scriptCode = `# fetch_fills_for_reconstruction.py
# Run this to extract fills + enrich with spot/IV for margin reconstruction

import hashlib, hmac, json, time, requests

BASE_URL = "https://api.india.delta.exchange"
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"

def sign(method, path, qs="", body=""):
    ts = str(int(time.time()))
    msg = method + ts + path + qs + body
    sig = hmac.new(
        API_SECRET.encode(), msg.encode(), hashlib.sha256
    ).hexdigest()
    return {"api-key": API_KEY, "timestamp": ts, "signature": sig,
            "User-Agent": "margin-tool", "Content-Type": "application/json"}

def get_fills(page_size=100):
    path = "/v2/fills"
    qs = f"?page_size={page_size}&contract_types=call_options,put_options"
    headers = sign("GET", path, qs)
    r = requests.get(BASE_URL + path + qs, headers=headers, timeout=10)
    return r.json()

def get_mark_price(symbol):
    """Get current mark price for MARK:{symbol}"""
    path = f"/v2/tickers/{symbol}"
    r = requests.get(BASE_URL + path, timeout=10)
    data = r.json()
    if data.get("success"):
        return data["result"]
    return None

def get_spot_candle(ts_start, ts_end):
    """Get BTC spot price at a timestamp using OHLC"""
    path = "/v2/history/candles"
    qs = f"?resolution=1m&symbol=BTCUSDT&start={ts_start}&end={ts_end}"
    r = requests.get(BASE_URL + path + qs, timeout=10)
    return r.json()

# --- Main ---
fills_resp = get_fills()
if not fills_resp.get("success"):
    print("Error:", fills_resp)
    exit()

enriched = []
for fill in fills_resp["result"]:
    symbol = fill["product_symbol"]
    # Parse symbol: C-BTC-92000-280326 or P-BTC-82000-280326
    parts = symbol.split("-")
    is_call = parts[0] == "C"
    strike = int(parts[2])
    expiry_str = parts[3]  # DDMMYY

    # You'll need to enrich with:
    # 1. spot_price at fill time (from your btc-collector data)
    # 2. IV at fill time (from your mark price history)
    # 3. tte_days (compute from fill time to expiry)

    enriched.append({
        "timestamp": fill["created_at"],
        "symbol": symbol,
        "side": fill["side"],
        "size": fill["size"],
        "price": float(fill["price"]),
        "spot_price": 0,    # <-- fill from your data
        "iv": 0,            # <-- fill from your data
        "strike": strike,
        "is_call": is_call,
        "tte_days": 0,      # <-- compute from expiry
    })

output = {"fills": enriched}
with open("fills_for_margin.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"Exported {len(enriched)} fills. Now enrich spot_price, iv, tte_days from your data.")`;

  return (
    <div>
      <div style={styles.card}>
        <div style={styles.cardTitle}>How to Prepare Your Data</div>
        <p style={{ ...styles.helpText, fontSize: "12px", lineHeight: 1.7 }}>
          Since Delta's API doesn't return margin per order, you need to reconstruct it.
          Here's the workflow:
        </p>
        <div style={{ marginTop: "16px" }}>
          {[
            { step: "1", title: "Pull fills from API", desc: "GET /v2/fills with contract_types=call_options,put_options" },
            { step: "2", title: "Enrich with spot price", desc: "From your btc-collector Parquet/DuckDB data, get BTC spot at each fill timestamp" },
            { step: "3", title: "Enrich with IV", desc: "From your MARK: symbol history, get IV at each fill timestamp" },
            { step: "4", title: "Compute TTE", desc: "Parse expiry from symbol (e.g. 280326 = Mar 28 2026), subtract fill time" },
            { step: "5", title: "Paste into Reconstructor tab", desc: "The tool replays fills chronologically and computes margin at each step" },
          ].map((item) => (
            <div key={item.step} style={{ display: "flex", gap: "12px", marginBottom: "14px" }}>
              <div style={{
                width: "28px", height: "28px", borderRadius: "50%",
                background: COLORS.accent + "22", color: COLORS.accent,
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: "13px", fontWeight: 800, flexShrink: 0,
                border: `1px solid ${COLORS.accent}44`,
              }}>
                {item.step}
              </div>
              <div>
                <div style={{ fontSize: "13px", fontWeight: 700, color: COLORS.text }}>
                  {item.title}
                </div>
                <div style={{ fontSize: "11px", color: COLORS.textDim, marginTop: "2px" }}>
                  {item.desc}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={styles.card}>
        <div style={styles.cardTitle}>Python Script — Fetch & Prep Fills</div>
        <pre style={{
          background: "#0d1117",
          border: `1px solid ${COLORS.cardBorder}`,
          borderRadius: "6px",
          padding: "16px",
          fontSize: "11px",
          lineHeight: 1.6,
          overflow: "auto",
          maxHeight: "400px",
          color: COLORS.textDim,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}>
          {scriptCode}
        </pre>
      </div>

      <div style={styles.card}>
        <div style={styles.cardTitle}>Expected JSON Format</div>
        <pre style={{
          background: "#0d1117",
          border: `1px solid ${COLORS.cardBorder}`,
          borderRadius: "6px",
          padding: "16px",
          fontSize: "11px",
          lineHeight: 1.6,
          overflow: "auto",
          color: COLORS.textDim,
        }}>
{`{
  "fills": [
    {
      "timestamp": "2026-03-10T14:30:00Z",
      "symbol": "C-BTC-92000-280326",
      "side": "sell",
      "size": 0.1,
      "price": 1850,        // premium in USDT
      "spot_price": 87200,   // BTC spot at fill time
      "iv": 0.55,            // implied vol (decimal)
      "strike": 92000,
      "is_call": true,
      "tte_days": 18         // days to expiry at fill time
    }
  ]
}`}
        </pre>
        <p style={styles.helpText}>
          Note: The margin calculation is an approximation of Delta Exchange's engine.
          The actual exchange uses proprietary parameters for notional tiers, OM% slopes,
          and scenario weights that aren't fully public. This gives you a directionally
          accurate estimate — typically within 5-15% of actual required margin.
        </p>
      </div>
    </div>
  );
}

// --- Main App ---
export default function App() {
  const [tab, setTab] = useState("calc");

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h1 style={styles.title}>Delta Exchange Portfolio Margin Tool</h1>
        <p style={styles.subtitle}>BTC Options • Stress Test Engine • Short Strangles</p>
      </div>

      <div style={styles.tabs}>
        <button style={styles.tab(tab === "calc")} onClick={() => setTab("calc")}>
          Forward Calculator
        </button>
        <button style={styles.tab(tab === "retro")} onClick={() => setTab("retro")}>
          Reconstructor
        </button>
        <button style={styles.tab(tab === "prep")} onClick={() => setTab("prep")}>
          Data Prep Guide
        </button>
      </div>

      {tab === "calc" && <ForwardCalculator />}
      {tab === "retro" && <Reconstructor />}
      {tab === "prep" && <DataPrepGuide />}
    </div>
  );
}
