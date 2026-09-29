"""ingest job step timings

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30 01:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ingest_jobs', sa.Column(
        'timings', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))


def downgrade() -> None:
    op.drop_column('ingest_jobs', 'timings')
