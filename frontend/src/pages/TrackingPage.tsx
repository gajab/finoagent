/**
 * Tracking Page — companies grouped by theme, with agent badges,
 * mini-agent creation, archive/restore lifecycle, and research links.
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Bookmark, RefreshCw, Loader2, Trash2, Edit3, Check, X,
  ExternalLink, Bot, ChevronDown, ChevronRight, TrendingUp,
  TrendingDown, AlertTriangle, Cpu, Gem, Target, Sparkles,
  ArrowUpRight, ArrowDownRight, BarChart2, BookOpen,
  PlusCircle, ClipboardList, Plus, Archive, RotateCcw,
  Zap, CircleDot, Activity, PenLine,
} from 'lucide-react';
import {
  fetchTrackedCompanies, untrackCompany, updateTrackedNotes,
  refreshTrackedPrice, agentFromTracking, createAgent,
  archiveTrackedCompany, restoreTrackedCompany,
  fetchCompanyAgents, fetchMiniAgentTemplates,
} from '../api';
import type {
  TrackedCompany, TrackedGroup, AgentScaffold, Agent, MiniAgentTemplate,
} from '../types';
import AddCompanyModal from '../components/AddCompanyModal';
import LogTradeModal from '../components/LogTradeModal';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const fmtPrice = (v: number | null | undefined) =>
  v != null ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

const fmtPct = (v: number | null | undefined) => {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${v.toFixed(2)}%`;
};

const TIER_LABEL: Record<string, string> = {
  direct: 'Theme Core', enabler: 'Backbone', deep: 'Hidden Pick', deeper: 'Dig Deeper',
};

const TIER_CLS: Record<string, string> = {
  direct:  'border-amber-500/30 bg-amber-500/5 text-amber-400',
  enabler: 'border-cyan-500/30 bg-cyan-500/5 text-cyan-400',
  deep:    'border-violet-500/30 bg-violet-500/5 text-violet-400',
  deeper:  'border-fuchsia-500/30 bg-fuchsia-500/5 text-fuchsia-400',
};

const AGENT_STATUS_CLS: Record<string, string> = {
  active:  'text-success',
  paused:  'text-warning',
  stopped: 'text-base-content/30',
};

// ---------------------------------------------------------------------------
// Mini-agent picker modal
// ---------------------------------------------------------------------------

function MiniAgentPickerModal({
  company,
  templates,
  onClose,
  onPick,
}: {
  company: TrackedCompany;
  templates: MiniAgentTemplate[];
  onClose: () => void;
  onPick: (key: string | null) => void; // null = custom
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-xl shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-primary" />
            <div>
              <div className="font-semibold text-sm">Add Monitoring Agent</div>
              <div className="text-[10px] text-base-content/40">{company.ticker} — {company.name}</div>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-base-content/50">Choose a focused mini-agent or create a custom one:</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {templates.map(t => (
              <button
                key={t.key}
                onClick={() => onPick(t.key)}
                className="text-left p-3 rounded-xl border border-white/[0.08] bg-base-100/40 hover:border-primary/30 hover:bg-primary/5 transition-all group"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="font-semibold text-xs group-hover:text-primary transition-colors">{t.label}</div>
                  <span className="text-[9px] px-1.5 py-0.5 rounded-full border border-white/10 text-base-content/40 flex-shrink-0">
                    {t.schedule_type === 'manual' ? 'manual' : 'auto'}
                  </span>
                </div>
                <p className="text-[10px] text-base-content/50 mt-0.5 leading-relaxed">{t.description}</p>
              </button>
            ))}
          </div>

          <button
            onClick={() => onPick(null)}
            className="w-full text-left p-3 rounded-xl border border-dashed border-white/[0.08] hover:border-primary/20 hover:bg-primary/5 transition-all group flex items-center gap-2"
          >
            <Sparkles className="w-4 h-4 text-base-content/30 group-hover:text-primary transition-colors" />
            <div>
              <div className="font-semibold text-xs text-base-content/60 group-hover:text-primary transition-colors">Custom Agent</div>
              <div className="text-[10px] text-base-content/40">Full research context, blank instruction</div>
            </div>
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent creation modal (pre-filled from scaffold)
// ---------------------------------------------------------------------------

function AgentModal({
  scaffold,
  company,
  onClose,
  onCreated,
}: {
  scaffold: AgentScaffold;
  company: TrackedCompany;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState(scaffold.name);
  const [instruction, setInstruction] = useState(scaffold.instruction);
  const [scheduleType, setScheduleType] = useState(scaffold.schedule_type || 'manual');
  const [cron, setCron] = useState(scaffold.schedule_cron || '');
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setCreating(true);
    setErr(null);
    try {
      await createAgent({
        name,
        description: scaffold.description,
        instruction,
        schedule_type: scheduleType as any,
        schedule_cron: scheduleType === 'recurring' && cron ? cron : undefined,
        tracked_company_id: company.id,
      });
      onCreated();
    } catch (e: any) {
      setErr(e?.message || 'Failed to create agent');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl">
        <div className="flex items-center justify-between p-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-2">
            <Bot className="w-5 h-5 text-primary" />
            <span className="font-semibold">Configure Agent</span>
            <span className="text-xs text-base-content/40">· {company.ticker}</span>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto p-4 space-y-4 flex-1">
          <div>
            <label className="label py-1"><span className="label-text text-xs">Agent Name</span></label>
            <input
              className="input input-bordered input-sm w-full"
              value={name}
              onChange={e => setName(e.target.value)}
            />
          </div>

          <div>
            <label className="label py-1">
              <span className="label-text text-xs">Monitoring Instructions</span>
              <span className="label-text-alt text-[10px] text-base-content/40">Pre-populated from research — edit freely</span>
            </label>
            <textarea
              className="textarea textarea-bordered w-full text-xs leading-relaxed font-mono"
              rows={16}
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
            />
          </div>

          <div>
            <label className="label py-1"><span className="label-text text-xs">Schedule</span></label>
            <div className="flex gap-2 flex-wrap">
              {['manual', 'recurring'].map(s => (
                <button
                  key={s}
                  onClick={() => setScheduleType(s)}
                  className={`btn btn-sm ${scheduleType === s ? 'btn-primary' : 'btn-ghost'}`}
                >
                  {s === 'manual' ? 'Run manually' : 'Recurring (cron)'}
                </button>
              ))}
            </div>
            {scheduleType === 'recurring' && (
              <input
                className="input input-bordered input-sm w-full mt-2"
                placeholder="Cron expression e.g. 0 9 * * 1 (Mon 9am)"
                value={cron}
                onChange={e => setCron(e.target.value)}
              />
            )}
          </div>

          {err && <div className="alert alert-error text-sm"><AlertTriangle className="w-4 h-4" />{err}</div>}
        </div>

        <div className="p-4 border-t border-white/[0.06] flex justify-end gap-2">
          <button onClick={onClose} className="btn btn-ghost btn-sm">Cancel</button>
          <button onClick={submit} disabled={creating || !name.trim()} className="btn btn-primary btn-sm gap-2">
            {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Bot className="w-4 h-4" />}
            {creating ? 'Creating…' : 'Create Agent'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Notes editor (inline)
// ---------------------------------------------------------------------------

function NotesEditor({
  companyId, initialNotes, onSaved,
}: {
  companyId: number;
  initialNotes: string | null | undefined;
  onSaved: (notes: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(initialNotes || '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await updateTrackedNotes(companyId, draft.trim() || null);
      onSaved(updated.user_notes || null);
      setEditing(false);
    } catch (e: any) {
      alert(e?.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    if (!window.confirm('Delete your notes for this company?')) return;
    setSaving(true);
    try {
      const updated = await updateTrackedNotes(companyId, null);
      onSaved(null);
      setDraft('');
      setEditing(false);
    } catch (e: any) {
      alert(e?.message || 'Clear failed');
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div className="group flex items-start gap-2">
        <div className="flex-1 min-w-0">
          {initialNotes
            ? <p className="text-xs text-base-content/80 leading-relaxed whitespace-pre-wrap">{initialNotes}</p>
            : <p className="text-xs text-base-content/30 italic">No notes yet — click to add</p>}
        </div>
        <button
          onClick={() => { setDraft(initialNotes || ''); setEditing(true); }}
          className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-base-200"
        >
          <Edit3 className="w-3 h-3 text-base-content/50" />
        </button>
        {initialNotes && (
          <button onClick={clear} disabled={saving} className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-error/10">
            <Trash2 className="w-3 h-3 text-error/60" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <textarea
        className="textarea textarea-bordered w-full text-xs leading-relaxed"
        rows={4}
        autoFocus
        value={draft}
        onChange={e => setDraft(e.target.value)}
        placeholder="Your research notes…"
      />
      <div className="flex gap-2">
        <button onClick={save} disabled={saving} className="btn btn-xs btn-success gap-1">
          {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Save
        </button>
        <button onClick={() => setEditing(false)} className="btn btn-xs btn-ghost">Cancel</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lazy-loaded agents section (shown in expanded card)
// ---------------------------------------------------------------------------

function AgentsSection({
  company,
  onAddAgent,
}: {
  company: TrackedCompany;
  onAddAgent: () => void;
}) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const fetched = useRef(false);

  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    fetchCompanyAgents(company.id)
      .then(setAgents)
      .catch(() => setAgents([]))
      .finally(() => setLoading(false));
  }, [company.id]);

  return (
    <div className="border-t border-white/[0.04] pt-3 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold flex items-center gap-1.5">
          <Bot className="w-3 h-3 text-primary" /> Monitoring Agents
        </div>
        <button
          onClick={onAddAgent}
          className="flex items-center gap-1 px-2 py-1 rounded-lg border border-primary/20 bg-primary/5 text-primary text-[10px] font-semibold hover:bg-primary/10 transition-all"
        >
          <Plus className="w-3 h-3" /> Add Agent
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-base-content/40 py-1">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading agents…
        </div>
      )}

      {!loading && agents && agents.length === 0 && (
        <div className="text-xs text-base-content/30 italic py-1">
          No agents yet — click Add Agent to set one up.
        </div>
      )}

      {!loading && agents && agents.length > 0 && (
        <div className="space-y-1.5">
          {agents.map(agent => (
            <Link
              key={agent.id}
              to={`/agents?id=${agent.id}`}
              className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg bg-base-200/40 hover:bg-base-200/70 border border-white/[0.04] hover:border-primary/20 transition-all group"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                  agent.status === 'active' ? 'bg-success' :
                  agent.status === 'paused' ? 'bg-warning' :
                  'bg-base-content/20'
                }`} />
                <span className="text-xs font-medium text-base-content/80 group-hover:text-primary transition-colors truncate">
                  {agent.name}
                </span>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`text-[9px] capitalize ${AGENT_STATUS_CLS[agent.status] || 'text-base-content/40'}`}>
                  {agent.status}
                </span>
                <ExternalLink className="w-3 h-3 text-base-content/20 group-hover:text-primary transition-colors" />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single company row / card
// ---------------------------------------------------------------------------

function TrackedCard({
  company,
  isOnHold,
  onUntrack,
  onArchive,
  onRestore,
  onNotesUpdated,
  onAgentCreate,
  onLogTrade,
}: {
  company: TrackedCompany;
  isOnHold: boolean;
  onUntrack: (id: number) => void;
  onArchive: (id: number) => void;
  onRestore: (id: number) => void;
  onNotesUpdated: (id: number, notes: string | null) => void;
  onAgentCreate: (company: TrackedCompany) => void;
  onLogTrade: (company: TrackedCompany) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [localFin, setLocalFin] = useState(company.financial_data);
  const [archiving, setArchiving] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const fin = localFin;
  const llm = company.llm_data;
  const chg = fin.price_chg_1d;
  const up = chg != null && chg >= 0;
  const agentCount = company.agent_count ?? 0;
  const activeAgentCount = company.active_agent_count ?? 0;

  const doRefresh = async () => {
    setRefreshing(true);
    try {
      const updated = await refreshTrackedPrice(company.id);
      setLocalFin(updated.financial_data);
    } catch {
      // stale data still shown
    } finally {
      setRefreshing(false);
    }
  };

  const doArchive = async () => {
    setArchiving(true);
    try {
      await archiveTrackedCompany(company.id);
      onArchive(company.id);
    } catch (e: any) {
      alert(e?.message || 'Failed to archive');
    } finally {
      setArchiving(false);
    }
  };

  const doRestore = async () => {
    setRestoring(true);
    try {
      await restoreTrackedCompany(company.id);
      onRestore(company.id);
    } catch (e: any) {
      alert(e?.message || 'Failed to restore');
    } finally {
      setRestoring(false);
    }
  };

  const doDelete = async () => {
    setDeleting(true);
    try {
      await untrackCompany(company.id);
      onUntrack(company.id);
    } catch (e: any) {
      alert(e?.message || 'Failed to delete');
      setDeleting(false);
      setDeleteConfirm(false);
    }
  };

  return (
    <div className={`rounded-2xl border overflow-hidden transition-all ${
      isOnHold
        ? 'border-white/[0.04] bg-base-100/30 opacity-70'
        : 'border-white/[0.06] bg-base-100/60'
    }`}>
      {/* Header row */}
      <div
        className="grid grid-cols-[1fr_auto_auto] sm:grid-cols-[1fr_auto_auto_auto] md:grid-cols-[minmax(140px,1.5fr)_2fr_90px_60px_100px_auto] lg:grid-cols-[180px_2fr_90px_60px_100px_auto] items-center gap-3 md:gap-4 px-4 py-3 cursor-pointer hover:bg-base-200/30 transition-colors"
        onClick={() => setExpanded(e => !e)}
      >
        {/* Ticker + name + agent badge */}
        <div className="min-w-0 pr-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-bold text-sm">{company.ticker}</span>
            {agentCount > 0 && (
              <span className={`flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded-full border ${
                activeAgentCount > 0
                  ? 'border-success/30 bg-success/5 text-success'
                  : 'border-white/10 bg-white/5 text-base-content/40'
              }`}>
                <span className={`w-1 h-1 rounded-full ${activeAgentCount > 0 ? 'bg-success' : 'bg-base-content/30'}`} />
                {agentCount} {agentCount === 1 ? 'agent' : 'agents'}
              </span>
            )}
          </div>
          <div className="text-xs text-base-content/60 truncate mt-0.5">{company.name}</div>
        </div>

        {/* Thesis */}
        <div className="hidden md:flex min-w-0 items-center border-l border-white/[0.04] pl-4">
          {llm.thesis ? (
            <div className="text-xs text-base-content/70 italic line-clamp-2 pr-2">
              "{llm.thesis}"
            </div>
          ) : (
            <div className="text-xs text-base-content/30 italic">No thesis available</div>
          )}
        </div>

        {/* Price block */}
        <div className="text-right">
          <div className="text-base font-bold tabular-nums">{fmtPrice(fin.price)}</div>
          {chg != null && (
            <div className={`flex items-center justify-end gap-0.5 text-xs font-semibold ${up ? 'text-success' : 'text-error'}`}>
              {up ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
              {fmtPct(chg)}
            </div>
          )}
        </div>

        {/* P/E */}
        <div className="text-right hidden sm:block">
          <div className="text-[10px] text-base-content/40">P/E</div>
          <div className="text-sm tabular-nums font-medium">{fin.pe_ratio != null ? fin.pe_ratio : '—'}</div>
        </div>

        {/* 52W */}
        <div className="text-right hidden md:block">
          <div className="text-[10px] text-base-content/40">52W H/L</div>
          <div className="text-xs tabular-nums text-base-content/70">
            {fmtPrice(fin.week52_high)}<br/><span className="text-[10px] text-base-content/40">/ {fmtPrice(fin.week52_low)}</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-0.5" onClick={e => e.stopPropagation()}>
          <button
            onClick={doRefresh}
            disabled={refreshing}
            className="btn btn-ghost btn-xs px-1.5"
            title="Refresh price"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <Link to={`/dashboard?ticker=${company.ticker}`} className="btn btn-ghost btn-xs px-1.5" title="Full analysis">
            <ExternalLink className="w-3.5 h-3.5" />
          </Link>
          {!isOnHold && (
            <>
              <button
                onClick={() => onLogTrade(company)}
                className="btn btn-ghost btn-xs px-1.5 text-success/70 hover:text-success"
                title="Log a trade"
              >
                <ClipboardList className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => onAgentCreate(company)}
                className="btn btn-ghost btn-xs px-1.5 text-primary"
                title="Add monitoring agent"
              >
                <Bot className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={doArchive}
                disabled={archiving}
                className="btn btn-ghost btn-xs px-1.5 text-base-content/40 hover:text-warning"
                title="Move to On Hold"
              >
                {archiving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Archive className="w-3.5 h-3.5" />}
              </button>
            </>
          )}
          {isOnHold && (
            <>
              <button
                onClick={doRestore}
                disabled={restoring}
                className="btn btn-ghost btn-xs px-1.5 text-success/70 hover:text-success"
                title="Restore to active"
              >
                {restoring ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
              </button>
              {!deleteConfirm ? (
                <button
                  onClick={() => setDeleteConfirm(true)}
                  className="btn btn-ghost btn-xs px-1.5 text-error/50 hover:text-error"
                  title="Remove from tracking"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              ) : (
                <div className="flex items-center gap-1 bg-error/10 border border-error/20 rounded-lg px-1.5 py-0.5">
                  <span className="text-[9px] text-error">Delete?</span>
                  <button
                    onClick={doDelete}
                    disabled={deleting}
                    className="btn btn-error btn-xs h-5 min-h-0 text-[9px] px-1.5"
                  >
                    {deleting ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : 'Yes'}
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(false)}
                    className="btn btn-ghost btn-xs h-5 min-h-0 text-[9px] px-1"
                    disabled={deleting}
                  >
                    No
                  </button>
                </div>
              )}
            </>
          )}
          <ChevronRight className={`w-4 h-4 ml-1 text-base-content/30 transition-transform ${expanded ? 'rotate-90' : ''}`} />
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-white/[0.04] px-4 py-3 space-y-4">
          {/* Two-column: LLM data + User notes */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            {/* LLM-discovered information */}
            <div className="space-y-3">
              <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold flex items-center gap-1.5">
                <Sparkles className="w-3 h-3 text-primary" />
                AI Research Findings
              </div>

              {llm.thesis && (
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Investment Thesis</div>
                  <p className="text-xs text-base-content/80 leading-relaxed">{llm.thesis}</p>
                </div>
              )}

              {llm.supply_chain_role && (
                <div className="flex items-start gap-1.5">
                  <Cpu className="w-3 h-3 text-cyan-400/70 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Supply Chain Role</div>
                    <p className="text-xs text-base-content/75">{llm.supply_chain_role}</p>
                  </div>
                </div>
              )}

              {llm.hidden_link && (
                <div className="flex items-start gap-1.5">
                  <Gem className="w-3 h-3 text-violet-400/70 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Hidden Connection</div>
                    <p className="text-xs text-base-content/75">{llm.hidden_link}</p>
                  </div>
                </div>
              )}

              {llm.catalysts && llm.catalysts.length > 0 && (
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-1">Key Catalysts</div>
                  <div className="flex flex-wrap gap-1">
                    {llm.catalysts.map((c, i) => (
                      <span key={i} className="text-[9px] px-1.5 py-0.5 rounded-full border border-primary/20 bg-primary/5 text-base-content/70">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {llm.revenue_exposure && (
                <div className="flex items-start gap-1.5">
                  <BarChart2 className="w-3 h-3 text-base-content/40 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Theme Exposure</div>
                    <p className="text-xs text-base-content/70">{llm.revenue_exposure}</p>
                  </div>
                </div>
              )}

              {llm.earnings_signal && (
                <div className="flex items-start gap-1.5">
                  <BookOpen className="w-3 h-3 text-base-content/40 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Earnings Signal</div>
                    <p className="text-xs text-base-content/70 italic">{llm.earnings_signal}</p>
                  </div>
                </div>
              )}

              {llm.risk && (
                <div className="flex items-start gap-1.5">
                  <AlertTriangle className="w-3 h-3 text-warning/60 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Key Risk</div>
                    <p className="text-xs text-warning/70">{llm.risk}</p>
                  </div>
                </div>
              )}

              {llm.why_nobody_covers_this && (
                <div className="flex items-start gap-1.5">
                  <Gem className="w-3 h-3 text-fuchsia-400/60 mt-0.5 flex-shrink-0" />
                  <div>
                    <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Why Analysts Miss This</div>
                    <p className="text-xs text-base-content/65">{llm.why_nobody_covers_this}</p>
                  </div>
                </div>
              )}

              {/* Financial snapshot */}
              <div className="pt-2 border-t border-white/[0.04]">
                <div className="text-[9px] uppercase text-base-content/40 mb-1.5 flex items-center gap-1">
                  <TrendingUp className="w-3 h-3" /> Financial Snapshot
                  {company.last_price_refresh && (
                    <span className="ml-auto font-normal text-base-content/30">
                      refreshed {new Date(company.last_price_refresh).toLocaleTimeString()}
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  {[
                    { label: 'Mkt Cap', val: fin.market_cap || '—' },
                    { label: 'P/E', val: fin.pe_ratio != null ? String(fin.pe_ratio) : '—' },
                    { label: 'Fwd P/E', val: fin.forward_pe != null ? String(fin.forward_pe) : '—' },
                  ].map(({ label, val }) => (
                    <div key={label} className="bg-base-200/50 rounded-lg p-1.5">
                      <div className="text-[9px] text-base-content/40">{label}</div>
                      <div className="text-xs font-semibold">{val}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* User notes */}
            <div>
              <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold flex items-center gap-1.5 mb-2">
                <Edit3 className="w-3 h-3 text-success" />
                Your Research Notes
                <span className="font-normal text-base-content/30">(hover to edit)</span>
              </div>
              <div className="bg-base-200/30 rounded-xl p-3 min-h-[80px]">
                <NotesEditor
                  companyId={company.id}
                  initialNotes={company.user_notes}
                  onSaved={notes => onNotesUpdated(company.id, notes)}
                />
              </div>
            </div>
          </div>

          {/* Research quick-links */}
          <div className="border-t border-white/[0.04] pt-3">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold flex items-center gap-1.5">
                <ExternalLink className="w-3 h-3" />
                Quick Research
              </div>
              <Link
                to={`/dashboard?ticker=${company.ticker}`}
                className="flex items-center gap-1 text-[10px] text-primary/70 hover:text-primary transition-colors font-medium"
              >
                <ExternalLink className="w-3 h-3" />
                Full Research →
              </Link>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {([
                { label: 'Analyst Ratings',    tab: 'overview' },
                { label: 'DCF Valuation',       tab: 'fundamental' },
                { label: 'Technical Analysis',  tab: 'technical' },
                { label: 'Financial Health',    tab: 'fundamental' },
              ] as const).map(({ label, tab }) => (
                <Link
                  key={label}
                  to={`/dashboard?ticker=${company.ticker}&tab=${tab}`}
                  className="badge badge-ghost badge-sm text-[9px] hover:badge-primary transition-colors"
                >
                  {label}
                </Link>
              ))}
              <Link
                to={`/dashboard?ticker=${company.ticker}&tab=exit`}
                className="badge badge-ghost badge-sm text-[9px] hover:badge-error transition-colors"
              >
                Exit Analysis
              </Link>
            </div>
          </div>

          {/* Agents section (lazy loaded) */}
          <AgentsSection company={company} onAddAgent={() => onAgentCreate(company)} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Theme group
// ---------------------------------------------------------------------------

function ThemeGroup({
  group, isOnHold, onUntrack, onArchive, onRestore, onNotesUpdated, onAgentCreate, onLogTrade, onAddCompany,
}: {
  group: TrackedGroup;
  isOnHold: boolean;
  onUntrack: (id: number) => void;
  onArchive: (id: number) => void;
  onRestore: (id: number) => void;
  onNotesUpdated: (id: number, notes: string | null) => void;
  onAgentCreate: (company: TrackedCompany) => void;
  onLogTrade: (company: TrackedCompany) => void;
  onAddCompany: (group: TrackedGroup) => void;
}) {
  const [collapsed, setCollapsed] = useState(true);

  return (
    <div className="space-y-2">
      {/* Group header */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setCollapsed(c => !c)}
          className="flex-1 flex items-center gap-3 text-left py-2 px-1 rounded-lg hover:bg-base-200/30 transition-colors"
        >
          <ChevronDown className={`w-4 h-4 text-base-content/40 transition-transform ${collapsed ? '-rotate-90' : ''}`} />
          <div className="flex-1">
            <span className="font-bold text-base">{group.theme_slug}</span>
            <span className="ml-2 text-xs text-base-content/40">
              {group.companies.length} {group.companies.length === 1 ? 'company' : 'companies'}
            </span>
          </div>
          <span className="text-[10px] text-base-content/30 truncate max-w-xs hidden md:block">
            {group.theme_raw !== group.theme_slug && group.theme_raw.length > group.theme_slug.length
              ? group.theme_raw
              : ''}
          </span>
        </button>
        {!isOnHold && (
          <button
            onClick={() => onAddCompany(group)}
            className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-white/[0.08] text-[10px] font-medium text-base-content/50 hover:text-base-content hover:border-primary/30 hover:bg-primary/5 hover:text-primary transition-all flex-shrink-0"
            title="Add a company to this theme"
          >
            <Plus className="w-3 h-3" /> Add
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="space-y-2 pl-4 border-l-2 border-white/[0.04]">
          {group.companies.map(c => (
            <TrackedCard
              key={c.id}
              company={c}
              isOnHold={isOnHold}
              onUntrack={onUntrack}
              onArchive={onArchive}
              onRestore={onRestore}
              onNotesUpdated={onNotesUpdated}
              onAgentCreate={onAgentCreate}
              onLogTrade={onLogTrade}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function TrackingPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<'active' | 'on_hold'>('active');
  const [groups, setGroups] = useState<TrackedGroup[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mini-agent picker state
  const [pickerCompany, setPickerCompany] = useState<TrackedCompany | null>(null);
  const [templates, setTemplates] = useState<MiniAgentTemplate[]>([]);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);

  // Agent scaffold modal state
  const [agentModalCompany, setAgentModalCompany] = useState<TrackedCompany | null>(null);
  const [agentScaffold, setAgentScaffold] = useState<AgentScaffold | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);

  // Add Company modal (group=null for manual standalone entry)
  const [addModalGroup, setAddModalGroup] = useState<TrackedGroup | null | undefined>(undefined);
  const addModalOpen = addModalGroup !== undefined;

  // Log Trade modal
  const [tradeModalCompany, setTradeModalCompany] = useState<TrackedCompany | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTrackedCompanies(activeTab);
      setGroups(data.groups);
      setTotal(data.total);
    } catch (e: any) {
      setError(e?.message || 'Failed to load tracked companies');
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => { load(); }, [load]);

  // Load mini-agent templates once
  useEffect(() => {
    if (templatesLoaded) return;
    fetchMiniAgentTemplates()
      .then(t => { setTemplates(t); setTemplatesLoaded(true); })
      .catch(() => setTemplatesLoaded(true));
  }, [templatesLoaded]);

  const removeFromGroups = (id: number) => {
    setGroups(prev =>
      prev.map(g => ({ ...g, companies: g.companies.filter(c => c.id !== id) }))
          .filter(g => g.companies.length > 0)
    );
    setTotal(t => t - 1);
  };

  const handleUntrack = (id: number) => removeFromGroups(id);
  const handleArchive = (id: number) => removeFromGroups(id); // removed from active list
  const handleRestore = (id: number) => removeFromGroups(id); // removed from on_hold list

  const handleNotesUpdated = (id: number, notes: string | null) => {
    setGroups(prev =>
      prev.map(g => ({
        ...g,
        companies: g.companies.map(c => c.id === id ? { ...c, user_notes: notes } : c),
      }))
    );
  };

  // Open mini-agent picker
  const openAgentPicker = (company: TrackedCompany) => {
    setPickerCompany(company);
  };

  // User picks a template (or null for custom)
  const handleTemplatePick = async (templateKey: string | null) => {
    if (!pickerCompany) return;
    const company = pickerCompany;
    setPickerCompany(null);
    setAgentModalCompany(company);
    setAgentLoading(true);
    try {
      const result = await agentFromTracking(
        company.id,
        undefined,
        undefined,
        undefined,
        templateKey ?? undefined,
      );
      setAgentScaffold(result.agent_scaffold);
    } catch (e: any) {
      alert(e?.message || 'Failed to build agent scaffold');
      setAgentModalCompany(null);
    } finally {
      setAgentLoading(false);
    }
  };

  const closeAgentModal = () => {
    setAgentModalCompany(null);
    setAgentScaffold(null);
  };

  const handleAgentCreated = () => {
    closeAgentModal();
    navigate('/agents');
  };

  // Add Company modal handler
  const handleCompanyAdded = (company: TrackedCompany) => {
    setGroups(prev => {
      const slug = company.theme_slug;
      const existing = prev.find(g => g.theme_slug === slug);
      if (existing) {
        return prev.map(g =>
          g.theme_slug === slug
            ? { ...g, companies: [...g.companies, company] }
            : g
        );
      }
      return [...prev, {
        theme_slug: slug,
        theme_raw: company.theme_raw,
        theme_summary: company.theme_summary ?? null,
        companies: [company],
      }];
    });
    setTotal(t => t + 1);
    setAddModalGroup(undefined);
  };

  const isOnHold = activeTab === 'on_hold';

  return (
    <div className="container-app py-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight flex items-center gap-2">
            <Bookmark className="w-7 h-7 text-primary" />
            Tracked Companies
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setAddModalGroup(null)}
            className="btn btn-sm btn-ghost gap-1.5"
            title="Add a company manually"
          >
            <PenLine className="w-4 h-4" />
            Add Manually
          </button>
          <Link to="/ai-research?module=pickshv" className="btn btn-sm btn-outline gap-1.5">
            <Sparkles className="w-4 h-4" />
            Add via AI Research
          </Link>
          <button onClick={load} disabled={loading} className="btn btn-sm btn-ghost gap-1.5">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-white/[0.06]">
        <button
          onClick={() => setActiveTab('active')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === 'active'
              ? 'border-primary text-primary'
              : 'border-transparent text-base-content/50 hover:text-base-content'
          }`}
        >
          Active
          {activeTab === 'active' && total > 0 && (
            <span className="ml-2 text-xs bg-primary/10 text-primary px-1.5 py-0.5 rounded-full">{total}</span>
          )}
        </button>
        <button
          onClick={() => setActiveTab('on_hold')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors flex items-center gap-1.5 ${
            activeTab === 'on_hold'
              ? 'border-warning text-warning'
              : 'border-transparent text-base-content/50 hover:text-base-content'
          }`}
        >
          <Archive className="w-3.5 h-3.5" />
          On Hold
          {activeTab === 'on_hold' && total > 0 && (
            <span className="ml-1 text-xs bg-warning/10 text-warning px-1.5 py-0.5 rounded-full">{total}</span>
          )}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="alert alert-error rounded-2xl">
          <AlertTriangle className="w-4 h-4" />
          <span>{error}</span>
          <button onClick={load} className="btn btn-xs btn-ghost">Retry</button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="space-y-3">
          {[1, 2].map(i => (
            <div key={i} className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-4 animate-pulse h-24" />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && total === 0 && !error && (
        <div className="rounded-2xl border border-dashed border-white/10 p-12 text-center space-y-3">
          {isOnHold ? (
            <>
              <Archive className="w-10 h-10 text-base-content/20 mx-auto" />
              <p className="text-base-content/50 text-sm">No companies on hold.</p>
              <p className="text-base-content/30 text-xs">
                Archive a company from the Active tab to move it here. It can be restored anytime.
              </p>
            </>
          ) : (
            <>
              <Bookmark className="w-10 h-10 text-base-content/20 mx-auto" />
              <p className="text-base-content/50 text-sm">No companies tracked yet.</p>
              <p className="text-base-content/30 text-xs max-w-sm mx-auto leading-relaxed">
                Use AI Research to discover theme-based investment ideas, then track the companies you find.
                Or add companies manually to start building your watchlist.
              </p>
              <div className="flex items-center gap-2 justify-center flex-wrap mt-2">
                <Link to="/ai-research?module=pickshv" className="btn btn-sm btn-primary gap-1.5">
                  <Sparkles className="w-4 h-4" /> AI Research
                </Link>
                <button
                  onClick={() => setAddModalGroup(null)}
                  className="btn btn-sm btn-outline gap-1.5"
                >
                  <PenLine className="w-4 h-4" /> Add Manually
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Groups */}
      {!loading && groups.length > 0 && (
        <div className="space-y-6">
          {groups.map(group => (
            <ThemeGroup
              key={group.theme_slug}
              group={group}
              isOnHold={isOnHold}
              onUntrack={handleUntrack}
              onArchive={handleArchive}
              onRestore={handleRestore}
              onNotesUpdated={handleNotesUpdated}
              onAgentCreate={openAgentPicker}
              onLogTrade={setTradeModalCompany}
              onAddCompany={setAddModalGroup}
            />
          ))}
        </div>
      )}

      {/* Mini-agent picker modal */}
      {pickerCompany && (
        <MiniAgentPickerModal
          company={pickerCompany}
          templates={templates}
          onClose={() => setPickerCompany(null)}
          onPick={handleTemplatePick}
        />
      )}

      {/* Add Company modal (group=null for manual entry, group=TrackedGroup for theme-specific) */}
      {addModalOpen && (
        <AddCompanyModal
          open={addModalOpen}
          onClose={() => setAddModalGroup(undefined)}
          group={addModalGroup ?? undefined}
          onAdded={handleCompanyAdded}
        />
      )}

      {/* Log Trade modal */}
      {tradeModalCompany && (
        <LogTradeModal
          open={!!tradeModalCompany}
          onClose={() => setTradeModalCompany(null)}
          company={tradeModalCompany}
          onLogged={() => {
            setTradeModalCompany(null);
            navigate('/my-trades');
          }}
        />
      )}

      {/* Agent scaffold loading overlay */}
      {agentModalCompany && agentLoading && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="bg-base-200 rounded-2xl p-8 flex items-center gap-3 text-sm">
            <Loader2 className="w-5 h-5 animate-spin text-primary" />
            Building agent from research context…
          </div>
        </div>
      )}

      {/* Agent creation modal */}
      {agentModalCompany && !agentLoading && agentScaffold && (
        <AgentModal
          scaffold={agentScaffold}
          company={agentModalCompany}
          onClose={closeAgentModal}
          onCreated={handleAgentCreated}
        />
      )}
    </div>
  );
}
