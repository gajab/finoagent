import React, { useState, useEffect, useCallback } from 'react';
import {
  ArrowLeft,
  Play,
  Pause,
  Square,
  Trash2,
  Loader2,
  Clock,
  CheckCircle,
  XCircle,
  Globe,
  Lock,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Pencil,
  Save,
  X,
} from 'lucide-react';
import type { Agent, AgentRun, AgentUpdateInput, ScheduleType } from '../types';
import {
  fetchAgent,
  fetchAgentRuns,
  triggerAgentRun,
  updateAgentStatus,
  updateAgent,
  deleteAgent,
} from '../api';
import AgentRunOutput from './AgentRunOutput';

interface AgentDetailProps {
  agentId: number;
  onBack: () => void;
  onDeleted: () => void;
}

function RunStatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'running':
      return <Loader2 className="w-4 h-4 animate-spin text-info" />;
    case 'completed':
      return <CheckCircle className="w-4 h-4 text-success" />;
    case 'failed':
      return <XCircle className="w-4 h-4 text-error" />;
    default:
      return null;
  }
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return 'N/A';
  const d = new Date(dateStr);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function formatDuration(start: string, end: string | null): string {
  if (!end) return 'In progress...';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

export default function AgentDetail({ agentId, onBack, onDeleted }: AgentDetailProps) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [expandedRun, setExpandedRun] = useState<number | null>(null);
  const [pollingActive, setPollingActive] = useState(false);

  // Edit mode state
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editInstruction, setEditInstruction] = useState('');
  const [editIsShared, setEditIsShared] = useState(false);
  const [editScheduleType, setEditScheduleType] = useState<ScheduleType>('manual');
  const [editScheduleCron, setEditScheduleCron] = useState('');
  const [editScheduledAt, setEditScheduledAt] = useState('');
  const [editSendEmailOnRun, setEditSendEmailOnRun] = useState(false);
  const [editEmailReportTo, setEditEmailReportTo] = useState('');
  const [saving, setSaving] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [agentData, runsData] = await Promise.all([
        fetchAgent(agentId),
        fetchAgentRuns(agentId),
      ]);
      setAgent(agentData);
      setRuns(runsData);

      // Auto-expand latest run if it just completed or is running
      if (runsData.length > 0) {
        const latest = runsData[0];
        if (latest.status === 'running' || (latest.status === 'completed' && expandedRun === null)) {
          setExpandedRun(latest.id);
        }
      }

      // Check if any run is still active
      const hasRunning = runsData.some((r) => r.status === 'running');
      setPollingActive(hasRunning);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agent');
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Poll while a run is active (every 8s to minimise overhead)
  useEffect(() => {
    if (!pollingActive) return;
    const interval = setInterval(loadData, 8000);
    return () => clearInterval(interval);
  }, [pollingActive, loadData]);

  const enterEditMode = () => {
    if (!agent) return;
    setEditName(agent.name);
    setEditDescription(agent.description || '');
    setEditInstruction(agent.instruction);
    setEditIsShared(agent.is_shared);
    setEditScheduleType(agent.schedule_type);
    setEditScheduleCron(agent.schedule_cron || '');
    setEditScheduledAt(agent.scheduled_at ? agent.scheduled_at.slice(0, 16) : '');
    setEditSendEmailOnRun(agent.send_email_on_run || false);
    setEditEmailReportTo(agent.email_report_to || '');
    setEditing(true);
    setError(null);
  };

  const cancelEdit = () => {
    setEditing(false);
    setError(null);
  };

  const handleSaveEdit = async () => {
    if (!editName.trim() || !editInstruction.trim()) {
      setError('Name and instruction are required.');
      return;
    }

    const data: AgentUpdateInput = {};

    if (editName.trim() !== agent?.name) data.name = editName.trim();
    if (editDescription.trim() !== (agent?.description || '')) data.description = editDescription.trim();
    if (editInstruction.trim() !== agent?.instruction) data.instruction = editInstruction.trim();
    if (editIsShared !== agent?.is_shared) data.is_shared = editIsShared;
    if (editScheduleType !== agent?.schedule_type) data.schedule_type = editScheduleType;
    if (editScheduleType === 'recurring' && editScheduleCron.trim() !== (agent?.schedule_cron || '')) {
      data.schedule_cron = editScheduleCron.trim();
    }
    if (editScheduleType === 'one_time' && editScheduledAt) {
      data.scheduled_at = new Date(editScheduledAt).toISOString();
    }

    // Check Email settings
    if (editSendEmailOnRun && !editEmailReportTo.trim()) {
      setError('Destination Email is required when "Send Email on Run" is checked.');
      return;
    }
    if (editSendEmailOnRun !== agent?.send_email_on_run) data.send_email_on_run = editSendEmailOnRun;
    if (editSendEmailOnRun && editEmailReportTo.trim() !== (agent?.email_report_to || '')) {
      data.email_report_to = editEmailReportTo.trim();
    }
    if (!editSendEmailOnRun && agent?.email_report_to) {
      data.email_report_to = '';
    }

    // Nothing changed
    if (Object.keys(data).length === 0) {
      setEditing(false);
      return;
    }

    try {
      setSaving(true);
      setError(null);
      await updateAgent(agentId, data);
      setEditing(false);
      setSuccess('Agent updated!');
      await loadData();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update agent');
    } finally {
      setSaving(false);
    }
  };

  const handleRun = async () => {
    try {
      setActionLoading(true);
      setError(null);
      const run = await triggerAgentRun(agentId);
      setExpandedRun(run.id);
      setPollingActive(true);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to trigger run');
    } finally {
      setActionLoading(false);
    }
  };

  const handleStatusChange = async (status: 'active' | 'paused' | 'stopped') => {
    try {
      setActionLoading(true);
      setError(null);
      await updateAgentStatus(agentId, status);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update status');
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete agent "${agent?.name}"? This will remove all run history.`)) return;
    try {
      setActionLoading(true);
      await deleteAgent(agentId);
      onDeleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete agent');
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <Loader2 className="w-10 h-10 animate-spin text-primary" />
        <p className="text-base-content/60 mt-4">Loading agent...</p>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="text-center py-20">
        <p className="text-error">Agent not found</p>
        <button className="btn btn-ghost btn-sm mt-4" onClick={onBack}>
          Go back
        </button>
      </div>
    );
  }

  const isRunning = runs.some((r) => r.status === 'running');

  return (
    <div className="space-y-6">
      {/* Back button + header */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
        <div>
          <button className="btn btn-ghost btn-sm gap-1 mb-2" onClick={onBack}>
            <ArrowLeft className="w-4 h-4" />
            Back to Agents
          </button>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            {agent.name}
            {agent.is_shared ? (
              <span title="Shared"><Globe className="w-5 h-5 text-info" /></span>
            ) : (
              <span title="Private"><Lock className="w-5 h-5 text-base-content/40" /></span>
            )}
          </h2>
          {agent.description && (
            <p className="text-base-content/60 mt-1">{agent.description}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {!editing && (
            <button
              className="btn btn-ghost btn-sm gap-1"
              onClick={enterEditMode}
              disabled={actionLoading}
            >
              <Pencil className="w-4 h-4" />
              Edit
            </button>
          )}
          {agent.status === 'active' && (
            <>
              <button
                className="btn btn-primary btn-sm gap-1"
                onClick={handleRun}
                disabled={actionLoading || isRunning || editing}
              >
                {actionLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Play className="w-4 h-4" />
                )}
                {isRunning ? 'Running...' : 'Run Now'}
              </button>
              <button
                className="btn btn-warning btn-sm gap-1"
                onClick={() => handleStatusChange('paused')}
                disabled={actionLoading || editing}
              >
                <Pause className="w-4 h-4" />
                Pause
              </button>
            </>
          )}
          {agent.status === 'paused' && (
            <button
              className="btn btn-success btn-sm gap-1"
              onClick={() => handleStatusChange('active')}
              disabled={actionLoading || editing}
            >
              <Play className="w-4 h-4" />
              Resume
            </button>
          )}
          {agent.status === 'stopped' && (
            <button
              className="btn btn-success btn-sm gap-1"
              onClick={() => handleStatusChange('active')}
              disabled={actionLoading || editing}
            >
              <Play className="w-4 h-4" />
              Reactivate
            </button>
          )}
          {agent.status !== 'stopped' && (
            <button
              className="btn btn-ghost btn-sm text-error gap-1"
              onClick={() => handleStatusChange('stopped')}
              disabled={actionLoading || editing}
            >
              <Square className="w-4 h-4" />
              Stop
            </button>
          )}
          <button
            className="btn btn-ghost btn-sm text-error gap-1"
            onClick={handleDelete}
            disabled={actionLoading || editing}
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </button>
        </div>
      </div>

      {/* Alerts */}
      {error && (
        <div className="alert alert-error">
          <span>{error}</span>
          <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}
      {success && (
        <div className="alert alert-success">
          <CheckCircle className="w-5 h-5" />
          <span>{success}</span>
        </div>
      )}

      {/* Agent info card — VIEW or EDIT mode */}
      <div className="glass-card">
        <div className="p-4 gap-3 flex flex-col">
          {editing ? (
            /* ====== EDIT MODE ====== */
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="font-bold text-base flex items-center gap-2">
                  <Pencil className="w-4 h-4 text-primary" />
                  Edit Agent
                </h3>
                <div className="flex gap-2">
                  <button className="btn btn-ghost btn-sm gap-1" onClick={cancelEdit} disabled={saving}>
                    <X className="w-4 h-4" />
                    Cancel
                  </button>
                  <button className="btn btn-primary btn-sm gap-1" onClick={handleSaveEdit} disabled={saving}>
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    Save
                  </button>
                </div>
              </div>

              {/* Name */}
              <div className="form-control">
                <label className="label">
                  <span className="label-text font-medium">Name</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  maxLength={200}
                />
              </div>

              {/* Description */}
              <div className="form-control">
                <label className="label">
                  <span className="label-text font-medium">Description</span>
                  <span className="label-text-alt">Optional</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered"
                  placeholder="A brief description of what this agent does..."
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                />
              </div>

              {/* Instruction */}
              <div className="form-control">
                <label className="label">
                  <span className="label-text font-medium">Instruction</span>
                </label>
                <textarea
                  className="textarea textarea-bordered h-32 font-mono text-sm"
                  value={editInstruction}
                  onChange={(e) => setEditInstruction(e.target.value)}
                />
              </div>

              {/* Schedule Type */}
              <div className="form-control">
                <label className="label">
                  <span className="label-text font-medium">Schedule</span>
                </label>
                <select
                  className="select select-bordered"
                  value={editScheduleType}
                  onChange={(e) => setEditScheduleType(e.target.value as ScheduleType)}
                >
                  <option value="manual">Manual (run on demand)</option>
                  <option value="one_time">One-time (run at specific time)</option>
                  <option value="recurring">Recurring (cron schedule)</option>
                </select>
              </div>

              {editScheduleType === 'recurring' && (
                <div className="form-control">
                  <label className="label">
                    <span className="label-text font-medium">Cron Expression</span>
                  </label>
                  <input
                    type="text"
                    className="input input-bordered font-mono"
                    placeholder="0 9 * * 1-5  (weekdays at 9 AM)"
                    value={editScheduleCron}
                    onChange={(e) => setEditScheduleCron(e.target.value)}
                  />
                  <label className="label">
                    <span className="label-text-alt text-base-content/50">
                      Format: minute hour day-of-month month day-of-week
                    </span>
                  </label>
                </div>
              )}

              {editScheduleType === 'one_time' && (
                <div className="form-control">
                  <label className="label">
                    <span className="label-text font-medium">Run At</span>
                  </label>
                  <input
                    type="datetime-local"
                    className="input input-bordered"
                    value={editScheduledAt}
                    onChange={(e) => setEditScheduledAt(e.target.value)}
                  />
                </div>
              )}

              {/* Email Notification */}
              <div className="form-control">
                <label className="label cursor-pointer justify-start gap-3">
                  <input
                    type="checkbox"
                    className="toggle toggle-secondary"
                    checked={editSendEmailOnRun}
                    onChange={(e) => setEditSendEmailOnRun(e.target.checked)}
                  />
                  <div>
                    <span className="label-text font-medium">Send Email on Run</span>
                    <p className="text-xs text-base-content/50 mt-0.5">
                      Send the final agent report directly to an email address
                    </p>
                  </div>
                </label>
              </div>

              {editSendEmailOnRun && (
                <div className="form-control">
                  <label className="label">
                    <span className="label-text font-medium">Destination Email</span>
                    <span className="label-text-alt text-error">Required</span>
                  </label>
                  <input
                    type="email"
                    className="input input-bordered"
                    placeholder="Required: enter a valid email address"
                    value={editEmailReportTo}
                    onChange={(e) => setEditEmailReportTo(e.target.value)}
                    required={editSendEmailOnRun}
                  />
                </div>
              )}

              {/* Shared toggle */}
              <div className="form-control">
                <label className="label cursor-pointer justify-start gap-3">
                  <input
                    type="checkbox"
                    className="toggle toggle-primary"
                    checked={editIsShared}
                    onChange={(e) => setEditIsShared(e.target.checked)}
                  />
                  <div>
                    <span className="label-text font-medium">Share with community</span>
                    <p className="text-xs text-base-content/50 mt-0.5">
                      Other users can discover and clone this agent
                    </p>
                  </div>
                </label>
              </div>
            </div>
          ) : (
            /* ====== VIEW MODE ====== */
            <>
              <div className="flex flex-wrap gap-4 text-sm">
                <div>
                  <span className="text-base-content/50">Status:</span>{' '}
                  <span
                    className={`font-medium ${agent.status === 'active'
                        ? 'text-success'
                        : agent.status === 'paused'
                          ? 'text-warning'
                          : 'text-error'
                      }`}
                  >
                    {agent.status.charAt(0).toUpperCase() + agent.status.slice(1)}
                  </span>
                </div>
                <div>
                  <span className="text-base-content/50">Schedule:</span>{' '}
                  <span className="font-medium">
                    {agent.schedule_type === 'manual'
                      ? 'Manual'
                      : agent.schedule_type === 'recurring'
                        ? `Recurring (${agent.schedule_cron})`
                        : 'One-time'}
                  </span>
                </div>
                <div>
                  <span className="text-base-content/50">Last Run:</span>{' '}
                  <span className="font-medium">{formatDateTime(agent.last_run_at)}</span>
                </div>
                {agent.next_run_at && (
                  <div>
                    <span className="text-base-content/50">Next Run:</span>{' '}
                    <span className="font-medium">{formatDateTime(agent.next_run_at)}</span>
                  </div>
                )}
                {agent.send_email_on_run && (
                  <div>
                    <span className="text-base-content/50">Email To:</span>{' '}
                    <span className="font-medium">{agent.email_report_to}</span>
                  </div>
                )}
                <div>
                  <span className="text-base-content/50">Created:</span>{' '}
                  <span className="font-medium">{formatDateTime(agent.created_at)}</span>
                </div>
              </div>

              <div className="divider my-0" />

              <div>
                <span className="text-xs text-base-content/50 uppercase font-semibold">Instruction</span>
                <p className="text-sm font-mono bg-base-200/40 rounded-xl p-3 mt-1 whitespace-pre-wrap border border-white/[0.03]">
                  {agent.instruction}
                </p>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Run History */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-bold flex items-center gap-2">
            <Clock className="w-5 h-5 text-primary" />
            Run History
          </h3>
          <button className="btn btn-ghost btn-xs gap-1" onClick={loadData}>
            <RefreshCw className="w-3 h-3" />
            Refresh
          </button>
        </div>

        {runs.length === 0 ? (
          <div className="glass-card">
            <div className="flex flex-col items-center text-center py-10">
              <Clock className="w-10 h-10 text-base-content/30" />
              <p className="text-base-content/60 mt-2">No runs yet. Click "Run Now" to execute this agent.</p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {runs.map((run) => (
              <div
                key={run.id}
                className="glass-card"
              >
                <div
                  className="flex items-center gap-3 p-3 cursor-pointer hover:bg-base-200/30 transition-colors rounded-t-2xl"
                  onClick={() => setExpandedRun(expandedRun === run.id ? null : run.id)}
                >
                  <RunStatusIcon status={run.status} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-medium">Run #{run.id}</span>
                      <span className="text-base-content/50">
                        {formatDateTime(run.started_at)}
                      </span>
                      <span className="text-base-content/40 text-xs">
                        ({formatDuration(run.started_at, run.completed_at)})
                      </span>
                    </div>
                  </div>
                  {expandedRun === run.id ? (
                    <ChevronUp className="w-4 h-4 text-base-content/50" />
                  ) : (
                    <ChevronDown className="w-4 h-4 text-base-content/50" />
                  )}
                </div>

                {expandedRun === run.id && (
                  <div className="border-t border-white/[0.05] p-4">
                    {run.status === 'running' && (
                      <div className="flex items-center gap-2 text-info">
                        <Loader2 className="w-5 h-5 animate-spin" />
                        <span>Agent is running... Results will appear here automatically.</span>
                      </div>
                    )}
                    {run.status === 'completed' && run.output && (
                      <AgentRunOutput content={run.output} />
                    )}
                    {run.status === 'completed' && !run.output && (
                      <p className="text-base-content/50 text-sm italic">No output generated.</p>
                    )}
                    {run.status === 'failed' && (
                      <div className="alert alert-error">
                        <XCircle className="w-4 h-4" />
                        <span className="text-sm">{run.error || 'Unknown error'}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
