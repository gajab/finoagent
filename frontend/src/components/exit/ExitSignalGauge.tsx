import React from 'react';
import { Gauge, ShieldCheck, ShieldAlert, ShieldX, Shield } from 'lucide-react';

interface Props {
  score: number;
  label: string;
}

function getConfig(score: number) {
  if (score >= 70) return { color: 'text-error', bg: 'bg-error/10 border-error/20', icon: <ShieldX className="w-6 h-6 text-error" />, barColor: 'bg-error', glow: 'shadow-error/20' };
  if (score >= 55) return { color: 'text-warning', bg: 'bg-warning/10 border-warning/20', icon: <ShieldAlert className="w-6 h-6 text-warning" />, barColor: 'bg-warning', glow: 'shadow-warning/20' };
  if (score >= 40) return { color: 'text-info', bg: 'bg-info/10 border-info/20', icon: <Shield className="w-6 h-6 text-info" />, barColor: 'bg-info', glow: 'shadow-info/20' };
  return { color: 'text-success', bg: 'bg-success/10 border-success/20', icon: <ShieldCheck className="w-6 h-6 text-success" />, barColor: 'bg-success', glow: 'shadow-success/20' };
}

export function ExitSignalGauge({ score, label }: Props) {
  const cfg = getConfig(score);

  return (
    <div className={`rounded-2xl border p-5 ${cfg.bg} flex flex-col items-center gap-3 transition-all duration-300 shadow-lg ${cfg.glow}`}>
      <div className="flex items-center gap-2">
        <Gauge className={`w-4 h-4 ${cfg.color}`} />
        <span className="font-bold text-xs uppercase tracking-widest text-base-content/50">Exit Signal</span>
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
        {score >= 70 ? 'Strongly consider exiting or hedging.' :
         score >= 55 ? 'Elevated signals — prepare exit plan.' :
         score >= 40 ? 'Mixed signals — stay vigilant.' :
         score >= 22 ? 'Generally healthy — monitor periodically.' :
         'Strong fundamentals support holding.'}
      </p>
    </div>
  );
}
