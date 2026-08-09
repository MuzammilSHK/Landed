"""suppliers as entities, project quantity and assumptions

Revision ID: 9c3f21a4b7de
Revises: 675d58ff0929
Create Date: 2026-08-09 03:10:00.000000

Suppliers used to exist only as a string parsed out of a filename. This gives them a
row, so a supplier is in the comparison because someone said so — and a missing quote
shows as a gap in that supplier's column instead of a supplier that never appeared.

`projects.target_quantity` and `projects.assumptions` move two inputs that were
previously implicit onto the decision itself: the quantity every figure is a function
of, and the freight/duty/insurance/FX the quotations never state.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9c3f21a4b7de'
down_revision: Union[str, None] = '675d58ff0929'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'suppliers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('country', sa.String(length=80), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'code', name='uq_supplier_project_code'),
    )

    op.add_column(
        'documents', sa.Column('supplier_ref_id', sa.Integer(), nullable=True)
    )
    op.create_index(
        'ix_documents_supplier_ref_id', 'documents', ['supplier_ref_id']
    )
    op.create_foreign_key(
        'fk_documents_supplier_ref_id',
        'documents',
        'suppliers',
        ['supplier_ref_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Existing projects keep working: the previous hard-coded run default becomes the
    # stored default, and an empty assumptions object reads as "nothing supplied yet",
    # which the cost engine already reports as a refusal rather than guessing.
    op.add_column(
        'projects',
        sa.Column(
            'target_quantity', sa.Integer(), nullable=False, server_default='10000'
        ),
    )
    op.add_column(
        'projects',
        sa.Column(
            'assumptions',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='{}',
        ),
    )


def downgrade() -> None:
    op.drop_column('projects', 'assumptions')
    op.drop_column('projects', 'target_quantity')
    op.drop_constraint('fk_documents_supplier_ref_id', 'documents', type_='foreignkey')
    op.drop_index('ix_documents_supplier_ref_id', table_name='documents')
    op.drop_column('documents', 'supplier_ref_id')
    op.drop_table('suppliers')
