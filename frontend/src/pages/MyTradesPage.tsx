import React, { useState } from 'react';
import { ClipboardList } from 'lucide-react';
import MyTradesV2 from '../components/trades/MyTradesV2';
import LogTradeModal from '../components/LogTradeModal';

export default function MyTradesPage() {
  const [showLog, setShowLog]   = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleLogged = () => {
    setShowLog(false);
    setRefreshKey(k => k + 1);   // force MyTradesV2 to re-fetch
  };

  return (
    <div className="container-app py-6 space-y-6">

      {/* Standalone log-trade modal */}
      <LogTradeModal
        open={showLog}
        onClose={() => setShowLog(false)}
        onLogged={handleLogged}
      />

      {/* Page header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl lg:text-3xl font-bold tracking-tight flex items-center gap-2">
            <ClipboardList className="w-7 h-7 text-primary" />
            Derivative Trades
          </h1>
          <p className="text-sm text-base-content/50 mt-1">
            Grouped by strategy type · Live P&L · Transaction history · AI advisor
          </p>
        </div>
        <button
          className="btn btn-primary btn-sm gap-2"
          onClick={() => setShowLog(true)}
        >
          <ClipboardList className="w-3.5 h-3.5" />
          Log Trade
        </button>
      </div>

      {/* Main content — key forces re-mount/re-fetch after a new trade is logged */}
      <MyTradesV2 key={refreshKey} />
    </div>
  );
}
