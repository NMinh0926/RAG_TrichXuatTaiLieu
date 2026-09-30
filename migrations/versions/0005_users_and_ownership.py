"""users, document ownership and sharing

Documents that existed before accounts have no owner and become shared, so every user keeps seeing them.
The SHA-256 duplicate check becomes per owner.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 02:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('admin', 'user', name='user_role'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('token_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.add_column('documents', sa.Column('owner_id', sa.Uuid(), nullable=True))
    op.add_column('documents', sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute('UPDATE documents SET is_shared = true WHERE owner_id IS NULL')
    op.alter_column('documents', 'is_shared', server_default=None)
    op.create_index(op.f('ix_documents_owner_id'), 'documents', ['owner_id'], unique=False)
    op.create_foreign_key('fk_documents_owner_id_users', 'documents', 'users', ['owner_id'], ['id'], ondelete='SET NULL')
    op.drop_constraint('documents_sha256_key', 'documents', type_='unique')
    op.create_unique_constraint('uq_documents_owner_sha256', 'documents', ['owner_id', 'sha256'],
                                postgresql_nulls_not_distinct=True)
    op.add_column('conversations', sa.Column('owner_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_conversations_owner_id'), 'conversations', ['owner_id'], unique=False)
    op.create_foreign_key('fk_conversations_owner_id_users', 'conversations', 'users', ['owner_id'], ['id'],
                          ondelete='CASCADE')


def downgrade() -> None:
    op.drop_constraint('fk_conversations_owner_id_users', 'conversations', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_owner_id'), table_name='conversations')
    op.drop_column('conversations', 'owner_id')
    op.drop_constraint('uq_documents_owner_sha256', 'documents', type_='unique')
    op.create_unique_constraint('documents_sha256_key', 'documents', ['sha256'])
    op.drop_constraint('fk_documents_owner_id_users', 'documents', type_='foreignkey')
    op.drop_index(op.f('ix_documents_owner_id'), table_name='documents')
    op.drop_column('documents', 'is_shared')
    op.drop_column('documents', 'owner_id')
    op.drop_table('users')
    sa.Enum(name='user_role').drop(op.get_bind(), checkfirst=True)
