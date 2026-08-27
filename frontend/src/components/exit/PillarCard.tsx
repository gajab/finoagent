import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Brain, Loader2 } from 'lucide-react';
import { apiBase } from '../../api';

interface Props {
  title: string;
  icon: React.ReactNode;
  score: number;
  description: string;
  ticker: string;
  pillarKey: string;
  children: React.ReactNode;
}

// Health convention: higher = healthier / stronger.
function scoreBadge(score: number): string {
  if (score >= 75) return 'bg-success/15 text-success border-success/20';
  if (score >= 60) return 'bg-info/15 text-info border-info/20';
  if (score >= 40) return 'bg-warning/15 text-warning border-warning/20';
  return 'bg-error/15 text-error border-error/20';
}

function scoreLabel(score: number): string {
  if (score >= 75) return 'Healthy';
  if (score >= 60) return 'Solid';
  if (score >= 40) return 'Watch';
  return 'Weak';
}

function scoreBarColor(score: number): string {
  if (score >= 75) return 'bg-success';
  if (score >= 60) return 'bg-info';
  if (score >= 40) return 'bg-warning';
  return 'bg-error';
}

export function PillarCard({ title, icon, score, description, ticker, pillarKey, children }: Props) {
  const [expanded, setExpanded] = useState(true);
  const [llmAnalysis, setLlmAnalysis] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);

  const fetchLlmAnalysis = async () => {
    if (llmAnalysis) return;
    setLlmLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/portfolio/analyze-exit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ ticker, persona: `Expert analyst focusing on ${pillarKey}` }),
      });
      if (!res.ok) throw new Error('Failed');
      const data = await res.json();
      const pillarData = data.analysis?.pillars?.find((p: any) =>
        p.name.toLowerCase().includes(pillarKey.toLowerCase())
      );
      setLlmAnalysis(pillarData?.analysis || data.analysis?.persona_summary || 'Analysis not available.');
    } catch {
      setLlmAnalysis('Unable to fetch AI analysis. Please ensure your API key is configured.');
    } finally {
      setLlmLoading(false);
    }
  };

  return (
    <div className="glass-card overflow-hidden transition-all duration-300">
      <div className="p-4 sm:p-5">
        {/* Header */}
        <div
          className="flex items-center justify-between cursor-pointer group"
          onClick={() => setExpanded(!expanded)}
        >
          <div className="flex items-center gap-3">
            <span className="opacity-70 group-hover:opacity-100 transition-opacity">{icon}</span>
            <div>
              <h4 className="font-bold text-sm tracking-tight">{title}</h4>
              <p className="text-[11px] text-base-content/40 hidden sm:block">{description}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2.5">
              <div className="w-20 h-2 bg-base-300/50 rounded-full overflow-hidden hidden sm:block">
                <div
                  className={`h-2 rounded-full ${scoreBarColor(score)} transition-all duration-700`}
                  style={{ width: `${score}%` }}
                />
              </div>
              <span className={`text-xs font-bold px-2.5 py-1 rounded-lg border tabular-nums ${scoreBadge(score)}`}>
                {score} · {scoreLabel(score)}
              </span>
            </div>
            <span className="text-base-content/30 group-hover:text-base-content/60 transition-colors">
              {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </span>
          </div>
        </div>

        {/* Body */}
        {expanded && (
          <div className="mt-4 space-y-4 animate-fade-in">
            {children}

            {/* AI Analysis */}
            <div className="pt-3 border-t border-white/[0.04]">
              {!llmAnalysis && !llmLoading && (
                <button
                  className="btn btn-ghost btn-xs gap-1.5 rounded-lg text-primary/60 hover:text-primary"
                  onClick={(e) => { e.stopPropagation(); fetchLlmAnalysis(); }}
                >
                  <Brain className="w-3.5 h-3.5" />
                  Get AI Analysis
                </button>
              )}
              {llmLoading && (
                <div className="flex items-center gap-2 text-xs text-base-content/50">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Generating analysis...
                </div>
              )}
              {llmAnalysis && (
                <div className="bg-base-200/30 rounded-xl p-4 border border-white/[0.03]">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Brain className="w-3.5 h-3.5 text-primary" />
                    <span className="text-xs font-bold text-primary">AI Analysis</span>
                  </div>
                  <p className="text-xs text-base-content/70 leading-relaxed whitespace-pre-line">{llmAnalysis}</p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
