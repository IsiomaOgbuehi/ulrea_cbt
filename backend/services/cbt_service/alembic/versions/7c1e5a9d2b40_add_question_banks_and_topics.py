"""add question banks and topics

Revision ID: 7c1e5a9d2b40
Revises: 2fabab7d6959
Create Date: 2026-10-25 17:00:00.000000

Banks (class/level, past paper, custom) and topics inside a subject.
Purely additive: new tables plus nullable bank_id columns on items and exams,
so existing questions and exams are untouched (bank_id NULL = no bank).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c1e5a9d2b40'
down_revision: Union[str, Sequence[str], None] = '2fabab7d6959'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'question_banks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('subject_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('kind', sa.String(length=20), server_default='level', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('year', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'name', name='uq_question_banks_subject_name'),
    )
    op.create_index(op.f('ix_question_banks_org_id'), 'question_banks', ['org_id'], unique=False)
    op.create_index(op.f('ix_question_banks_subject_id'), 'question_banks', ['subject_id'], unique=False)

    op.create_table(
        'topics',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('subject_id', sa.Uuid(), nullable=False),
        sa.Column('parent_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['topics.id']),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_topics_org_id'), 'topics', ['org_id'], unique=False)
    op.create_index(op.f('ix_topics_subject_id'), 'topics', ['subject_id'], unique=False)
    op.create_index(op.f('ix_topics_parent_id'), 'topics', ['parent_id'], unique=False)

    op.create_table(
        'item_topics',
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('topic_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['items.id']),
        sa.ForeignKeyConstraint(['topic_id'], ['topics.id']),
        sa.PrimaryKeyConstraint('item_id', 'topic_id'),
    )
    op.create_index(op.f('ix_item_topics_topic_id'), 'item_topics', ['topic_id'], unique=False)

    op.create_table(
        'exam_topics',
        sa.Column('exam_id', sa.Uuid(), nullable=False),
        sa.Column('topic_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['exam_id'], ['exams.id']),
        sa.ForeignKeyConstraint(['topic_id'], ['topics.id']),
        sa.PrimaryKeyConstraint('exam_id', 'topic_id'),
    )
    op.create_index(op.f('ix_exam_topics_topic_id'), 'exam_topics', ['topic_id'], unique=False)

    op.add_column('items', sa.Column('bank_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_items_bank_id'), 'items', ['bank_id'], unique=False)
    op.create_foreign_key('fk_items_bank_id_question_banks', 'items', 'question_banks', ['bank_id'], ['id'])

    op.add_column('exams', sa.Column('bank_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_exams_bank_id'), 'exams', ['bank_id'], unique=False)
    op.create_foreign_key('fk_exams_bank_id_question_banks', 'exams', 'question_banks', ['bank_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_exams_bank_id_question_banks', 'exams', type_='foreignkey')
    op.drop_index(op.f('ix_exams_bank_id'), table_name='exams')
    op.drop_column('exams', 'bank_id')

    op.drop_constraint('fk_items_bank_id_question_banks', 'items', type_='foreignkey')
    op.drop_index(op.f('ix_items_bank_id'), table_name='items')
    op.drop_column('items', 'bank_id')

    op.drop_index(op.f('ix_exam_topics_topic_id'), table_name='exam_topics')
    op.drop_table('exam_topics')
    op.drop_index(op.f('ix_item_topics_topic_id'), table_name='item_topics')
    op.drop_table('item_topics')
    op.drop_index(op.f('ix_topics_parent_id'), table_name='topics')
    op.drop_index(op.f('ix_topics_subject_id'), table_name='topics')
    op.drop_index(op.f('ix_topics_org_id'), table_name='topics')
    op.drop_table('topics')
    op.drop_index(op.f('ix_question_banks_subject_id'), table_name='question_banks')
    op.drop_index(op.f('ix_question_banks_org_id'), table_name='question_banks')
    op.drop_table('question_banks')
