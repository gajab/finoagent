import React, { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { AllowlistManager } from '../components/AllowlistManager';
import { fetchApiKeys, saveApiKey, deleteApiKey, fetchModels, fetchSelectedModel, setSelectedModel, fetchEmailNotifications, setEmailNotifications, fetchDisplayPreferences, setDisplayPreferences } from '../api';
import type { ApiKeyInfo } from '../types';
import { BrokerConnectionCard } from '../components/BrokerConnectionCard';
import {
  Key,
  Trash2,
  Save,
  Loader2,
  CheckCircle,
  AlertCircle,
  ExternalLink,
  Brain,
  Search,
  Shield,
  Eye,
  EyeOff,
  Cpu,
  Mail,
  ActivitySquare,
  Crown,
  Wifi,
  Sparkles,
} from 'lucide-react';
import { Link } from 'react-router-dom';

interface KeyConfig {
  keyName: string;
  label: string;
  description: string;
  link: string;
  linkLabel: string;
  icon: React.ReactNode;
  placeholder: string;
}

const KEY_CONFIGS: KeyConfig[] = [
  {
    keyName: 'openai_api_key',
    label: 'OpenAI API Key',
    description:
      'Used for AI-powered news summaries, trading opportunity analysis, and industry insights. Requires a paid OpenAI account with API access.',
    link: 'https://platform.openai.com/api-keys',
    linkLabel: 'Get your key at platform.openai.com',
    icon: <Brain className="w-5 h-5 text-primary" />,
    placeholder: 'sk-...',
  },
  {
    keyName: 'gemini_api_key',
    label: 'Gemini API Key',
    description:
      'Used for Google Gemini models (Gemini 2.5 Flash, Gemini 1.5 Flash, etc.). Free tier with rate limits is available.',
    link: 'https://aistudio.google.com/app/apikey',
    linkLabel: 'Get your key at aistudio.google.com',
    icon: <Brain className="w-5 h-5 text-accent" />,
    placeholder: 'AIzaSy...',
  },
  {
    keyName: 'search_api_key',
    label: 'SerpAPI Key',
    description:
      'Used for web search to find real-time industry news and competitor information. Free tier provides 100 searches/month.',
    link: 'https://serpapi.com/manage-api-key',
    linkLabel: 'Get your key at serpapi.com',
    icon: <Search className="w-5 h-5 text-secondary" />,
    placeholder: 'Enter your SerpAPI key...',
  },
];

export default function SettingsPage() {
  const { user, isPremium } = useAuth();
  
  const [keys, setKeys] = useState<ApiKeyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [showValues, setShowValues] = useState<Record<string, boolean>>({});

  // Model selection state
  const [availableModels, setAvailableModels] = useState<{ id: string; label: string }[]>([]);
  const [selectedModel, setSelectedModelState] = useState<string>('');
  const [savingModel, setSavingModel] = useState(false);

  // Email notification state
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [emailAddress, setEmailAddress] = useState('');
  const [savingEmail, setSavingEmail] = useState(false);

  // Display preferences state
  const [showAiSections, setShowAiSections] = useState(false);
  const [savingDisplayPref, setSavingDisplayPref] = useState(false);

  const loadKeys = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchApiKeys();
      setKeys(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load API keys');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadModelSettings = useCallback(async () => {
    try {
      const [modelsData, currentModel] = await Promise.all([
        fetchModels(),
        fetchSelectedModel(),
      ]);
      setAvailableModels(modelsData.models);
      setSelectedModelState(currentModel.model);
    } catch (err) {
      // Non-critical — model defaults will be used
      console.error('Failed to load model settings', err);
    }
  }, []);

  const loadEmailSettings = useCallback(async () => {
    try {
      const data = await fetchEmailNotifications();
      setEmailEnabled(data.enabled);
      setEmailAddress(data.email);
    } catch (err) {
      console.error('Failed to load email settings', err);
    }
  }, []);

  const loadDisplayPreferences = useCallback(async () => {
    try {
      const data = await fetchDisplayPreferences();
      setShowAiSections(data.show_ai_sections);
    } catch (err) {
      console.error('Failed to load display preferences', err);
    }
  }, []);

  useEffect(() => {
    loadKeys();
    loadModelSettings();
    loadEmailSettings();
    loadDisplayPreferences();
  }, [loadKeys, loadModelSettings, loadEmailSettings, loadDisplayPreferences]);

  const handleSave = async (keyName: string) => {
    const value = inputValues[keyName]?.trim();
    if (!value) return;

    try {
      setSaving(keyName);
      setError(null);
      await saveApiKey(keyName, value);
      setSuccess(`${keyName} saved successfully!`);
      setInputValues((prev) => ({ ...prev, [keyName]: '' }));
      await loadKeys();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save key');
    } finally {
      setSaving(null);
    }
  };

  const handleDelete = async (keyName: string) => {
    if (!confirm('Are you sure you want to delete this API key?')) return;

    try {
      setDeleting(keyName);
      setError(null);
      await deleteApiKey(keyName);
      setSuccess(`${keyName} deleted.`);
      await loadKeys();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete key');
    } finally {
      setDeleting(null);
    }
  };

  const handleModelChange = async (modelId: string) => {
    try {
      setSavingModel(true);
      setError(null);
      await setSelectedModel(modelId);
      setSelectedModelState(modelId);
      setSuccess('Model updated!');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update model');
    } finally {
      setSavingModel(false);
    }
  };

  const handleEmailToggle = async () => {
    try {
      setSavingEmail(true);
      setError(null);
      const result = await setEmailNotifications(!emailEnabled);
      setEmailEnabled(result.enabled);
      setSuccess(result.enabled ? 'Email notifications enabled!' : 'Email notifications disabled.');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update email preference');
    } finally {
      setSavingEmail(false);
    }
  };

  const handleAiSectionsToggle = async () => {
    try {
      setSavingDisplayPref(true);
      setError(null);
      const result = await setDisplayPreferences(!showAiSections);
      setShowAiSections(result.show_ai_sections);
      setSuccess(result.show_ai_sections ? 'AI analysis sections shown.' : 'AI analysis sections hidden.');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update display preference');
    } finally {
      setSavingDisplayPref(false);
    }
  };

  const getConfiguredKey = (keyName: string): ApiKeyInfo | undefined =>
    keys.find((k) => k.key_name === keyName);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="container-app py-6 sm:py-8 space-y-6 animate-fade-in">
      <div className="page-header">
        <h1 className="page-title tracking-tight flex items-center gap-2">
          <Key className="w-6 h-6 text-primary" />
          Settings
        </h1>
        <p className="page-subtitle">
          Configure your API keys and AI model preferences.
        </p>
      </div>

      {/* Alerts */}
      {error && (
        <div className="glass-card flex items-center gap-3 p-4 border-error/30 text-error">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button className="btn btn-ghost btn-xs rounded-xl" onClick={() => setError(null)}>✕</button>
        </div>
      )}
      {success && (
        <div className="alert alert-success">
          <CheckCircle className="w-5 h-5" />
          <span>{success}</span>
        </div>
      )}

      {/* Info Banner */}
      <div className="alert alert-info">
        <Shield className="w-5 h-5" />
        <div>
          <h3 className="font-bold text-sm">Your keys are stored securely</h3>
          <p className="text-xs">
            API keys are encrypted and stored server-side. They are never exposed to the browser after saving.
          </p>
        </div>
      </div>

      {/* Settings Sections */}
      
      {/* Admin Allowlist Portal */}
      {user?.email === 'karwa.rahul@gmail.com' && <AllowlistManager />}

      {/* API Metrics & Observability — admin only */}
      {user?.email === 'karwa.rahul@gmail.com' && <div className="glass-card">
        <div className="card-body">
          <div className="flex items-center gap-3">
            <div className="metric-card p-2">
              <ActivitySquare className="w-5 h-5 text-indigo-500" />
            </div>
            <div className="flex-1">
              <h2 className="card-title text-lg">API Observability</h2>
              <p className="text-sm text-base-content/60 mt-0.5">
                Monitor request volume, error rates, and API latencies to IBKR, YFinance, and OpenAI.
              </p>
            </div>
            <Link to="/metrics" className="btn btn-primary btn-sm rounded-xl">View Metrics</Link>
          </div>
        </div>
      </div>}

      {/* AI Model Selector */}
      {availableModels.length > 0 && (
        <div className="glass-card">
          <div className="card-body">
            <div className="flex items-center gap-3">
              <div className="metric-card p-2">
                <Cpu className="w-5 h-5 text-accent" />
              </div>
              <div>
                <h2 className="card-title text-lg">AI Model Selection</h2>
                <p className="text-sm text-base-content/60 mt-0.5">
                  Choose the model used by AI Agents. Gemini models can use the free API tier.
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 mt-3">
              <select
                className="select select-bordered flex-1 rounded-xl"
                value={selectedModel}
                onChange={(e) => handleModelChange(e.target.value)}
                disabled={savingModel}
              >
                {availableModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
              {savingModel && <Loader2 className="w-5 h-5 animate-spin text-primary" />}
            </div>
          </div>
        </div>
      )}

      {/* Email Notifications */}
      <div className="glass-card">
        <div className="card-body">
          <div className="flex items-center gap-3">
            <div className="metric-card p-2">
              <Mail className="w-5 h-5 text-info" />
            </div>
            <div className="flex-1">
              <h2 className="card-title text-lg">Email Notifications</h2>
              <p className="text-sm text-base-content/60 mt-0.5">
                Receive agent run reports via email when agents complete their tasks.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between mt-3 metric-card px-4 py-3">
            <div className="flex flex-col">
              <span className="text-sm font-medium">Send reports to:</span>
              <span className="text-xs text-base-content/50 font-mono">{emailAddress || 'your email'}</span>
            </div>
            <div className="flex items-center gap-2">
              {savingEmail && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
              <input
                type="checkbox"
                className="toggle toggle-primary"
                checked={emailEnabled}
                onChange={handleEmailToggle}
                disabled={savingEmail}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Display Preferences — AI analysis sections visibility */}
      <div className="glass-card">
        <div className="card-body">
          <div className="flex items-center gap-3">
            <div className="metric-card p-2">
              <Sparkles className="w-5 h-5 text-violet-400" />
            </div>
            <div className="flex-1">
              <h2 className="card-title text-lg">AI Analysis Sections</h2>
              <p className="text-sm text-base-content/60 mt-0.5">
                Show the AI Impact, AI Fortress Architecture, and AI Business Stress Test blocks on a
                stock's Fundamental tab. Hidden by default.
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between mt-3 metric-card px-4 py-3">
            <div className="flex flex-col">
              <span className="text-sm font-medium">Show AI analysis sections</span>
              <span className="text-xs text-base-content/50">AI Impact · AI Fortress · AI Business Stress Test</span>
            </div>
            <div className="flex items-center gap-2">
              {savingDisplayPref && <Loader2 className="w-4 h-4 animate-spin text-primary" />}
              <input
                type="checkbox"
                className="toggle toggle-primary"
                checked={showAiSections}
                onChange={handleAiSectionsToggle}
                disabled={savingDisplayPref}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Broker Connection — Premium only */}
      {isPremium ? (
        <BrokerConnectionCard />
      ) : (
        <div className="glass-card">
          <div className="card-body flex items-center gap-3">
            <div className="metric-card p-2">
              <Wifi className="w-5 h-5 text-base-content/30" />
            </div>
            <div className="flex-1">
              <h2 className="card-title text-lg flex items-center gap-2">
                Interactive Brokers
                <Crown className="w-4 h-4 text-yellow-400" />
              </h2>
              <p className="text-sm text-base-content/60">
                Live order execution via IBKR is a Premium feature. Upgrade to connect your broker and enable automated trading.
              </p>
            </div>
            <div className="badge badge-warning gap-1 shrink-0">
              <Crown className="w-3 h-3" /> Premium
            </div>
          </div>
        </div>
      )}

      {/* Key Cards */}
      {KEY_CONFIGS.map((config) => {
        const configured = getConfiguredKey(config.keyName);
        const isSaving = saving === config.keyName;
        const isDeleting = deleting === config.keyName;

        return (
          <div key={config.keyName} className="glass-card">
            <div className="card-body">
              {/* Header */}
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="metric-card p-2">{config.icon}</div>
                  <div>
                    <h2 className="card-title text-lg">{config.label}</h2>
                    {configured ? (
                      <div className="flex items-center gap-2 mt-1">
                        <span className="badge badge-success badge-sm gap-1">
                          <CheckCircle className="w-3 h-3" />
                          Configured
                        </span>
                        <span className="text-xs text-base-content/50 font-mono">
                          {configured.masked_value}
                        </span>
                      </div>
                    ) : (
                      <span className="badge badge-warning badge-sm gap-1 mt-1">
                        <AlertCircle className="w-3 h-3" />
                        Not configured
                      </span>
                    )}
                  </div>
                </div>

                {configured && (
                  <button
                    onClick={() => handleDelete(config.keyName)}
                    disabled={isDeleting}
                    className="btn btn-ghost btn-sm btn-square text-error"
                    title="Delete key"
                  >
                    {isDeleting ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Trash2 className="w-4 h-4" />
                    )}
                  </button>
                )}
              </div>

              {/* Description */}
              <p className="text-sm text-base-content/60 mt-2">{config.description}</p>
              <a
                href={config.link}
                target="_blank"
                rel="noopener noreferrer"
                className="link link-primary text-sm inline-flex items-center gap-1 w-fit"
              >
                <ExternalLink className="w-3 h-3" />
                {config.linkLabel}
              </a>

              {/* Input */}
              <div className="flex gap-2 mt-3">
                <div className="relative flex-1">
                  <input
                    type={showValues[config.keyName] ? 'text' : 'password'}
                    className="input input-bordered w-full pr-10 font-mono text-sm rounded-xl"
                    placeholder={configured ? 'Enter new key to update...' : config.placeholder}
                    value={inputValues[config.keyName] || ''}
                    onChange={(e) =>
                      setInputValues((prev) => ({
                        ...prev,
                        [config.keyName]: e.target.value,
                      }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSave(config.keyName);
                    }}
                  />
                  <button
                    className="absolute right-2 top-1/2 -translate-y-1/2 btn btn-ghost btn-xs btn-square"
                    onClick={() =>
                      setShowValues((prev) => ({
                        ...prev,
                        [config.keyName]: !prev[config.keyName],
                      }))
                    }
                    type="button"
                  >
                    {showValues[config.keyName] ? (
                      <EyeOff className="w-3.5 h-3.5" />
                    ) : (
                      <Eye className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
                <button
                  onClick={() => handleSave(config.keyName)}
                  disabled={!inputValues[config.keyName]?.trim() || isSaving}
                  className="btn btn-primary gap-2 rounded-xl"
                >
                  {isSaving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Save className="w-4 h-4" />
                  )}
                  Save
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
