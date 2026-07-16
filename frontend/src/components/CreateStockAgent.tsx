import React, { useState } from 'react';
import { Bot, FileText, Bell, TrendingUp, Shield, Loader2, CheckCircle, AlertTriangle, Plus, X, ExternalLink } from 'lucide-react';
import { createAgent } from '../api';
import type { ScheduleType } from '../types';

interface CreateStockAgentProps {
  ticker: string;
  companyName: string;
  currentPrice: number;
}

interface Template {
  id: string;
  name: string;
  icon: React.ReactNode;
  description: string;
  instruction: string;
  hasTargetPrice?: boolean;
}

export function CreateStockAgent({ ticker, companyName, currentPrice }: CreateStockAgentProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [agentName, setAgentName] = useState('');
  const [instruction, setInstruction] = useState('');
  const [scheduleType, setScheduleType] = useState<ScheduleType>('manual');
  const [scheduleCron, setScheduleCron] = useState('');
  const [targetPrice, setTargetPrice] = useState('');
  const [creating, setCreating] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const templates: Template[] = [
    {
      id: 'weekly-analysis',
      name: 'Weekly Analysis',
      icon: <FileText className="w-5 h-5" />,
      description: 'Comprehensive weekly analysis report',
      instruction: `Run a comprehensive analysis of ${ticker} (${companyName}). Include: current price analysis vs my cost basis, technical indicators (MACD, RSI, Bollinger Bands), recent news sentiment, analyst ratings changes, options activity (unusual volume, IV changes), and actionable recommendations. Focus on changes since the last report. If I hold this stock, include tax implications and optimal exit strategies.`,
    },
    {
      id: 'price-alert',
      name: 'Price Alert',
      icon: <Bell className="w-5 h-5" />,
      description: 'Monitor and alert on price movements',
      hasTargetPrice: true,
      instruction: `Monitor ${ticker} (${companyName}). Current price: $${currentPrice.toFixed(2)}. Target price: $TARGET_PRICE. Analyze whether the stock is approaching the target level. Include: technical analysis showing support/resistance relative to target, recent catalysts that could drive price movement, volume analysis for institutional activity, and options market sentiment. Provide a probability assessment of reaching the target price within 30 days.`,
    },
    {
      id: 'sentiment-monitor',
      name: 'Sentiment Monitor',
      icon: <TrendingUp className="w-5 h-5" />,
      description: 'Track news and sentiment changes',
      instruction: `Analyze the latest news and market sentiment for ${ticker} (${companyName}). Search for: recent developments and breaking news, analyst rating changes and price target revisions, insider trading activity, institutional buying/selling patterns (13F filings), social media and retail sentiment trends, and options flow analysis. Compare current sentiment to the previous analysis. Highlight any significant sentiment shifts that could affect the stock price.`,
    },
    {
      id: 'tax-optimization',
      name: 'Tax Optimization',
      icon: <Shield className="w-5 h-5" />,
      description: 'Portfolio tax analysis for this stock',
      instruction: `Review my portfolio holdings of ${ticker} (${companyName}) for tax optimization opportunities. Analyze: unrealized gains/losses and holding period (short-term vs long-term capital gains implications), wash sale risks if I sell and rebuy within 30 days, tax-loss harvesting opportunities with correlated alternatives, covered call strategies that could generate income while deferring gains, optimal sell timing based on tax brackets and holding period thresholds, and impact on my overall portfolio tax liability. Provide specific, actionable tax strategies with dollar estimates.`,
    },
  ];

  const handleSelectTemplate = (t: Template) => {
    setSelectedTemplate(t.id);
    setAgentName(`${t.name} — ${ticker}`);
    if (t.hasTargetPrice && targetPrice) {
      setInstruction(t.instruction.replace('$TARGET_PRICE', targetPrice));
    } else {
      setInstruction(t.instruction);
    }
    setError(null);
    setSuccess(false);
  };

  const handleTargetPriceChange = (val: string) => {
    setTargetPrice(val);
    const tpl = templates.find((t) => t.id === selectedTemplate);
    if (tpl?.hasTargetPrice && val) {
      setInstruction(tpl.instruction.replace('$TARGET_PRICE', val));
    }
  };

  const handleCreate = async () => {
    if (!agentName.trim() || !instruction.trim()) {
      setError('Name and instruction are required.');
      return;
    }

    try {
      setCreating(true);
      setError(null);
      await createAgent({
        name: agentName.trim(),
        description: `Auto-created agent for ${ticker}`,
        instruction: instruction.trim(),
        schedule_type: scheduleType,
        schedule_cron: scheduleType === 'recurring' ? scheduleCron : undefined,
      });
      setSuccess(true);
      setSelectedTemplate(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create agent');
    } finally {
      setCreating(false);
    }
  };

  const handleCancel = () => {
    setSelectedTemplate(null);
    setError(null);
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        <h2 className="font-bold text-sm flex items-center gap-2">
          <Bot className="w-5 h-5 text-primary" />
          Quick Agent for {ticker}
        </h2>
        <p className="text-xs text-base-content/50 -mt-1">
          Create an automated agent to monitor and analyze this stock.
        </p>

        {/* Success State */}
        {success && (
          <div className="alert alert-success mt-2">
            <CheckCircle className="w-5 h-5" />
            <div>
              <span>Agent created successfully!</span>
              <a href="/agents" className="link link-primary ml-2 text-sm inline-flex items-center gap-1">
                View Agents <ExternalLink className="w-3 h-3" />
              </a>
            </div>
            <button className="btn btn-ghost btn-xs" onClick={() => setSuccess(false)}>
              <Plus className="w-3 h-3 rotate-45" />
            </button>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="alert alert-error mt-2 py-2">
            <AlertTriangle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
            <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}>✕</button>
          </div>
        )}

        {/* Template Grid (when no template selected) */}
        {!selectedTemplate && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
            {templates.map((t) => (
              <button
                key={t.id}
                className="bg-base-200/40 hover:bg-base-200/60 rounded-xl p-3 text-left transition-colors border border-white/[0.03] hover:border-primary/30"
                onClick={() => handleSelectTemplate(t)}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-primary">{t.icon}</span>
                  <span className="font-medium text-sm">{t.name}</span>
                </div>
                <p className="text-xs text-base-content/50">{t.description}</p>
              </button>
            ))}
          </div>
        )}

        {/* Agent Form (when template selected) */}
        {selectedTemplate && !success && (
          <div className="space-y-3 mt-2">
            {/* Target Price input for price alert */}
            {templates.find((t) => t.id === selectedTemplate)?.hasTargetPrice && (
              <div className="form-control">
                <label className="label py-1">
                  <span className="label-text text-sm font-medium">Target Price ($)</span>
                </label>
                <input
                  type="number"
                  className="input input-bordered input-sm"
                  placeholder={`e.g. ${(currentPrice * 1.1).toFixed(0)}`}
                  step="0.01"
                  value={targetPrice}
                  onChange={(e) => handleTargetPriceChange(e.target.value)}
                />
              </div>
            )}

            {/* Agent Name */}
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-sm font-medium">Agent Name</span>
              </label>
              <input
                type="text"
                className="input input-bordered input-sm"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                maxLength={200}
              />
            </div>

            {/* Instruction */}
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-sm font-medium">Instruction</span>
                <span className="label-text-alt text-xs">Editable</span>
              </label>
              <textarea
                className="textarea textarea-bordered text-sm h-28 font-mono"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
              />
            </div>

            {/* Schedule */}
            <div className="form-control">
              <label className="label py-1">
                <span className="label-text text-sm font-medium">Schedule</span>
              </label>
              <select
                className="select select-bordered select-sm"
                value={scheduleType}
                onChange={(e) => setScheduleType(e.target.value as ScheduleType)}
              >
                <option value="manual">Manual (run on demand)</option>
                <option value="recurring">Recurring (cron schedule)</option>
              </select>
            </div>

            {scheduleType === 'recurring' && (
              <div className="form-control">
                <label className="label py-1">
                  <span className="label-text text-sm font-medium">Cron Expression</span>
                </label>
                <input
                  type="text"
                  className="input input-bordered input-sm font-mono"
                  placeholder="0 9 * * 1-5 (weekdays at 9 AM)"
                  value={scheduleCron}
                  onChange={(e) => setScheduleCron(e.target.value)}
                />
                <label className="label py-0.5">
                  <span className="label-text-alt text-xs text-base-content/40">
                    Format: minute hour day-of-month month day-of-week
                  </span>
                </label>
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-1">
              <button className="btn btn-ghost btn-sm gap-1" onClick={handleCancel} disabled={creating}>
                <X className="w-4 h-4" />
                Cancel
              </button>
              <button className="btn btn-primary btn-sm gap-1" onClick={handleCreate} disabled={creating}>
                {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Bot className="w-4 h-4" />}
                Create Agent
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
