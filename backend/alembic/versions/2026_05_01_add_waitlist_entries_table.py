"""add waitlist entries table

Revision ID: add_waitlist_entries
Revises: 46cf08ef53f5
Create Date: 2026-05-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_waitlist_entries'
down_revision: Union[str, Sequence[str], None] = '46cf08ef53f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — idempotent: skips if table already exists."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'waitlist_entries' not in existing_tables:
        op.create_table(
            'waitlist_entries',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('email', sa.String(length=255), nullable=False),
            sa.Column('signed_up_at', sa.DateTime(timezone=True),
                      server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
            sa.Column('notified', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint('id'),
        )

    # Create index only if it doesn't exist yet
    existing_indexes = {idx['name'] for idx in inspector.get_indexes('waitlist_entries')} \
        if 'waitlist_entries' in existing_tables else set()
    if 'ix_waitlist_entries_email' not in existing_indexes:
        op.create_index('ix_waitlist_entries_email', 'waitlist_entries', ['email'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_waitlist_entries_email', table_name='waitlist_entries')
    op.drop_table('waitlist_entries')
