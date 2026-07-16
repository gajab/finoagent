import React, { useEffect, useState, useRef } from 'react';
import {
  Sparkles, TrendingUp, TrendingDown, Minus, AlertCircle, Settings,
  ChevronDown, ChevronUp,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { NewsItem, NewsSummary } from '../types';
import { callLLM } from '../api';

interface AiNewsSummaryProps {
  news: NewsItem[];
}

const THEME_COLORS: Record<string, string> = {
  'Earnings & Financials': 'badge-primary',
  'AI & Technology': 'badge-secondary',
  'Market Movement': 'badge-accent',
  'Analyst Coverage': 'badge-info',
  'Dividends & Buybacks': 'badge-success',
  'Regulatory & Legal': 'badge-warning',
  'M&A Activity': 'badge-error',
  'Product & Strategy': 'badge-primary',
};

function SentimentIndicator({ sentiment }: { sentiment: string }) {
  const lower = sentiment.toLowerCase();
  if (lower.includes('positive')) {
    return (
      <span className="flex items-center gap-1 text-success font-semibold text-sm">
        <TrendingUp size={14} /> {sentiment}
      </span>
    );
  }
  if (lower.includes('negative')) {
    return (
      <span className="flex items-center gap-1 text-error font-semibold text-sm">
        <TrendingDown size={14} /> {sentiment}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-warning font-semibold text-sm">
      <Minus size={14} /> {sentiment}
    </span>
  );
}

export const AiNewsSummary: React.FC<AiNewsSummaryProps> = ({ news }) => {
  const [summary, setSummary] = useState<NewsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsApiKey, setNeedsApiKey] = useState(false);
  const [apiKeyMsg, setApiKeyMsg] = useState<string>('');
  const [showDetails, setShowDetails] = useState(false);
  const [hasRequested, setHasRequested] = useState(false);
  const prevNewsRef = useRef<string>('');

  useEffect(() => {
    if (!news || news.length === 0) {
      setSummary(null);
      return;
    }

    if (!hasRequested) return;

    // Avoid re-fetching for the same news set
    const newsFingerprint = news.map(n => n.title).join('|');
    if (newsFingerprint === prevNewsRef.current) return;
    prevNewsRef.current = newsFingerprint;

    let cancelled = false;

    async function generateSummary() {
      setLoading(true);
      setError(null);
      setNeedsApiKey(false);
      setApiKeyMsg('');

      // Build rich text from news — include all available details
      const topNews = news.slice(0, 10);
      const newsText = topNews
        .map(
          (n, i) =>
            `${i + 1}. "${n.title}" — ${n.publisher} (${n.published})${n.summary ? `\n   ${n.summary}` : ''}`
        )
        .join('\n\n');

      const systemPrompt = `You are a senior financial news analyst. You will receive recent news articles about a stock or company.

Your task is to produce a comprehensive, actionable news digest.

Return a JSON object with this EXACT structure:
{
  "totalArticles": <number of articles you analyzed>,
  "digest": "<A 3-5 sentence narrative summary that synthesizes the most important developments from these articles. Focus on: what happened, why it matters for investors, and what the implications are. Write as a professional analyst briefing — be specific about numbers, dates, and facts mentioned in the articles. Do NOT just list headlines — actually summarize the substance and connect the dots between related stories.>",
  "sentiment": "<one of: Strongly Positive, Positive, Mixed/Neutral, Negative, Strongly Negative>",
  "themes": [<array of relevant themes from: Earnings & Financials, AI & Technology, Market Movement, Analyst Coverage, Dividends & Buybacks, Regulatory & Legal, M&A Activity, Product & Strategy>],
  "topHeadlines": [<the 3-5 most impactful headlines verbatim>],
  "sourceBreakdown": {<publisher>: <count>}
}

CRITICAL: The "digest" field must be a real analytical summary, NOT a list of headlines. Synthesize the information across articles to tell a coherent story about what's happening with this company/stock.

Return ONLY valid JSON, no markdown fences, no explanation.`;

      try {
        const response = await callLLM(
          'gpt-4o-mini',
          [
            { role: 'system', content: systemPrompt },
            {
              role: 'user',
              content: `Summarize and analyze these ${topNews.length} recent news articles:\n\n${newsText}`,
            },
          ],
          1200,
          true
        );

        if (cancelled) return;

        // Parse the JSON from the LLM response
        let parsed: NewsSummary;
        try {
          let content = response.content.trim();

          // Try to extract content between ```json and ``` if present
          if (content.includes('```')) {
            const parts = content.split(/```(?:json)?/);
            if (parts.length > 1) {
              const secondPart = parts[1].split('```')[0];
              content = secondPart.trim();
            }
          }

          // Try to parse, and fallback to extracting outer-most curly braces if direct parse fails
          try {
            parsed = JSON.parse(content);
          } catch (jsonErr) {
            const firstBrace = content.indexOf('{');
            const lastBrace = content.lastIndexOf('}');
            if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
              const jsonSub = content.substring(firstBrace, lastBrace + 1);
              parsed = JSON.parse(jsonSub);
            } else {
              throw jsonErr;
            }
          }

          // Ensure digest exists
          if (!parsed.digest) {
            parsed.digest = '';
          }
        } catch (err) {
          console.error('Failed to parse AI summary response. Raw:', response.content, err);
          throw new Error('Failed to parse AI summary response');
        }

        setSummary(parsed);
      } catch (err: any) {
        if (cancelled) return;
        const msg = err?.message || 'Unknown error';
        const lower = msg.toLowerCase();
        // Only treat genuine key/auth problems as "needs API key". Note: do NOT match
        // the substring "openai" — the Gemini compat URL contains it, so a Gemini 404
        // would otherwise be misreported as a missing OpenAI key.
        if (
          lower.includes('api key') ||
          lower.includes('not configured') ||
          lower.includes('unauthorized') ||
          msg.includes('401')
        ) {
          setApiKeyMsg(msg);
          setNeedsApiKey(true);
        } else {
          setError(msg);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    generateSummary();

    return () => {
      cancelled = true;
    };
  }, [news, hasRequested]);

  if (!hasRequested) {
    return (
      <div className="bg-base-300 rounded-xl p-6 border border-primary/20 flex flex-col items-center justify-center text-center">
        <Sparkles size={32} className="text-primary mb-3" />
        <h3 className="font-bold text-lg mb-2">AI News Digest</h3>
        <p className="text-sm text-base-content/60 max-w-sm mb-4">
          Generate a comprehensive, actionable summary of the latest news using our AI agents. This process uses LLM tokens.
        </p>
        <button
          className="btn btn-primary"
          onClick={() => setHasRequested(true)}
          disabled={!news || news.length === 0}
        >
          <Sparkles size={16} />
          Generate AI Digest
        </button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-base-300 rounded-xl p-4 border border-primary/20">
        <div className="flex items-center gap-2">
          <Sparkles size={18} className="text-primary animate-pulse" />
          <span className="font-bold text-base-content">AI News Digest</span>
          <span className="loading loading-dots loading-sm text-primary" />
        </div>
        <p className="text-sm text-base-content/50 mt-2">
          Summarizing {Math.min(news.length, 10)} articles...
        </p>
      </div>
    );
  }

  if (needsApiKey) {
    return (
      <div className="bg-base-300 rounded-xl p-4 border border-warning/30">
        <div className="flex items-center gap-2">
          <Settings size={18} className="text-warning" />
          <span className="font-bold text-base-content">AI News Digest</span>
        </div>
        <p className="text-sm text-base-content/60 mt-2">
          {apiKeyMsg || 'Configure your AI provider API key to enable AI-powered news digests.'}{' '}
          <a href="/settings" className="link link-primary">
            Open Settings
          </a>
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-base-300 rounded-xl p-4 border border-error/30">
        <div className="flex items-center gap-2">
          <AlertCircle size={18} className="text-error" />
          <span className="font-bold text-base-content">AI News Digest</span>
        </div>
        <p className="text-sm text-error/80 mt-2">{error}</p>
      </div>
    );
  }

  if (!summary) return null;

  return (
    <div className="bg-base-300 rounded-xl p-4 border border-primary/20">
      {/* Header Row */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Sparkles size={18} className="text-primary" />
          <span className="font-bold text-base-content">AI News Digest</span>
          <span className="badge badge-sm badge-primary badge-outline">
            {summary.totalArticles} articles
          </span>
        </div>
        <SentimentIndicator sentiment={summary.sentiment} />
      </div>

      {/* Narrative Summary — the main attraction */}
      {summary.digest && (
        <div className="prose prose-sm max-w-none prose-p:text-base-content/80 prose-p:my-1 prose-strong:text-base-content mb-3">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{summary.digest}</ReactMarkdown>
        </div>
      )}

      {/* Themes */}
      {summary.themes && summary.themes.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {summary.themes.map((theme) => (
            <span
              key={theme}
              className={`badge badge-xs ${THEME_COLORS[theme] || 'badge-ghost'}`}
            >
              {theme}
            </span>
          ))}
        </div>
      )}

      {/* Expandable Details */}
      <button
        className="text-xs text-base-content/40 hover:text-base-content/60 flex items-center gap-1 mt-1"
        onClick={() => setShowDetails(!showDetails)}
      >
        {showDetails ? (
          <>
            <ChevronUp size={12} /> Hide details
          </>
        ) : (
          <>
            <ChevronDown size={12} /> Top headlines & sources
          </>
        )}
      </button>

      {showDetails && (
        <div className="mt-2 space-y-2 border-t border-base-content/10 pt-2">
          {/* Top Headlines */}
          {summary.topHeadlines && summary.topHeadlines.length > 0 && (
            <div>
              <span className="text-[10px] text-base-content/40 uppercase tracking-wider">
                Key Headlines
              </span>
              <ul className="mt-1 space-y-0.5">
                {summary.topHeadlines.map((headline, i) => (
                  <li
                    key={i}
                    className="text-xs text-base-content/60 flex items-start gap-1.5"
                  >
                    <span className="text-primary mt-0.5 flex-shrink-0">•</span>
                    <span>{headline}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Source Breakdown */}
          {summary.sourceBreakdown &&
            Object.keys(summary.sourceBreakdown).length > 0 && (
              <div className="flex flex-wrap gap-2">
                <span className="text-[10px] text-base-content/40 uppercase tracking-wider w-full">
                  Sources
                </span>
                {Object.entries(summary.sourceBreakdown)
                  .slice(0, 5)
                  .map(([src, count]) => (
                    <span key={src} className="text-xs text-base-content/40">
                      {src} ({count as number})
                    </span>
                  ))}
              </div>
            )}
        </div>
      )}
    </div>
  );
};
