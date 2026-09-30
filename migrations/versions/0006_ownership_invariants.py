"""ownership invariants: ownerless documents are shared, owners cannot be deleted from under their
documents, usernames are unique case-insensitively

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 08:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rows written after 0005 by maintenance scripts could be ownerless but private (visible to nobody).
    op.execute('UPDATE documents SET is_shared = true WHERE owner_id IS NULL')
    op.create_check_constraint('ck_documents_ownerless_shared', 'documents', 'owner_id IS NOT NULL OR is_shared')
    # SET NULL would have turned a deleted user's private documents into shared ones.
    op.drop_constraint('fk_documents_owner_id_users', 'documents', type_='foreignkey')
    op.create_foreign_key('fk_documents_owner_id_users', 'documents', 'users', ['owner_id'], ['id'],
                          ondelete='RESTRICT')
    op.execute('UPDATE users SET username = lower(username)')
    op.create_index('uq_users_username_lower', 'users', [sa.text('lower(username)')], unique=True)


def downgrade() -> None:
    op.drop_index('uq_users_username_lower', table_name='users')
    op.drop_constraint('fk_documents_owner_id_users', 'documents', type_='foreignkey')
    op.create_foreign_key('fk_documents_owner_id_users', 'documents', 'users', ['owner_id'], ['id'],
                          ondelete='SET NULL')
    op.drop_constraint('ck_documents_ownerless_shared', 'documents', type_='check')
