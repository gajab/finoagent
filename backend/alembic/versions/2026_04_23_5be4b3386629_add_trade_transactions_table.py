"""add trade_transactions table

Append-only ledger of buy/sell/adjust actions against a SavedStrategy position.
One SavedStrategy row is the position header; every add-to, partial-close, or
full-close writes a row here. This enables tax-lot accounting, proper avg-cost
computation, and a full audit trail of trade activity.

Hand-cleaned from autogen output: local SQLite had a partial schema, so the
raw autogen attempted to (re)create tables that already exist in prod. Only
the trade_transactions table + its index are the real change.

Revision ID: 5be4b3386629
Revises: a1dd97ff3617
Create Date: 2026-04-23 14:14:21.973775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5be4b3386629'
down_revision: Union[str, Sequence[str], None] = 'a1dd97ff3617'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add trade_transactions table + index."""
    op.create_table(
        'trade_transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('strategy_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('leg_index', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('fees', sa.Float(), server_default='0', nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(length=20), server_default='manual', nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['strategy_id'], ['saved_strategies.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_trade_transactions_strategy_id'),
        'trade_transactions',
        ['strategy_id'],
        unique=False,
    )


def downgrade() -> None:
    """Remove trade_transactions table + index."""
    op.drop_index(
        op.f('ix_trade_transactions_strategy_id'),
        table_name='trade_transactions',
    )
    op.drop_table('trade_transactions')
