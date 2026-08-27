import React from 'react';
import { Gauge, ShieldCheck, ShieldAlert, ShieldX, Shield, TrendingUp } from 'lucide-react';

interface Props {
  score: number;
  label: string;
}

function getConfig(score: number) {
  // Health convention: higher = healthier / stronger hold.
  if (score >= 61) return { color: 'text-success', bg: 'bg-success/10 border-success/20', icon: <ShieldCheck className="w-6 h-6 text-success" />, barColor: 'bg-success', glow: 'shadow-success/20' };
  if (score >= 45) return { color: 'text-info', bg: 'bg-info/10 border-info/20', icon: <Shield className="w-6 h-6 text-info" />, barColor: 'bg-info', glow: 'shadow-info/20' };
  if (score >= 30) return { color: 'text-warning', bg: 'bg-warning/10 border-warning/20', icon: <ShieldAlert className="w-6 h-6 text-warning" />, barColor: 'bg-warning', glow: 'shadow-warning/20' };
  return { color: 'text-error', bg: 'bg-error/10 border-error/20', icon: <ShieldX className="w-6 h-6 text-error" />, barColor: 'bg-error', glow: 'shadow-error/20' };
}

export function ExitSignalGauge({ score, label }: Props) {
  const cfg = getConfig(score);

  return (
    <div className={`rounded-2xl border p-5 ${cfg.bg} flex flex-col items-center gap-3 transition-all duration-300 shadow-lg ${cfg.glow}`}>
      <div className="flex items-center gap-2">
        <Gauge className={`w-4 h-4 ${cfg.color}`} />
        <span className="font-bold text-xs uppercase tracking-widest text-base-content/50">Hold Signal</span>
      </div>

      {/* Gauge */}
      <div className="relative w-36 h-[72px]">
        <svg viewBox="0 0 160 80" className="w-full h-full">
          <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="currentColor" strokeWidth="8" className="text-base-content/[0.06]" strokeLinecap="round" />
          <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="currentColor" strokeWidth="8" className={cfg.color} strokeLinecap="round" strokeDasharray={`${(score / 100) * 220} 220`} style={{ transition: 'stroke-dasharray 1s ease-out' }} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-end pb-0">
          <span className={`text-4xl font-black tabular-nums ${cfg.color}`}>{score}</span>
        </div>
      </div>

      <span className={`text-base font-bold ${cfg.color}`}>{label}</span>
      <p className="text-[10px] text-center text-base-content/40 max-w-[200px] leading-relaxed">
        {score >= 78 ? 'Strong fundamentals & technicals — hold with conviction.' :
         score >= 61 ? 'Healthy — no exit triggers, monitor periodically.' :
         score >= 45 ? 'Mixed signals — stay vigilant.' :
         score >= 30 ? 'Elevated risk — prepare an exit plan.' :
         'Weak across the board — strongly consider exiting or hedging.'}
      </p>
    </div>
  );
}

/* Entry Attractiveness gauge — higher is BETTER (inverted color logic vs exit). */
function getEntryConfig(score: number) {
  if (score >= 70) return { color: 'text-success', bg: 'bg-success/10 border-success/20', glow: 'shadow-success/20' };
  if (score >= 58) return { color: 'text-success', bg: 'bg-success/[0.07] border-success/15', glow: 'shadow-success/10' };
  if (score >= 45) return { color: 'text-info', bg: 'bg-info/10 border-info/20', glow: 'shadow-info/20' };
  if (score >= 32) return { color: 'text-warning', bg: 'bg-warning/10 border-warning/20', glow: 'shadow-warning/20' };
  return { color: 'text-error', bg: 'bg-error/10 border-error/20', glow: 'shadow-error/20' };
}

export function EntryRatingGauge({ score, label }: Props) {
  const cfg = getEntryConfig(score);
  return (
    <div className={`rounded-2xl border p-5 ${cfg.bg} flex flex-col items-center gap-3 transition-all duration-300 shadow-lg ${cfg.glow}`}>
      <div className="flex items-center gap-2">
        <TrendingUp className={`w-4 h-4 ${cfg.color}`} />
        <span className="font-bold text-xs uppercase tracking-widest text-base-content/50">Entry Signal</span>
      </div>

      <div className="relative w-36 h-[72px]">
        <svg viewBox="0 0 160 80" className="w-full h-full">
          <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="currentColor" strokeWidth="8" className="text-base-content/[0.06]" strokeLinecap="round" />
          <path d="M 10 75 A 70 70 0 0 1 150 75" fill="none" stroke="currentColor" strokeWidth="8" className={cfg.color} strokeLinecap="round" strokeDasharray={`${(score / 100) * 220} 220`} style={{ transition: 'stroke-dasharray 1s ease-out' }} />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-end pb-0">
          <span className={`text-4xl font-black tabular-nums ${cfg.color}`}>{score}</span>
        </div>
      </div>

      <span className={`text-base font-bold ${cfg.color}`}>{label}</span>
      <p className="text-[10px] text-center text-base-content/40 max-w-[200px] leading-relaxed">
        {score >= 70 ? 'Attractive entry — timing & value align.' :
         score >= 58 ? 'Constructive — accumulate on setups.' :
         score >= 45 ? 'On the watch-list — wait for confirmation.' :
         score >= 32 ? 'Weak entry — be patient.' :
         'Poor entry point right now.'}
      </p>
    </div>
  );
}
