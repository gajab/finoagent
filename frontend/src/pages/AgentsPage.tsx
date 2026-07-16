import React, { useState, useEffect, useCallback } from 'react';
import {
  Bot,
  Plus,
  Loader2,
  AlertCircle,
  CheckCircle,
  Users,
  User,
  Crown,
  Lock,
  Square,
} from 'lucide-react';
import type { Agent, AgentCreateInput } from '../types';
import {
  fetchAgents,
  fetchCommunityAgents,
  createAgent,
  triggerAgentRun,
  updateAgentStatus,
  deleteAgent,
  cloneAgent,
} from '../api';
import AgentCard from '../components/AgentCard';
import AgentCreateModal from '../components/AgentCreateModal';
import AgentDetail from '../components/AgentDetail';
import { useAuth } from '../contexts/AuthContext';

type Tab = 'my' | 'stopped' | 'community';

export default function AgentsPage() {
  const { isPremium } = useAuth();
  const [tab, setTab] = useState<Tab>('my');
  const [myAgents, setMyAgents] = useState<Agent[]>([]);
  const [communityAgents, setCommunityAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<number | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<number | null>(null);

  const loadMyAgents = useCallback(async () => {
    try {
      const data = await fetchAgents();
      setMyAgents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load agents');
    }
  }, []);

  const loadCommunityAgents = useCallback(async () => {
    try {
      const data = await fetchCommunityAgents();
      setCommunityAgents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load community agents');
    }
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    await Promise.all([loadMyAgents(), loadCommunityAgents()]);
    setLoading(false);
  }, [loadMyAgents, loadCommunityAgents]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Poll for running agents (only while not on community tab)
  useEffect(() => {
    if (tab === 'community') return;
    const hasRunning = myAgents.some(
      (a) => a.latest_run?.status === 'running'
    );
    if (!hasRunning) return;
    const interval = setInterval(loadMyAgents, 8000);
    return () => clearInterval(interval);
  }, [myAgents, loadMyAgents, tab]);

  const handleCreate = async (data: AgentCreateInput) => {
    await createAgent(data);
    setSuccess('Agent created successfully!');
    await loadMyAgents();
    setTimeout(() => setSuccess(null), 3000);
  };

  const handleRun = async (id: number) => {
    try {
      setActionLoading(id);
      setError(null);
      await triggerAgentRun(id);
      setSuccess('Agent run started!');
      await loadMyAgents();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run agent');
    } finally {
      setActionLoading(null);
    }
  };

  const handlePause = async (id: number) => {
    try {
      setActionLoading(id);
      await updateAgentStatus(id, 'paused');
      await loadMyAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to pause agent');
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async (id: number) => {
    try {
      setActionLoading(id);
      await updateAgentStatus(id, 'stopped');
      await loadMyAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to stop agent');
    } finally {
      setActionLoading(null);
    }
  };

  const handleResume = async (id: number) => {
    try {
      setActionLoading(id);
      await updateAgentStatus(id, 'active');
      await loadMyAgents();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to resume agent');
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (id: number) => {
    const agent = myAgents.find((a) => a.id === id);
    if (!confirm(`Delete agent "${agent?.name}"? This will remove all run history.`)) return;
    try {
      setActionLoading(id);
      await deleteAgent(id);
      setSuccess('Agent deleted.');
      await loadMyAgents();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete agent');
    } finally {
      setActionLoading(null);
    }
  };

  const handleClone = async (id: number) => {
    try {
      setActionLoading(id);
      setError(null);
      await cloneAgent(id);
      setSuccess('Agent cloned to your collection!');
      setTab('my');
      await loadMyAgents();
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clone agent');
    } finally {
      setActionLoading(null);
    }
  };

  // If viewing a detail page
  if (selectedAgentId !== null) {
    return (
      <div className="container-app py-6 sm:py-8">
        <AgentDetail
          agentId={selectedAgentId}
          onBack={() => {
            setSelectedAgentId(null);
            loadMyAgents();
          }}
          onDeleted={() => {
            setSelectedAgentId(null);
            setSuccess('Agent deleted.');
            loadMyAgents();
            setTimeout(() => setSuccess(null), 3000);
          }}
        />
      </div>
    );
  }

  // Premium wall — non-premium users see a locked state
  if (!isPremium) {
    return (
      <div className="container-app py-6 sm:py-8 animate-fade-in">
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="relative mb-6">
            <div className="bg-base-200 rounded-full p-8">
              <Bot className="w-16 h-16 text-base-content/20" />
            </div>
            <div className="absolute -top-1 -right-1 bg-yellow-400 rounded-full p-2 shadow-lg">
              <Lock className="w-4 h-4 text-gray-900" />
            </div>
          </div>
          <h1 className="text-3xl font-bold tracking-tight mb-3 flex items-center gap-2 justify-center">
            <Crown className="w-7 h-7 text-yellow-400" />
            Premium Feature
          </h1>
          <p className="text-base-content/60 max-w-md text-base mb-2">
            AI Agents is available exclusively for Premium users.
          </p>
          <p className="text-base-content/40 max-w-sm text-sm">
            Please contact the admin to upgrade your account to Premium and unlock Agents, Strategies, and more.
          </p>
          <div className="mt-8 px-6 py-4 rounded-2xl bg-yellow-400/10 border border-yellow-400/20 text-sm text-yellow-400 max-w-xs">
            <Crown className="w-4 h-4 inline mr-2" />
            Premium unlocks: AI Agents · Strategies · Advanced Tools
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <Loader2 className="w-10 h-10 animate-spin text-primary" />
        <p className="text-base-content/60 mt-4">Loading agents...</p>
      </div>
    );
  }

  const activeMyAgents = myAgents.filter(a => a.status !== 'stopped');
  const stoppedAgents = myAgents.filter(a => a.status === 'stopped');
  const displayedAgents = tab === 'my' ? activeMyAgents : tab === 'stopped' ? stoppedAgents : communityAgents;

  return (
    <div className="container-app py-6 sm:py-8 space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div className="page-header">
          <h1 className="page-title tracking-tight flex items-center gap-2">
            <Bot className="w-6 h-6 text-primary" />
            AI Agents
          </h1>
          <p className="page-subtitle">
            Create and manage AI-powered agents that analyze your portfolio and market data.
          </p>
        </div>
        {isPremium && (
          <button
            className="btn btn-primary gap-2 rounded-xl"
            onClick={() => setShowCreate(true)}
          >
            <Plus className="w-4 h-4" />
            New Agent
          </button>
        )}
      </div>

      {/* Alerts */}
      {error && (
        <div className="glass-card flex items-center gap-3 p-4 border-error/30 text-error">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="flex-1">{error}</span>
          <button className="btn btn-ghost btn-xs rounded-xl" onClick={() => setError(null)}>
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

      {/* Tabs */}
      <div className="tabs tabs-boxed w-fit">
        <button
          className={`tab gap-2 ${tab === 'my' ? 'tab-active' : ''}`}
          onClick={() => setTab('my')}
        >
          <User className="w-4 h-4" />
          My Agents
          {activeMyAgents.length > 0 && (
            <span className="badge badge-sm">{activeMyAgents.length}</span>
          )}
        </button>
        <button
          className={`tab gap-2 ${tab === 'stopped' ? 'tab-active' : ''}`}
          onClick={() => setTab('stopped')}
        >
          <Square className="w-4 h-4" />
          Stopped
          {stoppedAgents.length > 0 && (
            <span className="badge badge-sm badge-ghost">{stoppedAgents.length}</span>
          )}
        </button>
        <button
          className={`tab gap-2 ${tab === 'community' ? 'tab-active' : ''}`}
          onClick={() => setTab('community')}
        >
          <Users className="w-4 h-4" />
          Community
          {communityAgents.length > 0 && (
            <span className="badge badge-sm">{communityAgents.length}</span>
          )}
        </button>
      </div>

      {/* Agent Grid */}
      {displayedAgents.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {displayedAgents.map((agent) => (
            <AgentCard
              key={agent.id}
              agent={agent}
              isCommunity={tab === 'community'}
              onRun={handleRun}
              onPause={handlePause}
              onStop={handleStop}
              onResume={handleResume}
              onDelete={handleDelete}
              onClone={handleClone}
              onClick={(id) => {
                if (tab !== 'community') setSelectedAgentId(id);
              }}
              actionLoading={actionLoading}
            />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="bg-base-200 rounded-full p-6 mb-6">
            {tab === 'my' ? (
              <Bot className="w-12 h-12 text-primary" />
            ) : (
              <Users className="w-12 h-12 text-primary" />
            )}
          </div>
          <h2 className="text-2xl font-bold tracking-tight text-base-content mb-2">
            {tab === 'my' ? 'No Agents Yet' : tab === 'stopped' ? 'No Stopped Agents' : 'No Community Agents'}
          </h2>
          <p className="text-base-content/60 max-w-md">
            {tab === 'my'
              ? 'Create your first AI agent to automate portfolio analysis, find opportunities, and get market insights.'
              : tab === 'stopped'
              ? 'Agents you stop or that are auto-paused from archived tracking companies appear here.'
              : 'No shared agents yet. Create an agent and toggle "Share with community" to make it available here.'}
          </p>
          {tab === 'my' && isPremium && (
            <button
              className="btn btn-primary gap-2 mt-6 rounded-xl"
              onClick={() => setShowCreate(true)}
            >
              <Plus className="w-4 h-4" />
              Create Your First Agent
            </button>
          )}
        </div>
      )}

      {/* Create Modal */}
      <AgentCreateModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={handleCreate}
      />
    </div>
  );
}
