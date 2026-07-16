import React from 'react';
import { ExternalLink, Newspaper } from 'lucide-react';
import type { NewsItem } from '../types';
import { AiNewsSummary } from './AiNewsSummary';

interface NewsFeedProps {
  news: NewsItem[];
}

export const NewsFeed: React.FC<NewsFeedProps> = ({ news }) => {
  if (news.length === 0) {
    return (
      <div className="glass-card p-5">
        <h3 className="font-bold text-sm flex items-center gap-2 mb-3">
          <Newspaper className="w-4 h-4 text-primary" /> Recent News
        </h3>
        <p className="text-sm text-base-content/40">No recent news found.</p>
      </div>
    );
  }

  return (
    <div className="glass-card p-5">
      <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
        <Newspaper className="w-4 h-4 text-primary" /> Recent News
      </h3>

      {/* AI News Summary at the top */}
      <AiNewsSummary news={news} />

      <div className="space-y-2.5 max-h-[500px] overflow-y-auto pr-1 mt-3">
        {news.map((item, i) => (
          <div key={i} className="bg-base-200/40 rounded-xl p-3.5 border border-white/[0.03] hover:border-white/[0.06] hover:bg-base-200/60 transition-all duration-200 group">
            <div className="flex justify-between items-start gap-2">
              <div className="flex-1 min-w-0">
                <a
                  href={item.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-semibold text-sm text-base-content/90 hover:text-primary flex items-start gap-1.5 transition-colors leading-snug"
                >
                  <span className="line-clamp-2">{item.title}</span>
                  <ExternalLink className="w-3 h-3 flex-shrink-0 mt-1 opacity-0 group-hover:opacity-60 transition-opacity" />
                </a>
                {item.summary && (
                  <p className="text-xs text-base-content/40 mt-1.5 line-clamp-2 leading-relaxed">{item.summary}</p>
                )}
                <div className="flex items-center gap-2 mt-2">
                  <span className="text-[10px] px-2 py-0.5 rounded-md bg-base-300/60 text-base-content/50 font-medium">{item.publisher}</span>
                  <span className="text-[10px] text-base-content/30">{item.published}</span>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
