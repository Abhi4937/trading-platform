// Dataset-aware dimension labels for the M7 sweep components.
//
// The calendar-sweep dataset (`dataset === 'calendar'`) reuses the M7 components but
// re-grounds the primary grouping axis from "IV band" onto "Gap band" (backwardation gap
// bucket) and the expiry axis from "Expiry" onto "Pair" (near-far selector pair). The
// underlying data field names are unchanged (`entry_atm_iv_band` = gap bucket,
// `expiry_bucket` = pair) — this only switches the user-visible labels.
import type { M7Dataset } from '../../services/m7_api';

export interface DimLabels {
  isCal: boolean;
  band: string;        // primary grouping axis ("Gap bucket" | "IV band")
  bandShort: string;   // compact form for "Band {x}" card headers
  expiry: string;      // expiry axis ("Pair" | "Expiry")
  bestExpiry: string;  // best-of column header ("Best pair" | "Best expiry")
  perBand: string;     // "per gap bucket" | "per IV band"
  byBand: string;      // "by Gap Bucket" | "by IV Band"
}

export function dimLabels(dataset?: M7Dataset | string): DimLabels {
  const isCal = dataset === 'calendar';
  return {
    isCal,
    band: isCal ? 'Gap bucket' : 'IV band',
    bandShort: isCal ? 'Gap bucket' : 'Band',
    expiry: isCal ? 'Pair' : 'Expiry',
    bestExpiry: isCal ? 'Best pair' : 'Best expiry',
    perBand: isCal ? 'per gap bucket' : 'per IV band',
    byBand: isCal ? 'by Gap Bucket' : 'by IV Band',
  };
}
