"""Add portfolio_transactions table and enrich portfolio_holdings.

Revision ID: 20260609001
Revises: 9f3c2b8e1a47
Create Date: 2026-06-09 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text, inspect

revision: str = "20260609001"
down_revision: Union[str, Sequence[str], None] = "9f3c2b8e1a47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.fetchone() is not None


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        ),
        {"t": table},
    )
    return result.fetchone() is not None


def _index_exists(conn, index: str) -> bool:
    result = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = :i"),
        {"i": index},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # --- Add missing columns to portfolio_holdings (idempotent) ---
    if not _column_exists(conn, "portfolio_holdings", "asset_type"):
        op.add_column(
            "portfolio_holdings",
            sa.Column("asset_type", sa.String(20), nullable=False, server_default="STOCK"),
        )
    if not _column_exists(conn, "portfolio_holdings", "realized_gain_loss"):
        op.add_column(
            "portfolio_holdings",
            sa.Column("realized_gain_loss", sa.Float(), nullable=False, server_default="0"),
        )
    if not _column_exists(conn, "portfolio_holdings", "total_invested"):
        op.add_column(
            "portfolio_holdings",
            sa.Column("total_invested", sa.Float(), nullable=True),
        )
    if not _column_exists(conn, "portfolio_holdings", "first_buy_date"):
        op.add_column(
            "portfolio_holdings",
            sa.Column("first_buy_date", sa.Date(), nullable=True),
        )

    # --- Create portfolio_transactions if it doesn't already exist ---
    if not _table_exists(conn, "portfolio_transactions"):
        op.create_table(
            "portfolio_transactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("holding_id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("ticker", sa.String(20), nullable=False),
            sa.Column("transaction_type", sa.String(20), nullable=False),
            sa.Column("shares", sa.Float(), nullable=False),
            sa.Column("price_per_share", sa.Float(), nullable=False),
            sa.Column("fees", sa.Float(), nullable=False, server_default="0"),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["holding_id"], ["portfolio_holdings.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists(conn, "ix_portfolio_transactions_holding_id"):
        op.create_index(
            "ix_portfolio_transactions_holding_id",
            "portfolio_transactions",
            ["holding_id"],
        )
    if not _index_exists(conn, "ix_portfolio_transactions_portfolio_ticker"):
        op.create_index(
            "ix_portfolio_transactions_portfolio_ticker",
            "portfolio_transactions",
            ["portfolio_id", "ticker"],
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_portfolio_transactions_portfolio_ticker"):
        op.drop_index("ix_portfolio_transactions_portfolio_ticker", table_name="portfolio_transactions")
    if _index_exists(conn, "ix_portfolio_transactions_holding_id"):
        op.drop_index("ix_portfolio_transactions_holding_id", table_name="portfolio_transactions")
    if _table_exists(conn, "portfolio_transactions"):
        op.drop_table("portfolio_transactions")

    if _column_exists(conn, "portfolio_holdings", "first_buy_date"):
        op.drop_column("portfolio_holdings", "first_buy_date")
    if _column_exists(conn, "portfolio_holdings", "total_invested"):
        op.drop_column("portfolio_holdings", "total_invested")
    if _column_exists(conn, "portfolio_holdings", "realized_gain_loss"):
        op.drop_column("portfolio_holdings", "realized_gain_loss")
    if _column_exists(conn, "portfolio_holdings", "asset_type"):
        op.drop_column("portfolio_holdings", "asset_type")
