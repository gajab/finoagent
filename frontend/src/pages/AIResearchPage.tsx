/**
 * AIResearchPage — consolidated home for LLM-powered research tools.
 *
 * Layout mirrors StrategiesPage: sidebar nav on the left, active module
 * rendered in the right pane. Adding a new research module is a one-row
 * addition to MODULES.
 *
 * Modules (current):
 *   - event    → EventImpactAnalyzer (former "Historical" tab)
 *   - whatif   → WhatIfScenarios     (former "Target Theme", fully rebuilt institutional-style)
 *   - pickshv  → PickAndShovel       (moved in from the Markets page)
 *
 * Route: /ai-research  (also wired as an alias for legacy /impact)
 * Deep link via ?module=whatif to open a specific tab.
 */

import React, { useState, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Zap, LineChart, BrainCircuit, Shovel, Sparkles, Crown, Lock } from 'lucide-react';

import EventImpactAnalyzer from '../components/EventImpactAnalyzer';
import WhatIfScenarios from '../components/WhatIfScenarios';
import { PickAndShovel } from '../components/PickAndShovel';
import { PickAndShovelV2 } from '../components/PickAndShovelV2';
import { useAuth } from '../contexts/AuthContext';

type ModuleId = 'event' | 'whatif' | 'pickshv' | 'pickshv2';

interface Module {
  id: ModuleId;
  label: string;
  icon: React.ReactNode;
  description: string;
  badge?: string;
}

const MODULES: Module[] = [
  {
    id: 'whatif',
    label: 'What If Scenarios',
    icon: <BrainCircuit className="w-5 h-5" />,
    description: 'Institutional-style stress testing with cross-asset reactions and portfolio impact.',
    badge: 'Upgraded',
  },
  {
    id: 'event',
    label: 'Event Impact',
    icon: <LineChart className="w-5 h-5" />,
    description: 'Analyze the historical fallout of a specific event, tweet, or Fed statement.',
  },
  {
    id: 'pickshv',
    label: 'Pick & Shovels',
    icon: <Shovel className="w-5 h-5" />,
    description: 'Map themes to suppliers, enablers, and hidden beneficiaries across the value chain.',
  },
  {
    id: 'pickshv2',
    label: 'Pick & Shovels v2',
    icon: <Shovel className="w-5 h-5" />,
    description: 'Structured, filing-grounded deep research: theme → ETF holdings → SEC/IR deep dive → picks.',
    badge: 'New',
  },
];

export default function AIResearchPage() {
  const { isPremium } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const initial = (searchParams.get('module') as ModuleId) || 'whatif';
  const [active, setActive] = useState<ModuleId>(
    MODULES.some(m => m.id === initial) ? initial : 'whatif',
  );

  const activeModule = useMemo(() => MODULES.find(m => m.id === active)!, [active]);

  const select = (id: ModuleId) => {
    setActive(id);
    // Keep URL in sync so deep links / back-forward preserve the module.
    const next = new URLSearchParams(searchParams);
    next.set('module', id);
    setSearchParams(next, { replace: true });
  };

  if (!isPremium) {
    return (
      <div className="container-app py-6 sm:py-8 animate-fade-in">
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="relative mb-6">
            <div className="bg-base-200 rounded-full p-8">
              <Sparkles className="w-16 h-16 text-base-content/20" />
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
            AI Research is available exclusively for Premium users.
          </p>
          <p className="text-base-content/40 max-w-sm text-sm">
            Please contact the admin to upgrade your account to Premium and unlock AI Research, Agents, Strategies, and more.
          </p>
          <div className="mt-8 px-6 py-4 rounded-2xl bg-yellow-400/10 border border-yellow-400/20 text-sm text-yellow-400 max-w-xs">
            <Crown className="w-4 h-4 inline mr-2" />
            Premium unlocks: AI Research · AI Agents · Strategies · Advanced Tools
          </div>
        </div>
      </div>
    );
  }



  return (
    <div className="container-app py-6 sm:py-8 animate-fade-in">
      {/* Page header */}
      <div className="page-header mb-6">
        <h1 className="page-title tracking-tight flex items-center gap-3">
          <Sparkles className="w-7 h-7 text-primary" />
          AI Research
        </h1>
        <p className="page-subtitle">
          LLM-powered market research tools: scenario stress testing, event impact analysis, and thematic value-chain mapping.
        </p>
      </div>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Sidebar */}
        <div className="w-full lg:w-1/4">
          <div className="flex flex-col gap-2">
            {MODULES.map(m => (
              <button
                key={m.id}
                onClick={() => select(m.id)}
                className={`rounded-xl p-4 text-left transition-all border-2 flex items-start gap-3
                  ${active === m.id
                    ? 'bg-primary/10 border-primary shadow-md'
                    : 'bg-base-200 border-base-300 hover:border-primary/40'}`}
              >
                <div className={`mt-1 ${active === m.id ? 'text-primary' : 'text-base-content/50'}`}>
                  {m.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <div className={`font-bold flex items-center gap-1.5 ${active === m.id ? 'text-primary' : 'text-base-content'}`}>
                    {m.label}
                    {m.badge && (
                      <span className="badge badge-xs badge-primary font-semibold">{m.badge}</span>
                    )}
                  </div>
                  <div className="text-xs text-base-content/60 mt-1 leading-relaxed">{m.description}</div>
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Content */}
        <div className="w-full lg:w-3/4">
          <div className="glass-card h-full">
            <div className="card-body">
              {/* Module title strip for clarity on narrow screens */}
              <div className="flex items-center gap-2 mb-4 lg:hidden">
                {activeModule.icon}
                <h2 className="text-lg font-bold">{activeModule.label}</h2>
              </div>

              {active === 'event'    && <EventImpactAnalyzer />}
              {active === 'whatif'   && <WhatIfScenarios />}
              {active === 'pickshv'  && <PickAndShovel />}
              {active === 'pickshv2' && <PickAndShovelV2 />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
