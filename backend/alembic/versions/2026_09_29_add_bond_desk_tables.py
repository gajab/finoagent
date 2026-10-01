"""add bond desk tables (bond_ladders, bond_holdings, bond_profiles)

Revision ID: add_bond_desk
Revises: add_paper_trades
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_bond_desk'
down_revision: Union[str, Sequence[str], None] = 'add_paper_trades'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'bond_ladders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False, server_default='My ladder'),
        sa.Column('ladder_type', sa.String(length=20), nullable=False, server_default='nominal'),
        sa.Column('params', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('plan', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='plan'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bond_ladders_user_id'), 'bond_ladders', ['user_id'], unique=False)

    op.create_table(
        'bond_holdings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ladder_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='held'),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=True),
        sa.Column('issuer', sa.String(length=200), nullable=True),
        sa.Column('cusip', sa.String(length=12), nullable=True),
        sa.Column('ticker', sa.String(length=20), nullable=True),
        sa.Column('face_value', sa.Float(), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=True),
        sa.Column('coupon_rate', sa.Float(), nullable=True),
        sa.Column('coupon_freq', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('day_count', sa.String(length=16), nullable=True),
        sa.Column('issue_date', sa.Date(), nullable=True),
        sa.Column('maturity_date', sa.Date(), nullable=True),
        sa.Column('purchase_date', sa.Date(), nullable=True),
        sa.Column('purchase_price', sa.Float(), nullable=True),
        sa.Column('cost_basis', sa.Float(), nullable=True),
        sa.Column('current_price', sa.Float(), nullable=True),
        sa.Column('price_as_of', sa.Date(), nullable=True),
        sa.Column('call_date', sa.Date(), nullable=True),
        sa.Column('call_price', sa.Float(), nullable=True),
        sa.Column('rating', sa.String(length=8), nullable=True),
        sa.Column('state', sa.String(length=2), nullable=True),
        sa.Column('federal_taxable', sa.Boolean(), nullable=True),
        sa.Column('state_taxable', sa.Boolean(), nullable=True),
        sa.Column('amt', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('tips_ref_cpi', sa.Float(), nullable=True),
        sa.Column('account_type', sa.String(length=16), nullable=False, server_default='taxable'),
        sa.Column('account_name', sa.String(length=100), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ladder_id'], ['bond_ladders.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bond_holdings_user_id'), 'bond_holdings', ['user_id'], unique=False)
    op.create_index(op.f('ix_bond_holdings_ladder_id'), 'bond_holdings', ['ladder_id'], unique=False)
    op.create_index(op.f('ix_bond_holdings_cusip'), 'bond_holdings', ['cusip'], unique=False)

    op.create_table(
        'bond_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('federal_rate', sa.Float(), nullable=False, server_default='24'),
        sa.Column('state', sa.String(length=2), nullable=True),
        sa.Column('state_rate', sa.Float(), nullable=False, server_default='5'),
        sa.Column('niit', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('ltcg_rate', sa.Float(), nullable=False, server_default='15'),
        sa.Column('filing_status', sa.String(length=20), nullable=True),
        sa.Column('inflation_assumption', sa.Float(), nullable=True),
        sa.Column('horizon_years', sa.Float(), nullable=True),
        sa.Column('goals', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('settings', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_bond_profiles_user_id'), 'bond_profiles', ['user_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_bond_profiles_user_id'), table_name='bond_profiles')
    op.drop_table('bond_profiles')
    op.drop_index(op.f('ix_bond_holdings_cusip'), table_name='bond_holdings')
    op.drop_index(op.f('ix_bond_holdings_ladder_id'), table_name='bond_holdings')
    op.drop_index(op.f('ix_bond_holdings_user_id'), table_name='bond_holdings')
    op.drop_table('bond_holdings')
    op.drop_index(op.f('ix_bond_ladders_user_id'), table_name='bond_ladders')
    op.drop_table('bond_ladders')
