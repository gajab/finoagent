import React, { useState, useEffect, useCallback } from 'react';
import { IndianRupee, Loader2, RefreshCw, AlertTriangle, Sparkles, BookOpen, Scale } from 'lucide-react';
import { fetchRupee, generateRupee } from '../api';
import type { RupeeData } from '../types';

interface Props {
  ticker: string;
}

/** Map the Sethji's final stance to a colour + plain-English gloss. */
function stanceStyle(stance: string): { cls: string; gloss: string } {
  const s = (stance || '').toLowerCase();
  if (s.includes('ghar')) {
    return { cls: 'bg-success/10 border-success/30 text-success', gloss: 'Great business, great price — buy it now' };
  }
  if (s.includes('taareef') || s.includes('tareef')) {
    return { cls: 'bg-warning/10 border-warning/30 text-warning', gloss: 'Great business, but price is too high — wait for a drop' };
  }
  if (s.includes('inkaar') || s.includes('inkar') || s.includes('saaf')) {
    return { cls: 'bg-error/10 border-error/30 text-error', gloss: 'Absolutely not — bleeding cash / high debt' };
  }
  return { cls: 'bg-base-200/60 border-white/[0.06] text-base-content', gloss: '' };
}

export function RupeeAnalysis({ ticker }: Props) {
  const [data, setData] = useState<RupeeData | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchRupee(ticker);
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
      const result = await generateRupee(ticker);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const analysis = data?.analysis ?? null;
  const hasAnalysis = !!analysis && Array.isArray(analysis.sections);
  const stance = hasAnalysis ? stanceStyle(analysis!.stance) : null;

  return (
    <div className="glass-card">
      <div className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-bold text-sm flex items-center gap-2">
              <IndianRupee className="w-6 h-6 text-warning" />
              RUPEE — Sethji's Bahi-Khata
            </h2>
            <p className="text-xs text-base-content/40 mt-0.5">
              Would a shrewd Marwari business owner buy the whole dukaan? Rokda · Udhaari · Price · Excellence · Earnings
            </p>
          </div>
          {hasAnalysis && (
            <button className="btn btn-ghost btn-sm gap-2" onClick={generate} disabled={generating}>
              {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              Regenerate
            </button>
          )}
        </div>

        {loading && (
          <div className="flex flex-col items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-warning opacity-50" />
            <p className="mt-4 text-base-content/60 font-medium">Opening the ledger...</p>
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
            <BookOpen className="w-12 h-12 mx-auto text-base-content/20 mb-4" />
            <h3 className="text-lg font-bold mb-2">Bahi-Khata Band Hai</h3>
            <p className="text-base-content/60 max-w-sm mx-auto mb-6 text-sm">
              Evaluate this company as if buying the entire business with your own money — cold hard cash, low debt,
              and a margin of safety. Heads I win, tails I don't lose much.
            </p>
            <button className="btn btn-warning gap-2" onClick={generate} disabled={generating}>
              {generating ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Opening the ledger...</>
              ) : (
                <><Sparkles className="w-4 h-4" /> Run RUPEE Analysis</>
              )}
            </button>
            <p className="text-xs text-base-content/40 mt-3">Uses LLM — costs apply per your API key</p>
          </div>
        )}

        {!loading && hasAnalysis && analysis && (
          <div className="space-y-4">
            {generating && (
              <div className="alert alert-info py-2 shadow-sm animate-pulse flex items-center gap-3">
                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                <span className="text-sm font-medium">Re-opening the ledger...</span>
              </div>
            )}

            {/* Final verdict banner */}
            {stance && (
              <div className={`rounded-xl border p-4 ${stance.cls}`}>
                <div className="flex items-center gap-2 mb-1">
                  <Scale className="w-4 h-4 shrink-0" />
                  <span className="text-[10px] font-bold uppercase tracking-widest opacity-70">
                    Dhandho Kharido Ya Nahi?
                  </span>
                </div>
                <div className="text-lg font-extrabold">{analysis.stance}</div>
                {stance.gloss && <div className="text-xs opacity-70 mb-1">{stance.gloss}</div>}
                <p className="text-sm text-base-content/80 leading-relaxed mt-1">{analysis.verdict}</p>
              </div>
            )}

            {/* RUPEE sections */}
            <div className="space-y-3">
              {analysis.sections.map((sec, i) => (
                <div key={i} className="rounded-xl bg-base-200/40 border border-white/[0.03] p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex items-center justify-center w-7 h-7 rounded-lg bg-warning/15 text-warning font-extrabold text-sm shrink-0">
                      {sec.key?.replace(/[0-9]/g, '') || '•'}
                    </span>
                    <h4 className="font-bold text-sm">{sec.title}</h4>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pl-9">
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-base-content/40 mb-1">
                        The Reality
                      </div>
                      <p className="text-sm text-base-content/75 leading-relaxed">{sec.reality}</p>
                    </div>
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-warning/70 mb-1">
                        The Marwari Verdict
                      </div>
                      <p className="text-sm text-base-content/75 leading-relaxed italic">{sec.verdict}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>

            {data?.created_at && (
              <p className="text-xs text-base-content/40 text-right">
                Last updated: {new Date(data.created_at).toLocaleString()}
              </p>
            )}
            <p className="text-[10px] text-base-content/30 text-center">
              Persona-based analysis for education — not investment advice. Verify the numbers before betting your galla.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
