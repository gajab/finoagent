"""add portfolio highlight dismissals table

Revision ID: 46cf08ef53f5
Revises: 20260609001
Create Date: 2026-06-10 15:16:31.760319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46cf08ef53f5'
down_revision: Union[str, Sequence[str], None] = '20260609001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('portfolio_highlight_dismissals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('ticker', sa.String(length=20), nullable=False),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_portfolio_highlight_dismissals_ticker'), 'portfolio_highlight_dismissals', ['ticker'], unique=False)
    op.create_index(op.f('ix_portfolio_highlight_dismissals_user_id'), 'portfolio_highlight_dismissals', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_portfolio_highlight_dismissals_user_id'), table_name='portfolio_highlight_dismissals')
    op.drop_index(op.f('ix_portfolio_highlight_dismissals_ticker'), table_name='portfolio_highlight_dismissals')
    op.drop_table('portfolio_highlight_dismissals')

