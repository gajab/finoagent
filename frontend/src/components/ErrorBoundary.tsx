import React from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props { children: React.ReactNode; label?: string }
interface State { error: Error | null; stack: string | null }

/**
 * Catches render-time exceptions so a single broken component degrades to a small
 * error card instead of unmounting the whole tree (which shows a blank/black page).
 * The fallback prints the error message + component stack so the failure is diagnosable
 * on-screen rather than silently disappearing.
 */
export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null, stack: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.setState({ stack: info.componentStack || null });
    // eslint-disable-next-line no-console
    console.error(`[ErrorBoundary${this.props.label ? ' · ' + this.props.label : ''}]`, error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="m-4 rounded-2xl border border-error/30 bg-error/5 p-5 text-sm">
          <div className="flex items-center gap-2 font-semibold text-error mb-2">
            <AlertTriangle className="w-4 h-4" /> Something went wrong{this.props.label ? ` in ${this.props.label}` : ''}.
          </div>
          <p className="text-base-content/70 mb-2">
            This section hit an error, but the rest of the app is fine. The details below help pinpoint the fix:
          </p>
          <pre className="max-h-60 overflow-auto rounded-lg bg-base-300/40 border border-base-300 p-2 text-[11px] whitespace-pre-wrap leading-snug">
{String(this.state.error?.message || this.state.error)}{this.state.stack ? `\n${this.state.stack}` : ''}
          </pre>
          <div className="mt-3 flex gap-2">
            <button className="btn btn-sm btn-primary" onClick={() => window.location.reload()}>Reload page</button>
            <button className="btn btn-sm btn-ghost" onClick={() => this.setState({ error: null, stack: null })}>Try again</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
