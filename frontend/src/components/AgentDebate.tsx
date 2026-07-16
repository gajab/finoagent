import React, { useState, useEffect, useCallback } from 'react';
import {
  Swords, TrendingUp, TrendingDown, Gavel, Loader2, RefreshCw,
  AlertTriangle, Sparkles, FileText, ChevronDown, Target,
  CheckCircle2, StopCircle, ArrowRightCircle, HelpCircle, Save, Trash2, History,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchAgentDebate, generateAgentDebate, continueAgentDebate,
  saveAgentDebate, clearAgentDebate, fetchDebateHistory, fetchDebateItem,
  type DcfScenarioParams, type DebateHistoryItem,
} from '../api';
import type {
  AgentDebateResult, DebateRound, BullValuation, DebateAnchors,
  StructuralValuation, StructuralClaim,
} from '../types';

interface Props {
  ticker: string;
  dcfOverride?: DcfScenarioParams | null;
}

const fmtRet = (v?: number | null) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;
const fmtConf = (v?: number | null) => (v == null ? '—' : `${Math.round(v * 100)}%`);

function Collapsible({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <details className="group">
      <summary className="cursor-pointer text-[11px] uppercase tracking-wider text-base-content/40 flex items-center gap-1 select-none">
        <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-180" />
        {label}
      </summary>
      <pre className="mt-2 text-[11px] leading-relaxed whitespace-pre-wrap bg-base-200/40 rounded-lg p-3 text-base-content/60 max-h-52 overflow-y-auto font-mono">
        {children}
      </pre>
    </details>
  );
}

function NewTag({ isNew }: { isNew: boolean }) {
  return isNew
    ? <span className="badge badge-xs badge-success gap-0.5">new</span>
    : <span className="badge badge-xs badge-ghost gap-0.5 opacity-60">repeat</span>;
}

const money0 = (x?: number | null) => (x == null ? '—' : `$${x.toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const signPct = (x?: number | null) => (x == null ? '—' : `${x > 0 ? '+' : ''}${x}%`);

/** The Bull's price target as a recomputed EPS × multiple bridge, with guardrail flags. */
function ValuationBridge({ v }: { v: BullValuation }) {
  return (
    <div className="mt-2 rounded-lg bg-base-200/40 p-2.5 text-xs">
      <div className="flex items-center gap-1.5 font-mono flex-wrap">
        <span className="text-base-content/50">EPS beat</span>
        <span className="font-semibold">{signPct(v.eps_beat_pct)}</span>
        <span className="text-base-content/30">×</span>
        <span className="text-base-content/50">multiple</span>
        <span className="font-semibold">{v.target_multiple ?? '—'}×</span>
        <span className="text-base-content/30">→</span>
        <span className="text-base-content/50">target</span>
        <span className="font-semibold">{money0(v.computed_target_price)}</span>
        <span className="text-base-content/30">=</span>
        <span className={`font-bold ${(v.computed_upside_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>{signPct(v.computed_upside_pct)}</span>
        <span className="badge badge-xs badge-ghost ml-1">recomputed in code</span>
      </div>
      {v.multiple_anchor && <div className="text-base-content/50 mt-1">Multiple: {v.multiple_anchor}</div>}
      {v.reconciliation && <div className="text-base-content/50">Reconciliation: {v.reconciliation}</div>}
      {v.flags && v.flags.length > 0 && (
        <div className="mt-1.5 space-y-0.5">
          {v.flags.map((f, i) => (
            <div key={i} className="flex items-start gap-1 text-warning">
              <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" /><span>{f}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Evidence tier → colour (E1 disclosed → strong, E5 narrative → faint).
const TIER_CLASS: Record<string, string> = {
  E1: 'badge-success', E2: 'badge-info', E3: 'badge-primary',
  E4: 'badge-warning', E5: 'badge-ghost',
};
const TIER_LABEL: Record<string, string> = {
  E1: 'disclosed', E2: 'guidance', E3: 'consensus', E4: 'trend', E5: 'narrative',
};

/** Format a claim magnitude in its native unit for display. */
function claimMag(c: StructuralClaim): string {
  switch (c.driver) {
    case 'revenue': return `${c.magnitude >= 0 ? '+' : ''}${(c.magnitude * 100).toFixed(1)}% rev`;
    case 'margin': return `${c.magnitude >= 0 ? '+' : ''}${c.magnitude.toFixed(0)}bps margin`;
    case 'buyback': return `${(-c.magnitude * 100).toFixed(1)}% buyback`;
    case 'multiple': return `${c.magnitude >= 0 ? '+' : ''}${c.magnitude.toFixed(1)} P/E`;
    default: return `$${(c.magnitude / 1e9).toFixed(2)}B one-off`;
  }
}

/** Driver-claims priced through a real P&L — the authoritative, arithmetic-owned target.
    The LLM only extracted + tiered the claims; every number below is computed in code. */
function StructuralValuationPanel({ v }: { v: StructuralValuation }) {
  const up = v.upside_pct ?? null;
  return (
    <div className="rounded-xl bg-primary/5 border border-primary/20 p-3">
      <div className="text-xs font-semibold text-base-content/60 mb-2 flex items-center gap-1">
        <Target className="w-3.5 h-3.5 text-primary" /> Structural valuation — driver-claims priced through the P&L
        <span className="badge badge-xs badge-primary ml-1">computed in code</span>
      </div>

      {/* headline */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <div className="text-[10px] text-base-content/40 uppercase tracking-wider">Target</div>
          <div className="text-lg font-black">{money0(v.target)}</div>
          {up != null && <div className={`text-[11px] font-semibold ${up >= 0 ? 'text-success' : 'text-error'}`}>{signPct(Math.round(up))} vs {money0(v.price)}</div>}
        </div>
        <div>
          <div className="text-[10px] text-base-content/40 uppercase tracking-wider">Fwd EPS × multiple</div>
          <div className="text-lg font-black font-mono">${v.eps} × {v.multiple}×</div>
          <div className="text-[11px] text-base-content/50">base EPS ${v.base_eps}</div>
        </div>
        <div>
          <div className="text-[10px] text-base-content/40 uppercase tracking-wider">Bear–bull range</div>
          <div className="text-sm font-bold">{money0(v.range[0])}–{money0(v.range[1])}</div>
          <div className="text-[11px] text-base-content/50">sustainable g {v.sustainable_growth_pct}%</div>
        </div>
        <div>
          <div className="text-[10px] text-base-content/40 uppercase tracking-wider">Confidence</div>
          <div className="text-lg font-black">{fmtConf(v.confidence)}</div>
          <div className="text-[11px] text-base-content/50">from range width</div>
        </div>
      </div>

      {/* EPS waterfall */}
      {v.waterfall.length > 0 && (
        <div className="mt-3 rounded-lg bg-base-100/40 p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1.5">Base EPS → forward EPS (survival-weighted)</div>
          <div className="space-y-1 font-mono text-xs">
            <div className="flex items-center justify-between text-base-content/50">
              <span>start · base EPS</span><span>${v.base_eps}</span>
            </div>
            {v.waterfall.map((s, i) => (
              <div key={i} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 truncate">
                  <span className={`badge badge-xs ${TIER_CLASS[s.tier] || 'badge-ghost'}`} title={`${s.tier} · ${TIER_LABEL[s.tier] || ''} · survival ${Math.round(s.survival * 100)}%`}>{s.tier}</span>
                  <span className="truncate text-base-content/70">{s.label}</span>
                </span>
                <span className={s.eps_delta >= 0 ? 'text-success' : 'text-error'}>{s.eps_delta >= 0 ? '+' : ''}{s.eps_delta.toFixed(2)}</span>
              </div>
            ))}
            <div className="flex items-center justify-between border-t border-base-300/40 pt-1 font-semibold">
              <span>forward EPS</span><span>${v.eps}</span>
            </div>
          </div>
        </div>
      )}

      {/* claims ledger */}
      {v.claims.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {v.claims.map((c, i) => (
            <span key={i} className={`badge badge-sm gap-1 ${c.unanswered ? 'badge-outline badge-error' : 'badge-ghost'}`}
                  title={`${c.tier} · ${TIER_LABEL[c.tier] || ''}${c.unanswered ? ' · unanswered Bear rebuttal → haircut' : ''}${c.persistence_nudge ? ` · persistence ${c.persistence_nudge > 0 ? '+' : ''}${c.persistence_nudge}` : ''}`}>
              <span className={`badge badge-xs ${TIER_CLASS[c.tier] || 'badge-ghost'}`}>{c.tier}</span>
              {claimMag(c)}
              {c.unanswered && <AlertTriangle className="w-3 h-3" />}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 text-[11px] text-base-content/45 leading-tight">
        Each surviving argument is a tiered driver-claim; code composes them through a real income statement
        (operating leverage + price↔margin covariance), prices EPS on a warranted multiple that fades with
        growth, and haircuts unanswered rebuttals. The LLM never multiplies — it only extracts &amp; classifies.
      </div>
    </div>
  );
}

/** The objective reference numbers (multiples, DCF, analyst range) the debate is anchored to. */
function AnchorsPanel({ a }: { a: DebateAnchors }) {
  const up = (x?: number | null) => (x == null ? '' : ` (${x > 0 ? '+' : ''}${x}%)`);
  const Item = ({ label, value, sub }: { label: string; value: string; sub?: string }) => (
    <div>
      <div className="text-[10px] text-base-content/40 uppercase tracking-wider">{label}</div>
      <div className="text-sm font-bold">{value}</div>
      {sub && <div className="text-[10px] text-base-content/50">{sub}</div>}
    </div>
  );
  return (
    <div className="rounded-xl bg-base-100/30 border border-base-300/30 p-3">
      <div className="text-xs font-semibold text-base-content/50 mb-2 flex items-center gap-1">
        <Target className="w-3.5 h-3.5 text-primary" /> Valuation anchors — the reference numbers the debate argues over
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Item label="Price" value={money0(a.price)} sub={a.forward_eps != null ? `fwd EPS ${a.forward_eps}` : undefined} />
        <Item label="Forward P/E" value={a.forward_pe != null ? `${a.forward_pe}×` : '—'} sub={a.peg != null ? `PEG ${a.peg}` : undefined} />
        <Item label="DCF (conservative)" value={`${money0(a.dcf_low)}–${money0(a.dcf_high)}`} sub={`base ${money0(a.dcf_fair_value)}${up(a.dcf_upside_pct)}`} />
        <Item label="Analyst range" value={`${money0(a.analyst_low)}–${money0(a.analyst_high)}`} sub={`mean ${money0(a.analyst_mean)}${up(a.analyst_mean_upside_pct)} · n=${a.analyst_count ?? '?'}`} />
      </div>
      {a.dcf_implied_growth != null && (
        <div className="mt-2 text-[11px] text-base-content/60 flex items-start gap-1">
          <span className="badge badge-xs badge-primary">reverse-DCF</span>
          <span>Today's price implies <b>~{a.dcf_implied_growth}% FCF growth/yr for 10y</b>{a.revenue_growth_pct != null && <> vs consensus rev growth {a.revenue_growth_pct}%</>} — the expectation the debate should test.</span>
        </div>
      )}
    </div>
  );
}

function RoundBlock({ r, isLast }: { r: DebateRound; isLast: boolean }) {
  return (
    <div className="relative pl-4 border-l-2 border-base-300/40">
      <div className="absolute -left-[7px] top-1 w-3 h-3 rounded-full bg-secondary" />
      <div className="text-xs font-bold text-base-content/50 uppercase tracking-wider mb-2">
        Round {r.round}{r.user_input ? ' · your input' : ''}
      </div>
      {r.user_input && (
        <div className="mb-2 rounded-lg bg-secondary/5 border border-secondary/20 p-2 text-xs">
          <span className="font-semibold text-secondary">You provided: </span>
          <span className="text-base-content/70">{r.user_input}</span>
        </div>
      )}

      {/* Bull */}
      <div className="rounded-xl border-l-4 border-success bg-base-100/40 p-3 mb-2">
        <div className="flex items-center gap-2 mb-1">
          <TrendingUp className="w-4 h-4 text-success" />
          <span className="font-semibold text-sm">Bull</span>
          {r.bull.upside_pct != null && (
            <span className="badge badge-sm badge-success gap-1"><Target className="w-3 h-3" />+{r.bull.upside_pct}%</span>
          )}
          <span className="ml-auto"><NewTag isNew={r.bull.new_argument} /></span>
        </div>
        <div className="prose prose-sm max-w-none prose-p:text-base-content/70 prose-strong:text-base-content prose-li:text-base-content/70">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.bull.argument || ''}</ReactMarkdown>
        </div>
        {r.bull.valuation && <ValuationBridge v={r.bull.valuation} />}
      </div>

      {/* Bear */}
      <div className="rounded-xl border-l-4 border-error bg-base-100/40 p-3 mb-2">
        <div className="flex items-center gap-2 mb-1">
          <TrendingDown className="w-4 h-4 text-error" />
          <span className="font-semibold text-sm">Bear</span>
          <span className="ml-auto"><NewTag isNew={r.bear.new_argument} /></span>
        </div>
        <div className="prose prose-sm max-w-none prose-p:text-base-content/70 prose-strong:text-base-content prose-li:text-base-content/70">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.bear.argument || ''}</ReactMarkdown>
        </div>
      </div>

      {/* Judge ruling for the round */}
      <div className="rounded-xl border-l-4 border-primary bg-primary/5 p-3 mb-4">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Gavel className="w-4 h-4 text-primary" />
          <span className="font-semibold">Judge</span>
          <span className="badge badge-sm badge-ghost">conf {fmtConf(r.judge.confidence)}</span>
          <span className={`badge badge-sm badge-ghost ${(r.judge.view_return ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
            view {fmtRet(r.judge.view_return)}
          </span>
          <span className="badge badge-sm badge-ghost gap-1">
            {r.judge.new_information ? <CheckCircle2 className="w-3 h-3 text-success" /> : <StopCircle className="w-3 h-3 text-warning" />}
            {r.judge.new_information ? 'new info' : 'stale'}
          </span>
          <span className="ml-auto flex items-center gap-1 text-base-content/50">
            {isLast
              ? <><StopCircle className="w-3 h-3" /> stopped</>
              : <><ArrowRightCircle className="w-3 h-3 text-secondary" /> continue</>}
          </span>
        </div>
        {r.judge.rationale && <p className="text-xs text-base-content/60 italic mt-1">{r.judge.rationale}</p>}
      </div>
    </div>
  );
}

/** A past debate: collapsed summary; lazy-loads its full transcript on expand. */
function HistoryCard({ ticker, item }: { ticker: string; item: DebateHistoryItem }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState<AgentDebateResult | null>(null);
  const [loading, setLoading] = useState(false);
  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !full) {
      setLoading(true);
      try { setFull(await fetchDebateItem(ticker, item.id)); } catch { /* ignore */ } finally { setLoading(false); }
    }
  };
  return (
    <div className="rounded-lg border border-base-300/30 bg-base-100/20">
      <button className="w-full flex items-center gap-2 p-2.5 text-xs hover:bg-base-content/5 rounded-lg" onClick={toggle}>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
        <span className="text-base-content/50">{new Date(item.created_at).toLocaleString()}</span>
        {item.saved && <span className="badge badge-xs badge-success">saved</span>}
        <span className="ml-auto flex items-center gap-2">
          <span className={`font-semibold ${(item.view_return ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>{fmtRet(item.view_return)}</span>
          <span className="text-base-content/50">conf {fmtConf(item.base_confidence)}</span>
          <span className="text-base-content/40">{item.rounds}r</span>
        </span>
      </button>
      {open && (
        <div className="p-3 pt-0">
          {loading && <div className="text-xs text-base-content/50 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> loading…</div>}
          {full?.conclusion?.rationale && <p className="text-xs text-base-content/60 italic mb-2">{full.conclusion.rationale}</p>}
          {full?.rounds?.map((r, i) => <RoundBlock key={r.round} r={r} isLast={i === full.rounds!.length - 1} />)}
        </div>
      )}
    </div>
  );
}

export function AgentDebate({ ticker, dcfOverride }: Props) {
  const [data, setData] = useState<AgentDebateResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [userInput, setUserInput] = useState('');
  const [continuing, setContinuing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [history, setHistory] = useState<DebateHistoryItem[] | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true); setError(null); setUserInput('');
      setHistory(null); setHistoryOpen(false);
      setData(await fetchAgentDebate(ticker));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load');
    } finally { setLoading(false); }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  const generate = async () => {
    try {
      setGenerating(true); setError(null);
      setData(await generateAgentDebate(ticker, dcfOverride));
      setHistory(null); setHistoryOpen(false);   // prior debate is now history
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Debate failed');
    } finally { setGenerating(false); }
  };

  const continueDebate = async () => {
    if (!userInput.trim()) return;
    try {
      setContinuing(true); setError(null);
      const res = await continueAgentDebate(ticker, userInput.trim());
      setData(res); setUserInput('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Continue failed');
    } finally { setContinuing(false); }
  };

  const saveDebate = async () => {
    try {
      setSaving(true); setError(null);
      setData(await saveAgentDebate(ticker));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally { setSaving(false); }
  };

  const clearDebate = async () => {
    if (!window.confirm('Delete this debate from the database? (Past debates in history are kept.)')) return;
    try {
      setClearing(true); setError(null);
      await clearAgentDebate(ticker);
      setHistory(null); setHistoryOpen(false);
      setData(await fetchAgentDebate(ticker));   // reveals the previous debate, or empty state
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Clear failed');
    } finally { setClearing(false); }
  };

  const toggleHistory = async () => {
    const next = !historyOpen;
    setHistoryOpen(next);
    if (next && history === null) {
      setHistoryLoading(true);
      try { setHistory((await fetchDebateHistory(ticker)).items); }
      catch { setHistory([]); }
      finally { setHistoryLoading(false); }
    }
  };

  const has = !!data?.available && !!data.rounds?.length;
  const concl = data?.conclusion;

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Swords className="w-5 h-5 text-secondary" />
            Multi-Agent Debate
            <span className="text-xs font-normal text-base-content/40">Bull ↔ Bear · Judge referees</span>
            {dcfOverride && <span className="badge badge-xs badge-secondary gap-1" title="Your tweaked DCF from the DCF page will be used as the valuation anchor">custom DCF</span>}
            {data?.saved && <span className="badge badge-xs badge-success gap-1" title="Saved — kept permanently"><CheckCircle2 className="w-3 h-3" /> Saved</span>}
          </h2>
          {has && (
            <div className="flex items-center gap-1">
              <button className="btn btn-ghost btn-xs gap-1" onClick={saveDebate} disabled={saving || !!data?.saved}
                title="Keep this debate permanently in the database">
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                {data?.saved ? 'Saved' : 'Save'}
              </button>
              <button className="btn btn-ghost btn-xs gap-1" onClick={generate} disabled={generating}
                title="Pull fresh EDGAR filings and run a new debate — the current one moves to History">
                {generating ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                Re-run
              </button>
              <button className="btn btn-ghost btn-xs gap-1 text-error hover:bg-error/10" onClick={clearDebate} disabled={clearing}
                title="Delete this debate from the database">
                {clearing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                Clear
              </button>
            </div>
          )}
        </div>

        {loading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
            <span className="ml-2 text-sm text-base-content/60">Loading…</span>
          </div>
        )}

        {error && !loading && (
          <div className="alert alert-error text-sm mt-2">
            <AlertTriangle className="w-4 h-4" /><span>{error}</span>
          </div>
        )}

        {/* Empty state → run button */}
        {!loading && !has && !error && (
          <div className="text-center py-8">
            <div className="flex flex-wrap justify-center gap-2 mb-4">
              <span className="badge badge-outline badge-sm gap-1 text-success"><TrendingUp className="w-3 h-3" /> Bull argues</span>
              <span className="badge badge-outline badge-sm gap-1 text-error"><TrendingDown className="w-3 h-3" /> Bear rebuts</span>
              <span className="badge badge-outline badge-sm gap-1"><Gavel className="w-3 h-3" /> Judge referees each round</span>
            </div>
            <p className="text-sm text-base-content/50 mb-4 max-w-md mx-auto">
              Bull and Bear debate {ticker} over its SEC filings, earnings and the macro backdrop for
              multiple rounds. The Judge stops the debate once it converges or hits high confidence,
              then outputs an expected return + confidence — the view a Black-Litterman step would consume.
            </p>
            <button className="btn btn-secondary btn-sm gap-2" onClick={generate} disabled={generating}>
              {generating
                ? <><Loader2 className="w-4 h-4 animate-spin" /> Debating… (may take a minute)</>
                : <><Sparkles className="w-4 h-4" /> Run the debate</>}
            </button>
            <p className="text-[10px] text-base-content/30 mt-2">Iterative — up to a few rounds × 3 LLM calls. Costs apply per your API key.</p>
          </div>
        )}

        {/* Result */}
        {!loading && has && data.rounds && (
          <div className="space-y-4 mt-3">
            {generating && (
              <div className="flex items-center gap-2 text-sm text-secondary">
                <Loader2 className="w-4 h-4 animate-spin" /> Re-running debate…
              </div>
            )}

            {/* Evidence + settings chips */}
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="text-base-content/40">Evidence:</span>
              {data.evidence?.filings?.filter(f => f.form).map((f, i) => (
                f.url
                  ? <a key={i} href={f.url} target="_blank" rel="noreferrer" className="badge badge-sm badge-ghost gap-1 hover:badge-primary">
                      <FileText className="w-3 h-3" /> {f.form} {f.date}
                    </a>
                  : <span key={i} className="badge badge-sm badge-ghost gap-1"><FileText className="w-3 h-3" /> {f.form}</span>
              ))}
              {!data.evidence?.documents_found && <span className="text-base-content/30">no SEC filings found</span>}
              {data.evidence?.macro && Object.keys(data.evidence.macro).length > 0 && <span className="badge badge-sm badge-ghost">macro ✓</span>}
              {data.model && <span className="text-base-content/30 ml-auto">{data.model}</span>}
            </div>

            {/* Verdict banner */}
            <div className="rounded-xl bg-base-200/40 border border-base-300/40 p-4">
              {concl?.parse_error ? (
                <p className="text-sm text-warning">Judge returned unstructured output on the final round.</p>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <div>
                    <div className="text-xs text-base-content/40 uppercase tracking-wider flex items-center gap-1">
                      View (expected return)
                      {concl?.view_source === 'structural' && <span className="badge badge-xs badge-primary" title="Headline priced by the structural engine from the debate's driver-claims, not the Judge's gestalt">priced from claims</span>}
                    </div>
                    <div className={`text-2xl font-black ${(concl?.view_return ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>{fmtRet(concl?.view_return)}</div>
                    {concl?.view_source === 'structural' && concl?.judge_view_return != null && (
                      <div className="text-[11px] text-base-content/45">Judge's read: {fmtRet(concl.judge_view_return)}</div>
                    )}
                  </div>
                  <div>
                    <div className="text-xs text-base-content/40 uppercase tracking-wider">Confidence</div>
                    <div className="text-2xl font-black">{fmtConf(concl?.base_confidence)}</div>
                    <progress className="progress progress-primary w-full h-1.5 mt-1" value={(concl?.base_confidence ?? 0) * 100} max={100} />
                  </div>
                  <div className="col-span-2 sm:col-span-1">
                    <div className="text-xs text-base-content/40 uppercase tracking-wider">Debate</div>
                    <div className="text-sm font-semibold mt-1">{concl?.rounds} round{concl?.rounds === 1 ? '' : 's'}</div>
                    <div className="text-[11px] text-base-content/50 leading-tight">stopped: {concl?.stop_reason}</div>
                  </div>
                </div>
              )}
              {concl?.rationale && <p className="text-sm text-base-content/70 italic mt-3">{concl.rationale}</p>}
              {concl?.open_questions && concl.open_questions.length > 0 && (
                <div className="mt-3 rounded-lg bg-warning/5 border border-warning/20 p-2.5">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-warning mb-1">
                    <HelpCircle className="w-3.5 h-3.5" /> What the filings don't answer — provide this data to sharpen the view
                  </div>
                  <ul className="text-xs text-base-content/70 space-y-1 list-disc pl-5">
                    {concl.open_questions.map((q, i) => <li key={i}>{q}</li>)}
                  </ul>
                </div>
              )}
              {/* Answer the open questions → run one more round */}
              <div className="mt-3 pt-3 border-t border-base-300/30">
                <textarea
                  className="textarea textarea-bordered textarea-sm w-full text-xs"
                  rows={2}
                  placeholder="Answer the open questions or add data (10-K figures, guidance, your own assumptions)…"
                  value={userInput}
                  onChange={e => setUserInput(e.target.value)}
                  disabled={continuing}
                />
                <div className="flex items-center justify-between gap-2 mt-2">
                  <span className="text-[11px] text-base-content/40">Runs one more round with your input; the Judge re-prices and lists any remaining gaps.</span>
                  <button className="btn btn-secondary btn-sm gap-2 shrink-0" onClick={continueDebate} disabled={continuing || !userInput.trim()}>
                    {continuing ? <><Loader2 className="w-4 h-4 animate-spin" /> Debating…</> : <><Sparkles className="w-4 h-4" /> Answer &amp; continue — 1 more round</>}
                  </button>
                </div>
              </div>
            </div>

            {/* Structural valuation — driver-claims priced through the P&L (authoritative) */}
            {data.structural_valuation && <StructuralValuationPanel v={data.structural_valuation} />}

            {/* Valuation anchors */}
            {data.anchors && <AnchorsPanel a={data.anchors} />}

            {/* Base prompts (the directives) */}
            {data.prompts && (
              <div className="space-y-1.5 rounded-lg bg-base-100/30 p-3">
                <div className="text-xs font-semibold text-base-content/50 mb-1">Agent directives (system prompts)</div>
                <Collapsible label="Bull prompt">{data.prompts.bull}</Collapsible>
                <Collapsible label="Bear prompt">{data.prompts.bear}</Collapsible>
                <Collapsible label="Judge prompt">{data.prompts.judge}</Collapsible>
              </div>
            )}

            {/* Round-by-round transcript */}
            <div className="space-y-0">
              {data.rounds.map((r, i) => (
                <RoundBlock key={r.round} r={r} isLast={i === data.rounds!.length - 1} />
              ))}
            </div>

            {/* Past debates (lazy) */}
            <div>
              <button className="flex items-center gap-1.5 text-xs font-semibold text-base-content/50 hover:text-base-content/70" onClick={toggleHistory}>
                <History className="w-3.5 h-3.5" />
                Past debates{history ? ` (${history.length})` : ''}
                <ChevronDown className={`w-3.5 h-3.5 transition-transform ${historyOpen ? 'rotate-180' : ''}`} />
              </button>
              {historyOpen && (
                <div className="mt-2 space-y-2">
                  {historyLoading && <div className="text-xs text-base-content/50 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> loading…</div>}
                  {history && history.length === 0 && <div className="text-xs text-base-content/40">No earlier debates — Re-run archives the current one here.</div>}
                  {history?.map(it => <HistoryCard key={it.id} ticker={ticker} item={it} />)}
                </div>
              )}
            </div>

            <p className="text-[10px] text-base-content/30">
              {data.generated_at && <>Generated {new Date(data.generated_at).toLocaleString()} · </>}
              The final view + confidence are the intended inputs to Black-Litterman. Educational, not investment advice.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
