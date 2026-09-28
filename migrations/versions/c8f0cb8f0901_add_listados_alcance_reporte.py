"""add 'listados' value to alcancereporteenum (vista alumnos por curso)

Revision ID: c8f0cb8f0901
Revises: 5163a84e7ba0
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8f0cb8f0901'
down_revision: Union[str, None] = '5163a84e7ba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE alcancereporteenum ADD VALUE IF NOT EXISTS 'listados'")


def downgrade() -> None:
    # Nota: Postgres no permite quitar un valor de un enum sin recrear el
    # tipo; se deja 'listados' en alcancereporteenum (mismo criterio que la
    # migración anterior, 5163a84e7ba0, para 'horarios').
    pass
