import React, { useState, useEffect, useCallback } from 'react';
import { Castle, Loader2, RefreshCw, AlertTriangle, Sparkles, CheckCircle2, XCircle } from 'lucide-react';
import { fetchAIFortress, generateAIFortress } from '../api';
import type { AIFortressData } from '../types';

interface Props {
  ticker: string;
}

export function AIFortressAnalysis({ ticker }: Props) {
  const [data, setData] = useState<AIFortressData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchAIFortress(ticker);
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
      const result = await generateAIFortress(ticker);
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
            <Castle className="w-6 h-6 text-primary" />
            AI Fortress Architecture
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
            <p className="mt-4 text-base-content/60 font-medium">Loading Fortress Analysis...</p>
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
            <Castle className="w-12 h-12 mx-auto text-base-content/20 mb-4" />
            <h3 className="text-lg font-bold mb-2">No Fortress Analysis Yet</h3>
            <p className="text-base-content/60 max-w-sm mx-auto mb-6 text-sm">
              Assess the stock across the Seven Fortress Architectures to see if it survives the shift from Human-centric to Agent-centric workflows.
            </p>
            <button
              className="btn btn-primary gap-2"
              onClick={generate}
              disabled={generating}
            >
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</>
              ) : (
                <><Sparkles className="w-4 h-4" /> Run Fortress Analysis</>
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

            <div className="bg-base-200/40 p-5 rounded-xl border border-white/[0.03] mb-6">
              <h3 className="text-sm font-bold text-base-content/50 uppercase tracking-widest mb-2">Overall Verdict</h3>
              <p className="text-base font-medium text-base-content">{data.analysis.summary}</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {data.analysis.fortresses.map((fort, i) => (
                <div key={i} className={`p-4 rounded-xl border transition-colors ${fort.present ? 'bg-success/5 border-success/20' : 'bg-base-200/40 border-white/[0.03]'}`}>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h4 className="font-bold flex items-center gap-2 text-sm">
                      {fort.present ? <CheckCircle2 className="w-4 h-4 text-success" /> : <XCircle className="w-4 h-4 text-base-content/30" />}
                      {fort.name}
                    </h4>
                    <span className={`text-sm font-bold px-2 py-0.5 rounded-full ${fort.present ? 'bg-success/20 text-success' : 'bg-base-200 text-base-content/50'}`}>
                      {fort.score}/5
                    </span>
                  </div>
                  <p className="text-sm text-base-content/70 leading-relaxed mt-1">
                    {fort.explanation}
                  </p>
                </div>
              ))}
            </div>

            {data.created_at && (
              <p className="text-xs text-base-content/40 text-right mt-4">
                Last updated: {new Date(data.created_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
