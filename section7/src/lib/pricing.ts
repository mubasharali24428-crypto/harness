// W18 — typed access to the pricing data.
//
// `pricing.json` is the page's only source of numbers, and the backend test
// (section1/tests/test_pricing_page_matches_enforced_caps.py) reads the same file
// and compares it to `harness_contracts.tiers.tier_caps()` plus the constants the
// doors enforce. So the page cannot advertise a cap the code does not have —
// which is exactly what the old PRICING.md did (50 runs/day against a real 20).

import data from "./pricing.json";

export interface PricingTier {
  id: string;
  name: string;
  price: string;
  priceNote: string;
}

export type CapField = "runsPerDay" | "contextIndexMaxChunks" | "seatLimit" | "auditRetentionDays";

export interface PricingData {
  tiers: PricingTier[];
  caps: Record<CapField, Record<string, number | null>>;
  capLabels: Record<CapField, string>;
  enforced: CapField[];
  declared: CapField[];
  platform: Record<string, number>;
  teamOnly: { surface: string; limit: string }[];
}

export const pricing = data as unknown as PricingData;

export const CAP_FIELDS: CapField[] = [
  "runsPerDay",
  "contextIndexMaxChunks",
  "seatLimit",
  "auditRetentionDays",
];

/** A cap cell: numbers as-is, `null` spelled out because "none" is a feature. */
export function formatCap(value: number | null | undefined): string {
  return value === null || value === undefined ? "Unlimited" : String(value);
}
