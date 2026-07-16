import React, { useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiBase } from '../api';
import {
  Loader2, LayoutDashboard, BarChart3, Layers, Bot,
  Users, Newspaper, ShieldCheck, ArrowRight, Sparkles, Zap,
  PieChart,
} from 'lucide-react';

const VALUE_PROPS = [
  {
    icon: Bot,
    title: 'Autonomous AI Agents',
    desc: 'Deploy dedicated agents that monitor assets and market dynamics 24/7. Automatically detect tax-loss harvesting opportunities, track volatility shifts, evaluate breaking events, and queue intelligent alerts.',
    gradient: 'from-amber-500/20 to-orange-500/10',
  },
  {
    icon: PieChart,
    title: 'Portfolio Intelligence',
    desc: 'Timely, interactive delivery of critical portfolio updates. Synthesizes key technical indicators, catalyst news, and AI hypotheses into structured cards for you to quickly review and confirm agentic actions.',
    gradient: 'from-cyan-500/20 to-blue-500/10',
  },
  {
    icon: BarChart3,
    title: 'Institutional Stock Research',
    desc: 'Professional-grade core research engine covering fundamentals, technicals, options, and sentiment alongside customizable, interactive valuation models.',
    gradient: 'from-violet-500/20 to-purple-500/10',
  },
  {
    icon: Layers,
    title: 'Advanced Strategies',
    desc: 'Deploy and backtest institutional-grade investment strategies and custom rules without writing a single line of code.',
    gradient: 'from-emerald-500/20 to-green-500/10',
  },
  {
    icon: Users,
    title: 'Legendary Strategies',
    desc: 'Filter and grade assets through the exact quantitative rules, checklists, and screening methodologies used by Buffett, Lynch, Soros, Dalio, and Munger.',
    gradient: 'from-rose-500/20 to-pink-500/10',
  },
  {
    icon: Newspaper,
    title: 'AI News Digest',
    desc: 'Real-time AI news synthesis that bypasses the noise, surfacing breaking global catalysts and explaining exactly how they impact your specific holdings.',
    gradient: 'from-sky-500/20 to-blue-500/10',
  },
  {
    icon: LayoutDashboard,
    title: 'Financial Command Center',
    desc: 'A unified, real-time dashboard consolidating your research workflow, active portfolio metrics, and running agent notifications.',
    gradient: 'from-blue-500/20 to-cyan-500/10',
  },
  {
    icon: ShieldCheck,
    title: 'Your Keys, Your Data',
    desc: 'True privacy-first architecture: bring your own API keys. Your data is stored securely, completely under your control, and never shared or sold.',
    gradient: 'from-teal-500/20 to-emerald-500/10',
  },
];

export default function LandingPage() {
  const { isAuthenticated, loading } = useAuth();
  const [searchParams] = useSearchParams();
  const [waitlistEmail, setWaitlistEmail] = useState('');
  const [waitlistStatus, setWaitlistStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  
  const isPromoActive = searchParams.get('promo') === 'pateron';
  const hasAuthError = searchParams.get('error') === 'unauthorized';

  const handleWaitlistSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!waitlistEmail) return;
    setWaitlistStatus('loading');
    try {
      const res = await fetch(`${apiBase}/api/waitlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: waitlistEmail })
      });
      if (res.ok) { setWaitlistStatus('success'); setWaitlistEmail(''); }
      else { setWaitlistStatus('error'); }
    } catch (err) { setWaitlistStatus('error'); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-base-200">
        <Loader2 className="w-10 h-10 animate-spin text-primary" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  return (
    <div className="min-h-screen bg-base-200 overflow-hidden">
      {/* ============================================================ */}
      {/* HERO                                                         */}
      {/* ============================================================ */}
      <section className="relative min-h-[90vh] flex items-center justify-center px-4">
        {/* Background effects */}
        <div className="absolute inset-0 overflow-hidden pointer-events-none select-none" aria-hidden="true">
          <img
            src="/hero-bg.jpg"
            alt=""
            className="w-full h-full object-cover opacity-15"
          />
          {/* Gradient overlays */}
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_transparent_20%,_oklch(var(--b2))_80%)]" />
          <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-base-200" />
          {/* Animated glow orbs */}
          <div className="absolute top-1/4 left-1/4 w-96 h-96 rounded-full bg-primary/5 blur-[120px] animate-float" />
          <div className="absolute bottom-1/4 right-1/4 w-80 h-80 rounded-full bg-secondary/5 blur-[100px] animate-float" style={{ animationDelay: '3s' }} />
        </div>

        {/* Hero content */}
        <div className="relative z-10 text-center max-w-3xl mx-auto">
          {/* Pill badge */}
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-primary/10 border border-primary/20 text-primary text-xs font-semibold mb-8 animate-fade-in-down">
            <Sparkles className="w-3.5 h-3.5" />
            AI-Powered Financial Intelligence
          </div>

          {hasAuthError && (
            <div className="alert alert-info shadow-lg mb-8 animate-fade-in-down mx-auto max-w-md rounded-2xl">
              <ShieldCheck className="w-5 h-5" />
              <div>
                <h3 className="font-bold">Invitation Only</h3>
                <div className="text-sm">Currently it is invitation only. If you are interested in being invited, please join the waitlist.</div>
              </div>
            </div>
          )}

          <h1 className="text-5xl sm:text-6xl lg:text-7xl font-black tracking-tight mb-4 leading-[1.1] animate-fade-in-up text-gradient-primary">
            Agentic Investing
          </h1>

          <p className="text-xl sm:text-2xl font-semibold text-primary/80 mb-4 animate-fade-in-up" style={{ animationDelay: '0.1s' }}>
            Your Digital Cortex is Active
          </p>

          <p className="text-base sm:text-lg text-base-content/50 max-w-xl mx-auto mb-10 leading-relaxed animate-fade-in-up" style={{ animationDelay: '0.2s' }}>
            Deploy your financial agents working only for you around the clock — helping you achieve your financial goals.
          </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4 animate-fade-in-up" style={{ animationDelay: '0.3s' }}>
              <a
                href={`${apiBase}/api/auth/google`}
                className="btn btn-primary btn-lg gap-3 rounded-2xl shadow-lg shadow-primary/25 hover:shadow-primary/40 transition-all duration-300 text-base w-full sm:w-auto hover:scale-[1.02] active:scale-[0.98]"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
                  <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                  <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                </svg>
                Get Started with Google
              </a>
            </div>

          {/* Waitlist */}
          <div className="mt-14 glass-card-elevated p-6 max-w-md mx-auto animate-fade-in-up" style={{ animationDelay: '0.4s' }}>
            <div className="flex items-center gap-2 mb-2">
              <Zap className="w-4 h-4 text-warning" />
              <h3 className="text-lg font-bold">Join the Waitlist</h3>
            </div>
            <p className="text-xs text-base-content/50 mb-4">
              Get early access to exclusive features before public launch.
            </p>

            {waitlistStatus === 'success' ? (
              <div className="glass-card p-4 border-success/20 text-center">
                <p className="text-sm font-semibold text-success">You're on the list! We'll be in touch soon.</p>
              </div>
            ) : (
              <form onSubmit={handleWaitlistSubmit} className="flex gap-2">
                <input
                  type="email"
                  placeholder="Enter your email"
                  className="input flex-1 rounded-xl bg-base-200/50 border-white/[0.06] focus:border-primary/40 text-sm"
                  required
                  value={waitlistEmail}
                  onChange={(e) => setWaitlistEmail(e.target.value)}
                  disabled={waitlistStatus === 'loading'}
                />
                <button
                  type="submit"
                  className="btn btn-secondary rounded-xl shadow-md shadow-secondary/20"
                  disabled={waitlistStatus === 'loading' || !waitlistEmail}
                >
                  {waitlistStatus === 'loading' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Join'}
                </button>
              </form>
            )}
            {waitlistStatus === 'error' && (
              <span className="text-xs text-error mt-2 block text-center">Something went wrong. Please try again.</span>
            )}
          </div>
        </div>
      </section>

      {/* ============================================================ */}
      {/* VALUE PROPOSITIONS                                           */}
      {/* ============================================================ */}
      <section className="relative z-10 container-app pb-20 -mt-4">
        <div className="text-center mb-12">
          <h2 className="text-2xl sm:text-3xl font-bold tracking-tight mb-3">
            Everything You Need to <span className="text-gradient-primary">Invest Smarter</span>
          </h2>
          <p className="text-sm text-base-content/40 max-w-lg mx-auto">
            Professional-grade tools, AI-powered insights, and autonomous agents — all in one platform.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
          {VALUE_PROPS.slice(0, 4).map((vp, i) => (
            <ValueCard key={vp.title} {...vp} index={i} />
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {VALUE_PROPS.slice(4).map((vp, i) => (
            <ValueCard key={vp.title} {...vp} index={i + 4} />
          ))}
        </div>
      </section>

      {/* ============================================================ */}
      {/* BOTTOM CTA                                                   */}
      {/* ============================================================ */}
      <section className="relative z-10 py-20 border-t border-white/[0.04]">
        <div className="text-center container-app">
          <h2 className="text-2xl sm:text-3xl font-bold tracking-tight mb-3">
            Activate Your Digital Cortex
          </h2>
          <p className="text-base-content/40 text-sm mb-8 max-w-md mx-auto">
            Sign in with Google and deploy your first financial agent in minutes.
          </p>
          <a
            href={`${apiBase}/api/auth/google`}
            className="btn btn-primary btn-lg gap-3 rounded-2xl shadow-lg shadow-primary/25 hover:shadow-primary/40 transition-all hover:scale-[1.02] active:scale-[0.98]"
          >
            <svg className="w-5 h-5" viewBox="0 0 24 24">
              <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
              <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
              <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
              <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
            </svg>
            Get Started
            <ArrowRight className="w-5 h-5" />
          </a>
          <p className="text-[10px] text-base-content/25 mt-6 tracking-wide uppercase">
            Your data is stored securely and never shared
          </p>
        </div>
      </section>

      {/* ============================================================ */}
      {/* FOOTER                                                       */}
      {/* ============================================================ */}
      <footer className="relative z-10 border-t border-white/[0.04] py-10">
        <div className="container-app text-center">
          {/* Plain anchors: these are prerendered static pages served outside
              the SPA router (see frontend/public/*.html) */}
          <nav className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-base-content/40 mb-4">
            <a href="/agentic-finance" className="hover:text-primary transition-colors">Agentic Finance</a>
            <a href="/agentic-stock-research" className="hover:text-primary transition-colors">Agentic Stock Research</a>
            <a href="/agentic-trading" className="hover:text-primary transition-colors">Agentic Trading</a>
            <a href="/agentic-quant" className="hover:text-primary transition-colors">Agentic Quant</a>
            <a href="/retirement-calculator" className="hover:text-primary transition-colors">Retirement Calculator</a>
            <a href="/finclaw" className="hover:text-primary transition-colors">FinClaw</a>
          </nav>
          <p className="text-[10px] text-base-content/25">
            © 2026 FinoAgent (FinClaw) · Research and analytics tools, not investment advice
          </p>
        </div>
      </footer>
    </div>
  );
}

function ValueCard({ icon: Icon, title, desc, gradient, index }: {
  icon: React.ElementType; title: string; desc: string; gradient: string; index: number;
}) {
  return (
    <div
      className="group glass-card p-5 hover:border-primary/15 hover:shadow-xl hover:shadow-primary/5 transition-all duration-300 hover:-translate-y-1 animate-fade-in-up"
      style={{ animationDelay: `${index * 80}ms`, animationFillMode: 'both' }}
    >
      <div className="flex items-start gap-3.5">
        <div className={`shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br ${gradient} flex items-center justify-center
          group-hover:scale-110 transition-transform duration-300`}>
          <Icon className="w-5 h-5 text-base-content/80" />
        </div>
        <div>
          <h3 className="font-bold text-sm mb-1.5 tracking-tight">{title}</h3>
          <p className="text-xs text-base-content/40 leading-relaxed">{desc}</p>
        </div>
      </div>
    </div>
  );
}
