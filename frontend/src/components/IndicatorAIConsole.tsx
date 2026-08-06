import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Send, Loader2, Code2, X, Activity } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { MicroChatMessage } from '../types';

export interface AiChip { key: string; label: string; tone?: string; onRemove?: () => void }

interface IndicatorAIConsoleProps {
  /** The exact JSON payload sent to the LLM — shown verbatim on the "JSON" toggle. */
  selectionJson: unknown;
  /** Selected indicators, rendered as removable chips. */
  chips: AiChip[];
  /** Bound analyze call: (selection, messages) → assistant reply. */
  analyzeFn: (selection: Record<string, unknown>, messages: MicroChatMessage[]) => Promise<MicroChatMessage>;
  emptyHint?: string;
}

/**
 * Reusable "analyze my selected indicators with AI" console: selected-indicator chips,
 * a click-to-reveal of the exact JSON payload (triage what goes to the LLM), an Analyze
 * action, and a follow-up chat. Stateless server-side — the transcript lives here.
 */
export default function IndicatorAIConsole({ selectionJson, chips, analyzeFn, emptyHint }: IndicatorAIConsoleProps) {
  const [showJson, setShowJson] = useState(false);
  const [chat, setChat] = useState<MicroChatMessage[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [chat, analyzing]);

  const run = async (msgs: MicroChatMessage[]) => {
    setAnalyzing(true);
    setChatError(null);
    try {
      const reply = await analyzeFn(selectionJson as Record<string, unknown>, msgs);
      setChat([...msgs, reply]);
    } catch (e: any) {
      setChatError(e?.message || 'Analysis failed');
      setChat(msgs);
    } finally {
      setAnalyzing(false);
    }
  };
  const start = () => {
    if (!chips.length || analyzing) return;
    run([{ role: 'user', content: 'Analyze the selected indicators and tell me what trades I could confidently make.' }]);
  };
  const send = (e: React.FormEvent) => {
    e.preventDefault();
    const text = followUp.trim();
    if (!text || analyzing) return;
    setFollowUp('');
    run([...chat, { role: 'user', content: text }]);
  };

  return (
    <div className="rounded-lg border border-primary/20 bg-base-200/20 p-2.5 space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <span className="text-xs font-bold text-primary flex items-center gap-1.5"><Sparkles className="w-4 h-4" /> AI Analysis</span>
        <div className="flex items-center gap-1.5">
          <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowJson(s => !s)}
            title="Show the exact JSON payload sent to the AI">
            <Code2 className="w-3.5 h-3.5" /> {showJson ? 'Hide payload' : 'View payload'}
          </button>
          <button className="btn btn-primary btn-xs gap-1" onClick={start} disabled={!chips.length || analyzing}>
            {analyzing && !chat.length ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
            {chat.length ? 'Re-analyze' : 'Analyze with AI'}
          </button>
        </div>
      </div>

      {chips.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {chips.map(c => (
            <button key={c.key} onClick={c.onRemove}
              className="badge badge-sm bg-base-100/60 border-base-content/10 gap-1 hover:border-error/40">
              <span className={c.tone}>{c.label}</span>
              {c.onRemove && <X className="w-2.5 h-2.5 opacity-50" />}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-[11px] text-base-content/40">{emptyHint || 'Select indicators to include them in the AI read.'}</p>
      )}

      {showJson && (
        <pre className="text-[10px] bg-base-300/60 rounded p-2 overflow-auto max-h-48 text-base-content/70">
          {JSON.stringify(selectionJson, null, 2)}
        </pre>
      )}

      {chatError && <div className="alert alert-error text-xs py-1.5">{chatError}</div>}

      {chat.length > 0 && (
        <div className="space-y-2 max-h-[24rem] overflow-y-auto pr-1">
          {chat.map((m, i) => (
            m.role === 'user' ? (
              <div key={i} className="text-[11px] text-base-content/60 bg-base-100/40 rounded-lg px-2.5 py-1.5">
                <span className="font-semibold text-base-content/80">You:</span> {m.content}
              </div>
            ) : (
              <div key={i} className="prose prose-sm prose-invert max-w-none text-xs prose-headings:text-base-content prose-strong:text-base-content prose-p:text-base-content/75 prose-li:text-base-content/75 bg-base-100/20 rounded-lg px-3 py-2 border border-white/[0.04]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
              </div>
            )
          ))}
          {analyzing && <div className="flex items-center gap-2 text-[11px] text-base-content/50"><Loader2 className="w-3.5 h-3.5 animate-spin" /> Thinking…</div>}
          <div ref={endRef} />
        </div>
      )}

      {chat.length > 0 && (
        <form onSubmit={send} className="flex gap-2">
          <input className="input input-bordered input-sm flex-1 text-xs"
            placeholder="Ask a follow-up — e.g. “Where exactly do I put my stop?”"
            value={followUp} onChange={e => setFollowUp(e.target.value)} disabled={analyzing} />
          <button type="submit" className="btn btn-primary btn-sm btn-square" disabled={analyzing || !followUp.trim()}>
            {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </form>
      )}
      <p className="text-[9px] text-base-content/30 flex items-center gap-1"><Activity className="w-3 h-3" /> The AI reads your current selection as JSON. Educational analysis, not financial advice.</p>
    </div>
  );
}
