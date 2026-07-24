import React, { useState } from 'react';
import {
  MessageSquare, X, Maximize2, Loader2, Gauge, AlertTriangle, RefreshCw, MoreHorizontal,
} from 'lucide-react';
import { runDeskReviewAgents } from '../api';
import type { DeskReviewParams, DeskFocusTrade } from '../api';
import type { DeskAgent, DeskAgentsResult } from '../types';

// ── Desk debate as a decision-first slide-over ────────────────────────────────
// Replaces the old stack of four long agent cards. The PM verdict + a 4-seat
// consensus strip are pinned at the top; the arguments render as a collapsible
// chat thread (Quant proposes → Risk challenges → Quant rebuts → PM decides) in a
// right-side slide-over, so the detail column behind it never stretches.

const GREEN = new Set(['CLEAR', 'ACCEPTABLE', 'ENTER', 'GOOD ENTRY', 'HOLD', 'APPROVE_WITH_CONDITIONS', 'APPROVE', 'EXECUTE']);
const RED = new Set(['NONE', 'EXCESSIVE', 'SKIP', 'NO', 'CONCEDE', 'VETO', 'REJECT']);
const verdictTone = (v?: string) => {
  const u = (v || '').toUpperCase();
  if (GREEN.has(u)) return 'text-success bg-success/10 border-success/25';
  if (RED.has(u)) return 'text-error bg-error/10 border-error/25';
  return 'text-warning bg-warning/10 border-warning/25';
};
const dotTone = (v?: string) => {
  const u = (v || '').toUpperCase();
  if (GREEN.has(u)) return 'bg-success';
  if (RED.has(u)) return 'bg-error';
  return 'bg-warning';
};

function ConsChip({ label, v }: { label: string; v?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] rounded-lg border border-white/[0.06] bg-base-200/40 px-2 py-0.5">
      <span className={`w-1.5 h-1.5 rounded-full ${dotTone(v)}`} />{label} · {(v || '—').toLowerCase()}
    </span>
  );
}

function AgentDebug({ agent }: { agent: DeskAgent }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(o => !o)}
        className="btn btn-ghost btn-xs btn-circle opacity-40" title="Inputs & prompt" aria-label="Inputs and prompt">
        <MoreHorizontal className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="mt-1 w-full text-[10px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-60 overflow-auto space-y-2">
          <div><span className="uppercase opacity-60">System prompt</span><pre className="whitespace-pre-wrap">{agent.system_prompt}</pre></div>
          <div><span className="uppercase opacity-60">Input context ({agent.model})</span><pre className="whitespace-pre-wrap">{agent.input_context}</pre></div>
        </div>
      )}
    </>
  );
}

function AgentBubble({ agent, side, name, avatar, tag }: {
  agent: DeskAgent; side: 'left' | 'right'; name: string; avatar: string; tag?: string;
}) {
  const [full, setFull] = useState(false);
  const right = side === 'right';
  const avatarTone = right ? 'bg-error/15 text-error' : 'bg-secondary/15 text-secondary';
  const bubbleTone = right ? 'bg-error/10 text-base-content/80' : 'bg-base-200/50 text-base-content/75';
  const long = (agent.content || '').length > 150;
  return (
    <div className={`flex gap-2.5 items-start ${right ? 'flex-row-reverse' : ''}`}>
      <div className={`w-7 h-7 shrink-0 rounded-full flex items-center justify-center text-[11px] font-bold ${avatarTone}`}>{avatar}</div>
      <div className="flex-1 min-w-0">
        <div className={`flex items-center gap-2 mb-1 ${right ? 'flex-row-reverse' : ''}`}>
          <span className="text-xs font-bold">{name}</span>
          {tag && <span className="text-[10px] text-base-content/40">{tag}</span>}
          <span className={`text-[10px] font-bold rounded border px-1.5 py-px ${verdictTone(agent.verdict)}`}>{agent.verdict || '—'}</span>
          <AgentDebug agent={agent} />
        </div>
        <div className={`rounded-xl p-2.5 text-xs leading-relaxed ${bubbleTone} ${right ? 'rounded-tr-sm' : 'rounded-tl-sm'}`}>
          <p className={`whitespace-pre-wrap ${full ? '' : 'line-clamp-3'}`}>{agent.content}</p>
          {long && (
            <button type="button" className="text-secondary text-[11px] mt-1" onClick={() => setFull(f => !f)}>
              {full ? 'Show less' : 'Show full argument'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function PmBubble({ pm }: { pm: DeskAgent }) {
  // Strip the machine-readable VERDICT/FINAL_DECISION lines — they're shown as the pill.
  const body = (pm.content || '').split('\n')
    .filter(l => { const u = l.trim().toUpperCase(); return !u.startsWith('VERDICT') && !u.startsWith('FINAL_DECISION'); })
    .join('\n').trim();
  return (
    <div className="rounded-xl border border-white/10 bg-base-200/40 p-3">
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-7 h-7 shrink-0 rounded-full bg-base-content text-base-100 flex items-center justify-center text-[10px] font-bold">PM</div>
        <span className="text-xs font-bold">Portfolio manager · decides</span>
        <span className={`ml-auto text-[10px] font-bold rounded border px-1.5 py-px ${verdictTone(pm.verdict)}`}>{pm.verdict || '—'}</span>
        <AgentDebug agent={pm} />
      </div>
      <p className="text-xs text-base-content/80 whitespace-pre-wrap leading-relaxed">{body}</p>
    </div>
  );
}

// Render-only: a compact inline result bar + the slide-over overlay (self-managed open state).
export function DeskDebatePanel({ agents, title, onRerun, renderExplore }: {
  agents: DeskAgentsResult;
  title?: string;
  onRerun?: () => void;
  renderExplore?: () => React.ReactNode;
}) {
  const [open, setOpen] = useState(true);   // pop the panel as soon as the debate lands
  const fr = agents.final_recommendation;
  const headline = fr.verdict || agents.pm.verdict;

  return (
    <>
      {/* Inline result bar — always in place so the debate can be reopened */}
      <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3 flex items-center gap-3 flex-wrap">
        <span className={`text-xs font-bold rounded-lg border px-2 py-0.5 ${verdictTone(headline)}`}>{headline || '—'}</span>
        <span className="text-sm font-semibold">{fr.decision || 'Desk decision'}</span>
        {fr.rationale && <span className="text-xs text-base-content/50 truncate flex-1 min-w-[140px]">{fr.rationale}</span>}
        <button type="button" className="btn btn-secondary btn-xs gap-1 ml-auto" onClick={() => setOpen(true)}>
          <Maximize2 className="w-3 h-3" /> View debate
        </button>
      </div>

      {/* Slide-over */}
      {open && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="relative w-full max-w-xl bg-base-100 h-full border-l border-white/10 flex flex-col shadow-2xl">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
              <span className="flex items-center gap-2 text-sm font-bold">
                <MessageSquare className="w-4 h-4 text-secondary" /> {title || 'Desk debate'} · {agents.ticker}
              </span>
              <button type="button" className="btn btn-ghost btn-xs btn-circle" onClick={() => setOpen(false)} aria-label="Close">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {/* Decision — verdict-first */}
              <div className="px-4 py-3 border-b border-white/10 bg-base-200/30">
                <p className="text-[10px] uppercase tracking-wide text-base-content/50">Desk decision</p>
                <div className="flex items-center gap-2 flex-wrap mt-1">
                  <span className={`text-sm font-bold rounded-lg border px-2 py-0.5 ${verdictTone(headline)}`}>{headline || '—'}</span>
                  {fr.decision && <span className="text-base font-bold">{fr.decision}</span>}
                </div>
                {fr.rationale && <p className="text-xs text-base-content/70 mt-1.5 leading-snug">{fr.rationale}</p>}
                <div className="flex flex-wrap gap-1.5 mt-2.5">
                  <ConsChip label="Quant" v={agents.quant.verdict} />
                  <ConsChip label="Risk" v={agents.risk.verdict} />
                  <ConsChip label="Rebuttal" v={agents.rebuttal.verdict} />
                  <ConsChip label="PM" v={agents.pm.verdict} />
                </div>
                {(fr.sizing || fr.desk_mandate || fr.algo_top_pick) && (
                  <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5 text-[11px] text-base-content/60">
                    {fr.sizing && <span><span className="text-base-content/40">Sizing</span> {fr.sizing}</span>}
                    {fr.desk_mandate && <span><span className="text-base-content/40">Mandate</span> {fr.desk_mandate}</span>}
                    {fr.algo_top_pick && <span><span className="text-base-content/40">Algo #1</span> <span className="font-mono">{fr.algo_top_pick}</span></span>}
                  </div>
                )}
              </div>

              {/* Transcript — chat thread */}
              <div className="px-4 py-4 space-y-4">
                <p className="text-[11px] text-base-content/40">Transcript · Quant proposes → Risk challenges → Quant rebuts → PM decides</p>
                <AgentBubble agent={agents.quant} side="left" name="Quant" avatar="Q" />
                <AgentBubble agent={agents.risk} side="right" name="Risk" avatar="R" />
                <AgentBubble agent={agents.rebuttal} side="left" name="Quant" avatar="Q" tag="rebuttal" />
                <PmBubble pm={agents.pm} />

                {renderExplore && (
                  <div className="rounded-xl border border-secondary/30 bg-base-200/40 p-3">
                    <p className="text-xs font-semibold mb-2 flex items-center gap-1.5">
                      <Maximize2 className="w-3.5 h-3.5 text-secondary" /> Recommended trade
                    </p>
                    {renderExplore()}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            <div className="px-4 py-3 border-t border-white/10 flex items-center gap-2">
              {onRerun && (
                <button type="button" className="btn btn-ghost btn-xs gap-1" onClick={() => { onRerun(); setOpen(false); }}>
                  <RefreshCw className="w-3 h-3" /> Re-run desk
                </button>
              )}
              <button type="button" className="btn btn-ghost btn-xs ml-auto" onClick={() => setOpen(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// Fetch-owning launcher for the detail pane: a trigger button that runs the focused
// agents debate for one trade, then hands off to DeskDebatePanel.
export function DeskDebate({ ticker, params, focus, label = 'Run the desk debate', renderExplore }: {
  ticker: string;
  params: DeskReviewParams & { model?: string };
  focus?: DeskFocusTrade;
  label?: string;
  renderExplore?: () => React.ReactNode;
}) {
  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const a = await runDeskReviewAgents(ticker, { ...params, focus });
      if (a.error) setErr(a.error); else setAgents(a);
    } catch (e: any) { setErr(e?.message || 'Desk review failed'); }
    finally { setLoading(false); }
  };

  if (agents) {
    return <DeskDebatePanel agents={agents} title="Debate this trade" onRerun={() => setAgents(null)} renderExplore={renderExplore} />;
  }

  return (
    <div>
      <button type="button" className="btn btn-secondary btn-sm gap-1.5" onClick={run} disabled={loading}>
        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Gauge className="w-4 h-4" />}
        {loading ? 'Running the desk debate…' : label}
      </button>
      {loading && <p className="text-[11px] text-base-content/40 mt-1">Quant → Risk → Rebuttal → PM debate this trade (~40–60s).</p>}
      {err && <div className="alert alert-error text-xs mt-1"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}
    </div>
  );
}
