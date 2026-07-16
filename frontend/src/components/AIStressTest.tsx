import React, { useState, useEffect, useCallback } from 'react';
import { Activity, Loader2, RefreshCw, AlertTriangle, Sparkles, Shield, AlertOctagon, Info } from 'lucide-react';
import { fetchAIStressTest, generateAIStressTest } from '../api';
import type { AIStressTestData } from '../types';

interface Props {
  ticker: string;
}

export function AIStressTest({ ticker }: Props) {
  const [data, setData] = useState<AIStressTestData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchAIStressTest(ticker);
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
      const result = await generateAIStressTest(ticker);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const hasAnalysis = data?.analysis !== null && data?.analysis !== undefined;

  const getRiskColor = (risk: string) => {
    const lowered = risk.toLowerCase();
    if (lowered.includes('high')) return 'text-error';
    if (lowered.includes('medium')) return 'text-warning';
    if (lowered.includes('low')) return 'text-success';
    return 'text-base-content';
  };

  const getRiskBadge = (risk: string) => {
    const lowered = risk.toLowerCase();
    if (lowered.includes('high')) return 'badge-error';
    if (lowered.includes('medium')) return 'badge-warning';
    if (lowered.includes('low')) return 'badge-success';
    return 'badge-ghost';
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Activity className="w-6 h-6 text-primary" />
            AI Business Stress Test
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
            <p className="mt-4 text-base-content/60 font-medium">Loading Stress Test...</p>
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
            <Activity className="w-12 h-12 mx-auto text-base-content/20 mb-4" />
            <h3 className="text-lg font-bold mb-2">No Stress Test Data Yet</h3>
            <p className="text-base-content/60 max-w-sm mx-auto mb-6 text-sm">
              Evaluate the company across 7 vulnerability angles including "Seat-Death" Vulnerability and Code Replicability.
            </p>
            <button
              className="btn btn-primary gap-2"
              onClick={generate}
              disabled={generating}
            >
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Generating...</>
              ) : (
                <><Sparkles className="w-4 h-4" /> Run AI Stress Test</>
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
                <span className="text-sm font-medium">Regenerating test...</span>
              </div>
            )}

            <div className="flex items-center justify-between bg-base-200/40 p-5 rounded-xl border border-white/[0.03] mb-6">
              <div>
                <h3 className="text-sm font-bold text-base-content/50 uppercase tracking-widest mb-1">Overall Risk Rating</h3>
                <p className="text-xs text-base-content/60">Aggregated vulnerability to AI disruption</p>
              </div>
              <div className="flex items-center gap-2">
                {data.analysis.overallRisk.toLowerCase().includes('high') ? (
                  <AlertOctagon className="w-8 h-8 text-error" />
                ) : data.analysis.overallRisk.toLowerCase().includes('low') ? (
                  <Shield className="w-8 h-8 text-success" />
                ) : (
                  <Info className="w-8 h-8 text-warning" />
                )}
                <span className={`text-2xl font-black ${getRiskColor(data.analysis.overallRisk)}`}>
                  {data.analysis.overallRisk}
                </span>
              </div>
            </div>

            <div className="space-y-4">
              {data.analysis.angles.map((angle, i) => (
                <div key={i} className="flex flex-col md:flex-row gap-4 p-4 rounded-xl bg-base-200/40 border border-white/[0.03]">
                  <div className="md:w-1/3 shrink-0">
                    <h4 className="font-bold text-sm mb-2">{angle.name}</h4>
                    <span className={`badge ${getRiskBadge(angle.rating)} badge-sm font-bold`}>
                      {angle.rating}
                    </span>
                  </div>
                  <div className="md:w-2/3 border-t md:border-t-0 md:border-l border-base-200 pt-3 md:pt-0 md:pl-4">
                    <p className="text-sm text-base-content/70 leading-relaxed">
                      {angle.defense}
                    </p>
                  </div>
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
