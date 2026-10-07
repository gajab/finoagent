import React, { useState, lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ClipboardList, ClipboardPaste } from 'lucide-react';
import MyTradesV2 from '../components/trades/MyTradesV2';
import LogTradeModal from '../components/LogTradeModal';
import PasteOrderModal from '../components/PasteOrderModal';
import { useBetaUi, TryBetaButton, BackToClassicButton, BetaBadge } from '../beta/BetaChrome';

// Beta is code-split: classic users never download it.
const MyTradesBeta = lazy(() => import('../beta/MyTradesBeta'));

export default function MyTradesPage() {
  const [showLog, setShowLog]   = useState(false);
  const [showPaste, setShowPaste] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // Classic is the default. `?ui=beta` opts into the Beta layout; `?open=<id>` deep-links a classic card.
  const { isBeta, enterBeta, leaveBeta } = useBetaUi();
  const [params] = useSearchParams();
  const openParam = Number(params.get('open'));
  const openId = Number.isFinite(openParam) && openParam > 0 ? openParam : null;

  const handleLogged = () => {
    setShowLog(false);
    setRefreshKey(k => k + 1);   // force the list to re-fetch
  };

  return (
    <div className={isBeta
      ? 'mx-auto w-full max-w-[1680px] px-4 sm:px-6 lg:px-8 py-6 space-y-6'
      : 'container-app py-6 space-y-6'}>

      {/* Standalone log-trade modal */}
      <LogTradeModal
        open={showLog}
        onClose={() => setShowLog(false)}
        onLogged={handleLogged}
      />

      {/* Paste-order importer — mounted only while open so its state is always fresh */}
      {showPaste && (
        <PasteOrderModal
          open={showPaste}
          onClose={() => setShowPaste(false)}
          onDone={() => { setShowPaste(false); setRefreshKey(k => k + 1); }}
          onLogManual={() => setShowLog(true)}
        />
      )}

      {/* Page header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight flex items-center gap-2">
            <ClipboardList className="w-7 h-7 text-primary" />
            Derivative Trades
            {isBeta && <BetaBadge className="ml-1" />}
          </h1>
          <p className="text-sm text-base-content/50 mt-1">
            {isBeta
              ? 'Verdict first · one urgency-sorted list · live P&L · same data as classic'
              : 'Grouped by strategy type · Live P&L · Transaction history · AI advisor'}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {isBeta ? <BackToClassicButton onClick={leaveBeta} /> : <TryBetaButton onClick={enterBeta} />}
          <button
            className="btn btn-ghost btn-sm gap-2 border border-white/10"
            onClick={() => setShowPaste(true)}
            title="Paste a broker order (Fidelity rows) — auto-creates or closes the trade"
          >
            <ClipboardPaste className="w-3.5 h-3.5" />
            Paste Order
          </button>
          <button
            className="btn btn-primary btn-sm gap-2"
            onClick={() => setShowLog(true)}
          >
            <ClipboardList className="w-3.5 h-3.5" />
            Log Trade
          </button>
        </div>
      </div>

      {/* Main content — key forces re-mount/re-fetch after a new trade is logged */}
      {isBeta
        ? <Suspense fallback={<div className="flex justify-center py-16"><span className="loading loading-spinner loading-md text-base-content/30" /></div>}><MyTradesBeta key={refreshKey} /></Suspense>
        : <MyTradesV2 key={refreshKey} openId={openId} />}
    </div>
  );
}
