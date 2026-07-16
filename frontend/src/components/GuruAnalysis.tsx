import React, { useState, useEffect, useCallback } from 'react';
import {
  Loader2, AlertTriangle, RefreshCw, Sparkles, Clock, MessageCircle,
  ChevronRight, ArrowLeft, Send, Mic, X,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchGuruAnalyses, refreshGuruAnalysis, refreshAllGurus } from '../api';
import type { GuruInfo, GuruAnalysisEntry } from '../types';

interface GuruAnalysisProps {
  ticker: string;
}

/** Real photo URLs for gurus. Hosted on Wikimedia Commons (direct upload links). */
const GURU_PHOTOS: Record<string, string> = {
  warren_buffett:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/Warren_Buffett_KU_Visit.jpg/440px-Warren_Buffett_KU_Visit.jpg',
  peter_lynch:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Peter_Lynch_-_1%2C_bAnswer.png/440px-Peter_Lynch_-_1%2C_bAnswer.png',
  george_soros:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/5/50/George_Soros_-_World_Economic_Forum_Annual_Meeting_2011.jpg/440px-George_Soros_-_World_Economic_Forum_Annual_Meeting_2011.jpg',
  jack_bogle:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/JohnCBowordle.jpg/440px-JohnCBowordle.jpg',
  ray_dalio:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Ray_Dalio_2017.jpg/440px-Ray_Dalio_2017.jpg',
  charlie_munger:
    'https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Charlie_Munger_%28crop%29.jpg/440px-Charlie_Munger_%28crop%29.jpg',
};

/** Gradient fallbacks */
const AVATAR_GRADIENTS: Record<string, string> = {
  warren_buffett: 'from-blue-600 to-indigo-700',
  peter_lynch: 'from-emerald-600 to-teal-700',
  george_soros: 'from-purple-600 to-violet-700',
  jack_bogle: 'from-amber-600 to-orange-700',
  ray_dalio: 'from-cyan-600 to-blue-700',
  charlie_munger: 'from-rose-600 to-red-700',
};

const INITIALS: Record<string, string> = {
  warren_buffett: 'WB',
  peter_lynch: 'PL',
  george_soros: 'GS',
  jack_bogle: 'JB',
  ray_dalio: 'RD',
  charlie_munger: 'CM',
};

/** Signature quotes for each guru to display in "idle" state */
const SIGNATURE_QUOTES: Record<string, string> = {
  warren_buffett: '"Be fearful when others are greedy, and greedy when others are fearful."',
  peter_lynch: '"Know what you own, and know why you own it."',
  george_soros: '"It\'s not whether you\'re right or wrong, but how much you make when you\'re right."',
  jack_bogle: '"Don\'t look for the needle in the haystack. Just buy the haystack."',
  ray_dalio: '"He who lives by the crystal ball will eat shattered glass."',
  charlie_munger: '"Invert, always invert."',
};

/** Accent colors per guru for chat theme */
const GURU_ACCENT: Record<string, { bg: string; border: string; text: string; lightBg: string }> = {
  warren_buffett: { bg: 'bg-blue-600', border: 'border-blue-500/30', text: 'text-blue-400', lightBg: 'bg-blue-500/10' },
  peter_lynch: { bg: 'bg-emerald-600', border: 'border-emerald-500/30', text: 'text-emerald-400', lightBg: 'bg-emerald-500/10' },
  george_soros: { bg: 'bg-purple-600', border: 'border-purple-500/30', text: 'text-purple-400', lightBg: 'bg-purple-500/10' },
  jack_bogle: { bg: 'bg-amber-600', border: 'border-amber-500/30', text: 'text-amber-400', lightBg: 'bg-amber-500/10' },
  ray_dalio: { bg: 'bg-cyan-600', border: 'border-cyan-500/30', text: 'text-cyan-400', lightBg: 'bg-cyan-500/10' },
  charlie_munger: { bg: 'bg-rose-600', border: 'border-rose-500/30', text: 'text-rose-400', lightBg: 'bg-rose-500/10' },
};

function GuruAvatar({
  guru,
  size = 'md',
  online,
}: {
  guru: GuruInfo;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  online?: boolean;
}) {
  const [imgError, setImgError] = useState(false);
  const sizeClasses = {
    sm: 'w-10 h-10',
    md: 'w-12 h-12',
    lg: 'w-16 h-16',
    xl: 'w-20 h-20',
  }[size];
  const textSize = { sm: 'text-sm', md: 'text-base', lg: 'text-xl', xl: 'text-2xl' }[size];
  const gradient = AVATAR_GRADIENTS[guru.id] || 'from-gray-600 to-gray-700';
  const photoUrl = GURU_PHOTOS[guru.id] || guru.avatar_url;

  return (
    <div className="relative shrink-0">
      {!imgError && photoUrl ? (
        <img
          src={photoUrl}
          alt={guru.name}
          className={`${sizeClasses} rounded-full object-cover ring-2 ring-base-content/10`}
          onError={() => setImgError(true)}
          referrerPolicy="no-referrer"
        />
      ) : (
        <div
          className={`${sizeClasses} rounded-full bg-gradient-to-br ${gradient} flex items-center justify-center text-white font-bold ${textSize} ring-2 ring-base-content/10`}
        >
          {INITIALS[guru.id] || guru.name[0]}
        </div>
      )}
      {online && (
        <span className="absolute bottom-0 right-0 w-3 h-3 bg-success rounded-full border-2 border-base-200" />
      )}
    </div>
  );
}

/* ───────── Chat view for a single guru ───────── */
function GuruChatView({
  guru,
  analysis,
  ticker,
  onRefresh,
  refreshing,
  onBack,
}: {
  guru: GuruInfo;
  analysis?: GuruAnalysisEntry;
  ticker: string;
  onRefresh: () => void;
  refreshing: boolean;
  onBack: () => void;
}) {
  const accent = GURU_ACCENT[guru.id] || GURU_ACCENT.warren_buffett;

  return (
    <div className="flex flex-col h-full">
      {/* Chat header */}
      <div className={`flex items-center gap-3 p-4 border-b border-base-content/10 ${accent.lightBg}`}>
        <button
          className="btn btn-ghost btn-sm btn-circle"
          onClick={onBack}
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <GuruAvatar guru={guru} size="md" online={!!analysis} />
        <div className="flex-1 min-w-0">
          <div className="font-bold text-base-content text-sm">{guru.name}</div>
          <div className="text-xs text-base-content/50">{guru.title}</div>
          {analysis && (
            <div className="flex items-center gap-1 text-[10px] text-success mt-0.5">
              <span className="w-1.5 h-1.5 rounded-full bg-success inline-block" />
              Analysis available
            </div>
          )}
        </div>
        <button
          className="btn btn-ghost btn-xs gap-1"
          onClick={onRefresh}
          disabled={refreshing}
        >
          {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
        </button>
      </div>

      {/* Chat messages area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-[300px] max-h-[500px]">
        {/* Philosophy intro — always shown */}
        <div className="flex gap-3">
          <GuruAvatar guru={guru} size="sm" />
          <div className={`rounded-2xl rounded-tl-sm px-4 py-3 max-w-[85%] ${accent.lightBg} border ${accent.border}`}>
            <p className="text-xs text-base-content/50 italic mb-1">Philosophy</p>
            <p className="text-sm text-base-content/70">{guru.philosophy}</p>
          </div>
        </div>

        {/* User's "question" — simulated */}
        {(analysis || refreshing) && (
          <div className="flex gap-3 justify-end">
            <div className="rounded-2xl rounded-tr-sm px-4 py-3 max-w-[75%] bg-primary/15 border border-primary/20">
              <p className="text-sm text-base-content/80">
                What&apos;s your take on <strong>{ticker}</strong> as an investment right now?
              </p>
            </div>
          </div>
        )}

        {/* Guru's response */}
        {refreshing && !analysis && (
          <div className="flex gap-3">
            <GuruAvatar guru={guru} size="sm" />
            <div className={`rounded-2xl rounded-tl-sm px-4 py-3 ${accent.lightBg} border ${accent.border}`}>
              <div className="flex items-center gap-2">
                <span className="loading loading-dots loading-xs" />
                <span className="text-xs text-base-content/40">{guru.name.split(' ')[0]} is thinking...</span>
              </div>
            </div>
          </div>
        )}

        {analysis && (
          <>
            <div className="flex gap-3">
              <GuruAvatar guru={guru} size="sm" />
              <div className={`rounded-2xl rounded-tl-sm px-4 py-3 max-w-[85%] ${accent.lightBg} border ${accent.border}`}>
                <div className="prose prose-sm max-w-none text-sm leading-relaxed text-base-content/80 prose-headings:text-base-content prose-strong:text-base-content">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {analysis.analysis}
                  </ReactMarkdown>
                </div>
              </div>
            </div>

            {/* Timestamp */}
            <div className="flex items-center gap-1.5 text-[10px] text-base-content/25 ml-14">
              <Clock className="w-3 h-3" />
              {new Date(analysis.created_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </div>

            {/* Refreshing indicator after existing analysis */}
            {refreshing && (
              <div className="flex gap-3">
                <GuruAvatar guru={guru} size="sm" />
                <div className={`rounded-2xl rounded-tl-sm px-4 py-3 ${accent.lightBg} border ${accent.border}`}>
                  <div className="flex items-center gap-2">
                    <span className="loading loading-dots loading-xs" />
                    <span className="text-xs text-base-content/40">Updating analysis...</span>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* No analysis yet and not refreshing */}
        {!analysis && !refreshing && (
          <div className="flex flex-col items-center py-6 gap-3">
            <MessageCircle className={`w-10 h-10 ${accent.text} opacity-30`} />
            <p className="text-sm text-base-content/40 text-center max-w-xs">
              Ask {guru.name} to analyze <strong>{ticker}</strong> using their legendary investment framework
            </p>
          </div>
        )}
      </div>

      {/* Chat input area */}
      <div className="p-3 border-t border-base-content/10">
        {!analysis && !refreshing ? (
          <button
            className={`btn btn-sm w-full gap-2 ${accent.bg} text-white border-none hover:opacity-90`}
            onClick={onRefresh}
            disabled={refreshing}
          >
            <Send className="w-4 h-4" />
            Ask {guru.name.split(' ')[0]} about {ticker}
          </button>
        ) : (
          <div className="flex gap-2">
            <button
              className="btn btn-ghost btn-sm flex-1 gap-2"
              onClick={onRefresh}
              disabled={refreshing}
            >
              {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {refreshing ? 'Thinking...' : 'Ask again'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────── Main component ───────── */

export function GuruAnalysis({ ticker }: GuruAnalysisProps) {
  const [gurus, setGurus] = useState<GuruInfo[]>([]);
  const [analyses, setAnalyses] = useState<Record<string, GuruAnalysisEntry>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [activeGuru, setActiveGuru] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchGuruAnalyses(ticker);
      setGurus(result.gurus);
      setAnalyses(result.analyses);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load guru analyses');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  const handleRefreshOne = async (guruId: string) => {
    try {
      setRefreshingId(guruId);
      const result = await refreshGuruAnalysis(ticker, guruId);
      setAnalyses((prev) => ({ ...prev, [guruId]: result }));
    } catch (err) {
      console.error('Refresh failed:', err);
    } finally {
      setRefreshingId(null);
    }
  };

  const handleRefreshAll = async () => {
    try {
      setRefreshingAll(true);
      const result = await refreshAllGurus(ticker);
      setAnalyses((prev) => {
        const updated = { ...prev };
        for (const [gid, entry] of Object.entries(result.results)) {
          if (!('error' in entry)) {
            updated[gid] = entry as GuruAnalysisEntry;
          }
        }
        return updated;
      });
    } catch (err) {
      console.error('Refresh all failed:', err);
    } finally {
      setRefreshingAll(false);
    }
  };

  const analysisCount = Object.keys(analyses).length;
  const selectedGuru = gurus.find((g) => g.id === activeGuru);

  return (
    <div className="glass-card">
      <div className="p-0">
        {/* If a guru is selected, show their chat view */}
        {selectedGuru ? (
          <GuruChatView
            guru={selectedGuru}
            analysis={analyses[selectedGuru.id]}
            ticker={ticker}
            onRefresh={() => handleRefreshOne(selectedGuru.id)}
            refreshing={refreshingId === selectedGuru.id}
            onBack={() => setActiveGuru(null)}
          />
        ) : (
          /* Guru selection view */
          <div className="p-5">
            {/* Header */}
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
              <div>
                <h2 className="font-bold text-sm flex items-center gap-2">
                  <Sparkles className="w-5 h-5 text-warning" />
                  Investment Guru Analysis
                </h2>
                <p className="text-xs text-base-content/50 mt-1">
                  Chat with legendary investors about <strong>{ticker}</strong>
                </p>
              </div>
              <button
                className="btn btn-primary btn-sm gap-2"
                onClick={handleRefreshAll}
                disabled={refreshingAll || loading}
              >
                {refreshingAll ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Generating...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    {analysisCount > 0 ? 'Refresh All' : 'Ask All Gurus'}
                  </>
                )}
              </button>
            </div>

            {/* Loading */}
            {loading && (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="w-8 h-8 animate-spin text-primary" />
              </div>
            )}

            {/* Error */}
            {error && !loading && (
              <div className="alert alert-error mb-4">
                <AlertTriangle className="w-5 h-5" />
                <span>{error}</span>
              </div>
            )}

            {/* Guru contact list — messenger style */}
            {!loading && gurus.length > 0 && (
              <div className="space-y-1">
                {gurus.map((guru) => {
                  const hasAnalysis = !!analyses[guru.id];
                  const accent = GURU_ACCENT[guru.id] || GURU_ACCENT.warren_buffett;
                  const quote = SIGNATURE_QUOTES[guru.id] || '';
                  const isRefreshing = refreshingId === guru.id || refreshingAll;

                  return (
                    <button
                      key={guru.id}
                      className={`w-full flex items-center gap-4 p-4 rounded-2xl text-left transition-all duration-200 hover:bg-base-content/5 active:scale-[0.99] group ${
                        hasAnalysis ? '' : 'opacity-80'
                      }`}
                      onClick={() => setActiveGuru(guru.id)}
                    >
                      <GuruAvatar guru={guru} size="lg" online={hasAnalysis} />

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-base-content text-sm">{guru.name}</span>
                          {hasAnalysis && (
                            <span className="badge badge-success badge-xs gap-0.5">
                              <MessageCircle className="w-2.5 h-2.5" /> replied
                            </span>
                          )}
                          {isRefreshing && (
                            <Loader2 className="w-3 h-3 animate-spin text-primary" />
                          )}
                        </div>
                        <div className="text-xs text-base-content/40 mt-0.5">{guru.title}</div>

                        {/* Preview: either last analysis snippet or signature quote */}
                        <div className="text-xs text-base-content/30 mt-1 line-clamp-1 italic">
                          {hasAnalysis
                            ? analyses[guru.id].analysis.replace(/[#*_]/g, '').slice(0, 80) + '...'
                            : quote
                          }
                        </div>
                      </div>

                      {/* Right side: time or CTA */}
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        {hasAnalysis ? (
                          <span className="text-[10px] text-base-content/25">
                            {new Date(analyses[guru.id].created_at).toLocaleDateString(undefined, {
                              month: 'short',
                              day: 'numeric',
                            })}
                          </span>
                        ) : (
                          <span className={`text-[10px] ${accent.text}`}>Ask</span>
                        )}
                        <ChevronRight className="w-4 h-4 text-base-content/20 group-hover:text-base-content/50 transition-colors" />
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            {/* Disclaimer */}
            {!loading && gurus.length > 0 && (
              <p className="text-[10px] text-base-content/25 mt-3 text-center">
                AI-generated analysis inspired by these investors&apos; known philosophies. Not actual quotes or financial advice.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
