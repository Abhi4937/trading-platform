export interface StrategyLeg {
  id: string;
  expiry: string;
  strike: number;
  type: 'CE' | 'PE';
  action: 'BUY' | 'SELL';
  qty: number;
  entryPremium: number;
  entryTimestamp: number; // unix seconds — simulation time when leg was added
  entrySpot: number;      // BTC spot at entry — frozen for slippage model inputs
  entryOi?: number;       // open interest at entry (BTC contracts), if available
}

export interface MtmPoint {
  time: number; // unix seconds
  pnl: number;  // total combined P&L in USD
}

export interface Strategy {
  id: string;
  label: string;
  legs: StrategyLeg[];
}
