import React from 'react';
import { Newspaper, ExternalLink } from 'lucide-react';
import type { ExitGeopoliticalData } from '../../types';

interface Props {
  data: ExitGeopoliticalData;
}

function sentimentBadge(sentiment: string) {
  if (sentiment === 'Negative') return 'badge-error';
  if (sentiment === 'Mixed' || sentiment === 'Slightly Negative') return 'badge-warning';
  return 'badge-success';
}

function timeAgo(dateStr?: string): string {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffH = Math.floor(diffMs / 3600000);
    if (diffH < 1) return 'just now';
    if (diffH < 24) return `${diffH}h ago`;
    const diffD = Math.floor(diffH / 24);
    if (diffD === 1) return '1d ago';
    if (diffD < 7) return `${diffD}d ago`;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}

export function PillarGeopolitical({ data }: Props) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">News Sentiment</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${sentimentBadge(data.news_sentiment)}`}>
              {data.news_sentiment}
            </span>
          </div>
          <div className="text-xs text-base-content/50 mt-0.5">
            {data.negative_news_count}/{data.total_news_count} negative
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Sector Regulatory Risk</div>
          <div className="mt-1">
            <span className={`badge badge-sm ${data.sector_regulatory_risk === 'High' ? 'badge-error' : data.sector_regulatory_risk === 'Medium' ? 'badge-warning' : 'badge-success'}`}>
              {data.sector_regulatory_risk}
            </span>
          </div>
        </div>
        <div className="bg-base-200 rounded-lg p-2.5">
          <div className="text-xs text-base-content/60">Regulatory Themes</div>
          <div className="flex flex-wrap gap-1 mt-1">
            {data.regulatory_themes_detected.length > 0 ? (
              data.regulatory_themes_detected.map((t, i) => (
                <span key={i} className="badge badge-xs badge-warning">{t}</span>
              ))
            ) : (
              <span className="text-xs text-base-content/50">None detected</span>
            )}
          </div>
        </div>
      </div>

      {/* Recent Headlines */}
      {data.recent_headlines.length > 0 && (
        <div>
          <h5 className="text-xs font-bold mb-2 flex items-center gap-1.5">
            <Newspaper className="w-3.5 h-3.5" />
            Recent Headlines ({data.total_news_count} articles)
          </h5>
          <div className="space-y-1.5 max-h-72 overflow-y-auto">
            {data.recent_headlines.slice(0, 10).map((h, i) => (
              <div key={i} className="flex items-start gap-2 text-xs group">
                <span className={`badge badge-xs mt-0.5 flex-shrink-0 ${h.sentiment === 'Negative' ? 'badge-error' : h.sentiment === 'Positive' ? 'badge-success' : 'badge-ghost'}`}>
                  {h.sentiment === 'Negative' ? '−' : h.sentiment === 'Positive' ? '+' : '·'}
                </span>
                <div className="flex-1 min-w-0">
                  {h.link ? (
                    <a
                      href={h.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-base-content/80 hover:text-primary line-clamp-2 cursor-pointer"
                    >
                      {h.title}
                      <ExternalLink className="w-2.5 h-2.5 inline ml-1 opacity-0 group-hover:opacity-100" />
                    </a>
                  ) : (
                    <span className="text-base-content/80 line-clamp-2">{h.title}</span>
                  )}
                  {(h.publisher || h.published) && (
                    <div className="text-base-content/40 text-[10px] mt-0.5">
                      {h.publisher && <span>{h.publisher}</span>}
                      {h.publisher && h.published && <span className="mx-1">·</span>}
                      {h.published && <span>{timeAgo(h.published)}</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.recent_headlines.length === 0 && (
        <div className="text-center text-base-content/40 text-xs py-4">
          No recent news articles available for this ticker.
        </div>
      )}
    </div>
  );
}
