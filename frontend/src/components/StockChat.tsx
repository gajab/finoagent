import React, { useState, useEffect, useCallback, useRef } from 'react';
import { MessageSquare, Send, Loader2, Trash2, AlertTriangle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchStockNotes, askStockQuestion } from '../api';
import type { StockNote } from '../types';

interface StockChatProps {
  ticker: string;
}

function formatTime(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function StockChat({ ticker }: StockChatProps) {
  const [notes, setNotes] = useState<StockNote[]>([]);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadNotes = useCallback(async () => {
    try {
      setLoadingHistory(true);
      const data = await fetchStockNotes(ticker);
      setNotes(data);
    } catch (err) {
      // Silently fail on initial load — notes may not exist yet
    } finally {
      setLoadingHistory(false);
    }
  }, [ticker]);

  useEffect(() => {
    loadNotes();
  }, [loadNotes]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [notes, loading]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || loading) return;

    setError(null);
    setLoading(true);
    setQuestion('');

    // Optimistically add user message
    const tempUserNote: StockNote = {
      id: Date.now(),
      role: 'user',
      content: q,
      created_at: new Date().toISOString(),
    };
    setNotes((prev) => [...prev, tempUserNote]);

    try {
      const response = await askStockQuestion(ticker, q);
      setNotes(response.notes);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to get response');
      // Remove optimistic message on error
      setNotes((prev) => prev.filter((n) => n.id !== tempUserNote.id));
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <h2 className="font-bold text-sm flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-primary" />
          Ask About {ticker}
        </h2>
        <p className="text-xs text-base-content/50 -mt-1">
          Ask questions with full stock context — your conversation is saved for future reference.
        </p>

        {/* Chat History */}
        <div className="bg-base-200/40 rounded-xl border border-white/[0.03] mt-2 max-h-[400px] overflow-y-auto p-3 space-y-3 min-h-[120px]">
          {loadingHistory && (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
            </div>
          )}

          {!loadingHistory && notes.length === 0 && (
            <div className="text-center py-8 text-base-content/40">
              <MessageSquare className="w-8 h-8 mx-auto mb-2 opacity-40" />
              <p className="text-sm">No questions yet. Ask anything about {ticker} — your conversation includes real-time stock data, technicals, and your portfolio context.</p>
            </div>
          )}

          {notes.map((note) => (
            <div
              key={note.id}
              className={`flex ${note.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 ${
                  note.role === 'user'
                    ? 'bg-primary/15 text-base-content'
                    : 'bg-base-100 text-base-content'
                }`}
              >
                {note.role === 'assistant' ? (
                  <div className="prose prose-sm prose-invert max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {note.content}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <p className="text-sm whitespace-pre-wrap">{note.content}</p>
                )}
                <p className="text-[10px] text-base-content/30 mt-1">
                  {formatTime(note.created_at)}
                </p>
              </div>
            </div>
          ))}

          {/* Loading indicator */}
          {loading && (
            <div className="flex justify-start">
              <div className="bg-base-100 rounded-lg px-3 py-2 flex items-center gap-2 text-sm text-base-content/60">
                <Loader2 className="w-4 h-4 animate-spin text-info" />
                Analyzing with full stock context...
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* Error */}
        {error && (
          <div className="alert alert-error alert-sm py-2">
            <AlertTriangle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
            <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}>✕</button>
          </div>
        )}

        {/* Input */}
        <form onSubmit={handleSubmit} className="flex gap-2 mt-1">
          <textarea
            ref={textareaRef}
            className="textarea textarea-bordered flex-1 min-h-[44px] max-h-[100px] resize-none text-sm"
            placeholder={`Ask about ${ticker} — e.g. "What are the tax implications of selling?" or "Is now a good entry point?"`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
            rows={1}
          />
          <button
            type="submit"
            className="btn btn-primary btn-square"
            disabled={loading || !question.trim()}
          >
            {loading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
