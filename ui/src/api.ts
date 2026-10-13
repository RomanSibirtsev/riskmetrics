import type {
  Calculation,
  CalculationList,
  InstrumentList,
  MoexBond,
  MoexBondSearchResult,
  MoexRiskResult,
  LiveFeedStatus,
  LiveQuote,
} from "./types";

export interface AuthUser {
  id: string;
  email: string;
}

interface ApiErrorBody {
  error?: { code?: string; message?: string; field?: string | null };
  detail?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorBody;
    const message =
      body.error?.message ?? body.detail ?? `Ошибка API (${response.status})`;
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  me: () => request<AuthUser>("/api/v1/auth/me"),
  register: (email: string, password: string) =>
    request<AuthUser>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    request<AuthUser>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/api/v1/auth/logout", { method: "POST" }),
  instruments: () => request<InstrumentList>("/api/v1/instruments"),
  history: () =>
    request<CalculationList>("/api/v1/calculations?limit=8&offset=0"),
  calculation: (id: string) =>
    request<Calculation>(`/api/v1/calculations/${encodeURIComponent(id)}`),
  createCalculation: (trades: Array<{
    isin: string;
    position_market_value: string;
    side: "long" | "short";
  }>) =>
    request<Calculation>("/api/v1/calculations", {
      method: "POST",
      body: JSON.stringify({ trades }),
    }),
  readiness: () => request<{ status: string; database: string }>("/readyz"),
  moexInstruments: () => request<{ source: string; items: MoexBond[] }>("/api/v1/market-data/moex/instruments"),
  searchMoexBonds: (isin: string) => request<{ source: string; items: MoexBondSearchResult[] }>(`/api/v1/market-data/moex/search?isin=${encodeURIComponent(isin)}`),
  addMoexBond: (isin: string, secid: string) =>
    request<{ source: string; item: MoexBond }>("/api/v1/market-data/moex/instruments", {
      method: "POST",
      body: JSON.stringify({ isin, secid }),
    }),
  liveStatus: () => request<LiveFeedStatus>("/api/v1/market-data/live/status"),
  liveQuotes: () => request<{ source: string; items: LiveQuote[] }>("/api/v1/market-data/live/quotes"),
  calculateMoexRisk: (trades: Array<{ secid: string; position_market_value: string; side: "long" | "short" }>) =>
    request<MoexRiskResult>("/api/v1/risk/moex", {
      method: "POST",
      body: JSON.stringify({ trades, confidence: "0.99", lookback: 500 }),
    }),
};
