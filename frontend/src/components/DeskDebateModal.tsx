import React, { useEffect, useRef, useState } from 'react';
import {
  X, Loader2, Cpu, Shield, Briefcase, FileText, Zap, Gavel,
  RefreshCw, FastForward, ChevronDown, ChevronUp, Terminal, AlertTriangle, Sparkles,
} from 'lucide-react';
import type { DeskAgent, DeskAgentsResult } from '../types';

// ───────────────────────────────────────────────────────────────────────────
// Institutional Desk — a dramatized Quant → Risk → Quant(rebuttal) → PM debate.
// The agents' text is fetched once (no streaming); this modal STAGES it as a live
// boardroom scene: three seats, documents sliding between them, a heated challenge,
// and the PM's gavel. Purely presentational — every word comes from the LLM cascade.
// ───────────────────────────────────────────────────────────────────────────

const SEATS = {
  quant: { name: 'Quant', role: 'Volatility & structure', Icon: Cpu, pos: 'left-[16%] top-[62%]' },
  risk: { name: 'Risk', role: 'Tail & downside', Icon: Shield, pos: 'left-[84%] top-[62%]' },
  pm: { name: 'PM', role: 'Capital & mandate', Icon: Briefcase, pos: 'left-1/2 top-[16%]' },
} as const;

type SeatKey = keyof typeof SEATS;

// Debate beats. `speaker` drives the highlight + bubble; `paper` the sliding document.
const BEATS: { stage: number; speaker: SeatKey; agent: keyof DeskAgentsResult; paper?: string; heated?: boolean; caption: string }[] = [
  { stage: 1, speaker: 'quant', agent: 'quant', paper: 'ddm-q2p', caption: 'Quant tables the trade' },
  { stage: 2, speaker: 'risk', agent: 'risk', paper: 'ddm-r2q', heated: true, caption: 'Risk challenges the tail' },
  { stage: 3, speaker: 'quant', agent: 'rebuttal', paper: 'ddm-q2r', caption: 'Quant rebuts' },
  { stage: 4, speaker: 'pm', agent: 'pm', caption: 'PM rules on the desk' },
];

const verdictTone = (v?: string) => {
  const u = (v || '').toUpperCase();
  if (['CLEAR', 'ACCEPTABLE', 'ENTER', 'HOLD', 'APPROVE', 'APPROVE_WITH_CONDITIONS', 'EXECUTE', 'GOOD ENTRY'].includes(u))
    return 'text-success bg-success/10 border-success/30';
  if (['NONE', 'EXCESSIVE', 'SKIP', 'NO', 'CONCEDE', 'VETO', 'REJECT'].includes(u))
    return 'text-error bg-error/10 border-error/30';
  return 'text-warning bg-warning/10 border-warning/30';
};

const STYLE = `
@keyframes ddm-q2p { 0%{left:18%;top:63%;opacity:0;transform:rotate(-8deg) scale(.7)} 12%{opacity:1} 88%{opacity:1} 100%{left:50%;top:22%;opacity:0;transform:rotate(10deg) scale(1)} }
@keyframes ddm-r2q { 0%{left:82%;top:63%;opacity:0;transform:rotate(12deg) scale(.7)} 12%{opacity:1} 88%{opacity:1} 100%{left:18%;top:63%;opacity:0;transform:rotate(-16deg) scale(1.05)} }
@keyframes ddm-q2r { 0%{left:18%;top:63%;opacity:0;transform:rotate(-8deg) scale(.7)} 12%{opacity:1} 88%{opacity:1} 100%{left:82%;top:63%;opacity:0;transform:rotate(14deg) scale(1)} }
.ddm-paper{position:absolute;z-index:20;animation-duration:1.5s;animation-timing-function:cubic-bezier(.5,-0.2,.3,1.2);animation-fill-mode:both}
@keyframes ddm-shake{0%,100%{transform:translateX(0)}20%{transform:translateX(-3px) rotate(-2deg)}40%{transform:translateX(3px) rotate(2deg)}60%{transform:translateX(-2px)}80%{transform:translateX(2px)}}
.ddm-shake{animation:ddm-shake .5s ease-in-out 2}
@keyframes ddm-gavel{0%{transform:rotate(-24deg)}50%{transform:rotate(6deg)}70%{transform:rotate(-4deg)}100%{transform:rotate(0)}}
.ddm-gavel{animation:ddm-gavel .6s ease-out both;transform-origin:80% 80%}
@keyframes ddm-spark{0%{opacity:0;transform:scale(.4)}30%{opacity:1;transform:scale(1.15)}100%{opacity:0;transform:scale(.7)}}
.ddm-spark{animation:ddm-spark .8s ease-out both}
@keyframes ddm-pulse{0%,100%{opacity:.35;transform:scale(1)}50%{opacity:.9;transform:scale(1.06)}}
.ddm-live .ddm-seat-active{animation:ddm-pulse 1.4s ease-in-out infinite}
@keyframes ddm-float{0%,100%{transform:translateY(0) rotate(var(--r,0deg))}50%{transform:translateY(-6px) rotate(var(--r,0deg))}}
.ddm-float{animation:ddm-float 2.4s ease-in-out infinite}
@keyframes ddm-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
.ddm-rise{animation:ddm-rise .35s ease-out both}
@media (prefers-reduced-motion: reduce){.ddm-paper,.ddm-shake,.ddm-gavel,.ddm-spark,.ddm-float,.ddm-live .ddm-seat-active{animation:none!important}}
`;

function Seat({ seat, active, revealed, verdict, heated }: {
  seat: SeatKey; active: boolean; revealed: boolean; verdict?: string; heated?: boolean;
}) {
  const s = SEATS[seat];
  const { Icon } = s;
  return (
    <div className={`absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center gap-1 transition-all duration-500 ${s.pos} ${active ? 'z-10 scale-110' : 'opacity-80'}`}>
      <div className={`relative grid place-items-center rounded-2xl border w-14 h-14 sm:w-16 sm:h-16 transition-all ${active ? 'ddm-seat-active border-secondary bg-secondary/15 shadow-lg shadow-secondary/20' : 'border-white/10 bg-base-200/70'} ${heated && active ? 'ddm-shake' : ''}`}>
        <Icon className={`w-6 h-6 sm:w-7 sm:h-7 ${active ? 'text-secondary' : 'text-base-content/60'}`} />
        {heated && active && <Zap className="ddm-spark absolute -right-2 -top-2 w-5 h-5 text-warning fill-warning/30" />}
        {seat === 'pm' && active && <Gavel className="ddm-gavel absolute -right-3 -bottom-2 w-5 h-5 text-warning" />}
      </div>
      <div className="text-center leading-tight">
        <p className={`text-[11px] font-bold ${active ? 'text-secondary' : 'text-base-content/70'}`}>{s.name}</p>
        <p className="text-[8px] text-base-content/40 hidden sm:block">{s.role}</p>
      </div>
      {revealed && verdict && (
        <span className={`text-[8px] font-bold rounded border px-1 py-px ${verdictTone(verdict)}`}>{verdict}</span>
      )}
    </div>
  );
}

function TranscriptCard({ agent, Icon, label, defaultOpen }: {
  agent: DeskAgent; Icon: React.ElementType; label: string; defaultOpen?: boolean;
}) {
  const [dbg, setDbg] = useState(false);
  const [open, setOpen] = useState(defaultOpen ?? true);
  return (
    <div className="ddm-rise rounded-xl border border-white/[0.07] bg-base-200/40 p-2.5">
      <button type="button" onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between gap-2">
        <span className="text-xs font-bold flex items-center gap-1.5"><Icon className="w-3.5 h-3.5 text-secondary" />{label}</span>
        <span className="flex items-center gap-1.5">
          <span className={`text-[10px] font-bold rounded border px-1.5 py-px ${verdictTone(agent.verdict)}`}>{agent.verdict || '—'}</span>
          {open ? <ChevronUp className="w-3.5 h-3.5 opacity-50" /> : <ChevronDown className="w-3.5 h-3.5 opacity-50" />}
        </span>
      </button>
      {open && <p className="text-[11px] text-base-content/75 whitespace-pre-wrap mt-1.5 leading-snug">{agent.content}</p>}
      {open && (
        <>
          <button type="button" onClick={() => setDbg(v => !v)} className="btn btn-ghost btn-xs gap-1 mt-1 text-base-content/40 h-auto min-h-0 py-0.5">
            <Terminal className="w-3 h-3" /> inputs &amp; prompt
          </button>
          {dbg && (
            <div className="mt-1 space-y-1.5 text-[9px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-56 overflow-auto">
              <pre className="whitespace-pre-wrap">{agent.system_prompt}</pre>
              <pre className="whitespace-pre-wrap border-t border-white/10 pt-1">{agent.input_context}</pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function DeskDebateModal({ open, onClose, loading, error, agents, tradeLabel, onRerun }: {
  open: boolean;
  onClose: () => void;
  loading: boolean;
  error?: string | null;
  agents: DeskAgentsResult | null;
  tradeLabel?: string;
  onRerun?: () => void;
}) {
  const reduceMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const [stage, setStage] = useState(1);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clearTimers = () => { timers.current.forEach(clearTimeout); timers.current = []; };

  useEffect(() => {
    clearTimers();
    if (!agents) { setStage(1); return; }
    if (reduceMotion) { setStage(5); return; }
    setStage(1);
    timers.current = [
      setTimeout(() => setStage(2), 3000),
      setTimeout(() => setStage(3), 6000),
      setTimeout(() => setStage(4), 9000),
      setTimeout(() => setStage(5), 10800),
    ];
    return clearTimers;
  }, [agents, reduceMotion]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const skip = () => { clearTimers(); setStage(5); };
  const replay = () => {
    clearTimers(); setStage(1);
    timers.current = [
      setTimeout(() => setStage(2), 3000),
      setTimeout(() => setStage(3), 6000),
      setTimeout(() => setStage(4), 9000),
      setTimeout(() => setStage(5), 10800),
    ];
  };

  const beat = BEATS.find(b => b.stage === stage) ?? BEATS[BEATS.length - 1];
  const activeAgent: DeskAgent | undefined = agents ? (agents[beat.agent] as DeskAgent) : undefined;
  const done = stage >= 5;
  const fr = agents?.final_recommendation;

  const bubble = activeAgent?.content
    ? (activeAgent.content.length > 260 ? activeAgent.content.slice(0, 260).trimEnd() + '…' : activeAgent.content)
    : '';

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/70 p-3 sm:p-6 backdrop-blur-sm" onClick={onClose}>
      <style>{STYLE}</style>
      <div className="relative w-full max-w-3xl my-auto rounded-2xl border border-white/10 bg-base-100 shadow-2xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="sticky top-0 z-30 flex items-center justify-between gap-2 rounded-t-2xl border-b border-white/10 bg-base-100/95 px-4 py-3 backdrop-blur">
          <div>
            <p className="text-sm font-bold flex items-center gap-1.5"><Sparkles className="w-4 h-4 text-secondary" /> Institutional Desk</p>
            <p className="text-[11px] text-base-content/50">{tradeLabel ? `Debating ${tradeLabel}` : 'Quant → Risk → Rebuttal → PM'}</p>
          </div>
          <button className="btn btn-ghost btn-xs px-1" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>

        <div className="p-4 space-y-4">
          {/* Error */}
          {error && (
            <div className="alert alert-error text-xs"><AlertTriangle className="w-4 h-4" /><span className="flex-1">{error}</span>
              {onRerun && <button className="btn btn-ghost btn-xs" onClick={onRerun}>Retry</button>}
            </div>
          )}

          {/* Boardroom scene */}
          {!error && (
            <div className={`relative h-[280px] sm:h-[320px] rounded-2xl border border-white/[0.06] overflow-hidden ${loading ? 'ddm-live' : ''}`}
              style={{ background: 'radial-gradient(120% 90% at 50% 0%, rgba(99,102,241,0.10), transparent 60%), radial-gradient(100% 80% at 50% 100%, rgba(30,41,59,0.5), rgba(15,23,42,0.2))' }}>
              {/* Table */}
              <div className="absolute left-1/2 top-[52%] -translate-x-1/2 -translate-y-1/2 w-[62%] h-[42%] rounded-[50%] border border-white/10 bg-gradient-to-b from-base-300/40 to-base-300/10 shadow-inner" />
              <div className="absolute left-1/2 top-[52%] -translate-x-1/2 -translate-y-1/2 text-[9px] uppercase tracking-[0.2em] text-base-content/25">the desk</div>

              {/* Seats */}
              <Seat seat="pm" active={(loading || stage >= 4)} revealed={done} verdict={agents?.pm.verdict} />
              <Seat seat="quant" active={loading || beat.speaker === 'quant'} revealed={!!agents && stage >= 1} verdict={agents?.quant.verdict} />
              <Seat seat="risk" active={loading || beat.speaker === 'risk'} revealed={!!agents && stage >= 2} verdict={agents?.risk.verdict} heated={beat.heated} />

              {/* Flying document */}
              {agents && !done && beat.paper && (
                <div key={`${stage}-${beat.paper}`} className="ddm-paper" style={{ animationName: beat.paper }}>
                  <div className="ddm-float flex items-center gap-1 rounded-md border border-secondary/40 bg-base-100/90 px-1.5 py-1 shadow-lg">
                    <FileText className="w-3.5 h-3.5 text-secondary" />
                  </div>
                </div>
              )}

              {/* Loading caption */}
              {loading && (
                <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-2 text-xs text-base-content/60">
                  <Loader2 className="w-4 h-4 animate-spin" /> The desk is convening — reading the tape…
                </div>
              )}

              {/* Speech bubble */}
              {agents && !done && bubble && (
                <div className={`ddm-rise absolute z-30 max-w-[64%] ${beat.speaker === 'pm' ? 'left-1/2 -translate-x-1/2 top-[34%]' : beat.speaker === 'quant' ? 'left-3 bottom-3' : 'right-3 bottom-3'}`} key={stage}>
                  <div className={`rounded-xl border px-3 py-2 text-[11px] leading-snug shadow-lg ${verdictTone(activeAgent?.verdict)}`}>
                    <p className="font-bold mb-0.5 flex items-center gap-1">{beat.caption}
                      {activeAgent?.verdict && <span className="opacity-60 font-medium">· {activeAgent.verdict}</span>}</p>
                    <p className="text-base-content/75">{bubble}</p>
                  </div>
                </div>
              )}

              {/* Progress dots */}
              {agents && (
                <div className="absolute top-2.5 right-3 flex items-center gap-1">
                  {BEATS.map(b => <span key={b.stage} className={`w-1.5 h-1.5 rounded-full transition-colors ${stage >= b.stage ? 'bg-secondary' : 'bg-white/15'}`} />)}
                </div>
              )}
            </div>
          )}

          {/* Scene controls */}
          {agents && !error && (
            <div className="flex items-center gap-2 flex-wrap">
              {!done
                ? <button className="btn btn-ghost btn-xs gap-1" onClick={skip}><FastForward className="w-3.5 h-3.5" /> Skip to decision</button>
                : <button className="btn btn-ghost btn-xs gap-1" onClick={replay}><RefreshCw className="w-3.5 h-3.5" /> Replay debate</button>}
              {onRerun && <button className="btn btn-ghost btn-xs gap-1 text-base-content/50" onClick={onRerun}><RefreshCw className="w-3.5 h-3.5" /> Re-run the desk</button>}
            </div>
          )}

          {/* Final decision — revealed at the end */}
          {done && fr && agents && (
            <div className={`ddm-rise rounded-xl border p-3 ${verdictTone(agents.pm.verdict)}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-bold flex items-center gap-1.5"><Gavel className="w-4 h-4" /> Desk decision</span>
                <span className={`text-xs font-bold rounded-lg border px-2 py-0.5 ${verdictTone(agents.pm.verdict)}`}>{agents.pm.verdict || fr.verdict || '—'}</span>
              </div>
              {(fr.final_pick || fr.decision) && <p className="text-sm font-semibold mt-1">{fr.final_pick || fr.decision}</p>}
              {fr.rationale && <p className="text-xs text-base-content/75 mt-1 leading-snug">{fr.rationale}</p>}
              <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {fr.sizing && <div className="rounded-lg border border-white/[0.06] bg-base-100/40 p-2"><p className="text-[10px] uppercase tracking-wide text-base-content/40">Sizing</p><p className="text-xs font-semibold">{fr.sizing}</p></div>}
                {fr.desk_mandate && <div className="rounded-lg border border-white/[0.06] bg-base-100/40 p-2"><p className="text-[10px] uppercase tracking-wide text-base-content/40">Mandate</p><p className="text-xs">{fr.desk_mandate}</p></div>}
              </div>
              {fr.winning_argument && <p className="text-[11px] text-base-content/55 mt-2 italic">Winning argument: {fr.winning_argument}</p>}
            </div>
          )}

          {/* Full transcript — revealed at the end */}
          {done && agents && (
            <div className="space-y-2">
              <p className="text-[10px] uppercase tracking-wide text-base-content/40">Full transcript</p>
              <TranscriptCard agent={agents.quant} Icon={Cpu} label="Quant — proposal" />
              <TranscriptCard agent={agents.risk} Icon={Shield} label="Risk — challenge" />
              <TranscriptCard agent={agents.rebuttal} Icon={Cpu} label="Quant — rebuttal" defaultOpen={false} />
              <TranscriptCard agent={agents.pm} Icon={Briefcase} label="PM — ruling" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
