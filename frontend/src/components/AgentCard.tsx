import React from 'react';
import { Link } from 'react-router-dom';
import {
  Play,
  Pause,
  Square,
  Trash2,
  Copy,
  Clock,
  Loader2,
  CheckCircle,
  XCircle,
  Globe,
  Lock,
  Bookmark,
} from 'lucide-react';
import type { Agent } from '../types';

interface AgentCardProps {
  agent: Agent;
  isCommunity?: boolean;
  onRun?: (id: number) => void;
  onPause?: (id: number) => void;
  onStop?: (id: number) => void;
  onResume?: (id: number) => void;
  onDelete?: (id: number) => void;
  onClone?: (id: number) => void;
  onClick?: (id: number) => void;
  actionLoading?: number | null;
}

function RunStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'running':
      return (
        <span className="badge badge-info badge-sm gap-1">
          <Loader2 className="w-3 h-3 animate-spin" />
          Running
        </span>
      );
    case 'completed':
      return (
        <span className="badge badge-success badge-sm gap-1">
          <CheckCircle className="w-3 h-3" />
          Completed
        </span>
      );
    case 'failed':
      return (
        <span className="badge badge-error badge-sm gap-1">
          <XCircle className="w-3 h-3" />
          Failed
        </span>
      );
    default:
      return null;
  }
}

function AgentStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'active':
      return <span className="badge badge-success badge-sm">Active</span>;
    case 'paused':
      return <span className="badge badge-warning badge-sm">Paused</span>;
    case 'stopped':
      return <span className="badge badge-error badge-sm">Stopped</span>;
    default:
      return <span className="badge badge-ghost badge-sm">{status}</span>;
  }
}

function ScheduleBadge({ type, cron }: { type: string; cron?: string | null }) {
  if (type === 'manual') {
    return <span className="badge badge-ghost badge-xs">Manual</span>;
  }
  if (type === 'recurring') {
    return (
      <span className="badge badge-accent badge-xs gap-1">
        <Clock className="w-2.5 h-2.5" />
        {cron || 'Recurring'}
      </span>
    );
  }
  if (type === 'one_time') {
    return (
      <span className="badge badge-secondary badge-xs gap-1">
        <Clock className="w-2.5 h-2.5" />
        One-time
      </span>
    );
  }
  return null;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Never';
  const d = new Date(dateStr);
  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function AgentCard({
  agent,
  isCommunity = false,
  onRun,
  onPause,
  onStop,
  onResume,
  onDelete,
  onClone,
  onClick,
  actionLoading,
}: AgentCardProps) {
  const isLoading = actionLoading === agent.id;

  return (
    <div
      className="glass-card hover:border-white/[0.08] transition-all cursor-pointer"
      onClick={() => onClick?.(agent.id)}
    >
      <div className="p-4 gap-3 flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <h3 className="font-bold text-sm truncate">{agent.name}</h3>
            {agent.description && (
              <p className="text-xs text-base-content/60 mt-0.5 line-clamp-2">
                {agent.description}
              </p>
            )}
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {agent.is_shared ? (
              <span title="Shared"><Globe className="w-3.5 h-3.5 text-info" /></span>
            ) : (
              <span title="Private"><Lock className="w-3.5 h-3.5 text-base-content/40" /></span>
            )}
            <AgentStatusBadge status={agent.status} />
          </div>
        </div>

        {/* Instruction preview */}
        <p className="text-xs text-base-content/70 line-clamp-2 bg-base-200/40 rounded-xl p-2 font-mono border border-white/[0.03]">
          {agent.instruction}
        </p>

        {/* Meta info */}
        <div className="flex flex-wrap items-center gap-2 text-xs text-base-content/60">
          <ScheduleBadge type={agent.schedule_type} cron={agent.schedule_cron} />
          {agent.latest_run && <RunStatusBadge status={agent.latest_run.status} />}
          {agent.last_run_at && (
            <span>Last run: {formatDate(agent.last_run_at)}</span>
          )}
          {agent.tracked_company_id && (
            <Link
              to="/tracking"
              onClick={e => e.stopPropagation()}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded-full border border-primary/20 bg-primary/5 text-primary/70 hover:text-primary transition-colors text-[10px]"
            >
              <Bookmark className="w-2.5 h-2.5" /> Tracking
            </Link>
          )}
        </div>

        {/* Community info */}
        {isCommunity && agent.creator_name && (
          <div className="flex items-center gap-2 text-xs text-base-content/60">
            {agent.creator_picture ? (
              <img
                src={agent.creator_picture}
                alt={agent.creator_name}
                className="w-4 h-4 rounded-full"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="w-4 h-4 rounded-full bg-primary flex items-center justify-center text-primary-content text-[8px]">
                {agent.creator_name[0]}
              </div>
            )}
            <span>by {agent.creator_name}</span>
          </div>
        )}

        {/* Actions */}
        <div className="flex justify-end mt-1" onClick={(e) => e.stopPropagation()}>
          {isCommunity ? (
            <button
              className="btn btn-primary btn-xs gap-1"
              onClick={() => onClone?.(agent.id)}
              disabled={isLoading}
            >
              {isLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Copy className="w-3 h-3" />}
              Clone
            </button>
          ) : (
            <>
              {agent.status === 'active' && (
                <>
                  <button
                    className="btn btn-primary btn-xs gap-1"
                    onClick={() => onRun?.(agent.id)}
                    disabled={isLoading || agent.latest_run?.status === 'running'}
                  >
                    {isLoading ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Play className="w-3 h-3" />
                    )}
                    Run
                  </button>
                  <button
                    className="btn btn-warning btn-xs gap-1"
                    onClick={() => onPause?.(agent.id)}
                    disabled={isLoading}
                  >
                    <Pause className="w-3 h-3" />
                  </button>
                </>
              )}
              {agent.status === 'paused' && (
                <button
                  className="btn btn-success btn-xs gap-1"
                  onClick={() => onResume?.(agent.id)}
                  disabled={isLoading}
                >
                  <Play className="w-3 h-3" />
                  Resume
                </button>
              )}
              {agent.status !== 'stopped' && (
                <button
                  className="btn btn-ghost btn-xs text-error"
                  onClick={() => onStop?.(agent.id)}
                  disabled={isLoading}
                >
                  <Square className="w-3 h-3" />
                </button>
              )}
              {agent.status === 'stopped' && (
                <button
                  className="btn btn-success btn-xs gap-1"
                  onClick={() => onResume?.(agent.id)}
                  disabled={isLoading}
                >
                  <Play className="w-3 h-3" />
                  Reactivate
                </button>
              )}
              <button
                className="btn btn-ghost btn-xs text-error"
                onClick={() => onDelete?.(agent.id)}
                disabled={isLoading}
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
