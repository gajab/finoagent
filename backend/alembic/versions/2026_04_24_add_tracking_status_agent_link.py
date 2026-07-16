"""Add status to tracked_companies and tracked_company_id to agents.

Revision ID: 9f3c2b8e1a47
Revises: 5be4b3386629
Create Date: 2026-04-24

Changes:
  - tracked_companies.status  VARCHAR(20) NOT NULL DEFAULT 'active'
  - agents.tracked_company_id INTEGER NULL FK tracked_companies.id ON DELETE SET NULL
"""
from alembic import op
import sqlalchemy as sa

revision = '9f3c2b8e1a47'
down_revision = '5be4b3386629'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add status column to tracked_companies
    op.add_column(
        'tracked_companies',
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
    )
    op.create_index('ix_tracked_companies_status', 'tracked_companies', ['status'])

    # 2. Add tracked_company_id to agents (nullable — not all agents come from tracking)
    op.add_column(
        'agents',
        sa.Column('tracked_company_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_agents_tracked_company_id',
        'agents', 'tracked_companies',
        ['tracked_company_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_agents_tracked_company_id', 'agents', ['tracked_company_id'])


def downgrade() -> None:
    op.drop_index('ix_agents_tracked_company_id', table_name='agents')
    op.drop_constraint('fk_agents_tracked_company_id', 'agents', type_='foreignkey')
    op.drop_column('agents', 'tracked_company_id')

    op.drop_index('ix_tracked_companies_status', table_name='tracked_companies')
    op.drop_column('tracked_companies', 'status')
