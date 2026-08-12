"""SQLAlchemy ORM models."""

import datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    google_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    whatsapp_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True, index=True)
    whatsapp_thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    api_keys: Mapped[list["UserApiKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    agents: Mapped[list["Agent"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    stock_notes: Mapped[list["StockNote"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    guru_analyses: Mapped[list["GuruAnalysis"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    stock_analyses: Mapped[list["StockAnalysis"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tlh_saved_portfolios: Mapped[list["TLHSavedPortfolio"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    broker_connections: Mapped[list["BrokerConnection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    broker_orders: Mapped[list["BrokerOrder"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    saved_strategies: Mapped[list["SavedStrategy"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tracked_companies: Mapped[list["TrackedCompany"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tracked_trades: Mapped[list["TrackedTrade"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    highlight_dismissals: Mapped[list["PortfolioHighlightDismissal"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserApiKey(Base):
    __tablename__ = "user_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key_name: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="api_keys")

    __table_args__ = (
        # Each user can only have one key per key_name
        {"sqlite_autoincrement": True},
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    last_activity: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship(back_populates="sessions")


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="My Portfolio")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="portfolios")
    holdings: Mapped[list["PortfolioHolding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    portfolio_transactions: Mapped[list["PortfolioTransaction"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="STOCK")
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    cost_basis: Mapped[float] = mapped_column(Float, nullable=False)  # avg cost per share
    purchase_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    realized_gain_loss: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    total_invested: Mapped[float | None] = mapped_column(Float, nullable=True)  # total dollars ever invested
    first_buy_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")
    transactions: Mapped[list["PortfolioTransaction"]] = relationship(
        back_populates="holding", cascade="all, delete-orphan"
    )


class PortfolioTransaction(Base):
    """Individual buy/sell/option transaction records for a holding."""
    __tablename__ = "portfolio_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolio_holdings.id", ondelete="CASCADE"), nullable=False
    )
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)  # BUY, SELL, OPTION_BUY, OPTION_SELL, TRANSFER_IN, TRANSFER_OUT
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    price_per_share: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    holding: Mapped["PortfolioHolding"] = relationship(back_populates="transactions")
    portfolio: Mapped["Portfolio"] = relationship(back_populates="portfolio_transactions")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cloned_from_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    # Non-null when this agent was created from a tracked company card.
    # SET NULL on delete so the agent survives if the company is untracked.
    tracked_company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tracked_companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    send_email_on_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_report_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_run_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="agents")
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", order_by="AgentRun.created_at.desc()"
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent: Mapped["Agent"] = relationship(back_populates="runs")


class StockNote(Base):
    __tablename__ = "stock_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'user' or 'assistant'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="stock_notes")


class GuruAnalysis(Base):
    __tablename__ = "guru_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    guru_id: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. 'warren_buffett'
    guru_name: Mapped[str] = mapped_column(String(100), nullable=False)
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="guru_analyses")


class StockAnalysis(Base):
    """Persisted LLM-generated analyses (qualitative, macro, etc.) per user+ticker+type."""
    __tablename__ = "stock_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    analysis_type: Mapped[str] = mapped_column(String(50), nullable=False)  # 'qualitative' or 'macro'
    analysis: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="stock_analyses")


class TLHSavedPortfolio(Base):
    """Saved TLH portfolio sets — named collections of holdings for tax-loss harvesting analysis."""
    __tablename__ = "tlh_saved_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=15.0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="tlh_saved_portfolios")
    holdings: Mapped[list["TLHSavedHolding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class TLHSavedHolding(Base):
    """Individual holding within a saved TLH portfolio."""
    __tablename__ = "tlh_saved_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tlh_saved_portfolios.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_basis: Mapped[float] = mapped_column(Float, nullable=False)

    portfolio: Mapped["TLHSavedPortfolio"] = relationship(back_populates="holdings")


class BrokerConnection(Base):
    """User's broker connection configuration. Credentials stored as encrypted UserApiKey entries."""
    __tablename__ = "broker_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "interactive_brokers"
    account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # discovered from broker
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_connected_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="broker_connections")

    __table_args__ = (
        # One connection per user per broker type
        {"sqlite_autoincrement": True},
    )


class BrokerOrder(Base):
    """Audit trail for orders placed through broker integrations."""
    __tablename__ = "broker_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_connection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("broker_connections.id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)  # "dual_direction_buffer"
    order_data: Mapped[str] = mapped_column(Text, nullable=False)  # JSON of all legs
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    broker_order_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of IB order IDs
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="broker_orders")
    broker_connection: Mapped["BrokerConnection"] = relationship()


class SavedStrategy(Base):
    """User-saved strategy configurations with leg prices at save time."""
    __tablename__ = "saved_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "dual_direction_buffer" | "box_spread"
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    parameters: Mapped[str] = mapped_column(Text, nullable=False)       # JSON — input params
    legs_data: Mapped[str] = mapped_column(Text, nullable=False)        # JSON — legs with bid/ask/mid/price
    result_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON — computed result summary
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Trade tracking fields
    trade_status: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # None=research, "active", "closed"
    entry_prices: Mapped[str | None] = mapped_column(Text, nullable=True)       # JSON — per-leg entry prices
    entry_date: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_net_debit: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "ibkr" | "manual"
    broker_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("broker_orders.id", ondelete="SET NULL"), nullable=True
    )
    exit_date: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_prices: Mapped[str | None] = mapped_column(Text, nullable=True)        # JSON — per-leg exit prices
    exit_net: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="saved_strategies")
    broker_order: Mapped["BrokerOrder | None"] = relationship()
    transactions: Mapped[list["TradeTransaction"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="TradeTransaction.executed_at.asc()",
    )


class TradeTransaction(Base):
    """Append-only ledger of buy/sell/adjust actions against a SavedStrategy position.

    One ``SavedStrategy`` row is the *position header*; every add-to, partial-close,
    or full-close writes a row here. This is the source of truth for tax-lot
    accounting, average cost, and total quantity. The header's entry_net_debit
    remains for back-compat with existing views but derived values should be
    computed from the ledger.
    """

    __tablename__ = "trade_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("saved_strategies.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Semantic action. 'open' = initial entry. 'add' = increase size. 'reduce' = partial close.
    # 'close' = full exit. 'adjust' = roll/repair (qty may be zero, price may reflect net debit/credit).
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # For multi-leg strategies, which leg this transaction touches. None = applies to whole position.
    leg_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Quantity in the natural unit for the strategy (shares for stock, contracts for options).
    # Signed: positive = long added / short reduced; negative = short added / long reduced.
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    # Per-unit price paid (stock: $/share, options: $/share of premium — ×100 for contract cost).
    price: Mapped[float] = mapped_column(Float, nullable=False)
    # Commissions + exchange fees. Always non-negative.
    fees: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    executed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="manual")  # manual | ibkr
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    strategy: Mapped["SavedStrategy"] = relationship(back_populates="transactions")


class ApiMetric(Base):
    """Log of external API calls for rate limiting and observability."""
    __tablename__ = "api_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # 'ibkr', 'yfinance', 'openai', etc.
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class TrackedCompany(Base):
    """A company the user has explicitly chosen to track from Pick & Shovel research.

    Stores the full LLM-discovered context (thesis, catalysts, hidden link, etc.)
    alongside refreshable price data and user-written notes — all in one place.
    """
    __tablename__ = "tracked_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Theme context
    theme_raw: Mapped[str] = mapped_column(Text, nullable=False)   # original theme string
    theme_slug: Mapped[str] = mapped_column(String(80), nullable=False)   # short ≤50 char label
    theme_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Exchange / sector
    exchange: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # LLM-discovered data (JSON blob)
    llm_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # thesis, catalysts, hidden_link, etc.

    # Live financial data (refreshed on demand, JSON blob)
    financial_data: Mapped[str | None] = mapped_column(Text, nullable=True)    # price, pe_ratio, week52_*, market_cap
    last_price_refresh: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # User notes (plain text)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Source tier (direct | enabler | deep | deeper)
    source_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    depth_level: Mapped[int | None] = mapped_column(Integer, nullable=True)   # None = initial 3 tiers

    # Lifecycle state: 'active' (in focus list) or 'on_hold' (archived, not deleted)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="tracked_companies")


class TrackedTrade(Base):
    """A trade setup the user has chosen to actively track through its lifecycle.

    Lifecycle:
      • ``watching``     — waiting at/near entry; each Refresh re-checks the secondary
                           entry confirmations (LTF CHOCH, VWAP σ-bands, rejection wick,
                           1H RSI, CVD, level integrity …) → verdict EXECUTE / WAIT / INVALID.
      • ``in_progress``  — the user executed; Refresh now checks exit conditions
                           (target, stop, CHOCH-against, VWAP reclaim, gamma flip, trail)
                           → verdict HOLD / SCALE-OUT / EXIT / TIGHTEN.
      • ``closed``       — exited; realized P&L booked.
      • ``invalidated``  — the setup died before entry (conditions changed).

    The whole setup is snapshotted as JSON at track-time so the plan stays stable even as
    live TA drifts; each Refresh caches its verdict + full payload in ``last_eval``.
    """
    __tablename__ = "tracked_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)   # long | short | neutral
    instrument: Mapped[str] = mapped_column(String(12), nullable=False, default="equity")  # equity | options | futures
    setup_type: Mapped[str | None] = mapped_column(String(40), nullable=True)  # trend_continuation | mean_reversion_fade | ...
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="watching", index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Plan levels — denormalized from the snapshot for fast list rendering + eval math.
    entry_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_levels: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON list[float]

    # Snapshots (JSON blobs) captured when the trade was first tracked.
    setup_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="{}")   # the whole setup object
    context_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)         # compact bias/regime/dealer/EM

    # Execution (set when the user presses Execute → moves to in_progress).
    executed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Exit (set when the user presses Close).
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Last refresh evaluation (cached so the list can show the verdict without recomputing).
    last_eval: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON — full eval payload
    last_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True) # execute|wait|invalid|hold|scale_out|exit|tighten
    last_eval_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="tracked_trades")


class DataCache(Base):
    """Generic key-value cache for external API responses (yfinance, etc.).

    ``cache_key``  — unique string identifier, e.g. ``"market:overview"``
                     or ``"sector:data:1m"``.
    ``payload``    — JSON blob of the cached data.
    ``expires_at`` — UTC timestamp; callers skip the row if now > expires_at.
    """
    __tablename__ = "data_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)           # JSON
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortfolioHighlightDismissal(Base):
    """Tickers dismissed by the user in Portfolio Highlights."""
    __tablename__ = "portfolio_highlight_dismissals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    dismissed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="highlight_dismissals")


class WaitlistEntry(Base):
    """Email addresses that have joined the public waitlist."""
    __tablename__ = "waitlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    signed_up_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

