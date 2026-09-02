"""add paper_trades table (Income Desk paper-trading loop)

Revision ID: add_paper_trades
Revises: add_tracked_trades
Create Date: 2026-08-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_paper_trades'
down_revision: Union[str, Sequence[str], None] = 'add_tracked_trades'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'paper_trades',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ticker', sa.String(length=20), nullable=False),
        sa.Column('structure', sa.String(length=40), nullable=False),
        sa.Column('expiration', sa.String(length=20), nullable=True),
        sa.Column('label', sa.String(length=200), nullable=True),
        sa.Column('short_strike', sa.Float(), nullable=True),
        sa.Column('contracts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('legs', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('focus', sa.Text(), nullable=True),
        sa.Column('entry_spot', sa.Float(), nullable=True),
        sa.Column('entry_premium_per_share', sa.Float(), nullable=True),
        sa.Column('entry_credit', sa.Float(), nullable=True),
        sa.Column('placed_snapshot', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('placed_desk_score', sa.Float(), nullable=True),
        sa.Column('placed_algo_grade', sa.String(length=8), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='open'),
        sa.Column('last_eval', sa.Text(), nullable=True),
        sa.Column('last_eval_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_spot', sa.Float(), nullable=True),
        sa.Column('last_value_per_share', sa.Float(), nullable=True),
        sa.Column('last_pnl', sa.Float(), nullable=True),
        sa.Column('last_desk_score', sa.Float(), nullable=True),
        sa.Column('last_algo_grade', sa.String(length=8), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('close_pnl', sa.Float(), nullable=True),
        sa.Column('close_note', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_paper_trades_user_id'), 'paper_trades', ['user_id'], unique=False)
    op.create_index(op.f('ix_paper_trades_ticker'), 'paper_trades', ['ticker'], unique=False)
    op.create_index(op.f('ix_paper_trades_status'), 'paper_trades', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_paper_trades_status'), table_name='paper_trades')
    op.drop_index(op.f('ix_paper_trades_ticker'), table_name='paper_trades')
    op.drop_index(op.f('ix_paper_trades_user_id'), table_name='paper_trades')
    op.drop_table('paper_trades')
