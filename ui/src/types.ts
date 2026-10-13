export type Side = "long" | "short";

export interface Instrument {
  isin: string;
  name: string;
  coupon_type: "fixed" | "floating";
  currency: string;
  duration_mod: string | null;
  convexity: string | null;
  payments_per_year: number | null;
}

export interface InstrumentList {
  source: string;
  catalog_version: string;
  items: Instrument[];
}

export interface Scenario {
  shock_bps: number;
  estimated_value_change: string;
}

export interface TradeResult {
  isin: string;
  instrument_name: string;
  side: Side;
  position_market_value: string;
  signed_market_value: string;
  duration_mod: string;
  convexity: string;
  dv01: string;
  risk_share: string;
  scenarios: Scenario[];
  source: string;
  catalog_version: string;
}

export interface Calculation {
  id: string;
  created_at: string;
  currency: string;
  source: string;
  catalog_version: string;
  signed_market_value: string;
  dv01: string;
  trades: TradeResult[];
  scenarios: Scenario[];
}

export interface CalculationListItem {
  id: string;
  created_at: string;
  currency: string;
  signed_market_value: string;
  dv01: string;
  trade_count: number;
  source: string;
  catalog_version: string;
}

export interface CalculationList {
  items: CalculationListItem[];
  limit: number;
  offset: number;
  total: number;
}

export interface DraftTrade {
  rowId: string;
  isin: string;
  position_market_value: string;
  side: Side;
}

export interface MoexBond {
  secid: string;
  isin: string;
  short_name: string;
  name: string;
  bond_type: string | null;
  face_value: string | null;
  latest_trade_date: string | null;
  latest_price_pct: string | null;
  latest_z_spread_pct: string | null;
  finam_symbol: string | null;
  live_price_pct: string | null;
  live_quote_at: string | null;
  quote_source: string | null;
}

export interface LiveQuote {
  secid: string;
  isin: string;
  symbol: string;
  last_price_pct: string;
  bid_price_pct: string | null;
  ask_price_pct: string | null;
  quote_at: string;
  source: string;
}

export interface MoexBondSearchResult {
  secid: string;
  isin: string;
  short_name: string;
  name: string;
  group: string;
  is_traded: boolean;
  primary_boardid: string;
  already_added: boolean;
}

export interface LiveFeedStatus {
  source: string;
  enabled: boolean;
  connected: boolean;
  last_update: string | null;
  mapped_instruments: number;
  message: string;
}

export interface MoexRiskResult {
  source: string;
  as_of: string;
  history_as_of: string;
  curve_as_of: string;
  live_prices_used: boolean;
  confidence: number;
  lookback_observations: number;
  signed_market_value: number;
  dv01_value_change_per_bp: number;
  cs01_value_change_per_bp: number;
  key_rate_dv01: Array<{ tenor: string; years: number; value_change_per_bp: number }>;
  var_1d: number;
  expected_shortfall_1d: number;
  pnl_scenarios: Array<{ date: string; pnl: number; loss: number }>;
  trades: Array<{
    secid: string;
    isin: string;
    short_name: string;
    name: string;
    side: Side;
    market_value: number;
    signed_market_value: number;
    market_price_pct: number;
    price_source: string;
    live_quote_at: string | null;
    z_spread_pct: number | null;
    z_spread_source: string;
    krd_dv01: Array<{ tenor: string; years: number; value_change_per_bp: number }>;
    cs01_value_change_per_bp: number | null;
  }>;
  methodology: Record<string, string>;
}
