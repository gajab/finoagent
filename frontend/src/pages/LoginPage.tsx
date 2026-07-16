import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiBase } from '../api';
import { TrendingUp, Newspaper, Brain, Loader2 } from 'lucide-react';
import { Logo } from '../components/Logo';

export default function LoginPage() {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="w-10 h-10 animate-spin text-primary" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-base-200 p-4">
      <div className="card bg-base-100 shadow-2xl w-full max-w-md">
        <div className="card-body items-center text-center">
          {/* Logo */}
          <div className="flex items-center gap-3 mb-2">
            <Logo size={64} />
          </div>

          <h1 className="text-3xl font-bold">FinoAgent.ai</h1>
          <p className="text-base-content/60 mt-1 mb-6">
            AI-powered stock analysis with real-time data, news, technical analysis, and options insights.
          </p>

          {/* Feature highlights */}
          <div className="grid grid-cols-3 gap-3 w-full mb-8">
            <div className="flex flex-col items-center gap-1 p-3 bg-base-200 rounded-lg">
              <TrendingUp className="w-5 h-5 text-success" />
              <span className="text-xs">Technical Analysis</span>
            </div>
            <div className="flex flex-col items-center gap-1 p-3 bg-base-200 rounded-lg">
              <Newspaper className="w-5 h-5 text-info" />
              <span className="text-xs">News & Sentiment</span>
            </div>
            <div className="flex flex-col items-center gap-1 p-3 bg-base-200 rounded-lg">
              <Brain className="w-5 h-5 text-warning" />
              <span className="text-xs">AI Insights</span>
            </div>
          </div>

          {/* Google Sign In */}
          <a
            href={`${apiBase}/api/auth/google`}
            className="btn btn-primary btn-block gap-2"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24">
              <path
                fill="currentColor"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"
              />
              <path
                fill="currentColor"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              />
              <path
                fill="currentColor"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
              />
              <path
                fill="currentColor"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
              />
            </svg>
            Sign in with Google
          </a>

          <p className="text-xs text-base-content/40 mt-4">
            Your data is stored securely and never shared.
          </p>
        </div>
      </div>
    </div>
  );
}
