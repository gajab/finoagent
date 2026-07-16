import React, { useState, useEffect, useCallback } from 'react';
import {
  Server,
  CheckCircle,
  AlertCircle,
  Loader2,
  Save,
  Trash2,
  Eye,
  EyeOff,
  ExternalLink,
  Wifi,
  WifiOff,
  KeyRound,
  Copy,
  Check,
  ChevronRight,
  ChevronDown,
  Download,
} from 'lucide-react';
import {
  fetchBrokerConnection,
  generateBrokerKeys,
  saveBrokerConnection,
  deleteBrokerConnection,
  fetchBrokerStatus,
  diagnoseBrokerConnection,
} from '../api';
import type { BrokerStatus, BrokerKeyGenResponse } from '../types';
import { Stethoscope } from 'lucide-react';

type WizardStep = 1 | 2 | 3;

export function BrokerConnectionCard() {
  const [configured, setConfigured] = useState(false);
  const [hasKeys, setHasKeys] = useState(false);
  const [connectionId, setConnectionId] = useState<number | null>(null);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [status, setStatus] = useState<BrokerStatus | null>(null);

  // Wizard state
  const [wizardStep, setWizardStep] = useState<WizardStep>(1);
  const [showWizard, setShowWizard] = useState(false);
  const [keyGenResult, setKeyGenResult] = useState<BrokerKeyGenResponse | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Credential fields (simplified — only 4 fields)
  const [credAccountId, setCredAccountId] = useState('');
  const [consumerKey, setConsumerKey] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [accessTokenSecret, setAccessTokenSecret] = useState('');
  const [showTokens, setShowTokens] = useState(false);

  // Loading states
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagResults, setDiagResults] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadConnection = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchBrokerConnection();
      if (data?.configured === false) {
        setConfigured(false);
        setHasKeys(false);
      } else if (data?.id) {
        setConfigured(true);
        setConnectionId(data.id);
        setAccountId(data.account_id);
        setHasKeys(data.has_keys ?? false);
      }
    } catch {
      setConfigured(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConnection();
  }, [loadConnection]);

  const handleCopy = async (text: string, field: string) => {
    await navigator.clipboard.writeText(text);
    setCopiedField(field);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const handleGenerateKeys = async () => {
    setGenerating(true);
    setError(null);
    try {
      const result = await generateBrokerKeys();
      setKeyGenResult(result);
      setHasKeys(true);
      setSuccess('Keys generated and stored securely.');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to generate keys');
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    if (!credAccountId.trim()) { setError('Account ID is required'); return; }
    if (!consumerKey.trim()) { setError('Consumer Key is required'); return; }
    if (!accessToken.trim()) { setError('Access Token is required'); return; }
    if (!accessTokenSecret.trim()) { setError('Access Token Secret is required'); return; }

    setSaving(true);
    setError(null);
    try {
      await saveBrokerConnection({
        account_id: credAccountId.trim(),
        consumer_key: consumerKey.trim(),
        access_token: accessToken.trim(),
        access_token_secret: accessTokenSecret.trim(),
      });
      setSuccess('Credentials saved! Testing connection...');
      setCredAccountId('');
      setConsumerKey('');
      setAccessToken('');
      setAccessTokenSecret('');
      setShowWizard(false);
      await loadConnection();

      // Auto-test connection
      try {
        const s = await fetchBrokerStatus();
        setStatus(s);
        if (s.connected && s.authenticated) {
          setSuccess('Connected and authenticated!');
        } else {
          setSuccess('Credentials saved. Connection test: ' + (s.error || 'not authenticated yet.'));
        }
      } catch {
        setSuccess('Credentials saved.');
      }
      setTimeout(() => setSuccess(null), 5000);
    } catch (err: any) {
      setError(err.message || 'Failed to save credentials');
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setError(null);
    try {
      const s = await fetchBrokerStatus();
      setStatus(s);
      if (s.connected && s.authenticated) {
        setSuccess('Connected and authenticated!');
        setTimeout(() => setSuccess(null), 3000);
      } else if (s.connected) {
        setError('Server reachable but not authenticated. Check your credentials.');
      } else {
        setError(s.error || 'Cannot connect to Interactive Brokers.');
      }
    } catch (err: any) {
      setError(err.message || 'Connection test failed');
      setStatus(null);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm('Remove Interactive Brokers connection and all stored credentials?')) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteBrokerConnection();
      setConfigured(false);
      setConnectionId(null);
      setAccountId(null);
      setStatus(null);
      setHasKeys(false);
      setKeyGenResult(null);
      setSuccess('Broker connection removed.');
      setTimeout(() => setSuccess(null), 3000);
    } catch (err: any) {
      setError(err.message || 'Failed to remove connection');
    } finally {
      setDeleting(false);
    }
  };

  const handleDiagnose = async () => {
    setDiagnosing(true);
    setError(null);
    setDiagResults(null);
    try {
      const results = await diagnoseBrokerConnection();
      setDiagResults(results);
    } catch (err: any) {
      setError(err.message || 'Diagnosis failed');
    } finally {
      setDiagnosing(false);
    }
  };

  if (loading) return null;

  const CopyButton = ({ text, field }: { text: string; field: string }) => (
    <button
      onClick={() => handleCopy(text, field)}
      className="btn btn-ghost btn-xs gap-1"
      title="Copy to clipboard"
    >
      {copiedField === field ? (
        <><Check className="w-3 h-3 text-success" /> Copied</>
      ) : (
        <><Copy className="w-3 h-3" /> Copy</>
      )}
    </button>
  );

  const downloadPem = (content: string, filename: string) => {
    const blob = new Blob([content], { type: 'application/x-pem-file' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const DownloadButton = ({ content, filename }: { content: string; filename: string }) => (
    <button
      onClick={() => downloadPem(content, filename)}
      className="btn btn-ghost btn-xs gap-1 text-primary"
      title={`Download ${filename}`}
    >
      <Download className="w-3 h-3" /> {filename}
    </button>
  );

  return (
    <div className="glass-card">
      <div className="card-body">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="metric-card p-2">
              <Server className="w-5 h-5 text-warning" />
            </div>
            <div>
              <h2 className="card-title text-lg">Interactive Brokers</h2>
              {configured ? (
                <div className="flex items-center gap-2 mt-1">
                  <span className="badge badge-success badge-sm gap-1">
                    <CheckCircle className="w-3 h-3" />
                    Configured
                  </span>
                  {accountId && (
                    <span className="text-xs text-base-content/50 font-mono">{accountId}</span>
                  )}
                </div>
              ) : (
                <span className="badge badge-warning badge-sm gap-1 mt-1">
                  <AlertCircle className="w-3 h-3" />
                  Not configured
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            {configured && (
              <>
                <button
                  onClick={handleTestConnection}
                  disabled={testing}
                  className="btn btn-ghost btn-sm gap-1"
                  title="Test connection"
                >
                  {testing ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : status?.connected && status?.authenticated ? (
                    <Wifi className="w-4 h-4 text-success" />
                  ) : (
                    <WifiOff className="w-4 h-4 text-error" />
                  )}
                  Test
                </button>
                <button
                  onClick={handleDiagnose}
                  disabled={diagnosing}
                  className="btn btn-ghost btn-sm gap-1"
                  title="Run step-by-step diagnostics"
                >
                  {diagnosing ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Stethoscope className="w-4 h-4 text-info" />
                  )}
                  Diagnose
                </button>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className="btn btn-ghost btn-sm btn-square text-error"
                  title="Remove connection"
                >
                  {deleting ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                </button>
              </>
            )}
          </div>
        </div>

        {/* Description */}
        <p className="text-sm text-base-content/60 mt-2">
          Connect your Interactive Brokers account to place trades directly from strategy recommendations.
          Uses the IBKR Web API with OAuth — no local gateway required.
        </p>

        {/* Status banner */}
        {status && (
          <div className={`alert ${status.connected && status.authenticated ? 'alert-success' : 'alert-warning'} mt-3 py-2`}>
            {status.connected && status.authenticated ? (
              <Wifi className="w-4 h-4" />
            ) : (
              <WifiOff className="w-4 h-4" />
            )}
            <span className="text-sm">
              {status.connected && status.authenticated
                ? `Connected — Account ${status.account_id}`
                : status.error || 'Not connected'}
            </span>
          </div>
        )}

        {/* Error / Success */}
        {error && (
          <div className="alert alert-error mt-2 py-2">
            <AlertCircle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
            <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}>✕</button>
          </div>
        )}
        {success && (
          <div className="alert alert-success mt-2 py-2">
            <CheckCircle className="w-4 h-4" />
            <span className="text-sm">{success}</span>
          </div>
        )}

        {/* Diagnostic results */}
        {diagResults && (
          <div className="mt-3 bg-base-200 rounded-xl p-4 space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">Diagnostic Results</h3>
              <button className="btn btn-ghost btn-xs" onClick={() => setDiagResults(null)}>✕</button>
            </div>

            {/* Summary */}
            {diagResults.summary && (
              <div className={`alert py-2 text-xs ${
                diagResults.summary.startsWith('ALL CHECKS') ? 'alert-success' : 'alert-warning'
              }`}>
                <span className="whitespace-pre-wrap">{diagResults.summary}</span>
              </div>
            )}

            {/* Step-by-step results */}
            {Object.entries(diagResults).filter(([k]) => k.startsWith('step')).map(([key, value]: [string, any]) => {
              const stepName = key.replace(/_/g, ' ').replace('step', 'Step ');
              const isObj = typeof value === 'object' && value !== null;
              const hasError = isObj && (
                value.ok === false ||
                Object.values(value).some((v: any) => typeof v === 'object' && v?.ok === false)
              );
              const allOk = isObj && (
                value.ok === true ||
                (Object.keys(value).length > 0 && Object.values(value).every((v: any) => typeof v !== 'object' || v?.ok !== false))
              );

              return (
                <details key={key} className="collapse collapse-arrow bg-base-100 rounded-lg">
                  <summary className="collapse-title py-2 px-3 min-h-0 text-xs font-medium flex items-center gap-2">
                    {hasError ? (
                      <AlertCircle className="w-3.5 h-3.5 text-error shrink-0" />
                    ) : allOk ? (
                      <CheckCircle className="w-3.5 h-3.5 text-success shrink-0" />
                    ) : (
                      <span className="w-3.5 h-3.5 shrink-0" />
                    )}
                    {stepName}
                  </summary>
                  <div className="collapse-content px-3 pb-2">
                    <pre className="text-xs font-mono whitespace-pre-wrap break-all bg-base-200 rounded p-2 overflow-auto max-h-48">
                      {JSON.stringify(value, null, 2)}
                    </pre>
                  </div>
                </details>
              );
            })}
          </div>
        )}

        {/* Setup wizard toggle */}
        {!configured && !showWizard && (
          <button
            onClick={() => setShowWizard(true)}
            className="btn btn-primary gap-2 mt-3 rounded-xl"
          >
            <KeyRound className="w-4 h-4" />
            Set Up Connection
          </button>
        )}
        {configured && !showWizard && (
          <button
            onClick={() => { setShowWizard(true); setWizardStep(3); }}
            className="btn btn-ghost btn-sm mt-2 text-primary"
          >
            Update credentials
          </button>
        )}

        {/* ---- SETUP WIZARD ---- */}
        {showWizard && (
          <div className="mt-4 space-y-4">
            {/* Step indicators */}
            <div className="flex items-center gap-2 text-sm">
              {[1, 2, 3].map((step) => (
                <React.Fragment key={step}>
                  <button
                    onClick={() => setWizardStep(step as WizardStep)}
                    className={`flex items-center gap-1.5 px-3 py-1 rounded-full font-medium transition-colors ${
                      wizardStep === step
                        ? 'bg-primary text-primary-content'
                        : (step < wizardStep || (step === 1 && hasKeys))
                          ? 'bg-success/20 text-success'
                          : 'bg-base-200 text-base-content/50'
                    }`}
                  >
                    {(step < wizardStep || (step === 1 && hasKeys)) && step !== wizardStep ? (
                      <CheckCircle className="w-3.5 h-3.5" />
                    ) : (
                      <span className="w-4 text-center">{step}</span>
                    )}
                    {step === 1 && 'Generate Keys'}
                    {step === 2 && 'IBKR Portal'}
                    {step === 3 && 'Enter Credentials'}
                  </button>
                  {step < 3 && <ChevronRight className="w-4 h-4 text-base-content/30" />}
                </React.Fragment>
              ))}
            </div>

            {/* Step 1: Generate Keys */}
            {wizardStep === 1 && (
              <div className="space-y-3">
                <p className="text-sm text-base-content/70">
                  We'll generate the required cryptographic keys automatically. You'll copy the <strong>public keys</strong> into
                  your IBKR portal in the next step. Private keys are stored encrypted on our server.
                </p>

                {!keyGenResult && !hasKeys && (
                  <button
                    onClick={handleGenerateKeys}
                    disabled={generating}
                    className="btn btn-primary gap-2 rounded-xl"
                  >
                    {generating ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <KeyRound className="w-4 h-4" />
                    )}
                    {generating ? 'Generating...' : 'Generate Keys'}
                  </button>
                )}

                {!keyGenResult && hasKeys && (
                  <div className="space-y-3">
                    <div className="alert alert-success py-2">
                      <CheckCircle className="w-4 h-4" />
                      <span className="text-sm">Keys already generated and stored. You can regenerate or proceed to Step 2.</span>
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={handleGenerateKeys}
                        disabled={generating}
                        className="btn btn-outline btn-sm gap-1 rounded-xl"
                      >
                        {generating ? <Loader2 className="w-4 h-4 animate-spin" /> : <KeyRound className="w-4 h-4" />}
                        Regenerate Keys
                      </button>
                      <button
                        onClick={() => setWizardStep(2)}
                        className="btn btn-primary btn-sm gap-1 rounded-xl"
                      >
                        Next <ChevronRight className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                )}

                {keyGenResult && (
                  <div className="space-y-3">
                    <div className="alert alert-info py-2">
                      <Download className="w-4 h-4 shrink-0" />
                      <span className="text-sm">
                        Download each <code className="font-mono text-xs">.pem</code> file below — the IBKR portal requires file uploads, not copy-paste.
                      </span>
                    </div>

                    {/* Signature Public Key */}
                    <div>
                      <div className="flex items-center justify-between">
                        <label className="label py-1">
                          <span className="label-text text-sm font-medium">Signing Public Key</span>
                          <span className="label-text-alt text-xs text-base-content/40">RSA 2048</span>
                        </label>
                        <div className="flex gap-1">
                          <CopyButton text={keyGenResult.signature_public_key} field="sig" />
                          <DownloadButton content={keyGenResult.signature_public_key} filename="signing_public_key.pem" />
                        </div>
                      </div>
                      <textarea
                        readOnly
                        className="textarea textarea-bordered w-full font-mono text-xs rounded-xl h-20 bg-base-200"
                        value={keyGenResult.signature_public_key}
                      />
                    </div>

                    {/* Encryption Public Key */}
                    <div>
                      <div className="flex items-center justify-between">
                        <label className="label py-1">
                          <span className="label-text text-sm font-medium">Encryption Public Key</span>
                          <span className="label-text-alt text-xs text-base-content/40">RSA 2048</span>
                        </label>
                        <div className="flex gap-1">
                          <CopyButton text={keyGenResult.encryption_public_key} field="enc" />
                          <DownloadButton content={keyGenResult.encryption_public_key} filename="encryption_public_key.pem" />
                        </div>
                      </div>
                      <textarea
                        readOnly
                        className="textarea textarea-bordered w-full font-mono text-xs rounded-xl h-20 bg-base-200"
                        value={keyGenResult.encryption_public_key}
                      />
                    </div>

                    {/* DH Parameters */}
                    <div>
                      <div className="flex items-center justify-between">
                        <label className="label py-1">
                          <span className="label-text text-sm font-medium">Diffie-Hellman Parameters</span>
                          <span className="label-text-alt text-xs text-base-content/40">2048-bit prime</span>
                        </label>
                        <div className="flex gap-1">
                          <CopyButton text={keyGenResult.dh_params} field="dh" />
                          <DownloadButton content={keyGenResult.dh_params} filename="dh_params.pem" />
                        </div>
                      </div>
                      <textarea
                        readOnly
                        className="textarea textarea-bordered w-full font-mono text-xs rounded-xl h-20 bg-base-200"
                        value={keyGenResult.dh_params}
                      />
                    </div>

                    <div className="bg-base-200/60 rounded-xl p-3 text-xs text-base-content/60 space-y-1">
                      <p className="font-medium text-base-content/80">🔒 Private keys stay on our server</p>
                      <p>Only the public keys above leave the app. Your private signing and encryption keys are stored encrypted and never exposed.</p>
                    </div>

                    <button
                      onClick={() => setWizardStep(2)}
                      className="btn btn-primary gap-2 rounded-xl"
                    >
                      Next: Configure IBKR Portal <ChevronRight className="w-4 h-4" />
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Step 2: IBKR Portal Instructions */}
            {wizardStep === 2 && (
              <div className="space-y-3">
                <p className="text-sm text-base-content/70">
                  Open your IBKR Account Management portal and configure Web API access:
                </p>

                <div className="bg-base-200 rounded-xl p-4 space-y-3 text-sm">
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">1</span>
                    <span>
                      Go to{' '}
                      <a
                        href="https://www.interactivebrokers.com/portal"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="link link-primary inline-flex items-center gap-1"
                      >
                        IBKR Account Management <ExternalLink className="w-3 h-3" />
                      </a>
                      {' '}and log in
                    </span>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">2</span>
                    <span>Navigate to <strong>Settings → API → OAuth Self-Service Portal</strong></span>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">3</span>
                    <span>Choose a <strong>Consumer Key</strong> (9 characters, A-Z uppercase) — note it down</span>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">4</span>
                    <div>
                      Upload <code className="font-mono text-xs bg-base-300 px-1 rounded">signing_public_key.pem</code> as the <strong>Signing Public Key</strong>
                      <div className="text-xs text-base-content/50 mt-0.5">Downloaded from Step 1 above</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">5</span>
                    <div>
                      Upload <code className="font-mono text-xs bg-base-300 px-1 rounded">encryption_public_key.pem</code> as the <strong>Encryption Public Key</strong>
                      <div className="text-xs text-base-content/50 mt-0.5">Downloaded from Step 1 above</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">6</span>
                    <div>
                      Upload <code className="font-mono text-xs bg-base-300 px-1 rounded">dh_params.pem</code> as the <strong>Diffie-Hellman Parameters</strong>
                      <div className="text-xs text-base-content/50 mt-0.5">2048-bit prime, downloaded from Step 1 above</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">7</span>
                    <div>
                      Click <strong>Generate Access Token</strong> — copy both the <strong>Access Token</strong> and <strong>Access Token Secret</strong> immediately
                      <div className="text-xs text-warning mt-1">⚠ Shown only once. Store them safely before leaving the page.</div>
                    </div>
                  </div>
                  <div className="flex gap-3">
                    <span className="badge badge-primary badge-sm mt-0.5">8</span>
                    <span>Enable the <strong>OAuth toggle</strong> switch and save</span>
                  </div>
                </div>

                <a
                  href="https://github.com/Voyz/ibind/wiki/OAuth-1.0a"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="link link-primary text-sm inline-flex items-center gap-1"
                >
                  <ExternalLink className="w-3 h-3" />
                  Detailed setup guide with screenshots
                </a>

                <div className="flex gap-2">
                  <button
                    onClick={() => setWizardStep(1)}
                    className="btn btn-ghost btn-sm rounded-xl"
                  >
                    Back
                  </button>
                  <button
                    onClick={() => setWizardStep(3)}
                    className="btn btn-primary gap-2 rounded-xl"
                  >
                    Next: Enter Credentials <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Enter Credentials */}
            {wizardStep === 3 && (
              <div className="space-y-3">
                <p className="text-sm text-base-content/70">
                  Enter the values from your IBKR portal. All values are encrypted at rest.
                </p>

                <div>
                  <label className="label py-1">
                    <span className="label-text text-sm font-medium">Account ID</span>
                  </label>
                  <input
                    type="text"
                    className="input input-bordered w-full font-mono text-sm rounded-xl"
                    placeholder="e.g. U1234567"
                    value={credAccountId}
                    onChange={(e) => setCredAccountId(e.target.value)}
                  />
                </div>

                <div>
                  <label className="label py-1">
                    <span className="label-text text-sm font-medium">Consumer Key</span>
                  </label>
                  <input
                    type="text"
                    className="input input-bordered w-full font-mono text-sm rounded-xl"
                    placeholder="Your 9-character consumer key"
                    value={consumerKey}
                    onChange={(e) => setConsumerKey(e.target.value)}
                  />
                </div>

                <div>
                  <label className="label py-1">
                    <span className="label-text text-sm font-medium">Access Token</span>
                  </label>
                  <div className="relative">
                    <input
                      type={showTokens ? 'text' : 'password'}
                      className="input input-bordered w-full pr-10 font-mono text-sm rounded-xl"
                      placeholder="OAuth access token from IBKR portal"
                      value={accessToken}
                      onChange={(e) => setAccessToken(e.target.value)}
                    />
                    <button
                      className="absolute right-3 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
                      onClick={() => setShowTokens(!showTokens)}
                      type="button"
                    >
                      {showTokens ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                <div>
                  <label className="label py-1">
                    <span className="label-text text-sm font-medium">Access Token Secret</span>
                  </label>
                  <div className="relative">
                    <input
                      type={showTokens ? 'text' : 'password'}
                      className="input input-bordered w-full pr-10 font-mono text-sm rounded-xl"
                      placeholder="OAuth access token secret from IBKR portal"
                      value={accessTokenSecret}
                      onChange={(e) => setAccessTokenSecret(e.target.value)}
                    />
                    <button
                      className="absolute right-3 top-1/2 -translate-y-1/2 opacity-50 hover:opacity-100"
                      onClick={() => setShowTokens(!showTokens)}
                      type="button"
                    >
                      {showTokens ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </div>

                <div className="flex gap-2 mt-4">
                  {!configured && (
                    <button
                      onClick={() => setWizardStep(2)}
                      className="btn btn-ghost rounded-xl"
                    >
                      Back
                    </button>
                  )}
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="btn btn-primary gap-2 rounded-xl"
                  >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    Save & Connect
                  </button>
                  {configured && (
                    <button
                      onClick={() => { setShowWizard(false); setCredAccountId(''); setConsumerKey(''); setAccessToken(''); setAccessTokenSecret(''); }}
                      className="btn btn-ghost rounded-xl"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Collapse wizard */}
            {showWizard && (
              <button
                onClick={() => setShowWizard(false)}
                className="btn btn-ghost btn-xs text-base-content/40 mt-2"
              >
                <ChevronDown className="w-3 h-3" /> Collapse
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
