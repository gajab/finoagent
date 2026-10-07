/**
 * betaStateMeta.ts — the action-state vocabulary's labels and styles. Deliberately dependency-free so the light
 * Beta chrome (switch button, state chip) can be imported by classic pages without pulling in the heavy
 * classic trade module that betaState.ts needs for its maths.
 */
export type ActionState = 'act' | 'watch' | 'harvest' | 'hold' | 'noquote' | 'pending';

/** Most urgent first. */
export const STATE_ORDER: ActionState[] = ['act', 'noquote', 'watch', 'harvest', 'hold', 'pending'];

// Static class strings so Tailwind's JIT generates them (no safelist).
export const STATE_META: Record<ActionState, {
  label: string; hint: string; chip: string; dot: string; text: string; border: string; bar: string; soft: string;
}> = {
  act: {
    label: 'Act', hint: 'Tested or losing — defend or close',
    chip: 'bg-error/15 text-error border-error/30', dot: 'bg-error', text: 'text-error',
    border: 'border-error/30', bar: 'bg-error', soft: 'bg-error/[0.06]',
  },
  watch: {
    label: 'Watch', hint: 'Expiring soon or a strike is near spot',
    chip: 'bg-warning/15 text-warning border-warning/30', dot: 'bg-warning', text: 'text-warning',
    border: 'border-warning/30', bar: 'bg-warning', soft: 'bg-warning/[0.06]',
  },
  harvest: {
    label: 'Harvest', hint: 'Most of the reward is banked — take profit',
    chip: 'bg-info/15 text-info border-info/30', dot: 'bg-info', text: 'text-info',
    border: 'border-info/30', bar: 'bg-info', soft: 'bg-info/[0.06]',
  },
  hold: {
    label: 'Hold', hint: 'On plan — nothing to do',
    chip: 'bg-success/15 text-success border-success/30', dot: 'bg-success', text: 'text-success',
    border: 'border-success/20', bar: 'bg-success', soft: 'bg-success/[0.04]',
  },
  noquote: {
    label: 'No quote', hint: 'An option leg is unpriced — verdict withheld',
    chip: 'bg-base-content/10 text-base-content/70 border-base-content/25', dot: 'bg-base-content/40',
    text: 'text-base-content/60', border: 'border-base-content/25', bar: 'bg-base-content/40', soft: 'bg-base-content/[0.05]',
  },
  pending: {
    label: 'Loading', hint: 'Waiting for live P&L',
    chip: 'bg-base-content/10 text-base-content/50 border-base-content/15', dot: 'bg-base-content/30',
    text: 'text-base-content/50', border: 'border-base-content/15', bar: 'bg-base-content/30', soft: 'bg-base-content/[0.03]',
  },
};

