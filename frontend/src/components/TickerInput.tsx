import React, { useState } from 'react';
import { Search, Loader2 } from 'lucide-react';

interface TickerInputProps {
  onSearch: (ticker: string) => void;
  loading: boolean;
  defaultValue?: string;
}

export const TickerInput: React.FC<TickerInputProps> = ({ onSearch, loading, defaultValue }) => {
  const [value, setValue] = useState(defaultValue || '');
  const [focused, setFocused] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim().toUpperCase();
    if (trimmed) {
      onSearch(trimmed);
    }
  };

  const isEmpty = !value.trim();

  return (
    <form onSubmit={handleSubmit} className="flex gap-2 items-center w-full">
      <div className={`
        flex items-center gap-2 flex-1 px-4 py-2.5 rounded-xl
        bg-base-200/60 border-2 transition-all duration-200
        ${focused
          ? 'border-primary bg-base-200/80 shadow-sm shadow-primary/20'
          : 'border-primary/30 hover:border-primary/50'}
      `}>
        <Search className={`w-4 h-4 flex-shrink-0 transition-colors duration-200 ${focused ? 'text-primary' : 'text-primary/50'}`} />
        <input
          type="text"
          className="bg-transparent flex-1 text-sm outline-none placeholder:text-base-content/40 font-medium"
          placeholder="Search ticker..."
          value={value}
          onChange={(e) => setValue(e.target.value.toUpperCase())}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={loading}
        />
        {value && !loading && (
          <kbd className="hidden sm:inline text-[10px] font-mono px-1.5 py-0.5 rounded bg-base-300/50 text-base-content/30 border border-white/[0.06]">
            ↵
          </kbd>
        )}
      </div>
      <button
        type="submit"
        className={`btn btn-sm rounded-xl px-4 gap-1.5 transition-all duration-200 flex-shrink-0
          ${isEmpty
            ? 'btn-outline border-primary/40 text-primary/50 cursor-not-allowed'
            : 'btn-primary shadow-md shadow-primary/20 hover:shadow-primary/30'
          }`}
        disabled={loading || isEmpty}
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Search className="w-3.5 h-3.5" />
        )}
        <span className="hidden sm:inline">Search</span>
      </button>
    </form>
  );
};
