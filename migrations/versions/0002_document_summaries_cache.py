"""document summaries cache

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29 16:21:02.324068
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('document_summaries',
    sa.Column('document_id', sa.Uuid(), nullable=False),
    sa.Column('model', sa.String(length=255), nullable=False),
    sa.Column('prompt_version', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('document_id')
    )


def downgrade() -> None:
    op.drop_table('document_summaries')
