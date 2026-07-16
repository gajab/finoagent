import React, { useState, useEffect, useCallback } from 'react';
import { Bot, Loader2, RefreshCw, AlertTriangle, Sparkles } from 'lucide-react';
import { fetchAIImpact, generateAIImpact } from '../api';
import type { AIImpactData } from '../types';

interface Props {
  ticker: string;
}

export function AIImpact({ ticker }: Props) {
  const [data, setData] = useState<AIImpactData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchAIImpact(ticker);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  const generate = async () => {
    try {
      setGenerating(true);
      setError(null);
      const result = await generateAIImpact(ticker);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const hasAnalysis = data?.analysis !== null && data?.analysis !== undefined;

  return (
    <div className="glass-card">
      <div className="p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Bot className="w-6 h-6 text-primary" />
            AI Impact Analysis
          </h2>
          {hasAnalysis && (
            <button
              className="btn btn-ghost btn-sm gap-2"
              onClick={generate}
              disabled={generating}
            >
              {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Regenerate
            </button>
          )}
        </div>

        {loading && (
          <div className="flex flex-col items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-primary opacity-50" />
            <p className="mt-4 text-base-content/60 font-medium">Loading AI Impact...</p>
          </div>
        )}

        {error && !loading && (
          <div className="alert alert-error">
            <AlertTriangle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !hasAnalysis && !error && (
          <div className="py-12 text-center rounded-xl bg-base-200/30 border border-white/[0.03]">
            <Bot className="w-12 h-12 mx-auto text-base-content/20 mb-4" />
            <h3 className="text-lg font-bold mb-2">No AI Impact Data Yet</h3>
            <p className="text-base-content/60 max-w-sm mx-auto mb-6 text-sm">
              Generate an analysis across 8 dimensions including Labor Automation, Revenue Disruption, and Competitive Moat.
            </p>
            <button
              className="btn btn-primary gap-2"
              onClick={generate}
              disabled={generating}
            >
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</>
              ) : (
                <><Sparkles className="w-4 h-4" /> Run AI Impact Analysis</>
              )}
            </button>
            <p className="text-xs text-base-content/40 mt-3">Uses LLM — costs apply per your API key</p>
          </div>
        )}

        {!loading && hasAnalysis && data?.analysis && (
          <div className="space-y-6">
            {generating && (
              <div className="alert alert-info py-2 shadow-sm animate-pulse flex items-center gap-3">
                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                <span className="text-sm font-medium">Regenerating analysis...</span>
              </div>
            )}

            <div className="space-y-4">
              {data.analysis.dimensions.map((dim, i) => (
                <div key={i} className="flex flex-col sm:flex-row gap-4 p-4 rounded-xl bg-base-200/40 border border-white/[0.03] hover:border-white/[0.06] transition-colors">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-mono px-2 py-0.5 rounded-full bg-base-200 text-base-content/70">
                        #{i + 1}
                      </span>
                      <h4 className="font-bold text-sm">{dim.name}</h4>
                    </div>
                    <p className="text-sm text-base-content/70 leading-relaxed pl-8">
                      {dim.explanation}
                    </p>
                  </div>
                  <div className="sm:border-l sm:border-base-200 sm:pl-4 flex flex-row sm:flex-col items-center sm:items-end justify-between sm:justify-center gap-2 sm:gap-1 min-w-[100px] shrink-0">
                    <div className="text-xs font-medium text-base-content/50 uppercase tracking-wider">
                      Weight: {dim.weight}%
                    </div>
                    <div className="flex items-baseline gap-1">
                      <span className={`text-xl font-bold ${
                        dim.score >= 4 ? 'text-success' : dim.score <= 2 ? 'text-error' : 'text-warning'
                      }`}>
                        {dim.score}
                      </span>
                      <span className="text-sm font-medium text-base-content/40">/5</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex flex-col sm:flex-row items-center justify-between p-6 rounded-xl bg-gradient-to-r from-primary/10 to-transparent border border-primary/20 mt-6">
              <div>
                <h3 className="text-lg font-bold text-base-content">Composite Score</h3>
                <p className="text-sm text-base-content/60">Weighted average of all 8 dimensions</p>
              </div>
              <div className="flex items-baseline gap-1 mt-2 sm:mt-0 bg-base-200/30 px-6 py-3 rounded-xl border border-white/[0.03]">
                <span className={`text-3xl font-black ${
                  data.analysis.compositeScore >= 4 ? 'text-success' : data.analysis.compositeScore <= 2 ? 'text-error' : 'text-warning'
                }`}>
                  {data.analysis.compositeScore.toFixed(1)}
                </span>
                <span className="text-lg font-bold text-base-content/40">/5</span>
              </div>
            </div>

            {data.created_at && (
              <p className="text-xs text-base-content/40 text-right mt-2">
                Last updated: {new Date(data.created_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
