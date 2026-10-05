import React, { useState } from 'react';
import type { BondPortfolio } from '../../types';
import { Seg } from './bondUi';
import BondGapPlan from './BondGapPlan';
import BondRebalance from './BondRebalance';

// Two ways to decide what to do next:
//  • Fund my plan's gaps — new purchases sized to the plan's shortfall years, stress-tested for inflation.
//  • Balance the whole book — every holding in every account kept / sold / swapped (plus new money) so the plan
//    is funded at the best after-tax outcome with limited change.
export default function BondBuyPlanner({ data, onChanged, onGotoPlanner }: { data: BondPortfolio; onChanged: () => void; onGotoPlanner: () => void }) {
  const hasGoals = (data.profile.goals ?? []).length > 0;
  const [mode, setMode] = useState<'plan' | 'balance'>(hasGoals ? 'plan' : 'balance');
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Seg value={mode} onChange={v => setMode(v as typeof mode)} options={[
          { value: 'plan', label: 'Fund my plan’s gaps' }, { value: 'balance', label: 'Balance the whole book' }]} />
        <span className="text-[11px] text-base-content/50">
          {mode === 'plan' ? 'New purchases only: which years are short, what to buy for each, and what inflation does to it.'
            : 'Everything you hold, account by account: what to keep, what to swap, and where new money goes.'}
        </span>
      </div>
      {mode === 'plan' ? <BondGapPlan onChanged={onChanged} onGotoPlanner={onGotoPlanner} /> : <BondRebalance data={data} onChanged={onChanged} />}
    </div>
  );
}
