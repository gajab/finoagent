import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiBase } from '../api';
import {
  Settings, LogOut, LayoutDashboard, Briefcase, Bot, Layers, Zap, Share2,
  Menu, X, Crown, Lock, Globe, Bookmark, AlignJustify, ClipboardList, Sparkles, Landmark, Calculator, Crosshair,
} from 'lucide-react';
import { TickerInput } from './TickerInput';

// ---------------------------------------------------------------------------
// Nav item definitions
// ---------------------------------------------------------------------------

const PRIMARY_NAV = [
  { path: '/market',      icon: Globe,            label: 'Markets',     premiumOnly: false },
  { path: '/portfolio',   icon: Briefcase,        label: 'Portfolio',   premiumOnly: false },
  { path: '/tracking',    icon: Bookmark,         label: 'Tracking',    premiumOnly: false },
  { path: '/trade-tracking', icon: Crosshair,     label: 'Trade Tracker', premiumOnly: false },
  { path: '/my-trades',   icon: ClipboardList,    label: 'Derivative Trades',      premiumOnly: false },
  { path: '/ai-research', icon: Sparkles,         label: 'AI Research', premiumOnly: false },
  { path: '/agents',      icon: Bot,              label: 'Agents',      premiumOnly: true  },
  { path: '/strategies',  icon: Layers,           label: 'Strategies',  premiumOnly: true  },
];

const EVERYTHING_NAV = [
  { path: '/dashboard',   icon: LayoutDashboard, label: 'Dashboard',   premiumOnly: false, desc: 'Stock research & analysis' },
  { path: '/market',      icon: Globe,            label: 'Markets',     premiumOnly: false, desc: 'Live market overview' },
  { path: '/tracking',    icon: Bookmark,         label: 'Tracking',    premiumOnly: false, desc: 'Pick & shovel watchlist' },
  { path: '/ai-research', icon: Sparkles,         label: 'AI Research', premiumOnly: false, desc: 'What-if, event impact & pick-and-shovel', accent: true },
  { path: '/my-trades',   icon: ClipboardList,    label: 'My Trades',   premiumOnly: false, desc: 'Trade journal with live P&L' },
  { path: '/trade-tracking', icon: Crosshair,     label: 'Trade Tracker', premiumOnly: false, desc: 'Track setups to entry, manage to exit', accent: true },
  { path: '/calculators', icon: Calculator,       label: 'Calculators', premiumOnly: false, desc: 'Financial planning calculators' },
  { path: '/strategies',  icon: Layers,           label: 'Strategies',  premiumOnly: true,  desc: 'Long/short & options strategies' },
  { path: '/debt',        icon: Landmark,         label: 'Debt Radar',  premiumOnly: true,  desc: 'Bond/CLO entry-timing tracker' },
  { path: '/agents',      icon: Bot,              label: 'Agents',      premiumOnly: true,  desc: 'Automated monitoring agents' },
  { path: '/portfolio',   icon: Briefcase,        label: 'Portfolio',   premiumOnly: false, desc: 'Holdings & performance' },
  { path: '/channels',    icon: Share2,           label: 'Channels',    premiumOnly: false, desc: 'Research distribution' },
  { path: '/settings',    icon: Settings,         label: 'Settings',    premiumOnly: false, desc: 'API keys & preferences' },
];

export default function Navbar() {
  const { user, logout, isPremium, isAuthenticated } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const handleGuestClick = (e: React.MouseEvent, path: string) => {
    if (!isAuthenticated && path !== '/calculators') {
      e.preventDefault();
      setMobileOpen(false);
      navigate('/');
    }
  };

  const [mobileOpen, setMobileOpen]           = useState(false);
  const [everythingOpen, setEverythingOpen]   = useState(false);
  const [showPremiumToast, setShowPremiumToast] = useState(false);

  const everythingRef = useRef<HTMLDivElement>(null);

  // Close "everything" dropdown on outside click
  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (everythingRef.current && !everythingRef.current.contains(e.target as Node)) {
        setEverythingOpen(false);
      }
    }
    if (everythingOpen) document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [everythingOpen]);

  // Close everything dropdown on route change
  useEffect(() => { setEverythingOpen(false); }, [location.pathname]);

  const handleLogout = async () => { await logout(); navigate('/'); };

  const handleGlobalSearch = (ticker: string) => {
    navigate(`/dashboard?ticker=${ticker}`);
    setMobileOpen(false);
  };

  const isActive = (path: string) => {
    if (path.includes('?')) {
      return location.pathname + location.search === path;
    }
    return location.pathname === path;
  };

  const handlePremiumClick = (e: React.MouseEvent) => {
    e.preventDefault();
    setShowPremiumToast(true);
    setTimeout(() => setShowPremiumToast(false), 4000);
  };

  // Reuse for both primary and everything lists
  const renderNavItem = (item: typeof PRIMARY_NAV[0], compact = true) => {
    const Icon = item.icon;
    const active = isActive(item.path);
    const locked = item.premiumOnly && !isPremium && isAuthenticated;

    if (locked) {
      return (
        <button
          key={item.path}
          onClick={handlePremiumClick}
          className={`flex items-center gap-1.5 rounded-xl text-xs font-medium transition-all duration-200
            text-base-content/35 hover:text-yellow-400/70 hover:bg-yellow-400/5
            border border-transparent cursor-pointer relative group
            ${compact ? 'px-3 py-2' : 'px-3 py-2.5 w-full'}`}
          title="Premium feature"
        >
          <Icon className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="whitespace-nowrap">{item.label}</span>
          <Lock className="w-2.5 h-2.5 opacity-60 group-hover:opacity-100 transition-opacity" />
        </button>
      );
    }

    return (
      <Link
        key={item.path}
        to={item.path}
        onClick={(e) => handleGuestClick(e, item.path)}
        className={`flex items-center gap-1.5 rounded-xl text-xs font-medium transition-all duration-200
          ${active
            ? 'bg-primary/15 text-primary shadow-sm shadow-primary/10 border border-primary/15'
            : `text-base-content/60 hover:text-base-content hover:bg-base-200/50 border border-transparent
               ${'accent' in item && item.accent ? 'text-warning/80 hover:text-warning' : ''}`
          }
          ${compact ? 'px-3 py-2' : 'px-3 py-2.5 w-full'}`}
      >
        <Icon className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="whitespace-nowrap">{item.label}</span>
      </Link>
    );
  };

  return (
    <>
      {/* Premium upgrade toast */}
      {showPremiumToast && (
        <div className="fixed top-20 left-1/2 -translate-x-1/2 z-[9999] animate-fade-in-down">
          <div className="flex items-center gap-3 bg-gray-950 border border-yellow-400/30 text-white px-5 py-3 rounded-2xl shadow-[0_8px_32px_rgba(0,0,0,0.8)]">
            <Crown className="w-4 h-4 text-yellow-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-semibold text-yellow-400">Premium Feature</p>
              <p className="text-xs text-gray-300">Please contact the admin to upgrade to a Premium account.</p>
            </div>
          </div>
        </div>
      )}

      <nav className="sticky top-0 z-50 w-full border-b border-white/[0.06] bg-base-100/80 backdrop-blur-xl">
        <div className="container-app">
          <div className="flex h-16 items-center justify-between gap-4">

            {/* ── Left: Logo + Search ── */}
            <div className="flex items-center gap-4 flex-shrink-0">
              <Link to={isAuthenticated ? "/dashboard" : "/"} className="flex items-center gap-2.5 group">
                <img
                  src="/assets/finoagent_logo.png"
                  alt="FinoAgent"
                  className="h-9 w-auto rounded-xl transition-transform duration-200 group-hover:scale-105"
                />
                <span className="hidden lg:block text-sm font-bold tracking-tight text-gradient-primary">
                  FinoAgent
                </span>
              </Link>

              <div className="hidden md:block w-64 lg:w-80">
                <div className="relative">
                  <TickerInput onSearch={handleGlobalSearch} loading={false} />
                </div>
              </div>
            </div>

            {/* ── Center: Primary 5 nav links ── */}
            <div className="hidden lg:flex items-center gap-1">
              {PRIMARY_NAV.map(item => renderNavItem(item, true))}
            </div>

            {/* ── Right: Everything menu + User + Mobile toggle ── */}
            <div className="flex items-center gap-2">

              {/* Everything (≡) dropdown */}
              <div ref={everythingRef} className="relative hidden lg:block">
                <button
                  onClick={() => setEverythingOpen(v => !v)}
                  className={`flex items-center justify-center w-9 h-9 rounded-xl
                    transition-all duration-200 border
                    ${everythingOpen
                      ? 'bg-base-200 text-base-content border-white/10 shadow-inner'
                      : 'text-base-content/60 hover:text-base-content hover:bg-base-200/50 border-transparent'
                    }`}
                  title="All pages"
                >
                  <Menu className="w-4 h-4" />
                </button>

                {everythingOpen && (
                  <div className="absolute right-0 top-[calc(100%+8px)] w-72 rounded-2xl border border-white/[0.08] bg-base-100/95 backdrop-blur-xl shadow-[0_16px_48px_rgba(0,0,0,0.6)] z-[200] overflow-hidden animate-fade-in-down">
                    <div className="px-3 pt-3 pb-1">
                      <p className="text-[10px] uppercase tracking-widest text-base-content/30 font-semibold px-1 mb-1">
                        All Pages
                      </p>
                    </div>
                    <div className="px-2 pb-3 space-y-0.5">
                      {EVERYTHING_NAV.map(item => {
                        const Icon = item.icon;
                        const active = isActive(item.path);
                        const locked = item.premiumOnly && !isPremium && isAuthenticated;

                        if (locked) {
                          return (
                            <button
                              key={item.path}
                              onClick={handlePremiumClick}
                              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl
                                text-base-content/35 hover:text-yellow-400/70 hover:bg-yellow-400/5
                                transition-all duration-150 group text-left"
                            >
                              <Icon className="w-4 h-4 flex-shrink-0" />
                              <div className="flex-1 min-w-0">
                                <div className="text-xs font-medium flex items-center gap-1.5">
                                  {item.label}
                                  <Lock className="w-2.5 h-2.5 opacity-50" />
                                </div>
                                <div className="text-[10px] text-base-content/30 truncate">{item.desc}</div>
                              </div>
                            </button>
                          );
                        }

                        return (
                          <Link
                            key={item.path}
                            to={item.path}
                            onClick={(e) => handleGuestClick(e, item.path)}
                            className={`flex items-center gap-3 px-3 py-2.5 rounded-xl
                              transition-all duration-150
                              ${active
                                ? 'bg-primary/15 text-primary'
                                : `text-base-content/70 hover:text-base-content hover:bg-base-200/60
                                   ${'accent' in item && item.accent ? 'hover:text-warning' : ''}`
                              }`}
                          >
                            <Icon className={`w-4 h-4 flex-shrink-0 ${active ? 'text-primary' : ''}`} />
                            <div className="flex-1 min-w-0">
                              <div className={`text-xs font-medium ${'accent' in item && item.accent && !active ? 'text-warning/80' : ''}`}>
                                {item.label}
                              </div>
                              <div className="text-[10px] text-base-content/40 truncate">{item.desc}</div>
                            </div>
                            {active && (
                              <div className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0" />
                            )}
                          </Link>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {/* User section */}
              {user && (
                <div className="hidden md:flex items-center gap-3 pl-2 border-l border-white/[0.06]">
                  <div className="flex flex-col items-end">
                    <span className="text-xs font-semibold text-base-content/90 flex items-center gap-1">
                      {user.name}
                      {isPremium && (
                        <span title="Premium"><Crown className="w-3 h-3 text-yellow-400" /></span>
                      )}
                    </span>
                    <span className="text-[10px] text-base-content/40">{user.email}</span>
                  </div>
                  {user.picture ? (
                    <div className="avatar">
                      <div className="w-8 h-8 rounded-xl ring-2 ring-primary/20 ring-offset-1 ring-offset-base-100 overflow-hidden">
                        <img src={user.picture} alt={user.name} referrerPolicy="no-referrer" className="w-full h-full object-cover" />
                      </div>
                    </div>
                  ) : (
                    <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-white text-sm font-bold">
                      {user.name?.[0] || '?'}
                    </div>
                  )}
                  <button
                    onClick={handleLogout}
                    className="btn btn-ghost btn-sm btn-square rounded-xl text-base-content/40 hover:text-error hover:bg-error/10"
                    title="Logout"
                  >
                    <LogOut className="w-4 h-4" />
                  </button>
                </div>
              )}

              {!isAuthenticated && (
                <div className="hidden md:flex items-center gap-3 pl-2 border-l border-white/[0.06]">
                  <a
                    href={`${apiBase}/api/auth/google`}
                    className="btn btn-secondary btn-sm rounded-xl font-semibold shadow-md shadow-secondary/15 flex items-center gap-1.5 hover:scale-[1.02] active:scale-[0.98] transition-transform"
                  >
                    Sign In
                  </a>
                </div>
              )}

              {/* Mobile menu button */}
              <button
                className="lg:hidden btn btn-ghost btn-sm btn-square rounded-xl"
                onClick={() => setMobileOpen(!mobileOpen)}
              >
                {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
              </button>
            </div>
          </div>
        </div>

        {/* ── Mobile Drawer ── */}
        {mobileOpen && (
          <div className="lg:hidden border-t border-white/[0.04] bg-base-100/95 backdrop-blur-xl animate-fade-in-down">
            <div className="container-app py-4 space-y-3">
              {/* Mobile Search */}
              <div className="md:hidden">
                <TickerInput onSearch={handleGlobalSearch} loading={false} />
              </div>

              {/* Mobile Nav Links — full list */}
              <div className="grid grid-cols-2 gap-1.5">
                {EVERYTHING_NAV.map((item) => {
                  const Icon = item.icon;
                  const active = isActive(item.path);
                  const locked = item.premiumOnly && !isPremium && isAuthenticated;

                  if (locked) {
                    return (
                      <button
                        key={item.path}
                        onClick={(e) => { handlePremiumClick(e); setMobileOpen(false); }}
                        className="flex items-center gap-2.5 px-4 py-3 rounded-xl text-sm font-medium
                          transition-all duration-200 text-base-content/35 hover:text-yellow-400/70
                          hover:bg-yellow-400/5 border border-transparent w-full text-left"
                      >
                        <Icon className="w-4 h-4" />
                        {item.label}
                        <Lock className="w-3 h-3 ml-auto opacity-50" />
                      </button>
                    );
                  }

                  return (
                    <Link
                      key={item.path}
                      to={item.path}
                      onClick={(e) => handleGuestClick(e, item.path)}
                      className={`flex items-center gap-2.5 px-4 py-3 rounded-xl text-sm font-medium
                        transition-all duration-200
                        ${active
                          ? 'bg-primary/15 text-primary border border-primary/15'
                          : 'text-base-content/60 hover:bg-base-200/50 border border-transparent'
                        }`}
                    >
                      <Icon className="w-4 h-4" />
                      {item.label}
                    </Link>
                  );
                })}
              </div>

              {/* Mobile User Section */}
              {user && (
                <div className="flex items-center justify-between pt-3 border-t border-white/[0.04]">
                  <div className="flex items-center gap-3">
                    {user.picture ? (
                      <img src={user.picture} alt={user.name} className="w-8 h-8 rounded-xl" referrerPolicy="no-referrer" />
                    ) : (
                      <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-white text-sm font-bold">
                        {user.name?.[0] || '?'}
                      </div>
                    )}
                    <div>
                      <div className="text-sm font-semibold flex items-center gap-1">
                        {user.name}
                        {isPremium && <Crown className="w-3 h-3 text-yellow-400" />}
                      </div>
                      <div className="text-xs text-base-content/40">{user.email}</div>
                    </div>
                  </div>
                  <button onClick={handleLogout} className="btn btn-ghost btn-sm text-error gap-1.5">
                    <LogOut className="w-4 h-4" />
                    Logout
                  </button>
                </div>
              )}

              {!isAuthenticated && (
                <div className="pt-3 border-t border-white/[0.04]">
                  <a
                    href={`${apiBase}/api/auth/google`}
                    className="btn btn-secondary btn-sm w-full rounded-xl font-medium shadow-md flex items-center justify-center gap-2"
                  >
                    Sign In with Google
                  </a>
                </div>
              )}
            </div>
          </div>
        )}
      </nav>
    </>
  );
}
