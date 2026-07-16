/**
 * CreateAgentFromTradeModal — one-click agent creation pre-populated with trade context.
 *
 * Takes the trade + optional P&L snapshot and fills in:
 *  - Agent name: "{ticker} Trade Monitor"
 *  - Instructions: full trade context (ticker, strategy, entry, DTE, thesis, current P&L)
 *
 * Calls existing createAgent() API — no new backend needed.
 */

import React, { useState, useEffect } from 'react';
import { X, Bot, Loader2, AlertCircle, Sparkles, ChevronDown, ChevronUp } from 'lucide-react';
import type { SavedStrategyItem, LivePnlResponse } from '../../api';
import type { AgentCreateInput } from '../../types';
import { fmtMoney, fmtAnnualized, fmtDate, fmtDTE } from '../../lib/tradeFormat';

interface Props {
  open: boolean;
  onClose: () => void;
  trade: SavedStrategyItem;
  pnl?: LivePnlResponse | null;
  onCreateAgent: (data: AgentCreateInput) => Promise<unknown>;
}

function buildInstructions(trade: SavedStrategyItem, pnl?: LivePnlResponse | null): string {
  const t = trade.ticker;
  const stype = trade.strategy_type?.replace(/_/g, ' ');
  const entryDate = trade.entry_date ? fmtDate(trade.entry_date) : 'unknown';
  const entryNet = trade.entry_net_debit != null ? fmtMoney(trade.entry_net_debit) : 'unknown';
  const notes = trade.notes;

  // Extract expiry from legs or snapshot
  const expiry = trade.result_snapshot?.expirationDate
    || trade.result_snapshot?.spread?.expiration
    || trade.legs_data?.find((l: any) => l.expiration || l.expiry)?.expiration
    || null;

  const dte = expiry
    ? Math.max(0, Math.floor((new Date(expiry).getTime() - Date.now()) / 86400000))
    : null;

  const lines: string[] = [
    `You are a dedicated trade monitor agent for the following position.`,
    ``,
    `== POSITION CONTEXT ==`,
    `Ticker: ${t}`,
    `Strategy: ${stype}`,
    `Trade name: ${trade.name}`,
    `Entered: ${entryDate}`,
    `Entry cost / net: ${entryNet}`,
  ];

  if (expiry) lines.push(`Expiration: ${expiry} (${dte != null ? dte + 'd remaining' : 'unknown DTE'})`);

  if (trade.legs_data?.length) {
    lines.push(``, `Legs:`);
    trade.legs_data.forEach((leg: any, i: number) => {
      const ep = trade.entry_prices?.[i]?.price ?? leg.mid ?? leg.price ?? '?';
      lines.push(`  ${leg.action?.toUpperCase()} ${leg.type?.toUpperCase()} ${leg.strike} @ ${ep} — exp ${leg.expiration || expiry || '?'}`);
    });
  }

  if (pnl) {
    lines.push(``, `== CURRENT P&L SNAPSHOT ==`);
    lines.push(`Unrealized P&L: ${fmtMoney(pnl.unrealized_pnl)} (${pnl.pnl_pct}%)`);
    lines.push(`Entry cost: ${fmtMoney(pnl.entry_cost)}`);
    lines.push(`Current value: ${fmtMoney(pnl.current_value)}`);
    lines.push(`Days held: ${pnl.days_held}`);
    if (pnl.max_profit != null) lines.push(`Max profit: ${fmtMoney(pnl.max_profit)}`);
    if (pnl.max_loss != null) lines.push(`Max loss: ${fmtMoney(pnl.max_loss)}`);
    if (pnl.analysis?.annualized_return_to_expiry != null) {
      lines.push(`Annualized return to expiry: ${pnl.analysis.annualized_return_to_expiry.toFixed(2)}% ann.`);
    }
    if (pnl.analysis?.hold_vs_close) {
      lines.push(`Current hold/close signal: ${pnl.analysis.hold_vs_close}`);
    }
  }

  if (notes) {
    lines.push(``, `== TRADE THESIS ==`);
    lines.push(notes);
  }

  lines.push(``, `== YOUR TASKS ==`);
  lines.push(`1. Monitor this position and alert when significant changes occur.`);
  lines.push(`2. Analyze whether to hold or close based on current market conditions.`);
  lines.push(`3. Research ${t} for any news, earnings, or events that could impact the position.`);
  lines.push(`4. Provide actionable recommendations with specific reasoning.`);

  if (trade.strategy_type?.includes('box') || trade.strategy_type?.includes('put')) {
    lines.push(`5. For income-generating strategies: evaluate if current implied rates justify holding vs rolling.`);
    lines.push(`6. Watch for any change in interest rate environment that affects box spread pricing.`);
  } else if (trade.strategy_type?.startsWith('stock')) {
    lines.push(`5. Run technical analysis (support/resistance, trend, momentum indicators).`);
    lines.push(`6. Monitor fundamental catalysts: earnings, guidance, analyst ratings, insider activity.`);
    lines.push(`7. Assess position sizing vs your portfolio — is it over-concentrated?`);
  } else {
    lines.push(`5. Evaluate Greeks decay (especially theta) and its impact on the position value.`);
    lines.push(`6. Check IV changes — any volatility crush or expansion that changes the trade thesis.`);
  }

  return lines.join('\n');
}

const SCHEDULE_OPTIONS = [
  { value: '', label: 'No schedule (manual only)' },
  { value: 'daily_morning', label: 'Daily at market open (9:30 AM ET)' },
  { value: 'weekly_monday', label: 'Weekly on Monday morning' },
  { value: 'market_close', label: 'Daily at market close (4:00 PM ET)' },
];

export default function CreateAgentFromTradeModal({ open, onClose, trade, pnl, onCreateAgent }: Props) {
  const [name, setName] = useState('');
  const [instructions, setInstructions] = useState('');
  const [schedule, setSchedule] = useState('');
  const [showInstructions, setShowInstructions] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      const t = trade.ticker;
      const stype = trade.strategy_type?.replace(/_/g, ' ') || 'trade';
      setName(`${t} ${stype.charAt(0).toUpperCase() + stype.slice(1)} Monitor`);
      setInstructions(buildInstructions(trade, pnl));
      setSchedule('');
      setCreated(false);
      setErr(null);
      setShowInstructions(false);
    }
  }, [open, trade, pnl]);

  if (!open) return null;

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setErr(null);
    try {
      await onCreateAgent({
        name: name.trim(),
        description: `Trade monitor for ${trade.ticker} ${trade.strategy_type?.replace(/_/g, ' ')}`,
        instruction: instructions.trim(),
      });
      setCreated(true);
    } catch (e: any) {
      setErr(e?.message || 'Failed to create agent');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-lg shadow-2xl flex flex-col max-h-[85vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] flex-shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-secondary/15 flex items-center justify-center">
              <Bot className="w-4 h-4 text-secondary" />
            </div>
            <div>
              <p className="font-semibold text-sm">Create Trade Monitor Agent</p>
              <p className="text-xs text-base-content/40 mt-0.5">
                Pre-populated with context from{' '}
                <span className="font-mono font-bold text-base-content/70">{trade.ticker}</span>
              </p>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        {created ? (
          <div className="p-8 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-success/15 flex items-center justify-center mx-auto">
              <Bot className="w-6 h-6 text-success" />
            </div>
            <p className="font-semibold">Agent Created!</p>
            <p className="text-xs text-base-content/50">
              Your trade monitor agent is ready. Find it in the Agents tab to run or schedule it.
            </p>
            <button onClick={onClose} className="btn btn-sm btn-success">Done</button>
          </div>
        ) : (
          <>
            <div className="overflow-y-auto flex-1 p-5 space-y-4">

              {/* Agent name */}
              <div>
                <label className="text-[10px] uppercase text-base-content/40 mb-1 block">Agent Name</label>
                <input
                  type="text"
                  className="input input-bordered input-sm w-full"
                  value={name}
                  onChange={e => setName(e.target.value)}
                />
              </div>

              {/* Schedule */}
              <div>
                <label className="text-[10px] uppercase text-base-content/40 mb-1 block">Schedule</label>
                <select
                  className="select select-bordered select-sm w-full"
                  value={schedule}
                  onChange={e => setSchedule(e.target.value)}
                >
                  {SCHEDULE_OPTIONS.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              {/* Instructions — collapsible */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowInstructions(v => !v)}
                  className="flex items-center gap-1.5 text-[10px] uppercase text-base-content/40 hover:text-base-content/70 transition-colors mb-1"
                >
                  {showInstructions ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                  Agent Instructions (pre-filled from trade context)
                </button>
                {showInstructions && (
                  <textarea
                    className="textarea textarea-bordered w-full text-xs font-mono leading-relaxed resize-none"
                    rows={12}
                    value={instructions}
                    onChange={e => setInstructions(e.target.value)}
                  />
                )}
                {!showInstructions && (
                  <div className="flex items-center gap-1.5 text-[10px] text-base-content/40 bg-base-300/20 rounded-lg px-3 py-2">
                    <Sparkles className="w-3 h-3 text-secondary" />
                    Instructions auto-filled with trade details, P&L snapshot, and monitoring tasks.
                    <button
                      type="button"
                      onClick={() => setShowInstructions(true)}
                      className="ml-auto text-secondary hover:underline"
                    >
                      Preview
                    </button>
                  </div>
                )}
              </div>

              {err && (
                <p className="text-xs text-error flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />{err}
                </p>
              )}
            </div>

            {/* Footer */}
            <div className="px-5 py-4 border-t border-white/[0.06] flex items-center justify-end gap-2 flex-shrink-0">
              <button onClick={onClose} className="btn btn-ghost btn-sm">Cancel</button>
              <button
                onClick={handleCreate}
                disabled={!name.trim() || creating}
                className="btn btn-secondary btn-sm gap-2"
              >
                {creating
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Bot className="w-3.5 h-3.5" />}
                {creating ? 'Creating…' : 'Create Agent →'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
