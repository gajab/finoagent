import React, { useEffect, useState } from 'react';
import {
  Gauge, Loader2, AlertTriangle, Cpu, Shield, Briefcase, ChevronDown, ChevronUp,
  Trophy, Play, Terminal, Maximize2, X, MessageSquare, CalendarClock,
} from 'lucide-react';
import { runDeskReview, runDeskReviewAgents } from '../api';
import type { DeskReviewParams } from '../api';
import type { DeskReviewResult, DeskRankedTrade, DeskAgentsResult, DeskAgent } from '../types';

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
const ratio = (n: number | null | undefined) =>
  n == null ? '—' : Math.abs(n) >= 1000 ? `${Math.round(n).toLocaleString()}` : n.toFixed(2);
const annShort = (n: number | null | undefined) =>
  n == null ? '—' : n >= 1000 ? '999%+' : `${n.toFixed(0)}%`;

const scoreTone = (s: number) => (s >= 80 ? 'text-success' : s >= 60 ? 'text-warning' : 'text-error');
const GREEN = new Set(['CLEAR', 'ACCEPTABLE', 'ENTER', 'GOOD ENTRY', 'HOLD', 'APPROVE_WITH_CONDITIONS', 'APPROVE', 'EXECUTE']);
const RED = new Set(['NONE', 'EXCESSIVE', 'SKIP', 'NO', 'CONCEDE', 'VETO', 'REJECT']);   // WAIT / MIXED / ADJUST / EXECUTE_MODIFIED → amber
const verdictTone = (v?: string) => {
  const u = (v || '').toUpperCase();
  if (GREEN.has(u)) return 'text-success bg-success/10 border-success/25';
  if (RED.has(u)) return 'text-error bg-error/10 border-error/25';
  return 'text-warning bg-warning/10 border-warning/25';
};

function strikeStr(t: DeskRankedTrade) {
  if (t.structure === 'iron_condor') return `${t.put_long}/${t.put_short}–${t.call_short}/${t.call_long}`;
  if (t.structure === 'jade_lizard') return `put ${t.put_short} · call ${t.call_short}/${t.call_long}`;
  if (t.long_strike) return `${t.short_strike}/${t.long_strike}`;
  return `${t.short_strike}`;
}

function AgentCard({ agent, icon }: { agent: DeskAgent; icon: React.ReactNode }) {
  const [dbg, setDbg] = useState(false);
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-bold flex items-center gap-1.5">{icon}{agent.title}</span>
        <span className={`text-[11px] font-bold rounded-lg border px-2 py-0.5 ${verdictTone(agent.verdict)}`}>{agent.verdict || '—'}</span>
      </div>
      <div className="text-xs text-base-content/75 whitespace-pre-wrap mt-1.5 leading-snug">{agent.content}</div>
      <button type="button" onClick={() => setDbg(v => !v)}
        className="btn btn-ghost btn-xs gap-1 mt-1 text-base-content/40">
        <Terminal className="w-3 h-3" /> Inputs &amp; prompt {dbg ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>
      {dbg && (
        <div className="mt-1 space-y-2 text-[10px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-72 overflow-auto">
          <div><span className="text-base-content/40 uppercase">System prompt</span><pre className="whitespace-pre-wrap">{agent.system_prompt}</pre></div>
          <div><span className="text-base-content/40 uppercase">Input context ({agent.model})</span><pre className="whitespace-pre-wrap">{agent.input_context}</pre></div>
        </div>
      )}
    </div>
  );
}

// The PM's output IS the desk decision — shown ONCE, highlighted (no separate summary card).
function FinalDecisionCard({ pm, fr, onRerun, renderExplore }: {
  pm: DeskAgent; fr: DeskAgentsResult['final_recommendation']; onRerun: () => void;
  renderExplore?: () => React.ReactNode;   // the desk's recommended trade, rendered on demand
}) {
  const [dbg, setDbg] = useState(false);
  const [showExp, setShowExp] = useState(false);
  const body = pm.content.split('\n')
    .filter(l => { const u = l.trim().toUpperCase(); return !u.startsWith('VERDICT') && !u.startsWith('FINAL_DECISION'); })
    .join('\n').trim();
  return (
    <div className={`rounded-xl border p-4 ${verdictTone(pm.verdict)}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-bold flex items-center gap-1.5"><Briefcase className="w-4 h-4" /> Desk decision — {pm.title}</span>
        <span className={`text-xs font-bold rounded-lg border px-2 py-0.5 ${verdictTone(pm.verdict)}`}>{pm.verdict || '—'}</span>
      </div>
      <div className="text-xs text-base-content/80 whitespace-pre-wrap mt-1.5 leading-snug">{body}</div>
      {(fr.sizing || fr.desk_mandate) && (
        <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
          {fr.sizing && (
            <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
              <p className="text-[10px] uppercase tracking-wide text-base-content/40">Sizing</p>
              <p className="text-xs font-semibold">{fr.sizing}</p>
            </div>
          )}
          {fr.desk_mandate && (
            <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
              <p className="text-[10px] uppercase tracking-wide text-base-content/40">Desk mandate</p>
              <p className="text-xs">{fr.desk_mandate}</p>
            </div>
          )}
        </div>
      )}
      <div className="mt-2 text-[11px] text-base-content/60 space-y-0.5 border-t border-white/[0.06] pt-2">
        {fr.consistency && <p>Consistency check: <span className="italic">{fr.consistency}</span></p>}
        {fr.winning_argument && <p>Winning argument: <span className="italic">{fr.winning_argument}</span></p>}
        <p>Algorithm's #1: <span className="font-mono text-base-content/50">{fr.algo_top_pick || '—'}</span></p>
        <p>Debate: Quant → <span className="font-mono text-base-content/70">{fr.quant_choice || '—'}</span>
          {fr.quant_agrees_with_algo && <span className="italic opacity-70"> ({fr.quant_agrees_with_algo})</span>}
          {' · '}Risk → {fr.risk_verdict || '—'}{' · '}Rebuttal → {fr.rebuttal_stance || '—'}
          {fr.final_pick && <> · Desk pick → <span className="font-mono text-base-content/70">{fr.final_pick}</span></>}
        </p>
      </div>
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        {renderExplore && (
          <button type="button" className="btn btn-secondary btn-xs gap-1" onClick={() => setShowExp(v => !v)}>
            <Maximize2 className="w-3 h-3" /> {showExp ? 'Hide' : 'Explore'} recommended trade
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-xs gap-1 text-base-content/40" onClick={() => setDbg(v => !v)}>
          <Terminal className="w-3 h-3" /> Inputs &amp; prompt {dbg ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        <button type="button" className="btn btn-ghost btn-xs" onClick={onRerun}>Re-run the desk</button>
      </div>
      {showExp && renderExplore && (
        <div className="mt-2 rounded-xl border border-secondary/30 bg-base-100/50 p-3">{renderExplore()}</div>
      )}
      {dbg && (
        <div className="mt-1 space-y-2 text-[10px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-72 overflow-auto">
          <div><span className="text-base-content/40 uppercase">System prompt</span><pre className="whitespace-pre-wrap">{pm.system_prompt}</pre></div>
          <div><span className="text-base-content/40 uppercase">Input context ({pm.model})</span><pre className="whitespace-pre-wrap">{pm.input_context}</pre></div>
        </div>
      )}
    </div>
  );
}

// Desk Review v2 — a focused Quant → Risk → Rebuttal → PM debate on ONE selected trade, triggered
// per trade-structure (user action). Reuses the same agent cards as the ranking flow.
export function SingleTradeDeskReview({ ticker, params, trade }: {
  ticker: string;
  params: DeskReviewParams & { model?: string };
  trade: { structure: string; expiration?: string | null; short_strike?: number | null; label?: string };
}) {
  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const a = await runDeskReviewAgents(ticker, {
        ...params,
        focus: { structure: trade.structure, expiration: trade.expiration ?? null, short_strike: trade.short_strike ?? null },
      });
      if (a.error) setErr(a.error); else setAgents(a);
    } catch (e: any) { setErr(e?.message || 'Desk review failed'); }
    finally { setLoading(false); }
  };

  return (
    <div className="mt-3 border-t border-white/[0.06] pt-3">
      {!agents && (
        <button className="btn btn-secondary btn-xs gap-1.5" onClick={run} disabled={loading}>
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Gauge className="w-3.5 h-3.5" />}
          {loading ? 'Running the desk debate…' : 'Desk Review'}
        </button>
      )}
      {loading && !agents && (
        <p className="text-[11px] text-base-content/40 mt-1">Quant → Risk → Rebuttal → PM debate THIS trade (~40–60s).</p>
      )}
      {err && <div className="alert alert-error text-xs mt-1"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}
      {agents && (
        <div className="space-y-3">
          <p className="text-[11px] text-base-content/40 flex items-center gap-1.5">
            <MessageSquare className="w-3.5 h-3.5" /> Desk debate on {trade.label || trade.structure} — Quant → Risk → Rebuttal → PM decides.
          </p>
          <AgentCard agent={agents.quant} icon={<Cpu className="w-4 h-4 text-secondary" />} />
          <AgentCard agent={agents.risk} icon={<Shield className="w-4 h-4 text-secondary" />} />
          <AgentCard agent={agents.rebuttal} icon={<MessageSquare className="w-4 h-4 text-secondary" />} />
          <FinalDecisionCard pm={agents.pm} fr={agents.final_recommendation} onRerun={() => setAgents(null)} />
        </div>
      )}
    </div>
  );
}

export function DeskReview({ ticker, params, renderTrade }: {
  ticker: string;
  params: DeskReviewParams;
  renderTrade?: (trade: DeskRankedTrade) => React.ReactNode;
}) {
  const [rev, setRev] = useState<DeskReviewResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [explore, setExplore] = useState<number | null>(null);

  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentsErr, setAgentsErr] = useState<string | null>(null);

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    // Desk Review is a USER-TRIGGERED action — do NOT auto-fetch when a scan renders.
    // Just clear any prior review so a stale one never shows for a new scan.
    setRev(null); setErr(null); setLoading(false);
    setAgents(null); setAgentsErr(null); setExpanded(null); setExplore(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, paramsKey]);

  const runReview = async () => {
    setLoading(true); setErr(null);
    try {
      const r = await runDeskReview(ticker, params);
      if (r.error) setErr(r.error); else setRev(r);
    } catch (e: any) { setErr(e?.message || 'Desk review failed'); }
    finally { setLoading(false); }
  };

  const askDesk = async () => {
    setAgentsLoading(true); setAgentsErr(null);
    try {
      const a = await runDeskReviewAgents(ticker, params);
      if (a.error) setAgentsErr(a.error); else setAgents(a);
    } catch (e: any) { setAgentsErr(e?.message || 'Desk agents failed'); }
    finally { setAgentsLoading(false); }
  };

  return (
    <div className="rounded-xl border border-secondary/25 bg-secondary/[0.04] p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Gauge className="w-5 h-5 text-secondary" />
        <div>
          <p className="text-sm font-bold">Desk Review</p>
          <p className="text-[11px] text-base-content/50">Ranks every candidate trade, then the desk debates it (Quant · Risk · PM) to pick the best.</p>
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-base-content/50 py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Scoring &amp; ranking every trade…
        </div>
      )}
      {err && <div className="alert alert-warning text-xs"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}

      {!rev && !loading && (
        <div className="space-y-2">
          <p className="text-[11px] text-base-content/40">
            On demand — scores &amp; ranks every candidate trade, then (optionally) runs the Quant · Risk · PM debate.
          </p>
          <button className="btn btn-secondary btn-sm gap-2" onClick={runReview}>
            <Gauge className="w-4 h-4" /> {err ? 'Retry Desk Review' : 'Run Desk Review'}
          </button>
        </div>
      )}

      {rev && rev.ranked.length > 0 && (() => {
        const top = rev.ranked[0];
        const ts = rev.ta_summary;
        return (
          <div className="space-y-3">
            {/* Algo's best pick */}
            <div className="rounded-xl border border-success/25 bg-success/[0.06] p-3">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="flex items-center gap-1.5 text-sm font-bold text-success">
                  <Trophy className="w-4 h-4" /> Algorithmic pick: {top.label}
                </span>
                <span className={`text-lg font-bold ${scoreTone(top.desk_score)}`}>{top.desk_score}/100</span>
              </div>
              <p className="text-xs text-base-content/60 mt-0.5">
                {strikeStr(top)}{top.short_strike_pct != null && ` (${top.short_strike_pct >= 0 ? '+' : ''}${top.short_strike_pct}%)`} · {top.dte}d · exp {top.expiration}
                {' — '}PoP {pct(top.prob_keep_pct)} · prem {money(top.premium)} · Omega {ratio(top.desk_metrics.pm.omega)} · CVaR95 {money(top.desk_metrics.risk.cvar_95)}
              </p>
              <p className="text-[11px] text-base-content/50 mt-1">
                Market: <b>{ts.state}</b> ({ts.bias}) · RSI {ts.rsi != null ? ts.rsi.toFixed(0) : '—'} · POC {money(ts.poc)} · trend {ts.trend}
              </p>
              {renderTrade && (
                <button className="btn btn-secondary btn-xs gap-1 mt-2" onClick={() => setExplore(0)}>
                  <Maximize2 className="w-3 h-3" /> Explore this trade
                </button>
              )}
            </div>

            {/* Events during the contract — what the desk weighs for timing / gap / assignment risk */}
            {rev.events && rev.events.length > 0 && (
              <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
                <p className="text-[11px] font-semibold text-base-content/60 flex items-center gap-1.5 mb-1">
                  <CalendarClock className="w-3.5 h-3.5" /> Events before expiry — weighed for timing &amp; gap/assignment risk
                </p>
                <ul className="space-y-0.5">
                  {rev.events.map((e, i) => (
                    <li key={i} className={`text-[11px] flex gap-1.5 ${e.level === 'warn' ? 'text-warning' : 'text-base-content/60'}`}>
                      <span className="opacity-50">•</span><span>{e.text}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Full ranked table */}
            <div className="overflow-x-auto">
              <table className="table table-xs w-full">
                <thead>
                  <tr className="text-base-content/50">
                    <th>#</th><th>Structure</th><th>Strike</th><th>Desk</th><th>PoP</th>
                    <th>Omega</th><th>Sortino</th><th>CVaR95</th><th>Carry</th><th>net Δ</th><th>Θ/d</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rev.ranked.map((t, i) => {
                    const dm = t.desk_metrics;
                    const open = expanded === i;
                    return (
                      <React.Fragment key={i}>
                        <tr className={i === 0 ? 'bg-success/5' : ''}>
                          <td className="font-bold">{i + 1}</td>
                          <td className="whitespace-nowrap">{t.label}</td>
                          <td className="font-mono text-[11px]">{strikeStr(t)}</td>
                          <td className={`font-bold ${scoreTone(t.desk_score)}`}>{t.desk_score}</td>
                          <td>{pct(t.prob_keep_pct)}</td>
                          <td>{ratio(dm.pm.omega)}</td>
                          <td>{ratio(dm.pm.sortino)}</td>
                          <td>{money(dm.risk.cvar_95)}</td>
                          <td>{annShort(t.premium_annualized_pct)}</td>
                          <td className="font-mono">{dm.trader.net_delta ?? '—'}</td>
                          <td className="font-mono">{money(dm.trader.net_theta, 0)}</td>
                          <td>
                            <div className="flex gap-0.5">
                              {renderTrade && (
                                <button className="btn btn-ghost btn-xs px-1" title="Explore this trade" onClick={() => setExplore(i)}>
                                  <Maximize2 className="w-3 h-3" />
                                </button>
                              )}
                              <button className="btn btn-ghost btn-xs px-1" title="Quick metrics" onClick={() => setExpanded(open ? null : i)}>
                                {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                              </button>
                            </div>
                          </td>
                        </tr>
                        {open && (
                          <tr className="bg-base-200/20">
                            <td colSpan={12} className="text-[11px]">
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-2">
                                <div><p className="font-semibold text-base-content/60">PM</p>
                                  <p>Kelly {ratio(dm.pm.kelly_fraction)} · EV {money(dm.pm.expected_value)}</p>
                                  <p>Exp.ret {pct(dm.pm.expected_return_pct)} · Calmar {ratio(dm.pm.calmar)}</p></div>
                                <div><p className="font-semibold text-base-content/60">Risk</p>
                                  <p>VaR95 {money(dm.risk.var_95)} · CVaR95 {money(dm.risk.cvar_95)}</p>
                                  <p>Max loss {dm.risk.max_loss != null ? money(dm.risk.max_loss) : 'open'} · cap {money(dm.risk.capital)}</p></div>
                                <div><p className="font-semibold text-base-content/60">Trader Greeks</p>
                                  <p>ν {money(dm.trader.net_vega, 0)} · Vanna {dm.trader.net_vanna} · Charm {dm.trader.net_charm}</p>
                                  <p>Volga {dm.trader.net_volga} · avg IV {pct(dm.trader.avg_iv_pct)}</p></div>
                                <div><p className="font-semibold text-base-content/60">Quant + TA</p>
                                  <p>algo {dm.quant.score}/100 ({dm.quant.verdict})</p>
                                  <p className="text-base-content/50">{t.ta_note || 'neutral to regime'}</p></div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Explore — opens the full per-trade card (same as "Best opportunity per structure") */}
            {explore != null && renderTrade && rev.ranked[explore] && (
              <div className="rounded-xl border border-secondary/30 bg-base-100/50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-bold flex items-center gap-1.5">
                    <Maximize2 className="w-4 h-4 text-secondary" /> Exploring #{explore + 1}: {rev.ranked[explore].label}
                  </span>
                  <button className="btn btn-ghost btn-xs gap-1" onClick={() => setExplore(null)}>
                    <X className="w-3 h-3" /> Close
                  </button>
                </div>
                {renderTrade(rev.ranked[explore])}
              </div>
            )}

            {/* Ask the Desk (LLM cascade) */}
            {!agents && (
              <button className="btn btn-secondary btn-sm gap-2" onClick={askDesk} disabled={agentsLoading}>
                {agentsLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {agentsLoading ? 'Running the desk debate…' : 'Ask the Desk (run the debate)'}
              </button>
            )}
            {agentsLoading && !agents && (
              <p className="text-[11px] text-base-content/40">Quant proposes → Risk challenges → Quant rebuts → PM decides, over all {rev.n_trades} trades (~40–60s).</p>
            )}
            {agentsErr && <div className="alert alert-error text-xs"><AlertTriangle className="w-4 h-4" /><span>{agentsErr}</span></div>}

            {agents && (
              <div className="space-y-3">
                <p className="text-[11px] text-base-content/40 flex items-center gap-1.5">
                  <MessageSquare className="w-3.5 h-3.5" /> Desk debate — Quant proposes, Risk challenges, Quant rebuts, PM decides.
                </p>
                <AgentCard agent={agents.quant} icon={<Cpu className="w-4 h-4 text-secondary" />} />
                <AgentCard agent={agents.risk} icon={<Shield className="w-4 h-4 text-secondary" />} />
                <AgentCard agent={agents.rebuttal} icon={<MessageSquare className="w-4 h-4 text-secondary" />} />
                {(() => {
                  const ci = agents.final_recommendation.chosen_index;
                  const finalTrade = (renderTrade && ci != null && ci >= 0 && ci < rev.ranked.length) ? rev.ranked[ci] : null;
                  return (
                    <FinalDecisionCard
                      pm={agents.pm} fr={agents.final_recommendation}
                      onRerun={() => setAgents(null)}
                      renderExplore={finalTrade ? () => renderTrade!(finalTrade) : undefined}
                    />
                  );
                })()}
              </div>
            )}
          </div>
        );
      })()}

      {rev && rev.ranked.length === 0 && (
        <div className="alert alert-warning text-xs">
          <AlertTriangle className="w-4 h-4" />
          <span>{rev.note || 'No candidate trades to review under these filters.'}</span>
        </div>
      )}
    </div>
  );
}
