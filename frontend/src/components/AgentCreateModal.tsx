import React, { useState, useEffect } from 'react';
import { X, Bot, Loader2 } from 'lucide-react';
import type { AgentCreateInput, ScheduleType } from '../types';

interface AgentCreateModalProps {
  open: boolean;
  onClose: () => void;
  onCreate: (data: AgentCreateInput) => Promise<void>;
  /** Optional pre-fill (e.g. a hedge-watch agent seeded from the Hedging page). */
  initial?: Partial<AgentCreateInput>;
}

export default function AgentCreateModal({ open, onClose, onCreate, initial }: AgentCreateModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instruction, setInstruction] = useState('');
  const [isShared, setIsShared] = useState(false);
  const [scheduleType, setScheduleType] = useState<ScheduleType>('manual');
  const [scheduleCron, setScheduleCron] = useState('');
  const [scheduledAt, setScheduledAt] = useState('');
  const [sendEmailOnRun, setSendEmailOnRun] = useState(false);
  const [emailReportTo, setEmailReportTo] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Seed the form from `initial` whenever the modal is opened with pre-fill.
  useEffect(() => {
    if (!open || !initial) return;
    if (initial.name != null) setName(initial.name);
    if (initial.description != null) setDescription(initial.description);
    if (initial.instruction != null) setInstruction(initial.instruction);
    if (initial.schedule_type != null) setScheduleType(initial.schedule_type);
    if (initial.schedule_cron != null) setScheduleCron(initial.schedule_cron);
    if (initial.send_email_on_run != null) setSendEmailOnRun(initial.send_email_on_run);
    if (initial.email_report_to != null) setEmailReportTo(initial.email_report_to);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const resetForm = () => {
    setName('');
    setDescription('');
    setInstruction('');
    setIsShared(false);
    setScheduleType('manual');
    setScheduleCron('');
    setScheduledAt('');
    setSendEmailOnRun(false);
    setEmailReportTo('');
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !instruction.trim()) {
      setError('Name and instruction are required.');
      return;
    }

    const data: AgentCreateInput = {
      name: name.trim(),
      instruction: instruction.trim(),
      is_shared: isShared,
      schedule_type: scheduleType,
    };
    if (description.trim()) data.description = description.trim();
    if (scheduleType === 'recurring' && scheduleCron.trim()) {
      data.schedule_cron = scheduleCron.trim();
    }
    if (scheduleType === 'one_time' && scheduledAt) {
      data.scheduled_at = new Date(scheduledAt).toISOString();
    }

    data.send_email_on_run = sendEmailOnRun;
    if (sendEmailOnRun && emailReportTo.trim()) {
      data.email_report_to = emailReportTo.trim();
    }

    try {
      setSubmitting(true);
      setError(null);
      await onCreate(data);
      resetForm();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create agent');
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  return (
    <dialog className="modal modal-open">
      <div className="modal-box max-w-2xl">
        <button className="btn btn-sm btn-circle btn-ghost absolute right-2 top-2" onClick={() => { resetForm(); onClose(); }}>
          <X className="w-4 h-4" />
        </button>

        <h3 className="text-lg font-bold flex items-center gap-2 mb-4">
          <Bot className="w-5 h-5 text-primary" />
          Create New Agent
        </h3>

        {error && (
          <div className="alert alert-error mb-4">
            <span className="text-sm">{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="form-control">
            <label className="label">
              <span className="label-text font-medium">Agent Name</span>
            </label>
            <input
              type="text"
              className="input input-bordered"
              placeholder="e.g. Tax Loss Harvesting Analyzer"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              required
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
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {/* Instruction */}
          <div className="form-control">
            <label className="label">
              <span className="label-text font-medium">Instruction</span>
            </label>
            <textarea
              className="textarea textarea-bordered h-32 font-mono text-sm"
              placeholder="Analyze my portfolio for tax loss harvesting opportunities. Look at each position's unrealized gain/loss, check wash sale rules, and recommend specific lots to sell to offset gains..."
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              required
            />
            <label className="label">
              <span className="label-text-alt text-base-content/50">
                The agent will use AI to execute this instruction with access to your portfolio, stock data, technical analysis, and web search.
              </span>
            </label>
          </div>

          {/* Schedule Type */}
          <div className="form-control">
            <label className="label">
              <span className="label-text font-medium">Schedule</span>
            </label>
            <select
              className="select select-bordered"
              value={scheduleType}
              onChange={(e) => setScheduleType(e.target.value as ScheduleType)}
            >
              <option value="manual">Manual (run on demand)</option>
              <option value="one_time">One-time (run at specific time)</option>
              <option value="recurring">Recurring (cron schedule)</option>
            </select>
          </div>

          {/* Cron Expression (for recurring) */}
          {scheduleType === 'recurring' && (
            <div className="form-control">
              <label className="label">
                <span className="label-text font-medium">Cron Expression</span>
              </label>
              <input
                type="text"
                className="input input-bordered font-mono"
                placeholder="0 9 * * 1-5  (weekdays at 9 AM)"
                value={scheduleCron}
                onChange={(e) => setScheduleCron(e.target.value)}
                required
              />
              <label className="label">
                <span className="label-text-alt text-base-content/50">
                  Format: minute hour day-of-month month day-of-week
                </span>
              </label>
            </div>
          )}

          {/* Scheduled At (for one_time) */}
          {scheduleType === 'one_time' && (
            <div className="form-control">
              <label className="label">
                <span className="label-text font-medium">Run At</span>
              </label>
              <input
                type="datetime-local"
                className="input input-bordered"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                required
              />
            </div>
          )}

          {/* Email Notification */}
          <div className="form-control">
            <label className="label cursor-pointer justify-start gap-3">
              <input
                type="checkbox"
                className="toggle toggle-secondary"
                checked={sendEmailOnRun}
                onChange={(e) => setSendEmailOnRun(e.target.checked)}
              />
              <div>
                <span className="label-text font-medium">Send Email on Run</span>
                <p className="text-xs text-base-content/50 mt-0.5">
                  Send the final agent report directly to an email address
                </p>
              </div>
            </label>
          </div>

          {sendEmailOnRun && (
            <div className="form-control">
              <label className="label">
                <span className="label-text font-medium">Destination Email</span>
                <span className="label-text-alt text-error">Required</span>
              </label>
              <input
                type="email"
                className="input input-bordered"
                placeholder="Required: enter a valid email address"
                value={emailReportTo}
                onChange={(e) => setEmailReportTo(e.target.value)}
                required={sendEmailOnRun}
              />
            </div>
          )}

          {/* Shared toggle */}
          <div className="form-control">
            <label className="label cursor-pointer justify-start gap-3">
              <input
                type="checkbox"
                className="toggle toggle-primary"
                checked={isShared}
                onChange={(e) => setIsShared(e.target.checked)}
              />
              <div>
                <span className="label-text font-medium">Share with community</span>
                <p className="text-xs text-base-content/50 mt-0.5">
                  Other users can discover and clone this agent
                </p>
              </div>
            </label>
          </div>

          {/* Actions */}
          <div className="modal-action">
            <button type="button" className="btn btn-ghost" onClick={() => { resetForm(); onClose(); }}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary gap-2" disabled={submitting}>
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Bot className="w-4 h-4" />}
              Create Agent
            </button>
          </div>
        </form>
      </div>
      <form method="dialog" className="modal-backdrop">
        <button onClick={() => { resetForm(); onClose(); }}>close</button>
      </form>
    </dialog>
  );
}
