import React, { useState, useEffect, useCallback } from 'react';
import { fetchAllowlist, addAllowedUser, removeAllowedUser, setUserPremium } from '../api';
import type { AllowedUser } from '../types';
import { ShieldAlert, Plus, Trash2, Loader2, AlertCircle, Crown } from 'lucide-react';

export function AllowlistManager() {
  const [users, setUsers] = useState<AllowedUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [emailInput, setEmailInput] = useState('');
  const [adding, setAdding] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadUsers = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchAllowlist();
      setUsers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load allowlist');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!emailInput.trim()) return;
    try {
      setAdding(true);
      setError(null);
      await addAllowedUser(emailInput.trim());
      setEmailInput('');
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add user');
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (user: AllowedUser) => {
    if (!confirm(`Remove ${user.email} from allowlist?`)) return;
    try {
      setDeletingId(user.id);
      setError(null);
      await removeAllowedUser(user.email);
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove user');
    } finally {
      setDeletingId(null);
    }
  };

  const handlePremiumToggle = async (user: AllowedUser) => {
    try {
      setTogglingId(user.id);
      setError(null);
      await setUserPremium(user.email, !user.is_premium);
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update premium status');
    } finally {
      setTogglingId(null);
    }
  };

  return (
    <div className="glass-card mb-6 border-warning/30 bg-warning/5">
      <div className="card-body p-6">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 rounded-xl bg-warning/20">
            <ShieldAlert className="w-6 h-6 text-warning" />
          </div>
          <div>
            <h2 className="text-xl font-bold">Admin Allowlist</h2>
            <p className="text-sm text-base-content/60 mt-0.5">
              Manage authorized users and their tier. Premium users unlock Agents and Strategies.
            </p>
          </div>
        </div>

        {error && (
          <div className="alert alert-error text-sm py-2 mb-4">
            <AlertCircle className="w-4 h-4" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleAdd} className="flex gap-2 mb-6">
          <input
            type="email"
            value={emailInput}
            onChange={(e) => setEmailInput(e.target.value)}
            placeholder="Enter complete email address..."
            className="input input-bordered w-full rounded-xl bg-base-100"
            disabled={adding}
            required
          />
          <button
            type="submit"
            disabled={!emailInput.trim() || adding}
            className="btn btn-warning gap-2 rounded-xl text-warning-content"
          >
            {adding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Allow User
          </button>
        </form>

        <div className="bg-base-200/50 rounded-xl overflow-hidden border border-base-300">
          <table className="table w-full">
            <thead className="bg-base-300/30">
              <tr>
                <th className="font-semibold px-4">Email Address</th>
                <th className="font-semibold px-4 text-center">
                  <span className="flex items-center justify-center gap-1.5">
                    <Crown className="w-3.5 h-3.5 text-yellow-400" />
                    Premium
                  </span>
                </th>
                <th className="font-semibold px-4">Date Allowed</th>
                <th className="font-semibold text-right px-4">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={4} className="text-center py-6 text-base-content/50">
                    <Loader2 className="w-5 h-5 animate-spin mx-auto" />
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td colSpan={4} className="text-center py-6 text-base-content/50">
                    No users currently in the allowlist. Only the admin can log in.
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <tr key={u.id} className="hover:bg-base-300/20 transition-colors">
                    <td className="font-mono text-sm px-4 py-3">
                      <span className="flex items-center gap-2">
                        {u.email}
                        {u.is_premium && (
                          <span className="badge badge-xs bg-yellow-400/20 text-yellow-400 border-yellow-400/30 gap-1 font-semibold">
                            <Crown className="w-2.5 h-2.5" />
                            Premium
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="text-center px-4 py-3">
                      {togglingId === u.id ? (
                        <Loader2 className="w-4 h-4 animate-spin mx-auto text-base-content/50" />
                      ) : (
                        <input
                          type="checkbox"
                          checked={u.is_premium}
                          onChange={() => handlePremiumToggle(u)}
                          className="checkbox checkbox-sm checkbox-warning"
                          title={u.is_premium ? 'Revoke Premium' : 'Grant Premium'}
                        />
                      )}
                    </td>
                    <td className="text-xs text-base-content/60 px-4 py-3">
                      {u.created_at ? new Date(u.created_at).toLocaleDateString() : 'Unknown'}
                    </td>
                    <td className="text-right px-4 py-3">
                      <button
                        onClick={() => handleDelete(u)}
                        disabled={deletingId === u.id}
                        className="btn btn-ghost btn-xs text-error btn-square hover:bg-error/10"
                        title="Remove user"
                      >
                        {deletingId === u.id ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Trash2 className="w-4 h-4" />
                        )}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Legend */}
        <p className="text-xs text-base-content/40 mt-3">
          <Crown className="w-3 h-3 inline mr-1 text-yellow-400" />
          Premium users can access Agents and Strategies tabs and create agents. Basic users have Dashboard, Portfolio, and Channels access only.
        </p>
      </div>
    </div>
  );
}
