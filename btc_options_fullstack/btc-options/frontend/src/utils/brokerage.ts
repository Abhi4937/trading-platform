/**
 * Delta Exchange India — Options taker fee (market orders).
 *
 * Two rate tiers:
 *   standard : 0.03% of notional, capped at 10%  of premium
 *   offer    : 0.010% of notional, capped at 3.5% of premium  (X-Mas / current live rate)
 *
 * +18% GST on all fees (India).
 * Optional referral discount: 10% off the fee (first 2 months).
 *
 * Notional per contract = spot × 0.001 BTC
 * Fee per contract      = min(rate × spot, cap × mark) × 0.001
 * Total                 = fee_per_contract × qty × GST_mult × referral_mult
 */

export type BrokerageRate = 'standard' | 'offer';

const RATES: Record<BrokerageRate, { pct: number; cap: number; label: string }> = {
  standard: { pct: 0.0003, cap: 0.10,  label: 'Standard (0.03%)' },
  offer:    { pct: 0.0001, cap: 0.035, label: 'X-Mas offer (0.010%)' },
};

export const BROKERAGE_RATE_LABELS = RATES;

/**
 * Total brokerage cost in USD for one side of a trade.
 * @param spot  BTC index price at trade time ($/BTC)
 * @param mark  Option mark price at trade time ($/BTC)
 * @param qty   Number of contracts (lots)
 */
export function computeBrokerage(
  spot: number,
  mark: number,
  qty: number,
  rate: BrokerageRate,
  referral: boolean,
): number {
  if (mark <= 0 || spot <= 0 || qty <= 0) return 0;
  const { pct, cap } = RATES[rate];
  const raw = Math.min(pct * spot, cap * mark) * qty * 0.001;
  return raw * 1.18 * (referral ? 0.90 : 1.0);
}
