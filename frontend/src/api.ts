import type { User, ApiKeyInfo, AllowedUser, StockData, LLMMessage, LLMResponse, SearchResponse, PortfolioSummary, PortfolioHolding, HoldingInput, EnhancedPortfolioSummary, PortfolioTransaction, TransactionInput, DividendData, FundamentalData, PortfolioTechnicalData, Agent, AgentRun, AgentCreateInput, AgentUpdateInput, AgentStatus, PricePredictionData, StockNote, AskResponse, BulkParseResponse, BulkTransactionParseResponse, ParsedTransaction, DCFAnalysisData, DCFValuation, GuruAnalysisResponse, GuruAnalysisEntry, FinancialHealthData, LLMAnalysisResponse, BoxSpreadResponse, BoxScanResponse, DerivativeIncomeResult, DerivativeIncomePortfolioResult, DeskReviewResult, DeskAgentsResult, SingleStockLongShortResponse, PairTradeResponse, PairSuggestionsResponse, Portfolio130_30Response, ExitAnalysisData, AIImpactData, AIFortressData, AIStressTestData, RupeeData, EarningsInsight } from './types';

const API_BASE = import.meta.env.VITE_API_URL || '';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers || {}),
    },
    ...options,
  });

  if (!res.ok) {
    const body = await res.text();
    let message: string;
    try {
      const json = JSON.parse(body);
      const detail = json.detail || json.message || json.error;
      if (Array.isArray(detail) && detail.length > 0) {
        message = detail[0].msg || 'Sorry, Data not available';
      } else if (typeof detail === 'string') {
        message = detail;
      } else {
        message = detail ? 'Sorry, Data not available' : (body || `HTTP ${res.status}`);
      }
    } catch {
      message = body || `HTTP ${res.status}`;
    }
    throw new Error(message || 'Sorry, Data not available');
  }

  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// ===== Auth =====

export async function fetchMe(): Promise<User> {
  return apiFetch<User>('/api/auth/me');
}

export async function logout(): Promise<void> {
  return apiFetch<void>('/api/auth/logout', { method: 'POST' });
}

// ===== Stock Data =====

export async function fetchStockData(ticker: string): Promise<StockData> {
  return apiFetch<StockData>(`/api/stock/${encodeURIComponent(ticker)}`);
}

export interface TechnicalTimeframeResponse {
  ticker: string;
  presets: { key: string; label: string }[];
  technical: any;
}

export async function fetchTechnicalForTimeframe(
  ticker: string,
  timeframe: string,
): Promise<TechnicalTimeframeResponse> {
  return apiFetch<TechnicalTimeframeResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/technical?timeframe=${encodeURIComponent(timeframe)}`,
  );
}

// Microstructure & multi-timeframe volume profile
export async function fetchMicrostructure(
  ticker: string,
): Promise<import('./types').MicrostructureResponse> {
  return apiFetch<import('./types').MicrostructureResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/microstructure`,
  );
}

// Multi-timeframe market structure, liquidity pools & mitigation confluence
export async function fetchMarketStructure(
  ticker: string,
): Promise<import('./types').MarketStructureResponse> {
  return apiFetch<import('./types').MarketStructureResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/market-structure`,
  );
}

// Market regime & statistical extremes
export async function fetchRegime(
  ticker: string,
): Promise<import('./types').RegimeResponse> {
  return apiFetch<import('./types').RegimeResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/regime`,
  );
}

// Dealer positioning (GEX / gamma flip / expected move)
export async function fetchDealerPositioning(
  ticker: string,
): Promise<import('./types').DealerPositioningResponse> {
  return apiFetch<import('./types').DealerPositioningResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/dealer-positioning`,
  );
}

// Trade-setup engine — fused, ranked setups
export async function fetchTradeSetups(
  ticker: string,
): Promise<import('./types').TradeSetupsResponse> {
  return apiFetch<import('./types').TradeSetupsResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/trade-setups`,
  );
}

// Intraday day-trade setups — lazy (fetched only when the Day-trade style is opened).
export async function fetchDayTradeSetups(
  ticker: string,
): Promise<import('./types').DayTradeSetupsResponse> {
  return apiFetch<import('./types').DayTradeSetupsResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/day-trade-setups`,
  );
}

// Qullamäggie momentum-breakout setups — lazy (fetched only when the Qullamäggie style is opened).
export async function fetchQullamaggieSetups(
  ticker: string,
): Promise<import('./types').QullamaggieResponse> {
  return apiFetch<import('./types').QullamaggieResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/qullamaggie-setup`,
  );
}

// Connors 2-Period RSI mean-reversion setups — lazy (fetched when the Connors strategy is opened).
export async function fetchConnorsSetups(
  ticker: string,
): Promise<import('./types').ConnorsResponse> {
  return apiFetch<import('./types').ConnorsResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/connors-rsi-setup`,
  );
}

// OHLC candles for the interactive Advanced-tab charts (interval: 15m · 1h · 1d · 1wk)
export async function fetchCandles(
  ticker: string,
  interval: string = '1d',
): Promise<import('./types').CandlesResponse> {
  return apiFetch<import('./types').CandlesResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/candles?interval=${encodeURIComponent(interval)}`,
  );
}

// LLM pre-trade review of a quant setup (+ optional sentiment/fundamental/analyst)
export async function verifySetup(
  ticker: string,
  setup: Record<string, unknown>,
  dossier: Record<string, unknown>,
  enrichments: { sentiment?: boolean; fundamental?: boolean; analyst?: boolean },
): Promise<import('./types').VerifyResponse> {
  return apiFetch<import('./types').VerifyResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/verify-setup`,
    { method: 'POST', body: JSON.stringify({ setup, dossier, enrichments }) },
  );
}

// Classical chart-pattern recognition (geometry + education + measured-move targets).
export async function fetchChartPatterns(
  ticker: string,
): Promise<import('./types').ChartPatternsResponse> {
  return apiFetch<import('./types').ChartPatternsResponse>(
    `/api/stock/${encodeURIComponent(ticker)}/chart-patterns`,
  );
}
// Direct URL to the annotated PNG for one pattern (same-origin cookie auth) — for <img>/download.
export function chartPatternImageUrl(ticker: string, pattern: string): string {
  return `${API_BASE}/api/stock/${encodeURIComponent(ticker)}/chart-patterns/image?pattern=${encodeURIComponent(pattern)}`;
}

// Generic "analyze the selected TA indicators" — shared by every TA panel.
export async function analyzeTa(
  ticker: string,
  selection: Record<string, unknown>,
  messages: import('./types').MicroChatMessage[],
): Promise<import('./types').MicroChatMessage> {
  return apiFetch<import('./types').MicroChatMessage>(
    `/api/stock/${encodeURIComponent(ticker)}/ta/analyze`,
    { method: 'POST', body: JSON.stringify({ selection, messages }) },
  );
}

// ===== Trade Tracking & Management =====
export async function trackTrade(
  input: import('./types').TrackInput,
): Promise<import('./types').TrackedTrade> {
  return apiFetch<import('./types').TrackedTrade>('/api/tracked-trades', {
    method: 'POST', body: JSON.stringify(input),
  });
}
export async function listTrackedTrades(): Promise<import('./types').TrackedTradesResponse> {
  return apiFetch<import('./types').TrackedTradesResponse>('/api/tracked-trades');
}
export async function refreshTrackedTrade(id: number): Promise<import('./types').TrackRefreshResponse> {
  return apiFetch<import('./types').TrackRefreshResponse>(`/api/tracked-trades/${id}/refresh`, { method: 'POST' });
}
export async function executeTrackedTrade(
  id: number, body: { price?: number | null; qty?: number | null; note?: string | null },
): Promise<import('./types').TrackedTrade> {
  return apiFetch<import('./types').TrackedTrade>(`/api/tracked-trades/${id}/execute`, {
    method: 'POST', body: JSON.stringify(body),
  });
}
export async function closeTradeLifecycle(
  id: number, body: { price?: number | null; note?: string | null },
): Promise<import('./types').TrackedTrade> {
  return apiFetch<import('./types').TrackedTrade>(`/api/tracked-trades/${id}/close`, {
    method: 'POST', body: JSON.stringify(body),
  });
}
export async function invalidateTrackedTrade(id: number): Promise<import('./types').TrackedTrade> {
  return apiFetch<import('./types').TrackedTrade>(`/api/tracked-trades/${id}/invalidate`, { method: 'POST' });
}
export async function updateTrackedTradeNotes(id: number, user_notes: string | null): Promise<import('./types').TrackedTrade> {
  return apiFetch<import('./types').TrackedTrade>(`/api/tracked-trades/${id}/notes`, {
    method: 'PATCH', body: JSON.stringify({ user_notes }),
  });
}
export async function deleteTrackedTrade(id: number): Promise<void> {
  return apiFetch<void>(`/api/tracked-trades/${id}`, { method: 'DELETE' });
}
export async function askTrackedTradeLLM(
  id: number, body: { question?: string; refresh?: boolean },
): Promise<import('./types').TrackAdvice> {
  return apiFetch<import('./types').TrackAdvice>(`/api/tracked-trades/${id}/ask-llm`, {
    method: 'POST', body: JSON.stringify(body),
  });
}

// ===== Portfolio =====

export async function fetchPortfolioSummary(): Promise<PortfolioSummary> {
  return apiFetch<PortfolioSummary>('/api/portfolio/summary');
}

export async function fetchEnhancedPortfolioSummary(forceRefresh = false): Promise<EnhancedPortfolioSummary> {
  return apiFetch<EnhancedPortfolioSummary>(`/api/portfolio/enhanced${forceRefresh ? '?force_refresh=true' : ''}`);
}

export interface OptimizeOpts {
  forceRefresh?: boolean;
  maxWeight?: number | null;        // per-name cap, fraction 0..1
  sectorMax?: number | null;        // per-sector cap, fraction 0..1
  transactionCostBps?: number;      // turnover penalty vs. current book
  l2Gamma?: number;                 // L2 weight-dispersion penalty
}

export async function fetchAgentDebate(ticker: string): Promise<import('./types').AgentDebateResult> {
  return apiFetch<import('./types').AgentDebateResult>(`/api/stock/${encodeURIComponent(ticker)}/debate`);
}

export async function generateAgentDebate(
  ticker: string,
  dcfOverride?: DcfScenarioParams | null,
): Promise<import('./types').AgentDebateResult> {
  return apiFetch<import('./types').AgentDebateResult>(
    `/api/stock/${encodeURIComponent(ticker)}/debate/generate`,
    { method: 'POST', ...(dcfOverride ? { body: JSON.stringify(dcfOverride) } : {}) },
  );
}

export async function continueAgentDebate(
  ticker: string,
  userInput: string,
): Promise<import('./types').AgentDebateResult> {
  return apiFetch<import('./types').AgentDebateResult>(
    `/api/stock/${encodeURIComponent(ticker)}/debate/continue`,
    { method: 'POST', body: JSON.stringify({ user_input: userInput }) },
  );
}

export async function saveAgentDebate(ticker: string): Promise<import('./types').AgentDebateResult> {
  return apiFetch<import('./types').AgentDebateResult>(
    `/api/stock/${encodeURIComponent(ticker)}/debate/save`, { method: 'POST' });
}

export async function clearAgentDebate(ticker: string, allHistory = false): Promise<{ cleared: boolean }> {
  return apiFetch<{ cleared: boolean }>(
    `/api/stock/${encodeURIComponent(ticker)}/debate${allHistory ? '?all_history=true' : ''}`,
    { method: 'DELETE' });
}

export interface DebateHistoryItem {
  id: number;
  created_at: string;
  saved: boolean;
  view_return: number | null;
  base_confidence: number | null;
  rounds: number | null;
}

export async function fetchDebateHistory(ticker: string): Promise<{ items: DebateHistoryItem[] }> {
  return apiFetch<{ items: DebateHistoryItem[] }>(`/api/stock/${encodeURIComponent(ticker)}/debate/history`);
}

export async function fetchDebateItem(ticker: string, id: number): Promise<import('./types').AgentDebateResult> {
  return apiFetch<import('./types').AgentDebateResult>(`/api/stock/${encodeURIComponent(ticker)}/debate/item/${id}`);
}

export async function fetchPortfolioOptimization(
  opts: OptimizeOpts = {},
): Promise<import('./types').PortfolioOptimization> {
  const p = new URLSearchParams();
  if (opts.forceRefresh) p.set('force_refresh', 'true');
  if (opts.maxWeight != null) p.set('max_weight', String(opts.maxWeight));
  if (opts.sectorMax != null) p.set('sector_max', String(opts.sectorMax));
  if (opts.transactionCostBps) p.set('transaction_cost_bps', String(opts.transactionCostBps));
  if (opts.l2Gamma) p.set('l2_gamma', String(opts.l2Gamma));
  const q = p.toString();
  return apiFetch<import('./types').PortfolioOptimization>(`/api/portfolio/optimize${q ? `?${q}` : ''}`);
}

export async function addHolding(data: HoldingInput): Promise<PortfolioHolding> {
  return apiFetch<PortfolioHolding>('/api/portfolio/holdings', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function deleteHolding(holdingId: number): Promise<void> {
  return apiFetch<void>(`/api/portfolio/holdings/${holdingId}`, {
    method: 'DELETE',
  });
}

export async function updateHolding(
  holdingId: number,
  data: Partial<HoldingInput>
): Promise<PortfolioHolding> {
  return apiFetch<PortfolioHolding>(`/api/portfolio/holdings/${holdingId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function addTransaction(data: TransactionInput): Promise<PortfolioTransaction> {
  return apiFetch<PortfolioTransaction>('/api/portfolio/transactions', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchTransactions(ticker?: string): Promise<PortfolioTransaction[]> {
  const url = ticker ? `/api/portfolio/transactions?ticker=${encodeURIComponent(ticker)}` : '/api/portfolio/transactions';
  return apiFetch<PortfolioTransaction[]>(url);
}

export async function deleteTransaction(txnId: number): Promise<void> {
  return apiFetch<void>(`/api/portfolio/transactions/${txnId}`, { method: 'DELETE' });
}

export async function analyzeHolding(
  ticker: string,
  history: { role: string; content: string }[],
  question: string | null,
  holdingContext: Record<string, unknown>,
): Promise<{ content: string; role: string }> {
  return apiFetch<{ content: string; role: string }>(`/api/portfolio/holdings/${encodeURIComponent(ticker)}/analyze`, {
    method: 'POST',
    body: JSON.stringify({ question, history, holding_context: holdingContext }),
  });
}

export async function fetchDividendView(forceRefresh = false): Promise<DividendData[]> {
  return apiFetch<DividendData[]>(`/api/portfolio/dividends${forceRefresh ? '?force_refresh=true' : ''}`);
}

export async function fetchFundamentalView(forceRefresh = false): Promise<FundamentalData[]> {
  return apiFetch<FundamentalData[]>(`/api/portfolio/fundamentals${forceRefresh ? '?force_refresh=true' : ''}`);
}

export async function fetchTechnicalView(forceRefresh = false): Promise<PortfolioTechnicalData[]> {
  return apiFetch<PortfolioTechnicalData[]>(`/api/portfolio/technical${forceRefresh ? '?force_refresh=true' : ''}`);
}

export interface SectorMeta { ticker: string; sector: string; industry: string; company_name: string; asset_type: string; fund_category?: string; }
export async function fetchSectors(): Promise<SectorMeta[]> {
  return apiFetch<SectorMeta[]>('/api/portfolio/sectors');
}

export async function refreshTicker(ticker: string): Promise<void> {
  return apiFetch<void>(`/api/portfolio/refresh/${encodeURIComponent(ticker)}`, { method: 'POST' });
}

export async function refreshAllEnrichment(): Promise<void> {
  return apiFetch<void>('/api/portfolio/refresh-all', { method: 'POST' });
}

export async function fetchPortfolioEvents(daysAhead = 45): Promise<import('./types').PortfolioEvent[]> {
  return apiFetch<import('./types').PortfolioEvent[]>(`/api/portfolio/events?days_ahead=${daysAhead}`);
}

export async function fetchPortfolioBrief(forceRefresh = false): Promise<import('./types').PortfolioBrief> {
  return apiFetch<import('./types').PortfolioBrief>(`/api/portfolio/brief${forceRefresh ? '?force_refresh=true' : ''}`);
}

export async function portfolioCopilot(
  question: string | null,
  history: { role: string; content: string }[],
): Promise<{ content: string; role: string }> {
  return apiFetch<{ content: string; role: string }>('/api/portfolio/copilot', {
    method: 'POST',
    body: JSON.stringify({ question, history }),
  });
}

export async function bulkDeleteHoldings(holdingIds: number[]): Promise<{ ok: boolean; deleted: number }> {
  return apiFetch<{ ok: boolean; deleted: number }>('/api/portfolio/holdings/bulk-delete', {
    method: 'POST',
    body: JSON.stringify({ holding_ids: holdingIds }),
  });
}

// ===== Settings / API Keys =====

export async function fetchApiKeys(): Promise<ApiKeyInfo[]> {
  return apiFetch<ApiKeyInfo[]>('/api/settings/keys');
}

export async function saveApiKey(keyName: string, value: string): Promise<void> {
  return apiFetch<void>('/api/settings/keys', {
    method: 'POST',
    body: JSON.stringify({ key_name: keyName, value }),
  });
}

export async function deleteApiKey(keyName: string): Promise<void> {
  return apiFetch<void>(`/api/settings/keys/${encodeURIComponent(keyName)}`, {
    method: 'DELETE',
  });
}

// ===== Allowlist (Admin Only) =====

export async function fetchAllowlist(): Promise<AllowedUser[]> {
  return apiFetch<AllowedUser[]>('/api/settings/allowlist');
}

export async function addAllowedUser(email: string): Promise<AllowedUser> {
  return apiFetch<AllowedUser>('/api/settings/allowlist', {
    method: 'POST',
    body: JSON.stringify({ email }),
  });
}

export async function setUserPremium(email: string, is_premium: boolean): Promise<AllowedUser> {
  return apiFetch<AllowedUser>(`/api/settings/allowlist/${encodeURIComponent(email)}/premium`, {
    method: 'PATCH',
    body: JSON.stringify({ is_premium }),
  });
}

export async function removeAllowedUser(email: string): Promise<{ ok: boolean; deleted: string }> {
  return apiFetch<{ ok: boolean; deleted: string }>(`/api/settings/allowlist/${encodeURIComponent(email)}`, {
    method: 'DELETE',
  });
}

// ===== Models & Features =====

export async function fetchModels(): Promise<{ models: { id: string; label: string }[]; default: string }> {
  return apiFetch('/api/settings/models');
}

export async function fetchSelectedModel(): Promise<{ model: string }> {
  return apiFetch('/api/settings/model');
}

export async function setSelectedModel(model: string): Promise<{ model: string }> {
  return apiFetch('/api/settings/model', {
    method: 'PUT',
    body: JSON.stringify({ model }),
  });
}

export async function saveWhatsApp(whatsapp_number: string | null): Promise<{ whatsapp_number: string | null }> {
  return apiFetch('/api/settings/whatsapp', {
    method: 'POST',
    body: JSON.stringify({ whatsapp_number }),
  });
}

export async function fetchWhatsAppStatus(): Promise<{ ready: boolean; error?: string }> {
  return apiFetch('/api/channels/whatsapp/status');
}

export async function fetchWhatsAppQR(): Promise<{ qr: string | null; status: string; error?: string }> {
  return apiFetch('/api/channels/whatsapp/qr');
}

// ===== Proxy: LLM =====

export async function callLLM(
  model: string,
  messages: LLMMessage[],
  maxTokens?: number,
  expectJson?: boolean
): Promise<LLMResponse> {
  return apiFetch<LLMResponse>('/api/proxy/llm', {
    method: 'POST',
    body: JSON.stringify({ model, messages, max_tokens: maxTokens, expect_json: expectJson }),
  });
}

// ===== Proxy: Web Search =====

export async function searchWeb(query: string): Promise<SearchResponse> {
  return apiFetch<SearchResponse>('/api/proxy/search', {
    method: 'POST',
    body: JSON.stringify({ query }),
  });
}

// ===== Agents =====

export async function createAgent(data: AgentCreateInput): Promise<Agent> {
  return apiFetch<Agent>('/api/agents', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchAgents(): Promise<Agent[]> {
  return apiFetch<Agent[]>('/api/agents');
}

export async function fetchCommunityAgents(): Promise<Agent[]> {
  return apiFetch<Agent[]>('/api/agents/community');
}

export async function fetchAgent(agentId: number): Promise<Agent> {
  return apiFetch<Agent>(`/api/agents/${agentId}`);
}

export async function updateAgent(agentId: number, data: AgentUpdateInput): Promise<Agent> {
  return apiFetch<Agent>(`/api/agents/${agentId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function updateAgentStatus(agentId: number, status: AgentStatus): Promise<Agent> {
  return apiFetch<Agent>(`/api/agents/${agentId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

export async function deleteAgent(agentId: number): Promise<void> {
  return apiFetch<void>(`/api/agents/${agentId}`, { method: 'DELETE' });
}

export async function triggerAgentRun(agentId: number): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agents/${agentId}/run`, { method: 'POST' });
}

export async function fetchAgentRuns(agentId: number): Promise<AgentRun[]> {
  return apiFetch<AgentRun[]>(`/api/agents/${agentId}/runs`);
}

export async function fetchAgentRun(agentId: number, runId: number): Promise<AgentRun> {
  return apiFetch<AgentRun>(`/api/agents/${agentId}/runs/${runId}`);
}

export async function cloneAgent(agentId: number): Promise<Agent> {
  return apiFetch<Agent>(`/api/agents/${agentId}/clone`, { method: 'POST' });
}

// ===== Price Prediction =====

export async function fetchPricePrediction(ticker: string, horizon: number = 30): Promise<PricePredictionData> {
  return apiFetch<PricePredictionData>(`/api/stock/${encodeURIComponent(ticker)}/prediction?horizon=${horizon}`);
}

// ===== Stock Notes / Chat =====

export async function fetchStockNotes(ticker: string): Promise<StockNote[]> {
  return apiFetch<StockNote[]>(`/api/stock/${encodeURIComponent(ticker)}/notes`);
}

export async function askStockQuestion(ticker: string, question: string): Promise<AskResponse> {
  return apiFetch<AskResponse>(`/api/stock/${encodeURIComponent(ticker)}/notes/ask`, {
    method: 'POST',
    body: JSON.stringify({ question }),
  });
}

// ===== Bulk Portfolio Import =====

export async function bulkParseHoldings(text: string): Promise<BulkParseResponse> {
  return apiFetch<BulkParseResponse>('/api/portfolio/holdings/bulk-parse', {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

export async function bulkSaveHoldings(holdings: HoldingInput[]): Promise<{ ok: boolean; count: number }> {
  return apiFetch<{ ok: boolean; count: number }>('/api/portfolio/holdings/bulk-save', {
    method: 'POST',
    body: JSON.stringify({ holdings }),
  });
}

export async function bulkParseTransactions(text: string, ticker?: string): Promise<BulkTransactionParseResponse> {
  return apiFetch<BulkTransactionParseResponse>('/api/portfolio/transactions/bulk-parse', {
    method: 'POST',
    body: JSON.stringify({ text, ticker }),
  });
}

export async function bulkSaveTransactions(transactions: ParsedTransaction[]): Promise<{ ok: boolean; count: number }> {
  return apiFetch<{ ok: boolean; count: number }>('/api/portfolio/transactions/bulk-save', {
    method: 'POST',
    body: JSON.stringify({ transactions }),
  });
}

// ===== Email Notifications =====

export async function fetchEmailNotifications(): Promise<{ enabled: boolean; email: string }> {
  return apiFetch<{ enabled: boolean; email: string }>('/api/settings/email-notifications');
}

export async function setEmailNotifications(enabled: boolean): Promise<{ enabled: boolean }> {
  return apiFetch<{ enabled: boolean }>('/api/settings/email-notifications', {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}

// ===== Display Preferences =====

export async function fetchDisplayPreferences(): Promise<{ show_ai_sections: boolean }> {
  return apiFetch<{ show_ai_sections: boolean }>('/api/settings/display-preferences');
}

export async function setDisplayPreferences(showAiSections: boolean): Promise<{ show_ai_sections: boolean }> {
  return apiFetch<{ show_ai_sections: boolean }>('/api/settings/display-preferences', {
    method: 'PUT',
    body: JSON.stringify({ show_ai_sections: showAiSections }),
  });
}

// ===== DCF Analysis =====

export async function fetchDCFAnalysis(ticker: string): Promise<DCFAnalysisData> {
  return apiFetch<DCFAnalysisData>(`/api/stock/${encodeURIComponent(ticker)}/dcf`);
}

export interface DcfScenarioParams {
  base_fcf: number;
  stage1_growth: number;   // %
  cap_years: number;
  discount_rate: number;   // % WACC
  terminal_growth: number; // %
  exit_multiple: number;
  shares_outstanding: number;
  net_debt: number;
  current_price: number;
}

export async function computeDCFScenario(
  ticker: string,
  params: DcfScenarioParams,
): Promise<DCFValuation> {
  return apiFetch<DCFValuation>(`/api/stock/${encodeURIComponent(ticker)}/dcf/scenario`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// ===== Guru Analysis =====

export async function fetchGuruAnalyses(ticker: string): Promise<GuruAnalysisResponse> {
  return apiFetch<GuruAnalysisResponse>(`/api/stock/${encodeURIComponent(ticker)}/gurus`);
}

export async function refreshGuruAnalysis(ticker: string, guruId: string): Promise<GuruAnalysisEntry> {
  return apiFetch<GuruAnalysisEntry>(`/api/stock/${encodeURIComponent(ticker)}/gurus/${encodeURIComponent(guruId)}/refresh`, {
    method: 'POST',
  });
}

export async function refreshAllGurus(ticker: string): Promise<{ ticker: string; results: Record<string, GuruAnalysisEntry> }> {
  return apiFetch<{ ticker: string; results: Record<string, GuruAnalysisEntry> }>(`/api/stock/${encodeURIComponent(ticker)}/gurus/refresh-all`, {
    method: 'POST',
  });
}

// ===== Financial Health =====

export async function fetchFinancialHealth(ticker: string): Promise<FinancialHealthData> {
  return apiFetch<FinancialHealthData>(`/api/stock/${encodeURIComponent(ticker)}/financial-health`);
}

// ===== Earnings-report Insight (EDGAR + LLM) =====

export async function fetchEarningsInsights(ticker: string): Promise<EarningsInsight> {
  return apiFetch<EarningsInsight>(`/api/stock/${encodeURIComponent(ticker)}/earnings-insights`);
}

export async function generateEarningsInsights(ticker: string): Promise<EarningsInsight> {
  return apiFetch<EarningsInsight>(`/api/stock/${encodeURIComponent(ticker)}/earnings-insights/generate`, {
    method: 'POST',
  });
}

// ===== Qualitative Analysis =====

export async function fetchQualitativeAnalysis(ticker: string): Promise<LLMAnalysisResponse> {
  return apiFetch<LLMAnalysisResponse>(`/api/stock/${encodeURIComponent(ticker)}/qualitative`);
}

export async function generateQualitativeAnalysis(ticker: string): Promise<LLMAnalysisResponse> {
  return apiFetch<LLMAnalysisResponse>(`/api/stock/${encodeURIComponent(ticker)}/qualitative/generate`, {
    method: 'POST',
  });
}

// ===== Industry & Macro Analysis =====

export async function fetchMacroAnalysis(ticker: string): Promise<LLMAnalysisResponse> {
  return apiFetch<LLMAnalysisResponse>(`/api/stock/${encodeURIComponent(ticker)}/macro`);
}

export async function generateMacroAnalysis(ticker: string): Promise<LLMAnalysisResponse> {
  return apiFetch<LLMAnalysisResponse>(`/api/stock/${encodeURIComponent(ticker)}/macro/generate`, {
    method: 'POST',
  });
}

// ===== Fund / ETF Details =====

export async function fetchFundDetails(ticker: string): Promise<import('./types').FundDetails> {
  return apiFetch<import('./types').FundDetails>(`/api/stock/${encodeURIComponent(ticker)}/fund-details`);
}

export async function fetchFundManagerBrief(ticker: string): Promise<import('./types').FundManagerBrief> {
  return apiFetch<import('./types').FundManagerBrief>(`/api/stock/${encodeURIComponent(ticker)}/fund-manager-brief`);
}

export async function generateFundManagerBrief(ticker: string): Promise<import('./types').FundManagerBrief> {
  return apiFetch<import('./types').FundManagerBrief>(`/api/stock/${encodeURIComponent(ticker)}/fund-manager-brief/generate`, {
    method: 'POST',
  });
}

// ===== Options Expirations =====

export async function fetchOptionExpirations(ticker: string): Promise<{ expirations: string[] }> {
  return apiFetch<{ expirations: string[] }>(`/api/stock/options-expirations/${encodeURIComponent(ticker)}`);
}

// ===== Strategies =====

export async function computeBoxSpread(params: {
  ticker: string;
  amount: number;
  duration_days: number;
  target_annual_return: number;
  intent: 'lend' | 'borrow';
  quote_source?: string;
  target_expiration?: string;
  max_contracts?: number;
}): Promise<BoxSpreadResponse> {
  return apiFetch<BoxSpreadResponse>(`/api/stock/${encodeURIComponent(params.ticker)}/strategies/box-spread`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function scanBoxOpportunities(params: {
  intent: 'lend' | 'borrow';
  duration_days: number;
  target_annual_return: number;
  amount?: number;
  tickers?: string[];
  max_contracts?: number;
  per_ticker?: number;
  quote_source?: string;
}): Promise<BoxScanResponse> {
  return apiFetch<BoxScanResponse>('/api/stock/strategies/box-spread/scan', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// ===== Derivative Income =====

export async function runDerivativeIncome(ticker: string, params: {
  target_dte?: number | null;
  target_expiration?: string | null;
  min_prob?: number;
  min_income?: number;
  structures?: string[];
  quote_source?: string;
}): Promise<DerivativeIncomeResult> {
  return apiFetch<DerivativeIncomeResult>(`/api/stock/${encodeURIComponent(ticker)}/strategies/derivative-income`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function runDeskMonitor(ticker: string, trade: {
  structure: string; put_short?: number | null; call_short?: number | null;
  credit?: number; spot?: number | null; dte?: number;
}): Promise<import('./types').MonitorPlan> {
  return apiFetch<import('./types').MonitorPlan>(
    `/api/stock/${encodeURIComponent(ticker)}/desk-review/monitor`,
    { method: 'POST', body: JSON.stringify(trade) },
  );
}

export async function runDeskMonitorAnalyze(ticker: string, trade: {
  structure: string; put_short?: number | null; call_short?: number | null;
  credit?: number; spot?: number | null; dte?: number; model?: string;
}): Promise<{ content?: string; error?: string; levels_found?: number }> {
  return apiFetch(`/api/stock/${encodeURIComponent(ticker)}/desk-review/monitor/analyze`,
    { method: 'POST', body: JSON.stringify(trade) });
}

export type DerivativeIncomeWatchlistItem = {
  ticker: string;
  current_price: number | null;
  today_pct: number | null;
  week52_low: number | null;
  week52_high: number | null;
  atm_iv: number | null;
  hv30: number | null;
};

export async function fetchDerivativeIncomeWatchlist(refresh = false): Promise<DerivativeIncomeWatchlistItem[]> {
  return apiFetch(`/api/stock/strategies/derivative-income/watchlist?refresh=${refresh}`);
}

export async function addDerivativeIncomeWatchlist(ticker: string): Promise<void> {
  return apiFetch(`/api/stock/strategies/derivative-income/watchlist`, {
    method: 'POST',
    body: JSON.stringify({ ticker }),
  });
}

export async function deleteDerivativeIncomeWatchlist(ticker: string): Promise<void> {
  return apiFetch(`/api/stock/strategies/derivative-income/watchlist/${encodeURIComponent(ticker)}`, {
    method: 'DELETE',
  });
}

export async function runDerivativeIncomePortfolio(params: {
  offset?: number;
  limit?: number;
  target_dte?: number | null;
  min_prob?: number;
  min_income?: number;
  structures?: string[];
  quote_source?: string;
}): Promise<DerivativeIncomePortfolioResult> {
  return apiFetch<DerivativeIncomePortfolioResult>('/api/stock/strategies/derivative-income/portfolio', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// ===== Desk Review =====

export type DeskReviewParams = {
  target_dte?: number | null;
  target_expiration?: string | null;
  min_prob?: number;
  min_income?: number;
  structures?: string[];
  quote_source?: string;
  owns_underlying?: boolean;   // already hold the shares → covered calls scored as an income overlay
  earnings_aware?: boolean;    // discount walls the earnings gap can leap + penalise strikes inside ~1.5× the event move
};

export async function runDeskReview(ticker: string, params: DeskReviewParams): Promise<DeskReviewResult> {
  return apiFetch<DeskReviewResult>(`/api/stock/${encodeURIComponent(ticker)}/desk-review`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export type DeskFocusTrade = { structure: string; expiration?: string | null; short_strike?: number | null };

// Bring-your-own trade: the user's exact legs (+ optional stock) to evaluate on the desk.
export type EvaluateLeg = { action: 'BUY' | 'SELL'; type: 'CALL' | 'PUT'; strike: number; expiration: string };
export type DeskEvaluateParams = {
  legs: EvaluateLeg[];
  quote_source?: string;
  owns_underlying?: boolean;
  earnings_aware?: boolean;
};

export async function runDeskReviewAgents(
  ticker: string,
  params: DeskReviewParams & { model?: string; focus?: DeskFocusTrade; evaluate?: DeskEvaluateParams },
): Promise<DeskAgentsResult> {
  return apiFetch<DeskAgentsResult>(`/api/stock/${encodeURIComponent(ticker)}/desk-review/agents`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// Independent LLM second opinion on ONE trade — BLIND to our score/grade (un-anchored). Decision + cited
// factor calls (no fuzzy rating) + the divergence vs the rule grade.
export async function runDeskBlind(
  ticker: string,
  params: DeskReviewParams & { model?: string; focus?: DeskFocusTrade },
): Promise<import('./types').BlindRead> {
  return apiFetch<import('./types').BlindRead>(`/api/stock/${encodeURIComponent(ticker)}/desk-review/blind`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// Market sentiment — recent news + StockTwits crowd chatter distilled (earnings-focused) into an actionable
// read for the user's specific trade. NOT part of the grade — qualitative colour to build conviction.
export type SentimentTrade = {
  structure?: string; label?: string; short_strike?: number | null; expiration?: string | null;
  spot?: number | null; next_earnings?: string | null; earnings_gap_pct?: number | null; dte?: number | null;
};
export type MarketSentimentResult = {
  ticker: string;
  read?: {
    sentiment?: string; strength?: string; summary?: string; earnings?: string | null;
    price_levels?: string | null; trade_implication?: string; catalysts?: string[];
    confidence?: string; caveat?: string;
  };
  sources?: {
    stocktwits?: { count?: number; url?: string };
    news?: { title: string; publisher?: string; link?: string }[];
  };
  model?: string;
  error?: string;
};
export async function fetchMarketSentiment(ticker: string, trade: SentimentTrade, model?: string): Promise<MarketSentimentResult> {
  return apiFetch<MarketSentimentResult>(`/api/stock/${encodeURIComponent(ticker)}/desk-review/sentiment`, {
    method: 'POST',
    body: JSON.stringify({ trade, model }),
  });
}

// Book Exposure — DETERMINISTIC portfolio-risk read (NO LLM) on how a NEW trade changes the user's
// EXISTING active book (My Trades, first tab): same-name concentration, $ exposure across market moves
// (book vs book+trade), BPR/assignment, β-weighted directional delta, measured 1y correlations, macro
// sensitivity. Reuses the Manage-Book engine. Additional risk context, NOT part of the grade.
export type BookExposureTrade = {
  structure?: string; label?: string; short_strike?: number | null; long_strike?: number | null;
  expiration?: string | null; spot?: number | null; dte?: number | null; contracts?: number | null;
  legs?: { action?: string; type?: string; strike?: number; expiration?: string | null; iv?: number | null; qty?: number }[];
};
type ExpScenarioRow = { move_pct: number; book_pnl: number; with_pnl: number; delta_pnl: number };
export type BookExposureResult = {
  ticker: string;
  empty_book?: boolean;
  error?: string;
  verdict?: 'concentrates' | 'diversifies' | 'neutral';
  headline?: string;
  key_points?: { tone: 'bad' | 'warn' | 'good' | 'info'; text: string }[];
  recommendation?: string;
  market_move?: {
    rows?: ExpScenarioRow[];
    down20?: ExpScenarioRow | null;
    up20?: ExpScenarioRow | null;
    direction_plain?: string;
  };
  same_name?: {
    ticker: string; count: number;
    existing: { structure?: string; n_short?: number; capital?: number }[];
    posture?: string; downside_outlay?: number | null; upside_unbounded?: boolean;
    highest_put?: number | null; lowest_naked_call?: number | null; same_expiry?: boolean;
    n_short_puts?: number; n_short_calls?: number;
  } | null;
  theme_overlap?: { key: string; theme: string; driver: string; bellwether?: string; note?: string | null; book_tickers: string[] }[];
  sector_overlap?: { sector: string; book_tickers: string[]; capital: number } | null;
  related_earnings?: { bellwether: string; theme: string; date: string; days_out: number; driver: string }[];
  candidate_profile?: { sector?: string; industry?: string; themes?: string[]; macro_factors?: string[] };
  correlations?: { ticker: string; rho: number; cluster?: string | null }[];
  macro?: { factor: string; candidate_rho?: number; book_rho?: number; plain?: string }[];
  direction?: { book_per_pct?: number; with_per_pct?: number; trade_per_pct?: number; lean?: string };
  capital?: { book_bpr?: number; candidate_bpr?: number };
  book?: { position_count?: number; names?: string[] };
  candidate?: { ticker?: string; structure?: string; bpr?: number };
};
export async function fetchBookExposure(ticker: string, trade: BookExposureTrade, quoteSource?: string): Promise<BookExposureResult> {
  return apiFetch<BookExposureResult>(`/api/stock/${encodeURIComponent(ticker)}/desk-review/exposure`, {
    method: 'POST',
    body: JSON.stringify({ trade, quote_source: quoteSource }),
  });
}

// Evaluate a user-entered multi-leg trade — returns the same DeskReviewResult shape as the
// single-ticker scan (chrome + ranked=[the one trade]) so the UI renders it identically.
export async function evaluateDeskTrade(ticker: string, params: DeskEvaluateParams): Promise<DeskReviewResult> {
  return apiFetch<DeskReviewResult>(`/api/stock/${encodeURIComponent(ticker)}/desk-review/evaluate`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function computeHedging(params: {
  ticker: string;
  shares: number;
  horizon_days?: number;
  protection_pct?: number;
  upside_pct?: number;
  downside_buffer?: number;
  upside_giveup?: number;
  downside_cap?: number;
  max_cost_pct?: number;
  hedge_ratio?: number;
  benchmark?: string;
  quote_source?: string;
  target_expiration?: string;
}): Promise<import('./types').HedgingResponse> {
  return apiFetch<import('./types').HedgingResponse>(`/api/stock/${encodeURIComponent(params.ticker)}/strategies/hedging`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function getHedgingMarketCheck(
  ticker: string,
  benchmark = 'SPY',
  horizon_days = 45,
): Promise<import('./types').MarketConditions & { spot?: number; atm_iv?: number | null }> {
  return apiFetch(`/api/stock/${encodeURIComponent(ticker)}/strategies/hedging/market-check?benchmark=${benchmark}&horizon_days=${horizon_days}`);
}

export async function getHedgingPriceHistory(
  ticker: string,
  days = 90,
): Promise<import('./types').HedgePriceHistory> {
  return apiFetch(`/api/stock/${encodeURIComponent(ticker)}/strategies/hedging/price-history?days=${days}`);
}

export async function computeCppiStrategy(params: {
  ticker: string;
  amount: number;
  floor_pct: number;
  multiplier: number;
  duration_years: number;
  rebalance_freq: string;
  rebalance_threshold_pct: number;
  risk_free_rate: number;
  transaction_fee_pct: number;
  allow_leverage: boolean;
  dynamic_multiplier: boolean;
}): Promise<any> {
  return apiFetch<any>(`/api/stock/${encodeURIComponent(params.ticker)}/strategies/cppi`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function getSmartPrice(params: {
  legs: any[];
  dte: number;
  box_width: number;
  intent: string;
}): Promise<import('./types').SmartPriceResponse> {
  return apiFetch<import('./types').SmartPriceResponse>('/api/stock/strategies/smart-price', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function previewBrokerOrder(params: {
  ticker: string;
  strategy: string;
  legs: import('./types').BrokerOrderLeg[];
}): Promise<import('./types').OrderPreviewResponse> {
  return apiFetch<import('./types').OrderPreviewResponse>('/api/broker/order/preview', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function computeDualDirectionBuffer(params: {
  ticker: string;
  amount: number;
  duration_days: number;
  downside_buffer_pct: number;
  upside_cap_pct: number;
  target_expiration?: string;
  entry_cost_mode?: string;
}): Promise<any> {
  return apiFetch<any>(`/api/stock/${encodeURIComponent(params.ticker)}/strategies/dual-direction-buffer`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function computeDualDirectionBufferIBKR(params: {
  ticker: string;
  amount: number;
  duration_days: number;
  downside_buffer_pct: number;
  upside_cap_pct: number;
  target_expiration?: string;
  entry_cost_mode?: string;
}): Promise<any> {
  return apiFetch<any>('/api/broker/strategy/dual-direction-buffer', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function fetchOptionQuote(params: {
  ticker: string;
  expiration: string;
  strike: number;
  right: string;
  quote_source?: string;
}): Promise<{ strike: number; right: string; expiration: string; bid: number; ask: number; mid: number; last: number; iv: number; oi: number; volume: number }> {
  return apiFetch('/api/broker/option-quote', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export interface OptionQuoteRequest {
  ticker: string;
  expiration: string;
  strike: number;
  right: string;
  quote_source?: string;
}

export async function fetchOptionQuotesBatch(
  requests: OptionQuoteRequest[]
): Promise<Array<{ strike: number; right: string; expiration: string; bid: number; ask: number; mid: number; last: number; iv: number; oi: number; volume: number; error?: string }>> {
  return apiFetch('/api/broker/option-quotes-batch', {
    method: 'POST',
    body: JSON.stringify(requests),
  });
}

export async function computeConcentration(params: {
  ticker: string;
  shares: number;
  cost_basis: number;
  position_type: 'long' | 'short';
  tax_rate_pct: number;
  duration_days: number;
}): Promise<any> {
  return apiFetch<any>(`/api/stock/${encodeURIComponent(params.ticker)}/strategies/concentration`, {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function computeSingleStockLongShort(
  ticker: string,
  investmentAmount: number,
  hedgeType: string = 'sector_etf'
): Promise<SingleStockLongShortResponse> {
  return apiFetch<SingleStockLongShortResponse>(`/api/stock/${encodeURIComponent(ticker)}/strategies/long-short`, {
    method: 'POST',
    body: JSON.stringify({ investment_amount: investmentAmount, hedge_type: hedgeType }),
  });
}

export async function computePairTrade(
  longTicker: string,
  shortTicker: string,
  investmentAmount: number
): Promise<PairTradeResponse> {
  return apiFetch<PairTradeResponse>('/api/stock/strategies/pair-trade', {
    method: 'POST',
    body: JSON.stringify({ long_ticker: longTicker, short_ticker: shortTicker, investment_amount: investmentAmount }),
  });
}

export async function fetchPairSuggestions(
  ticker: string
): Promise<PairSuggestionsResponse> {
  return apiFetch<PairSuggestionsResponse>(`/api/stock/${encodeURIComponent(ticker)}/strategies/pair-suggestions`);
}

export interface Build130_30Params {
  long_positions: { ticker: string; shares?: number | null }[];
  short_positions: { ticker: string; shares?: number | null }[];
  investment_amount: number;
  leverage_ratio: string;
  tax_rate_st: number;
  tax_rate_lt: number;
}

export async function build130_30Portfolio(
  params: Build130_30Params
): Promise<Portfolio130_30Response> {
  return apiFetch<Portfolio130_30Response>('/api/stock/strategies/130-30', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// AI narrative for a long/short portfolio — separate, opt-in LLM action.
export async function build130_30Insights(
  params: Build130_30Params
): Promise<{ llm_insights: string | null }> {
  return apiFetch<{ llm_insights: string | null }>('/api/stock/strategies/130-30/insights', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function fetchExitAnalysis(ticker: string): Promise<ExitAnalysisData> {
  return apiFetch<ExitAnalysisData>(`/api/stock/${encodeURIComponent(ticker)}/exit-analysis`, {
    method: 'POST',
  });
}

// ===== AI Impact, Fortress, and Stress Tests =====

export async function fetchAIImpact(ticker: string): Promise<AIImpactData> {
  return apiFetch<AIImpactData>(`/api/stock/${encodeURIComponent(ticker)}/ai-impact`);
}

export async function generateAIImpact(ticker: string): Promise<AIImpactData> {
  return apiFetch<AIImpactData>(`/api/stock/${encodeURIComponent(ticker)}/ai-impact/generate`, {
    method: 'POST',
  });
}

export async function fetchAIFortress(ticker: string): Promise<AIFortressData> {
  return apiFetch<AIFortressData>(`/api/stock/${encodeURIComponent(ticker)}/ai-fortress`);
}

export async function generateAIFortress(ticker: string): Promise<AIFortressData> {
  return apiFetch<AIFortressData>(`/api/stock/${encodeURIComponent(ticker)}/ai-fortress/generate`, {
    method: 'POST',
  });
}

export async function fetchAIStressTest(ticker: string): Promise<AIStressTestData> {
  return apiFetch<AIStressTestData>(`/api/stock/${encodeURIComponent(ticker)}/ai-stress-test`);
}

export async function generateAIStressTest(ticker: string): Promise<AIStressTestData> {
  return apiFetch<AIStressTestData>(`/api/stock/${encodeURIComponent(ticker)}/ai-stress-test/generate`, {
    method: 'POST',
  });
}

export async function fetchRupee(ticker: string): Promise<RupeeData> {
  return apiFetch<RupeeData>(`/api/stock/${encodeURIComponent(ticker)}/rupee`);
}

export async function generateRupee(ticker: string): Promise<RupeeData> {
  return apiFetch<RupeeData>(`/api/stock/${encodeURIComponent(ticker)}/rupee/generate`, {
    method: 'POST',
  });
}

// ===== TLH Saved Portfolios =====

export interface TLHSavedPortfolioItem {
  id: number;
  name: string;
  tax_rate_pct: number;
  holding_count: number;
  tickers: string[];
  holdings: { ticker: string; shares: number; cost_basis: number }[];
  created_at: string;
}

export interface TLHSavedPortfolioFull {
  id: number;
  name: string;
  tax_rate_pct: number;
  holdings: { ticker: string; shares: number; cost_basis: number }[];
  holding_count: number;
  tickers: string[];
  created_at: string;
}

export async function fetchTLHPortfolios(): Promise<TLHSavedPortfolioItem[]> {
  return apiFetch<TLHSavedPortfolioItem[]>('/api/tlh-portfolios');
}

export async function saveTLHPortfolio(
  name: string,
  taxRatePct: number,
  holdings: { ticker: string; shares: number; cost_basis: number }[],
): Promise<TLHSavedPortfolioFull> {
  return apiFetch<TLHSavedPortfolioFull>('/api/tlh-portfolios', {
    method: 'POST',
    body: JSON.stringify({ name, tax_rate_pct: taxRatePct, holdings }),
  });
}

export async function deleteTLHPortfolio(id: number): Promise<void> {
  await apiFetch<void>(`/api/tlh-portfolios/${id}`, { method: 'DELETE' });
}

// ===== Saved Strategies =====

// Roll-campaign overlay — present (non-null) once a trade has been rolled ≥1×. Lets the UI
// treat a rolled position as ONE continuing trade with an adjusted cost basis, rather than a
// string of disconnected close+open events. All fields are pure (no live quotes).
export interface RollSummary {
  count: number;                       // number of rolls so far
  roll_realized_pnl: number;           // cumulative $ realized on the buy-backs (the cost-basis adjustment)
  raw_net_credit: number | null;       // net entry credit of the CURRENT open legs (SELL +, BUY −), ×100
  effective_net_credit: number | null; // raw_net_credit + roll_realized_pnl — the true premium banked
  raw_breakevens: number[] | null;     // breakevens of the open legs at their own entry
  effective_breakevens: number[] | null; // breakevens once roll-realized is folded into the cost basis
  history: {                           // one entry per roll, newest last
    roll_id: string; seq: number; date: string; realized: number;
    buyback_cost?: number; new_credit?: number; from: string[]; to: string[]; note?: string | null;
  }[];
}

export interface SavedStrategyItem {
  id: number;
  strategy_type: string;
  name: string;
  ticker: string;
  parameters: any;
  legs_data: any[];
  result_snapshot: any;
  notes?: string;
  trade_status?: string | null;
  entry_prices?: any[] | null;
  entry_date?: string | null;
  entry_net_debit?: number | null;
  order_source?: string | null;
  exit_date?: string | null;
  exit_prices?: any[] | null;
  exit_net?: number | null;
  bpr?: number | null;                 // buying-power reduction (backend Reg-T margin); BPR base for the closed ledger
  roll?: RollSummary | null;           // roll-campaign overlay; null/absent if never rolled
  close_month?: string;                // 'YYYY-MM' bucket tag (only set by the closed-ledger endpoint)
  created_at: string;
  updated_at: string;
}

// One immutable prior month on the Closed tab, pre-summarized server-side (no per-trade rows
// shipped). Mirrors the on-screen month band's subtotals. See fetchClosedLedger.
export interface ClosedMonthSummary {
  month: string;        // 'YYYY-MM'
  label: string;        // e.g. "August 2026"
  count: number;
  wins: number;
  scored: number;       // trades with a known realized figure (win-rate denominator)
  cost: number;
  proceeds: number;
  realized: number;
  bpr: number;
}

export interface ClosedLedgerResult {
  current_month: string;                 // 'YYYY-MM' — the one expandable month
  current_month_label: string;
  frozen_months: ClosedMonthSummary[];   // strictly-prior immutable months, summary-only
  trades: SavedStrategyItem[];           // full records for the current month + any loose prior rows
  total_realized: number;                // frozen + current, the whole-book banked total
  stored: boolean;                       // true = prior months served from the DB cache
  include_rolls?: boolean;               // echoes whether still-active rolled trades' partials are folded in
}

// Closed tab: current-month trades in full (expandable) + prior months as compact stored
// summaries. The full closed history is no longer downloaded on every landing.
// `includeRolls` opts in to still-active ROLLED trades' partial realized (hidden by default —
// a rolled campaign is still in play, so its roll P&L stays out of the Closed ledger).
export async function fetchClosedLedger(includeRolls = false): Promise<ClosedLedgerResult> {
  const qs = includeRolls ? '?include_rolls=true' : '';
  return apiFetch<ClosedLedgerResult>(`/api/saved-strategies/trades/closed-ledger${qs}`);
}

// Lazily load one prior (frozen) month's full trade records when the user expands its band.
export async function fetchClosedLedgerMonth(month: string): Promise<SavedStrategyItem[]> {
  return apiFetch<SavedStrategyItem[]>(`/api/saved-strategies/trades/closed-ledger/${encodeURIComponent(month)}`);
}

// Delete just the option legs OR the stock leg of a closed combo trade. Returns the updated
// trade, or null if that emptied it and the whole (fully-closed) trade was removed.
export async function deleteClosedPart(id: number, part: 'options' | 'stock'): Promise<SavedStrategyItem | null> {
  return apiFetch<SavedStrategyItem | null>(`/api/saved-strategies/${id}/delete-closed-part`, {
    method: 'POST',
    body: JSON.stringify({ part }),
  });
}

// Undo a close — restore a closed (or partially-closed) trade to Active with all its legs.
export async function reopenTrade(id: number): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}/reopen`, { method: 'POST' });
}

export async function fetchSavedStrategies(strategyType: string): Promise<SavedStrategyItem[]> {
  return apiFetch<SavedStrategyItem[]>(`/api/saved-strategies?strategy_type=${encodeURIComponent(strategyType)}`);
}

export async function saveStrategy(data: {
  strategy_type: string;
  name: string;
  ticker: string;
  parameters: any;
  legs_data: any[];
  result_snapshot: any;
  notes?: string;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>('/api/saved-strategies', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateSavedStrategy(id: number, data: {
  name?: string;
  ticker?: string;
  strategy_type?: string;
  parameters?: any;
  legs_data?: any[];
  result_snapshot?: any;
  notes?: string;
  entry_prices?: any[];
  entry_net_debit?: number;
  entry_date?: string;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteSavedStrategy(id: number): Promise<void> {
  await apiFetch<void>(`/api/saved-strategies/${id}`, { method: 'DELETE' });
}

// ===== Trade Tracking =====

export async function deleteTrade(strategyId: number): Promise<void> {
  return apiFetch<void>(`/api/saved-strategies/${strategyId}`, { method: 'DELETE' });
}

export async function fetchActiveTrades(status?: string, includePartial = false): Promise<SavedStrategyItem[]> {
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  if (includePartial) qs.set('include_partial', 'true');
  const params = qs.toString() ? `?${qs.toString()}` : '';
  return apiFetch<SavedStrategyItem[]>(`/api/saved-strategies/trades${params}`);
}

export async function markStrategyAsTraded(id: number, data: {
  entry_prices: any[];
  entry_net_debit: number;
  order_source?: string;
  broker_order_id?: number;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}/mark-traded`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function closeTrackedTrade(id: number, data: {
  exit_prices: any[];
  exit_net: number;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}/close-trade`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// Parse pasted broker order text → what it means vs the user's open trades.
export interface ImportedLeg {
  kind: 'option' | 'stock';
  underlying: string;
  type: string;                 // CALL | PUT | STOCK
  right?: string; strike?: number; expiration?: string;
  action: 'BUY' | 'SELL';
  qty: number;
  price: number | null;
  raw_symbol: string;
}
export interface ImportOrderResult {
  legs: ImportedLeg[];
  underlying: string;
  intent: 'new' | 'close' | 'duplicate';
  close_matches: { parsed_idx: number; trade_id: number; trade_name: string; leg_index: number; exit_price: number | null; label: string }[];
  warnings: string[];
  manual_trade: any;            // ManualTradeIn-shaped payload, ready to POST as a new trade
}
export async function importOrder(text: string, purpose?: string): Promise<ImportOrderResult> {
  return apiFetch<ImportOrderResult>(`/api/saved-strategies/import-order`, {
    method: 'POST',
    body: JSON.stringify({ text, purpose }),
  });
}

// Close SOME or ALL of a placed trade at the prices actually received. The backend
// banks realized P&L per leg (parameters.realized_pnl + closed_legs), and only flips
// the trade to 'closed' when no legs and no stock remain (partial closes stay active).
export async function closePosition(id: number, data: {
  legs: { leg_index: number; exit_price: number }[];
  close_stock?: boolean;
  stock_exit_price?: number | null;
  executed_at?: string;
  note?: string | null;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}/close-position`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// Roll a placed trade — buy back the tested leg(s) and open replacement leg(s) in ONE action.
// The trade stays ACTIVE as one continuing campaign; the buy-back's realized P&L is banked as a
// cost-basis adjustment (tagged as a roll) instead of a separate closed trade. Returns the
// updated trade, whose `roll` overlay carries the new effective breakeven / net credit.
export async function rollPosition(id: number, data: {
  close_legs: { leg_index: number; exit_price: number }[];
  open_legs: { action: 'buy' | 'sell'; type: 'call' | 'put'; strike: number; expiration: string; qty: number; premium: number }[];
  executed_at?: string;
  note?: string | null;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${id}/roll-position`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function createManualTrade(data: {
  strategy_type: string;
  name: string;
  ticker: string;
  parameters?: any;
  legs_data: any[];
  result_snapshot?: any;
  entry_prices: any[];
  entry_net_debit: number;
  order_source?: string;
  notes?: string;
  entry_date?: string;
}): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>('/api/saved-strategies/manual-trade', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export interface ScenarioPoint {
  price_change_pct: number;
  price: number;
  pnl_now: number;
  pnl_at_expiry: number;
  roi_now: number;
  roi_at_expiry: number;
  margin_at_risk: number;
}

export interface ThetaPoint {
  days_from_now: number;
  dte_remaining: number;
  value: number;
  pnl: number;
}

export interface NetGreeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
}

export type LegActionKind = 'CLOSE' | 'HOLD' | 'ROLL' | 'LET_EXPIRE';

export interface LegAdvice {
  leg: number;               // index into legs_data
  strike: number;
  type: string;              // "put" | "call"
  right: string;             // "P" | "C"
  action: LegActionKind;
  reason: string;
  p_itm_pct: number | null;  // risk-neutral prob this leg finishes ITM at its strike
  captured_pct: number | null;
  prob_source: 'rnd' | 'lognormal' | 'delta';
}

export interface TradeRecommendation {
  action: string;            // mirrors hold_vs_close signal
  headline: string;
  outcome?: string;          // whole-structure payoff summary
  leg_notes: string[];
  reasons: string[];
}

export interface TraderGreeks {
  net_delta: number; net_gamma: number; net_vega: number; net_theta: number;
  net_vanna: number; net_charm: number; net_volga: number;
}
export interface PmRatios {
  omega: number | null; sortino: number | null; calmar: number | null;
  expected_return_pct: number | null; downside_dev_pct: number | null;
}
export interface LifecycleMetrics {
  trader: Partial<TraderGreeks>;
  pm: Partial<PmRatios>;
  avg_iv_pct: number;
  risk?: { var_95: number | null; cvar_95: number | null; max_profit: number | null; max_loss: number | null; capital: number | null };
}

export interface StressTest { name: string; pnl: number; dS_pct: number; dVol_pts: number; }
export interface PortfolioRisk {
  horizon_days: number; n_sims: number; n_underlyings: number; n_positions: number;
  var_95: number | null; var_99: number | null; cvar_95: number | null; cvar_99: number | null;
  stress_tests: StressTest[];
  by_underlying: { ticker: string; spot: number; net_delta: number; net_gamma: number; net_vega: number; iv: number }[];
  net_delta_notional: number; net_vega: number;
}

export interface LifecycleAgentResult {
  role: string; title: string; verdict: string; action_needed: boolean;
  content: string; model: string;
}

export interface TradeAnalysis {
  annualized_return_to_expiry: number | null;
  probability_of_profit: number | null;
  pop_method?: 'rnd' | 'lognormal' | null;
  expected_value: number | null;
  risk_reward_ratio: number | null;
  kelly_fraction: number | null;
  theta_burn_rate_day: number;
  theta_burn_rate_pct: number;
  days_to_theta_breakeven: number | null;
  hold_vs_close: string;
  hold_vs_close_reasons: string[];
  dte_remaining: number;
  recommendation?: TradeRecommendation;
  exit_signal?: 'STRONG_HOLD' | 'HOLD' | 'CLOSE' | 'STRONG_CLOSE';
  exit_reasons?: string[];
  captured_pct?: number | null;
  quant_exit?: QuantExit | null;
  exit_scope?: 'whole_trade' | 'options_overlay';   // options_overlay = manage the options, stock held separately
}

// A market factor read for someone ALREADY in the position (holder perspective),
// where the entry sign is often inverted (e.g. falling IV is good when you're short).
export interface ManagementFactor {
  label: string;
  favorable: boolean | null;   // true = good for the holder · false = a risk · null = context
  note: string;
}

export interface QuantExit {
  signal: 'STRONG_HOLD' | 'HOLD' | 'CLOSE' | 'STRONG_CLOSE';
  score: number;              // 0-100 hold conviction
  hold_base: number;          // neutral-50 baseline shifted by the holder factors — drives the buildup
  base_source: string;        // 'neutral' (deep) | 'hold' (light)
  base_quality?: number | null; // entry 5-lens score — REFERENCE only, not the anchor
  subscores?: { edge: number; pop: number; sortino: number; tail: number; carry: number };
  adjustments: { name: string; pts: number; note: string }[];
  factors?: ManagementFactor[]; // holder-framed reads (vol decay, trend vs strike, cushion…)
  reasons?: string[];
  overrides: string[];
}

export interface LivePnlResponse {
  strategy_id: number;
  ticker: string;
  quote_source: string;
  underlying_price: number;
  entry_cost: number;
  current_value: number;
  unrealized_pnl: number;
  pnl_pct: number;
  days_held: number;
  current_quotes: any[];
  greeks: any[];
  net_greeks: NetGreeks;
  margin_required: number;
  total_capital: number;
  scenarios: ScenarioPoint[];
  theta_projection: ThetaPoint[];
  max_profit: number | null;
  max_loss: number | null;
  max_profit_price?: number | null;   // underlying price where max profit occurs
  max_loss_price?: number | null;     // underlying price where max loss occurs
  unbounded_profit?: boolean;         // true = unlimited upside (e.g. long call)
  unbounded_loss?: boolean;           // true = unlimited downside (e.g. naked short)
  breakevens: number[];
  expiration_date: string | null;
  leg_analysis?: LegAdvice[];
  lifecycle?: LifecycleMetrics;
  avg_iv?: number;
  analysis?: TradeAnalysis;
  // Stock-combo breakdown
  stock_pnl?: number;
  options_pnl?: number;
  stock_value?: number;
  stock_cost?: number;
  options_breakdown?: {
    cost_basis: number;
    current_value: number;
    options_pnl: number;
    net_delta: number;   // options only (stock's +1/share stripped out)
    net_theta: number;
    net_vega: number;
    net_gamma: number;
    entry_cost: number;
    pop: number | null;
    expected_value: number | null;
    kelly_fraction: number | null;
    max_profit: number | null;
    max_loss: number | null;
    max_profit_price: number | null;
    max_loss_price: number | null;
    unbounded_profit: boolean;
    unbounded_loss: boolean;
    days_held: number;
  };
}

// The underlying's stock context for a placed trade — same chrome the Income desk shows.
export interface UnderlyingDeskResult {
  ticker: string;
  spot: number;
  context: import('./types').DerivativeIncomeContext | null;
  events: import('./types').DerivativeIncomeFlag[];
  expiry_summaries: import('./types').DerivativeIncomeExpirySummary[];
}
export async function fetchUnderlyingDesk(id: number, quoteSource = 'yfinance'): Promise<UnderlyingDeskResult> {
  return apiFetch<UnderlyingDeskResult>(`/api/saved-strategies/${id}/underlying-desk?quote_source=${encodeURIComponent(quoteSource)}`);
}

// Institutional book-level short-vol / tail-risk desk.
export interface BookHedgeCandidate {
  label: string; long_put: number | null; short_put: number | null; contracts: number | null;
  instrument?: 'SPX' | 'VIX' | 'VIXY'; kind?: string;     // SPX put · VIX call · VIXY signal-based
  long_strike?: number | null; short_strike?: number | null;
  cost_per_spread: number; total_cost: number; annual_bleed: number;
  crash_payoff_20: number; offsets_pct: number | null; cvar_reduction: number;
  efficiency: number | null; cagr_lift_pct: number; cost_effective: boolean; recommended?: boolean;
  index?: string; pricing?: string; dte_days?: number | null; expiry?: string | null;
  vix_at_minus20?: number;                                // VIX future level a −20% month implies
  sleeve_capital?: number; signal?: string; capture_pct?: number;   // VIXY dynamic sleeve
}
// One concrete remediation leg-set (close, or a real-strike spread), priced from live data.
export interface RemediationLeg {
  action: string;          // 'cap' | 'close'
  legs: string;            // human-readable real legs, e.g. "BUY 5× 2026-10-17 130C (vs your short 120C)"
  cost: number;            // $ to execute
  tail_after: number;      // position P&L at the stress move AFTER the fix
  premium_kept: number;    // $ premium still collected after the fix
  detail?: string;
  // Structured legs for the Evaluate deep-dive (exact live-chain pricing). Present on 'cap'.
  prefill?: { ticker: string; legs: { action: 'BUY' | 'SELL'; type: 'CALL' | 'PUT'; strike: number; expiration: string | null; qty: number }[] };
}

export interface BookTailRiskResult {
  positions: number;
  error?: string;
  stored?: boolean;          // true when served from the persisted snapshot
  computed_at?: string;      // ISO time the stored snapshot was computed
  net_delta?: number; net_gamma?: number; net_vega?: number; net_theta?: number;
  net_delta_notional?: number; beta_delta_notional?: number; beta_delta_spy?: number | null;
  book_capital?: number; annual_income?: number; capital_basis?: string;
  theta_net_liq_pct?: number | null; carry_yield_pct?: number | null; cvar_capital_pct?: number | null;
  short_vol?: boolean; avg_beta?: number;
  stock_notional?: number;   // $ of shares behind the overlay (covered calls / collars / holdings)
  var_95?: number | null; cvar_95?: number | null; var_99?: number | null; cvar_99?: number | null;
  horizon?: string;
  concentration?: { ticker: string; trades: number; short_legs: number; net_gamma: number; net_vega: number; net_delta: number; beta?: number; gamma_share_pct: number; laddered: boolean; flags: string[] }[];
  // Per-underlying OPTION-OVERLAY P&L at a −20% month (options only; stock is a separate holding).
  loss_by_name?: { ticker: string; pnl: number; beta: number }[];
  // 2-D risk array: book P&L for each (spot move × vol-point shock).
  scenario_grid?: { spot_moves: number[]; vol_shocks: number[]; rows: { move_pct: number; cells: { pnl: number; pct: number | null }[] }[] };
  // Institutional risk scorecard: standardized guardrails, book value vs limit, cheapest fix per breach.
  risk_scorecard?: {
    grade: string; n_breach: number; n_warn: number; n_checks: number;
    checks: {
      key: string; label: string; status: 'pass' | 'warn' | 'breach';
      value_pct?: number; value_str: string; limit_str: string; note?: string;
      fix?: {
        headline: string; cost?: string; effect?: string; alt?: string;
        // Position-specific remediation with REAL legs, ranked by risk vs remaining premium.
        targets?: {
          ticker: string; name: string; trade_id?: number | null; structure?: string | null;
          risk: number; premium_left: number; why: string; tail_before: number;
          recommended: RemediationLeg; alt?: RemediationLeg | null;
          short?: { strike: number; right: string; qty: number } | null;   // targeted short leg — always closeable
        }[];
      };
    }[];
  } | null;
  // Deterministic factor read: GICS sector buckets, measured-ρ correlated clusters, macro loadings.
  factor_exposure?: {
    sectors?: { sector: string; tickers: string[]; capital: number; net_directional: number; n: number }[];
    clusters?: { tickers: string[]; capital: number; net_directional: number; avg_rho: number | null }[];
    macro?: { factor: string; label: string; rho: number }[];
    by_name?: { ticker: string; sector: string | null; capital: number; net_directional: number }[];
  } | null;
  crash_scenarios?: { label: string; move_pct: number; pnl: number; pct_of_capital: number | null }[];
  assignment_ladder?: { move_pct: number; pnl: number; put_assignment_capital: number; call_cover_cost: number; puts_itm: number; calls_itm: number }[];
  naked_assignment?: { put_capital: number; call_capital: number; total: number; n_naked_puts: number; n_naked_calls: number };
  hedge_menu?: BookHedgeCandidate[];
  hedge_note?: string | null;
  verdict?: { level: string; summary: string; actions: string[] };
  assumptions?: { beta: string; mkt_vol_pct?: number; tail?: string; crash_prob_annual_pct: number; hedge_rolls_per_year: number };
}
// refresh=false → the STORED snapshot (page load); refresh=true → recompute live + overwrite the store.
export async function fetchBookTailRisk(quoteSource = 'yfinance', refresh = false): Promise<BookTailRiskResult> {
  return apiFetch<BookTailRiskResult>(`/api/saved-strategies/book-tail-risk?quote_source=${encodeURIComponent(quoteSource)}&refresh=${refresh ? 1 : 0}`);
}

// LLM hedging strategy for the whole book — model is fed ONLY the computed numbers.
export async function fetchBookHedgeAdvice(quoteSource = 'yfinance'): Promise<{ advice?: string; error?: string; data_sent?: any }> {
  return apiFetch(`/api/saved-strategies/book-hedge-advice?quote_source=${encodeURIComponent(quoteSource)}`);
}

export interface RepairAlternative {
  name: string; category: string; group?: 'adjust' | 'replace' | 'benchmark';  // adjust = fix the current trade; replace = close & redeploy
  mechanics: string; rationale?: string; risk_note: string;
  net_cash: number;
  scenarios: { move_pct: number; spot: number; pnl: number }[];
  max_loss: number | null; max_gain: number; breakevens: number[];
  greeks: { delta: number; gamma: number; theta: number; vega: number }; theta_day: number;
  defined_risk: boolean; upside_risk_free: boolean; pop_pct: number | null;
  ev?: number | null;                                    // E[P&L] under the lognormal law
  d_pop?: number | null; d_max_loss?: number | null; d_ev?: number | null;   // vs the Hold baseline
  turns_profitable: boolean;
  legs?: { action: string; right: string; strike: number; qty: number; dte_days: number | null; expiry?: string | null }[];
}
// One factor in the recoverability build-up — a probability driver (kind 'prob') or a technical
// read (kind 'ta'). favorable: true = helps a recovery, false = works against it, null = neutral/2-sided.
export interface DefendFactor { label: string; kind: 'prob' | 'ta'; favorable: boolean | null; detail: string }
// Lens 1 — how recoverable the trade is from here, WITH the auditable build-up behind the score.
export interface DefendRecoverability {
  recovery_score: number | null;          // healthy/marginal: P(keep premium / expire OTM); tested: P(recover to breakeven)
  posture?: 'healthy' | 'marginal' | 'tested';   // healthy = safe; marginal = OTM but high breach/VRP/trend risk → de-risk; tested = in trouble
  breakeven: number | null; needed_move_pct: number | null; cushion_pct?: number | null; dist_to_be_sigma: number | null;
  expected_move?: number | null;          // ±1σ $ move to expiry (the yardstick for the needed move / cushion)
  tested_delta: number; severity: 'fresh' | 'deep' | 'assigned' | 'healthy';
  // the quant risk read that reconciles with the Manage desk (why an 82%-OTM short can still be MARGINAL)
  p_touch?: number | null;                // % chance the strike is BREACHED at any point before expiry (drift-aware)
  vrp_pct?: number | null; trend_pct?: number | null; iv_pct?: number | null; hv_pct?: number | null;
  risk_read?: string | null;
  factors?: DefendFactor[];               // prob drivers + TA factors that explain / tilt the score
  outlook?: { tilt: 'favorable' | 'adverse' | 'balanced' | 'watch'; note: string } | null;   // net TA read
}
// Lens 2 — assignment / exercise risk and its dollar consequence.
export interface DefendAssignment {
  p_itm: number | null; extrinsic: number; intrinsic: number;
  early_assignment_risk: boolean; early_reason: string | null;
  pin_ratio: number | null; effective_basis: number; assignment_capital: number; consequence: string;
}
export interface DefendCostOfWaiting { in_trading_days: number; dte_left: number; recovery_pop: number | null; expected_pnl: number | null; }
// Lens 5 — CONTEXT, computed in the SAME core fetch (folded in, no second click): recoverable time
// value, where price sits, the sector cohort, the vol state, and any earnings before expiry.
export interface DefendContext {
  loss_read?: { time_value_recoverable: number };
  technical?: { support: number | null; resistance: number | null; note: string };
  pattern?: { type: string; direction: string; status: string; target: number | null; breakout: number | null; confidence: number; window?: string } | null;
  range?: { low: number; high: number; width_pct: number; where: string; note: string } | null;
  sector?: { themes: string[]; peers: string[]; note: string };
  earnings?: { before_expiry: boolean; date: string | null; days: number | null; note: string };
  vol_note?: string;
}
export interface RepairMenuResult {
  error?: string; ticker?: string; tested?: boolean; cushion_pct?: number; covered?: boolean;
  short_right?: 'P' | 'C'; short_strike?: number; spot?: number; dte_days?: number; contracts?: number;
  unrealized_pnl?: number; pricing?: string; structure?: string; alternatives?: RepairAlternative[];
  recoverability?: DefendRecoverability; assignment?: DefendAssignment;
  cost_of_waiting?: DefendCostOfWaiting[]; context?: DefendContext;
  hold?: { pop_pct: number | null; expected_pnl: number | null; max_loss: number | null;
           greeks?: { delta: number; gamma: number; theta: number; vega: number }; breakevens?: number[] };
}
// The Defend desk for a tested short-premium trade: recoverability (+TA outlook) + assignment + the
// priced action menu (roll / spread / strangle / hedge / wheel / hold / close) + context — one fetch.
export async function fetchDefendMenu(id: number, quoteSource = 'yfinance'): Promise<RepairMenuResult> {
  return apiFetch(`/api/saved-strategies/${id}/repair-menu?quote_source=${encodeURIComponent(quoteSource)}`);
}
// Back-compat alias.
export const fetchTradeRepairMenu = fetchDefendMenu;

// Lens 7 — WAR ROOM: a Quant → Risk → PM defense cascade, fed ONLY the computed Defend numbers.
// LLM synthesizes; never invents data. Each role carries the specific metrics it leaned on.
export interface DefendCommitteeRole { role: string; stance: string; rationale: string; metrics?: string[] }
export interface DefendCommittee {
  error?: string;
  quant?: DefendCommitteeRole; risk?: DefendCommitteeRole; pm?: DefendCommitteeRole;
  verdict?: {
    primary_action: string; why: string; confidence?: string | null;
    alternates: { action: string; why: string }[]; do_not: string | null;
  };
  data_sent?: unknown;
}
export async function fetchDefendCommittee(id: number, defend: RepairMenuResult, quoteSource = 'yfinance'): Promise<DefendCommittee> {
  return apiFetch(`/api/saved-strategies/${id}/defend/committee?quote_source=${encodeURIComponent(quoteSource)}`, {
    method: 'POST', body: JSON.stringify({ defend }),
  });
}

// Deep-quant ROLL OPTIMIZER — credit-only (no net new money) strike+expiry roll picked on the
// confluence of RND probability, support/resistance, gamma flip/wall (GEX) and the volume POC.
export interface RollCandidate {
  expiry: string; dte: number; strike: number; right: 'P' | 'C';
  roll_net_cash: number; credit_per_share: number; new_credit_total: number; new_breakeven: number; new_capital: number;
  p_otm: number; p_otm_source: 'RND' | 'BS'; spans_earnings?: boolean;
  structure: Record<string, boolean | number>;      // per-level flags + cleared_count + levels_available
  scores: { composite: number; probability: number; structure: number; credit: number; cushion: number; cushion_sigma?: number; time_factor?: number };
  legs: { action: string; right: string; strike: number; qty: number; dte_days: number | null; expiry?: string | null }[];
  why: string;
}
export interface RollOptimizerResult {
  error?: string; ticker?: string; spot?: number;
  tested?: { right: 'P' | 'C'; strike: number; qty: number; entry: number; mark: number; dte: number; covered: boolean };
  structure?: { support: number | null; resistance: number | null; poc: number | null; gamma_flip: number | null; gamma_wall: number | null; gamma_regime: string | null; recent_5d?: { support: number | null; resistance: number | null; poc: number | null } | null; next_earnings?: string | null; window?: string };
  weights?: Record<string, number>;
  considered?: number; candidates?: RollCandidate[]; note?: string;
}
export async function fetchRollOptimizer(id: number, quoteSource = 'yfinance'): Promise<RollOptimizerResult> {
  return apiFetch(`/api/saved-strategies/${id}/defend/optimize-roll?quote_source=${encodeURIComponent(quoteSource)}`, { method: 'POST' });
}

export async function fetchTradeLivePnl(id: number, quoteSource: string = 'yfinance', marginMode?: string): Promise<LivePnlResponse> {
  const mm = marginMode || (typeof localStorage !== 'undefined' && localStorage.getItem('margin.mode') === 'portfolio' ? 'portfolio' : 'reg_t');
  return apiFetch<LivePnlResponse>(`/api/saved-strategies/${id}/live-pnl?quote_source=${encodeURIComponent(quoteSource)}&margin_mode=${encodeURIComponent(mm)}`);
}

// Persist the last-refreshed P&L (trimmed) so My Trades shows last-known numbers on landing.
export async function saveTradePnlSnapshot(id: number, snapshot: Record<string, any>): Promise<{ saved: boolean; last_pnl_at: string }> {
  return apiFetch(`/api/saved-strategies/${id}/pnl-snapshot`, { method: 'PUT', body: JSON.stringify({ snapshot }) });
}

export async function fetchTradeAdvisor(id: number, pnlSnapshot: LivePnlResponse, userQuestion?: string): Promise<{ content: string; model: string }> {
  return apiFetch<{ content: string; model: string }>(`/api/saved-strategies/${id}/trade-advisor`, {
    method: 'POST',
    body: JSON.stringify({ pnl_snapshot: pnlSnapshot, user_question: userQuestion || '' }),
  });
}

// ===== Continuous Lifecycle Management (Risk / PM / Trader) =====

export interface RiskPositionInput {
  ticker: string; spot: number; net_delta: number; net_gamma: number; net_vega: number; iv: number;
}

export async function computePortfolioRisk(positions: RiskPositionInput[], horizonDays = 1): Promise<PortfolioRisk> {
  return apiFetch<PortfolioRisk>(`/api/saved-strategies/portfolio-risk`, {
    method: 'POST',
    body: JSON.stringify({ positions, horizon_days: horizonDays }),
  });
}

export async function runLifecycleAgent(
  id: number, role: 'risk' | 'pm' | 'trader',
  pnlSnapshot: LivePnlResponse, portfolioRisk?: PortfolioRisk | null,
): Promise<LifecycleAgentResult> {
  return apiFetch<LifecycleAgentResult>(`/api/saved-strategies/${id}/lifecycle-agent`, {
    method: 'POST',
    body: JSON.stringify({ role, pnl_snapshot: pnlSnapshot, portfolio_risk: portfolioRisk ?? null }),
  });
}

export interface DeskFactor { label: string; points: number; }
export interface DeskScoreResult {
  matched: boolean;
  error?: string;
  desk_score?: number;
  base_quality?: number;
  subscores?: { edge: number; pop: number; sortino: number; tail: number; carry: number };
  grade_adjustments?: DeskFactor[];   // option math (VRP/Moneyness/Skew/Liquidity/Beta/Expectation)
  ta_factors?: DeskFactor[];          // technicals (regime/value-area/gamma)
  qp?: {
    implied_move_pct?: number | null; physical_move_pct?: number | null; dual_move_pct?: number | null;
    short_sigmas?: number | null; short_dist_pct?: number | null; iv_hv_ratio?: number | null;
    implied_vol_pct?: number | null; realized_vol_pct?: number | null; physical_wider?: boolean;
  };
  algo_grade?: string;
  merits?: string[]; demerits?: string[]; blocking?: string[];
  opp?: any;                    // full ranked candidate → render the scan's OpportunityCard
  spot?: number;
  lifecycle_adjustments?: { name: string; pts: number; note: string }[];
  lifecycle_score?: number;
  signal?: 'STRONG_HOLD' | 'HOLD' | 'CLOSE' | 'STRONG_CLOSE';
  overrides?: string[];
  hold_base?: number;                    // neutral-50 baseline (holder factors move it)
  base_source?: string;                  // 'neutral' (deep) | 'hold' (light)
  management_analysis?: ManagementAnalysis; // deep read: scan factors re-signed for the holder
}

// One re-signed scan factor, read for a HOLDER (e.g. entry VRP demerit → "Vol decay" positive).
export interface ManagementContribution {
  label: string;
  pts: number;
  favorable: boolean;
  note: string;
  dimension?: string | null;   // risk dimension for UI grouping (holder-relabel aware)
}
// The deep management read — scan factors re-signed + take-profit/time overlay → hold/close.
export interface ManagementLens {
  label: string; score: number;   // the 0-100 lens sub-score
  weight?: number;                 // its weight in the base (percent)
  contribution?: number;           // score × weight → points toward the base
  note: string;
}

export interface ManagementAnalysis {
  anchor: number;                 // COMPUTED hold-quality base (remaining risk vs reward), not a fixed 50
  anchor_label: string;
  base_lenses?: ManagementLens[]; // the 5 lenses behind the base (edge/reward/risk-adj/tail/cushion)
  contributions: ManagementContribution[]; // re-signed scan + dynamic-greek factors
  factors_net: number;
  overlay: { name: string; pts: number; note: string }[]; // slim time / gamma
  score: number;
  signal: 'STRONG_HOLD' | 'HOLD' | 'CLOSE' | 'STRONG_CLOSE';
  overrides: string[];
  advisories?: string[];          // covered / naked call structural advice
  greeks_used?: { net_gamma?: number | null; net_vega?: number | null; net_theta?: number | null };
}

export async function runDeskScore(
  id: number, pnlSnapshot: LivePnlResponse,
  focus: { structure: string; expiration?: string | null; short_strike?: number | null },
  quoteSource = 'yfinance',
): Promise<DeskScoreResult> {
  return apiFetch<DeskScoreResult>(`/api/saved-strategies/${id}/desk-score`, {
    method: 'POST',
    body: JSON.stringify({ pnl_snapshot: pnlSnapshot, ...focus, quote_source: quoteSource }),
  });
}

// ===== Paper Trader — place an Income-Desk opportunity, track placed-vs-now =====

// Short-premium mark-to-market: cost basis = net credit received; current_value = what it'd
// cost to buy the structure back now; unrealized = cost_basis − current_value (+ = decayed for you).
export interface PaperTradePnl {
  cost_basis: number | null;
  current_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  captured_pct: number | null;          // % of max profit banked (negative = moved against you)
  current_spot: number | null;
  entry_spot: number | null;
  spot_change_pct: number | null;
  dte_remaining: number | null;
  contracts: number;
  entry_premium_per_share: number | null;
  current_premium_per_share: number | null;
}

// The CURRENT desk read for a paper trade — the SAME shape as a placed trade's desk score
// (opp + management_analysis + signal), plus the paper P&L block.
export type PaperTradeCurrent = DeskScoreResult & { id?: number; pnl?: PaperTradePnl | null };

// LIGHTWEIGHT list row — cached/denormalized columns only (the list never triggers compute).
export interface PaperTradeListItem {
  id: number;
  ticker: string;
  structure: string;
  label: string | null;
  expiration: string | null;
  dte: number | null;
  contracts: number;
  status: 'open' | 'closed';
  notes: string | null;
  created_at: string;
  updated_at: string;
  cost_basis: number | null;
  entry_premium_per_share: number | null;
  entry_spot: number | null;
  placed_desk_score: number | null;
  placed_algo_grade: string | null;
  last_eval_at: string | null;          // when the cached current read was last refreshed
  last_spot: number | null;
  current_value: number | null;
  last_pnl: number | null;
  last_desk_score: number | null;
  last_algo_grade: string | null;
  closed_at: string | null;
  close_pnl: number | null;
  close_note: string | null;
}

export interface PaperTradeDetail extends PaperTradeListItem {
  legs: import('./types').DerivativeIncomeLeg[];
  placed_snapshot: import('./types').DeskRankedTrade;   // → the "when placed" Quant Analysis
  last_eval: PaperTradeCurrent | null;                  // → the cached "now" read
}

export interface PaperTradesResponse {
  items: PaperTradeListItem[];
  counts: { open: number; closed: number; total: number };
}

/** All paper trades (newest first). LIGHTWEIGHT — no yfinance/scan. status = open | closed | all. */
export async function fetchPaperTrades(status: string = 'all'): Promise<PaperTradesResponse> {
  return apiFetch<PaperTradesResponse>(`/api/paper-trades?status=${encodeURIComponent(status)}`);
}

/** One paper trade with the placed snapshot + cached current read (no compute). */
export async function fetchPaperTrade(id: number): Promise<PaperTradeDetail> {
  return apiFetch<PaperTradeDetail>(`/api/paper-trades/${id}`);
}

/** Place a paper trade from an Income-Desk opportunity (always 1 contract; no compute). */
export async function createPaperTrade(
  ticker: string, opp: import('./types').DeskRankedTrade,
  spot?: number | null, quoteSource = 'yfinance', note?: string | null,
): Promise<PaperTradeListItem> {
  return apiFetch<PaperTradeListItem>('/api/paper-trades', {
    method: 'POST',
    body: JSON.stringify({ ticker, opp, spot: spot ?? null, quote_source: quoteSource, note: note ?? null }),
  });
}

/** THE heavy path — re-price the exact legs + recompute the current quant/management read. */
export async function refreshPaperTrade(id: number, quoteSource = 'yfinance'): Promise<PaperTradeCurrent> {
  return apiFetch<PaperTradeCurrent>(
    `/api/paper-trades/${id}/refresh?quote_source=${encodeURIComponent(quoteSource)}`, { method: 'POST' });
}

/** Bank the current P&L and archive to Closed. */
export async function closePaperTrade(id: number, quoteSource = 'yfinance', note?: string | null): Promise<PaperTradeDetail> {
  return apiFetch<PaperTradeDetail>(`/api/paper-trades/${id}/close`, {
    method: 'POST',
    body: JSON.stringify({ quote_source: quoteSource, note: note ?? null }),
  });
}

export async function deletePaperTrade(id: number): Promise<void> {
  return apiFetch<void>(`/api/paper-trades/${id}`, { method: 'DELETE' });
}

export interface LifecycleManagerResult {
  role: string; title: string;
  signal: 'STRONG_HOLD' | 'HOLD' | 'CLOSE' | 'STRONG_CLOSE';
  action_needed: boolean; content: string; model: string;
}

// The institutional quant PM managing a live trade → the 4-level exit signal + plan.
export async function runLifecycleManager(id: number, pnlSnapshot: LivePnlResponse): Promise<LifecycleManagerResult> {
  return apiFetch<LifecycleManagerResult>(`/api/saved-strategies/${id}/lifecycle-manager`, {
    method: 'POST',
    body: JSON.stringify({ pnl_snapshot: pnlSnapshot }),
  });
}

export interface RiskTradeSummary {
  ticker: string; name: string; unrealized_pnl: number; net_delta: number; net_vega: number;
}

export async function runPortfolioRiskAgent(
  portfolioRisk: PortfolioRisk, tradesSummary: RiskTradeSummary[],
): Promise<LifecycleAgentResult> {
  return apiFetch<LifecycleAgentResult>(`/api/saved-strategies/portfolio-risk-agent`, {
    method: 'POST',
    body: JSON.stringify({ portfolio_risk: portfolioRisk, trades_summary: tradesSummary }),
  });
}

// ===== Pre-trade desk agents (Risk / PM / Trader on a not-yet-placed structure) =====

export interface PreTradeLeg {
  action: string; qty?: number; contracts?: number; type: string; strike: number; expiration?: string;
  iv?: number | null;    // decimal or %; used for Greeks when available (else fetched from chain)
  price?: number | null; // entry mid/share; lets the backend synthesize the payoff when no curve is sent
}

export interface PreTradeScenario { move_pct: number; price?: number; roi?: number; pnl?: number; }

// Full desk read + algorithmic Quant recommendation for a proposed structure.
export interface QuantRecommendation {
  verdict: 'ENTER' | 'CONSIDER' | 'AVOID' | string;
  tone: 'good' | 'warn' | 'bad' | string;
  score: number;
  reasons: string[];
  subscores: { edge: number; pop: number; sortino: number; tail: number; carry: number };
}
export interface PreTradeDeskMetrics {
  trader: Partial<TraderGreeks> & { avg_iv_pct?: number | null };
  pm: Partial<PmRatios> & { pop?: number | null; expected_value?: number | null; kelly_fraction?: number | null };
  risk: { var_95: number | null; cvar_95: number | null; max_loss: number | null; max_profit: number | null; capital: number | null };
  quant: QuantRecommendation;
}

export async function fetchPreTradeMetrics(payload: {
  ticker: string; expiration?: string | null; spot: number; capital: number; dte: number;
  stockShares?: number; legs: PreTradeLeg[]; scenarios: PreTradeScenario[];
  maxLoss?: number | null; maxProfit?: number | null; sofrPct?: number; strategyType?: string;
}): Promise<PreTradeDeskMetrics> {
  return apiFetch<PreTradeDeskMetrics>(`/api/saved-strategies/pre-trade-metrics`, {
    method: 'POST',
    body: JSON.stringify({
      ticker: payload.ticker, expiration: payload.expiration ?? null, spot: payload.spot,
      capital: payload.capital, dte: payload.dte, stock_shares: payload.stockShares ?? 0,
      legs: payload.legs, scenarios: payload.scenarios,
      max_loss: payload.maxLoss ?? null, max_profit: payload.maxProfit ?? null,
      sofr_pct: payload.sofrPct ?? 5.0, strategy_type: payload.strategyType ?? '',
    }),
  });
}

export async function runPreTradeAgent(payload: {
  role: 'risk' | 'pm' | 'trader';
  ticker: string;
  strategyType: string;
  legs: PreTradeLeg[];
  metrics: Record<string, string | number | null | undefined>;
  scenarios?: PreTradeScenario[];
  breakevens?: (string | number)[];
  deskMetrics?: PreTradeDeskMetrics | null;
  notes?: string;
}): Promise<LifecycleAgentResult> {
  return apiFetch<LifecycleAgentResult>(`/api/saved-strategies/pre-trade-agent`, {
    method: 'POST',
    body: JSON.stringify({
      role: payload.role, ticker: payload.ticker, strategy_type: payload.strategyType,
      legs: payload.legs, metrics: payload.metrics,
      scenarios: payload.scenarios ?? [], breakevens: payload.breakevens ?? [],
      desk_metrics: payload.deskMetrics ?? null,
      notes: payload.notes ?? null,
    }),
  });
}

// ===== Trade Transactions =====

export interface TradeTransaction {
  id: number;
  strategy_id: number;
  action: string;          // open | add | reduce | close | adjust
  leg_index?: number | null;
  quantity: number;        // signed
  price: number;
  fees: number;
  executed_at: string;     // ISO datetime
  source: string;
  note?: string | null;
  created_at: string;
}

export async function fetchTradeTransactions(strategyId: number): Promise<TradeTransaction[]> {
  return apiFetch<TradeTransaction[]>(`/api/saved-strategies/${strategyId}/transactions`);
}

export async function appendTradeTransaction(
  strategyId: number,
  data: {
    action: string;
    quantity: number;
    price: number;
    fees?: number;
    executed_at: string;
    leg_index?: number | null;
    source?: string;
    note?: string | null;
  }
): Promise<TradeTransaction> {
  return apiFetch<TradeTransaction>(`/api/saved-strategies/${strategyId}/transactions`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateTradeTransaction(
  strategyId: number,
  txId: number,
  data: {
    action?: string;
    quantity?: number;
    price?: number;
    fees?: number;
    executed_at?: string;
    note?: string | null;
  }
): Promise<TradeTransaction> {
  return apiFetch<TradeTransaction>(
    `/api/saved-strategies/${strategyId}/transactions/${txId}`,
    { method: 'PUT', body: JSON.stringify(data) },
  );
}

export async function deleteTradeTransaction(
  strategyId: number,
  txId: number,
): Promise<void> {
  return apiFetch<void>(
    `/api/saved-strategies/${strategyId}/transactions/${txId}`,
    { method: 'DELETE' },
  );
}

export async function updateTradeNotes(strategyId: number, notes: string): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${strategyId}/notes`, {
    method: 'PATCH',
    body: JSON.stringify({ notes }),
  });
}

// The purpose a trade serves — drives My Trades grouping + the lifecycle view.
export type TradePurpose =
  'income' | 'hedge' | 'trade' | 'managed_floor' | 'managed_buffer' | 'dual_directional' | 'other';

export async function updateTradePurpose(strategyId: number, purpose: TradePurpose): Promise<SavedStrategyItem> {
  return apiFetch<SavedStrategyItem>(`/api/saved-strategies/${strategyId}/purpose`, {
    method: 'PATCH',
    body: JSON.stringify({ purpose }),
  });
}

// ===== Broker =====

export async function fetchBrokerConnection(): Promise<any> {
  return apiFetch<any>('/api/broker/connection');
}

export async function generateBrokerKeys(): Promise<import('./types').BrokerKeyGenResponse> {
  return apiFetch<import('./types').BrokerKeyGenResponse>('/api/broker/generate-keys', {
    method: 'POST',
  });
}

export async function saveBrokerConnection(creds: {
  account_id: string;
  consumer_key: string;
  access_token: string;
  access_token_secret: string;
}): Promise<any> {
  return apiFetch<any>('/api/broker/connection', {
    method: 'POST',
    body: JSON.stringify({ broker_type: 'interactive_brokers', ...creds }),
  });
}

export async function diagnoseBrokerConnection(): Promise<any> {
  return apiFetch<any>('/api/broker/diagnose', { method: 'POST' });
}

export async function deleteBrokerConnection(): Promise<void> {
  await apiFetch<void>('/api/broker/connection', { method: 'DELETE' });
}

export async function fetchBrokerStatus(): Promise<import('./types').BrokerStatus> {
  return apiFetch<import('./types').BrokerStatus>('/api/broker/status');
}

export async function placeBrokerStrategyOrder(
  ticker: string,
  strategy: string,
  legs: import('./types').BrokerOrderLeg[],
): Promise<import('./types').BrokerStrategyResult> {
  return apiFetch<import('./types').BrokerStrategyResult>('/api/broker/order/place-strategy', {
    method: 'POST',
    body: JSON.stringify({ ticker, strategy, legs }),
  });
}

export async function fetchBrokerOrders(): Promise<any> {
  return apiFetch<any>('/api/broker/orders');
}

// ===== Market Overview =====

export async function fetchMarketOverview(): Promise<import('./types').MarketOverviewResponse> {
  return apiFetch<import('./types').MarketOverviewResponse>('/api/market/overview');
}

export async function fetchIndexQuote(symbol: string): Promise<import('./types').IndexData> {
  return apiFetch<import('./types').IndexData>(`/api/market/index/${encodeURIComponent(symbol)}`);
}

/** Fetch AI market briefing — call only on explicit user action */
export async function fetchMarketNarrative(): Promise<{ narrative: string | null }> {
  return apiFetch<{ narrative: string | null }>('/api/market/overview/narrative', { method: 'POST' });
}

export async function fetchSectorDashboard(
  timeframe: string = '1m',
): Promise<import('./types').SectorDashboardResponse> {
  const q = new URLSearchParams({ timeframe });
  return apiFetch<import('./types').SectorDashboardResponse>(`/api/market/sectors?${q.toString()}`);
}

/** Fetch AI sector rotation diagnosis — call only on explicit user action */
export async function fetchSectorNarrative(
  timeframe: string = '1m',
): Promise<{ intelligence: string | null }> {
  const q = new URLSearchParams({ timeframe });
  return apiFetch<{ intelligence: string | null }>(`/api/market/sectors/narrative?${q.toString()}`, { method: 'POST' });
}

export async function fetchTopMovers(
  timeframe: string = '1d',
  limit: number = 10,
): Promise<import('./types').MoversResponse> {
  const q = new URLSearchParams({ timeframe, limit: String(limit) });
  return apiFetch<import('./types').MoversResponse>(`/api/market/movers?${q.toString()}`);
}

export async function fetchLiquidityDashboard(): Promise<import('./types').MacroLiquidityResponse> {
  return apiFetch<import('./types').MacroLiquidityResponse>('/api/market/liquidity');
}

export async function fetchLiquidityNarrative(): Promise<{ narrative: string | null }> {
  return apiFetch<{ narrative: string | null }>('/api/market/liquidity/narrative', { method: 'POST' });
}

export async function fetchBusinessCycle(): Promise<import('./types').BusinessCycleResponse> {
  return apiFetch<import('./types').BusinessCycleResponse>('/api/market/business-cycle');
}

export async function refreshBusinessCycle(): Promise<import('./types').BusinessCycleResponse> {
  return apiFetch<import('./types').BusinessCycleResponse>('/api/market/business-cycle/refresh', { method: 'POST' });
}

export async function fetchCycleSectorTA(phase: string): Promise<import('./types').CycleSectorTaResponse> {
  return apiFetch<import('./types').CycleSectorTaResponse>(`/api/market/cycle-sectors-ta?phase=${phase}`);
}

export async function sectorChat(
  question: string,
  timeframe: string,
  history: import('./types').SectorChatTurn[],
): Promise<import('./types').SectorChatResponse> {
  return apiFetch<import('./types').SectorChatResponse>('/api/market/sectors/chat', {
    method: 'POST',
    body: JSON.stringify({ question, timeframe, history }),
  });
}

export async function fetchSectorCalendarReturns(): Promise<import('./types').SectorCalendarResponse> {
  return apiFetch<import('./types').SectorCalendarResponse>('/api/market/sectors/calendar-returns');
}

export async function fetchStyleCalendarReturns(
  category?: string,
): Promise<import('./types').StyleCalendarResponse> {
  const q = category ? `?category=${encodeURIComponent(category)}` : '';
  return apiFetch<import('./types').StyleCalendarResponse>(`/api/market/styles/calendar-returns${q}`);
}

export async function interpretThesis(
  params: import('./types').InterpretParams,
): Promise<import('./types').InterpretResponse> {
  return apiFetch<import('./types').InterpretResponse>('/api/market/pick-shovel/interpret', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

export async function analyzePickShovel(
  theme: string,
  brief?: import('./types').ResearchBrief | null,
): Promise<import('./types').PickShovelResponse> {
  return apiFetch<import('./types').PickShovelResponse>('/api/market/pick-shovel', {
    method: 'POST',
    body: JSON.stringify({ theme, brief: brief ?? undefined }),
  });
}

export async function refinePickShovel(
  params: import('./types').RefineParams,
): Promise<import('./types').RefineResponse> {
  return apiFetch<import('./types').RefineResponse>('/api/market/pick-shovel/refine', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// ---------------------------------------------------------------------------
// Pick & Shovel v2 — /api/research/v2/*
// ---------------------------------------------------------------------------

export async function v2Decompose(theme: string, extra_context = ''): Promise<import('./types').DecomposeResponse> {
  return apiFetch('/api/research/v2/decompose', { method: 'POST', body: JSON.stringify({ theme, extra_context }) });
}

export async function v2RefineComponents(
  theme: string, components: import('./types').ThemeComponent[], user_input: string,
): Promise<{ components: import('./types').ThemeComponent[]; generated_at: string }> {
  return apiFetch('/api/research/v2/components/refine', { method: 'POST', body: JSON.stringify({ theme, components, user_input }) });
}

export async function v2Etfs(theme: string, components: import('./types').ThemeComponent[]): Promise<import('./types').EtfDiscoveryResponse> {
  return apiFetch('/api/research/v2/etfs', { method: 'POST', body: JSON.stringify({ theme, components }) });
}

export async function v2FindComponentCompanies(
  theme: string, component: string, description = '', existing: string[] = [],
): Promise<{ companies: import('./types').V2Company[]; generated_at: string }> {
  return apiFetch('/api/research/v2/components/companies', {
    method: 'POST',
    body: JSON.stringify({ theme, component, description, existing }),
  });
}

export async function v2EtfHoldings(ticker: string): Promise<import('./types').EtfHoldingsResponse> {
  return apiFetch('/api/research/v2/etfs/holdings', { method: 'POST', body: JSON.stringify({ ticker }) });
}

export async function v2Ingest(theme: string, inputs: import('./types').UserInput[]): Promise<import('./types').IngestResponse> {
  return apiFetch('/api/research/v2/inputs/ingest', { method: 'POST', body: JSON.stringify({ theme, inputs }) });
}

export async function v2ValidateCompanies(tickers: string[]): Promise<import('./types').ValidateResponse> {
  return apiFetch('/api/research/v2/companies/validate', { method: 'POST', body: JSON.stringify({ tickers }) });
}

export async function v2Match(
  theme: string, components: import('./types').ThemeComponent[], companies: import('./types').V2Company[],
  deep_dives: import('./types').CompanyDeepDive[] = [],
): Promise<import('./types').MatchResponse> {
  return apiFetch('/api/research/v2/match', { method: 'POST', body: JSON.stringify({ theme, components, companies, deep_dives }) });
}

export async function v2DeepDive(
  theme: string, ticker: string, name = '', website?: string | null,
): Promise<import('./types').CompanyDeepDive> {
  return apiFetch('/api/research/v2/deep-dive', { method: 'POST', body: JSON.stringify({ theme, ticker, name, website: website ?? undefined }) });
}

export async function v2Summary(
  theme: string,
  deep_dives: import('./types').CompanyDeepDive[],
  components: import('./types').ThemeComponent[] = [],
  holdings: import('./types').EtfHolding[] = [],
  companies: import('./types').V2Company[] = [],
  emphasis: string[] = [],
): Promise<import('./types').ResearchSummary> {
  return apiFetch('/api/research/v2/summary', {
    method: 'POST',
    body: JSON.stringify({ theme, deep_dives, components, holdings, companies, emphasis }),
  });
}

export async function v2ComponentPicks(
  theme: string,
  component: string,
  anchors: { ticker: string; name?: string; how?: string }[],
  deep_dives: import('./types').CompanyDeepDive[] = [],
  emphasis: string[] = [],
): Promise<import('./types').ComponentPicks> {
  return apiFetch('/api/research/v2/component-picks', {
    method: 'POST',
    body: JSON.stringify({ theme, component, anchors, deep_dives, emphasis }),
  });
}

export async function v2PickShovel(
  theme: string, deep_dives: import('./types').CompanyDeepDive[], summary: import('./types').ResearchSummary | object,
  emphasis: string[] = [],
  context: {
    components?: import('./types').ThemeComponent[];
    holdings?: import('./types').EtfHolding[];
    companies?: import('./types').V2Company[];
    matches?: import('./types').ComponentMatch[];
  } = {},
): Promise<import('./types').PickShovelResponse> {
  return apiFetch('/api/research/v2/pick-shovel', {
    method: 'POST',
    body: JSON.stringify({
      theme, deep_dives, summary, emphasis,
      components: context.components || [], holdings: context.holdings || [],
      companies: context.companies || [], matches: context.matches || [],
    }),
  });
}

export async function digDeeperPickShovel(
  params: import('./types').DigDeeperParams,
): Promise<import('./types').DigDeeperLayer> {
  return apiFetch<import('./types').DigDeeperLayer>('/api/market/pick-shovel/deeper', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}

// ---------------------------------------------------------------------------
// Tracked Companies
// ---------------------------------------------------------------------------

export async function trackCompany(
  data: Omit<import('./types').TrackedCompany, 'id' | 'created_at' | 'updated_at' | 'already_tracked'> & { llm_data: object; financial_data: object }
): Promise<import('./types').TrackedCompany> {
  return apiFetch<import('./types').TrackedCompany>('/api/tracked-companies', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function fetchTrackedCompanies(status?: 'active' | 'on_hold'): Promise<import('./types').TrackedListResponse> {
  const qs = status ? `?status=${status}` : '';
  return apiFetch<import('./types').TrackedListResponse>(`/api/tracked-companies${qs}`);
}

export async function untrackCompany(id: number, deactivate_agents = false): Promise<void> {
  await apiFetch<void>(`/api/tracked-companies/${id}?deactivate_agents=${deactivate_agents}`, { method: 'DELETE' });
}

export async function archiveTrackedCompany(id: number): Promise<import('./types').TrackedCompany> {
  return apiFetch<import('./types').TrackedCompany>(`/api/tracked-companies/${id}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status: 'on_hold' }),
  });
}

export async function restoreTrackedCompany(id: number): Promise<import('./types').TrackedCompany> {
  return apiFetch<import('./types').TrackedCompany>(`/api/tracked-companies/${id}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status: 'active' }),
  });
}

export async function fetchCompanyAgents(company_id: number): Promise<import('./types').Agent[]> {
  return apiFetch<import('./types').Agent[]>(`/api/tracked-companies/${company_id}/agents`);
}

export async function fetchMiniAgentTemplates(): Promise<import('./types').MiniAgentTemplate[]> {
  return apiFetch<import('./types').MiniAgentTemplate[]>('/api/tracked-companies/mini-agent-templates');
}

export async function updateTrackedNotes(id: number, user_notes: string | null): Promise<import('./types').TrackedCompany> {
  return apiFetch<import('./types').TrackedCompany>(`/api/tracked-companies/${id}/notes`, {
    method: 'PATCH',
    body: JSON.stringify({ user_notes }),
  });
}

export async function refreshTrackedPrice(id: number): Promise<import('./types').TrackedCompany> {
  return apiFetch<import('./types').TrackedCompany>(`/api/tracked-companies/${id}/refresh-price`, { method: 'POST' });
}

export async function agentFromTracking(
  company_id: number,
  extra_instruction?: string,
  schedule_type?: string,
  schedule_cron?: string,
  mini_agent_type?: string,
): Promise<import('./types').AgentFromTrackingResponse> {
  return apiFetch<import('./types').AgentFromTrackingResponse>(`/api/tracked-companies/${company_id}/create-agent`, {
    method: 'POST',
    body: JSON.stringify({ company_id, extra_instruction, schedule_type: schedule_type ?? 'manual', schedule_cron: schedule_cron ?? null, mini_agent_type }),
  });
}

export interface HighlightHistoricalPoint {
  date: string;
  price: number;
}

export interface HighlightNewsItem {
  title: string;
  publisher: string;
  link: string;
  published: string;
  summary: string;
}

export interface HighlightTechnicalSignal {
  rsi: number | null;
  rsi_signal: string | null;
  support: number | null;
  resistance: number | null;
  macd_signal: string | null;
  sma_signal: string | null;
}

export interface HighlightFundamentalSignal {
  market_cap: string | null;
  sector: string | null;
  industry: string | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  dividend_yield: number | null;
}

export interface HighlightCatalyst {
  kind: 'earnings' | 'analyst' | 'valuation' | 'technical' | 'sector' | 'macro' | 'quality' | string;
  title: string;
  detail: string;
  date: string | null;
  days_until: number | null;
  sentiment: 'bullish' | 'bearish' | 'neutral' | 'event' | string;
  importance: 'high' | 'medium' | 'low' | string;
}

export interface HighlightVerdict {
  label: string;                 // Strong Hold | Hold | Caution | Consider Exit | Strong Exit
  score: number;                 // 0-100, higher = stronger hold
  pillars_score: number | null;
  technical_score: number | null;
  risk_score: number | null;
  summary: string | null;
}

export interface HighlightHolding {
  ticker: string;
  company_name: string | null;
  asset_type: string;
  shares: number;
  cost_basis: number;
  current_price: number | null;
  day_change: number | null;
  day_change_pct: number | null;
  unrealized_gain_loss: number | null;
  unrealized_gain_loss_pct: number | null;
  weight_pct: number | null;
  last_5d_history: HighlightHistoricalPoint[];
  technicals: HighlightTechnicalSignal;
  fundamentals: HighlightFundamentalSignal;
  news: HighlightNewsItem[];
  hypothesis: string;
  verdict?: HighlightVerdict | null;
  catalysts?: HighlightCatalyst[];
}

export interface HighlightResponse {
  highlights: HighlightHolding[];
  total_portfolio_holdings: number;
}

export async function fetchPortfolioHighlights(): Promise<HighlightResponse> {
  return apiFetch<HighlightResponse>('/api/portfolio/highlights');
}

export async function dismissPortfolioHighlight(ticker: string): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/api/portfolio/highlights/dismiss', {
    method: 'POST',
    body: JSON.stringify({ ticker }),
  });
}

export async function resetPortfolioHighlights(): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>('/api/portfolio/highlights/reset', {
    method: 'POST',
  });
}

// Export the base URL for use in auth redirects
export const apiBase = API_BASE;

// ===== Debt Radar =====

export async function fetchDebtEntry(
  ticker: string,
): Promise<import('./types').DebtEntryResponse> {
  return apiFetch<import('./types').DebtEntryResponse>(
    `/api/debt/entry/${encodeURIComponent(ticker)}`,
  );
}

export async function explainDebtInstrument(
  ticker: string,
): Promise<import('./types').DebtExplainResponse> {
  return apiFetch<import('./types').DebtExplainResponse>(
    `/api/debt/entry/${encodeURIComponent(ticker)}/explain`,
    { method: 'POST' },
  );
}

export async function fetchDebtHistory(
  ticker: string,
): Promise<import('./types').DebtHistoryResponse> {
  return apiFetch<import('./types').DebtHistoryResponse>(
    `/api/debt/history/${encodeURIComponent(ticker)}`,
  );
}

