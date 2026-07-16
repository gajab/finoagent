import React, { useState, useEffect, useCallback } from 'react';
import {
  Search, Loader2, AlertTriangle, RefreshCw, Sparkles,
  Shield, Briefcase, Award, Lightbulb, Users,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchQualitativeAnalysis, generateQualitativeAnalysis } from '../api';
import type { LLMAnalysisResponse } from '../types';

interface Props {
  ticker: string;
}

export function QualitativeAnalysis({ ticker }: Props) {
  const [data, setData] = useState<LLMAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchQualitativeAnalysis(ticker);
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
      const result = await generateQualitativeAnalysis(ticker);
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
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Search className="w-5 h-5 text-accent" />
            Qualitative Analysis
          </h2>
          {hasAnalysis && (
            <button
              className="btn btn-ghost btn-xs gap-1"
              onClick={generate}
              disabled={generating}
            >
              {generating ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              Regenerate
            </button>
          )}
        </div>

        {loading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-base-content/60">Loading...</span>
          </div>
        )}

        {error && !loading && (
          <div className="alert alert-error text-sm">
            <AlertTriangle className="w-4 h-4" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !hasAnalysis && !error && (
          <div className="text-center py-8">
            {/* Topic badges */}
            <div className="flex flex-wrap justify-center gap-2 mb-4">
              <span className="badge badge-outline badge-sm gap-1"><Briefcase className="w-3 h-3" /> Management Quality</span>
              <span className="badge badge-outline badge-sm gap-1"><Shield className="w-3 h-3" /> Business Resilience</span>
              <span className="badge badge-outline badge-sm gap-1"><Award className="w-3 h-3" /> Competitive Moat</span>
              <span className="badge badge-outline badge-sm gap-1"><Lightbulb className="w-3 h-3" /> Innovation</span>
              <span className="badge badge-outline badge-sm gap-1"><Users className="w-3 h-3" /> Reputation</span>
            </div>
            <p className="text-sm text-base-content/50 mb-4 max-w-md mx-auto">
              Generate an AI-powered qualitative analysis covering management quality, competitive advantages,
              business model resilience, product pipeline, and stakeholder perception.
            </p>
            <button
              className="btn btn-primary btn-sm gap-2"
              onClick={generate}
              disabled={generating}
            >
              {generating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Generating Analysis...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4" />
                  Generate Qualitative Analysis
                </>
              )}
            </button>
            <p className="text-[10px] text-base-content/30 mt-2">Uses LLM — costs apply per your API key</p>
          </div>
        )}

        {!loading && hasAnalysis && (
          <div className="space-y-3 mt-1">
            {generating && (
              <div className="flex items-center gap-2 text-sm text-primary">
                <Loader2 className="w-4 h-4 animate-spin" />
                Regenerating analysis...
              </div>
            )}

            {/* Analysis content */}
            <div className="prose prose-sm max-w-none prose-headings:text-base-content prose-p:text-base-content/70 prose-strong:text-base-content prose-li:text-base-content/70">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {data!.analysis!}
              </ReactMarkdown>
            </div>

            {/* Timestamp */}
            {data?.created_at && (
              <p className="text-[10px] text-base-content/30">
                Generated: {new Date(data.created_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
