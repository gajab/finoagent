import React, { lazy, Suspense } from 'react';
import { Routes, Route, useLocation, Navigate } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Navbar from './components/Navbar';
import { useAuth } from './contexts/AuthContext';

// Landing + login stay EAGER — they're the first paint for anonymous visitors.
import LandingPage from './pages/LandingPage';
import LoginPage from './pages/LoginPage';

// Everything behind auth (plus the heavy public calculators) is code-split into its own chunk,
// loaded on navigation — so the initial bundle no longer carries every page's dependencies.
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const MarketPage = lazy(() => import('./pages/MarketPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'));
const AgentsPage = lazy(() => import('./pages/AgentsPage'));
const StrategiesPage = lazy(() => import('./pages/StrategiesPage'));
const AIResearchPage = lazy(() => import('./pages/AIResearchPage'));
const ChannelsPage = lazy(() => import('./pages/ChannelsPage'));
const TrackingPage = lazy(() => import('./pages/TrackingPage'));
const MyTradesPage = lazy(() => import('./pages/MyTradesPage'));
const CalculatorsPage = lazy(() => import('./pages/CalculatorsPage'));
const MetricsDashboard = lazy(() => import('./components/MetricsDashboard').then(m => ({ default: m.MetricsDashboard })));

function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-32 text-base-content/50">
      <span className="loading loading-spinner loading-md" />
    </div>
  );
}

function AppLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  const isPublicCalculator = location.pathname === '/calculators';
  const showNavbar = isAuthenticated || isPublicCalculator;

  return (
    <div className="min-h-screen bg-base-200">
      {showNavbar && <Navbar />}
      <main className="animate-fade-in">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppLayout>
        <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/market"
            element={
              <ProtectedRoute>
                <MarketPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <DashboardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/portfolio"
            element={
              <ProtectedRoute>
                <PortfolioPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/agents"
            element={
              <ProtectedRoute>
                <AgentsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/strategies"
            element={
              <ProtectedRoute>
                <StrategiesPage />
              </ProtectedRoute>
            }
          />
          <Route path="/debt" element={<Navigate to="/market?section=debt" replace />} />
          <Route
            path="/settings"
            element={
              <ProtectedRoute>
                <SettingsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/ai-research"
            element={
              <ProtectedRoute>
                <AIResearchPage />
              </ProtectedRoute>
            }
          />
          {/* Legacy alias — /impact was the former Impact page; redirect in-place
              by rendering AIResearchPage so existing bookmarks still work. */}
          <Route
            path="/impact"
            element={
              <ProtectedRoute>
                <AIResearchPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/channels"
            element={
              <ProtectedRoute>
                <ChannelsPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/metrics"
            element={
              <ProtectedRoute>
                <MetricsDashboard />
              </ProtectedRoute>
            }
          />
          <Route
            path="/tracking"
            element={
              <ProtectedRoute>
                <TrackingPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/my-trades"
            element={
              <ProtectedRoute>
                <MyTradesPage />
              </ProtectedRoute>
            }
          />
          <Route path="/calculators" element={<CalculatorsPage />} />
          <Route path="/roth-ira-conversion" element={<Navigate to="/calculators?tab=retirement_longevity" replace />} />
          <Route path="/college-529" element={<Navigate to="/calculators?tab=college_529" replace />} />
          <Route path="/financial-calculators" element={<Navigate to="/calculators" replace />} />
          <Route path="/agentic-finance" element={<Navigate to="/" replace />} />
          <Route path="/agentic-stock-research" element={<Navigate to="/" replace />} />
          <Route path="/agentic-trading" element={<Navigate to="/" replace />} />
          <Route path="/agentic-quant" element={<Navigate to="/" replace />} />
          <Route path="/finclaw" element={<Navigate to="/" replace />} />
        </Routes>
        </Suspense>
      </AppLayout>
    </AuthProvider>
  );
}
