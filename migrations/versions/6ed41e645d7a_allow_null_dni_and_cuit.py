"""allow null dni and cuit

Revision ID: 6ed41e645d7a
Revises: ea6700f2e3e9
Create Date: 2026-09-16 15:40:20.064512

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '6ed41e645d7a'
down_revision: Union[str, None] = 'ea6700f2e3e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('alumno', 'dni', existing_type=sa.String(length=15), nullable=True)
    op.alter_column('referente_pago', 'cuit_cuil', existing_type=sa.String(length=20), nullable=True)


def downgrade() -> None:
    op.alter_column('referente_pago', 'cuit_cuil', existing_type=sa.String(length=20), nullable=False)
    op.alter_column('alumno', 'dni', existing_type=sa.String(length=15), nullable=False)
