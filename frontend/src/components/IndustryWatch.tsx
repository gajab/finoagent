import React, { useState } from 'react';
import { Globe, Search, ExternalLink, AlertTriangle, Settings } from 'lucide-react';
import { searchWeb, callLLM } from '../api';

interface IndustryWatchProps {
  ticker: string;
  companyName: string;
  sector: string;
}

interface IndustryResult {
  title: string;
  snippet: string;
  url: string;
}

export const IndustryWatch: React.FC<IndustryWatchProps> = ({
  ticker,
  companyName,
  sector,
}) => {
  const [results, setResults] = useState<IndustryResult[]>([]);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);

  const fetchUpdates = async () => {
    setLoading(true);
    setApiKeyMissing(false);
    try {
      const queries = [
        `${sector} industry outlook trends impact ${new Date().getFullYear()}`,
        `${ticker} ${companyName} economic factors risks opportunities`,
      ];

      const allResults: IndustryResult[] = [];

      for (const query of queries) {
        try {
          const res = await searchWeb(query);
          if (res.results && Array.isArray(res.results)) {
            for (const r of res.results) {
              if (r.title && r.snippet) {
                allResults.push({
                  title: r.title,
                  snippet: r.snippet,
                  url: r.link || '',
                });
              }
            }
          }
        } catch (err: any) {
          const msg = err?.message?.toLowerCase() || '';
          if (msg.includes('api key') || msg.includes('not configured') || msg.includes('unauthorized') || msg.includes('failed')) {
            throw err;
          }
          // Continue with other queries for non-critical errors
        }
      }

      setResults(allResults);
      setLoaded(true);

      // Try to get LLM analysis
      if (allResults.length > 0) {
        try {
          const context = allResults
            .map((r) => `- ${r.title}: ${r.snippet}`)
            .join('\n');
          const llmRes = await callLLM('gpt-4o-mini', [
            {
              role: 'system',
              content:
                'You are a financial analyst. Provide a brief analysis of the industry trends and their potential impact on the given stock. Be concise — 3-4 bullet points max.',
            },
            {
              role: 'user',
              content: `Analyze how these industry developments might affect ${ticker} (${companyName}, ${sector}):\n\n${context}`,
            },
          ], 400);
          setAnalysis(llmRes.content);
        } catch {
          // LLM analysis is optional
        }
      }
    } catch (err: any) {
      const msg = err?.message || '';
      const msgLower = msg.toLowerCase();
      if (msgLower.includes('api key') || msgLower.includes('not configured') || msgLower.includes('unauthorized') || msgLower.includes('failed')) {
        setApiKeyMissing(true);
      } else {
        setResults([]);
        setLoaded(true);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm flex items-center gap-2">
          <Globe size={20} /> Industry &amp; Economic Watch
          {sector && <span className="badge badge-sm badge-outline ml-2">{sector}</span>}
        </h3>

        {apiKeyMissing && (
          <div className="alert alert-warning py-2">
            <Settings size={14} />
            <span className="text-sm">
              API keys required. Please configure your SerpAPI and/or AI provider (OpenAI or Gemini) keys in{' '}
              <a href="/settings" className="link link-primary">
                Settings
              </a>{' '}
              to use this feature.
            </span>
          </div>
        )}

        {!loaded && !loading && !apiKeyMissing && (
          <div className="text-center py-6">
            <p className="text-base-content/60 mb-3">
              Search for industry trends, economic factors, and macro events that could impact{' '}
              {ticker}.
            </p>
            <button className="btn btn-primary btn-sm" onClick={fetchUpdates}>
              <Search size={14} /> Fetch Industry Updates
            </button>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-base-content/60">
            <span className="loading loading-spinner loading-sm" />
            Searching for industry and economic updates...
          </div>
        )}

        {loaded && results.length === 0 && !apiKeyMissing && (
          <div className="alert alert-warning">
            <AlertTriangle size={16} />
            <span>No recent industry updates found. Try again later.</span>
          </div>
        )}

        {loaded && results.length > 0 && (
          <>
            {analysis && (
              <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03] border border-primary/20 mb-3">
                <h4 className="text-sm font-bold text-primary mb-1">🔍 AI Analysis</h4>
                <p className="text-sm text-base-content/80 whitespace-pre-line">{analysis}</p>
              </div>
            )}
            <div className="space-y-3 max-h-[400px] overflow-y-auto pr-1">
              {results.map((r, i) => (
                <div key={i} className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-semibold text-primary hover:underline flex items-start gap-1 text-sm"
                  >
                    {r.title}
                    <ExternalLink size={12} className="flex-shrink-0 mt-0.5 opacity-60" />
                  </a>
                  <p className="text-xs text-base-content/60 mt-1">{r.snippet}</p>
                </div>
              ))}
              <button className="btn btn-ghost btn-xs btn-block mt-2" onClick={fetchUpdates}>
                Refresh
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
