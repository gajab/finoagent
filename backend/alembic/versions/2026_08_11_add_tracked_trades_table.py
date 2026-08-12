"""add tracked_trades table (Track & Manage lifecycle)

Revision ID: add_tracked_trades
Revises: add_waitlist_entries
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_tracked_trades'
down_revision: Union[str, Sequence[str], None] = 'add_waitlist_entries'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'tracked_trades',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ticker', sa.String(length=20), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('instrument', sa.String(length=12), nullable=False, server_default='equity'),
        sa.Column('setup_type', sa.String(length=40), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='watching'),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('entry_low', sa.Float(), nullable=True),
        sa.Column('entry_high', sa.Float(), nullable=True),
        sa.Column('entry_level', sa.Float(), nullable=True),
        sa.Column('stop_level', sa.Float(), nullable=True),
        sa.Column('target_levels', sa.Text(), nullable=True),
        sa.Column('setup_snapshot', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('context_snapshot', sa.Text(), nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('executed_price', sa.Float(), nullable=True),
        sa.Column('executed_qty', sa.Float(), nullable=True),
        sa.Column('execution_note', sa.Text(), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('exit_note', sa.Text(), nullable=True),
        sa.Column('realized_pnl', sa.Float(), nullable=True),
        sa.Column('last_eval', sa.Text(), nullable=True),
        sa.Column('last_verdict', sa.String(length=20), nullable=True),
        sa.Column('last_eval_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('user_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tracked_trades_user_id'), 'tracked_trades', ['user_id'], unique=False)
    op.create_index(op.f('ix_tracked_trades_ticker'), 'tracked_trades', ['ticker'], unique=False)
    op.create_index(op.f('ix_tracked_trades_status'), 'tracked_trades', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_tracked_trades_status'), table_name='tracked_trades')
    op.drop_index(op.f('ix_tracked_trades_ticker'), table_name='tracked_trades')
    op.drop_index(op.f('ix_tracked_trades_user_id'), table_name='tracked_trades')
    op.drop_table('tracked_trades')
