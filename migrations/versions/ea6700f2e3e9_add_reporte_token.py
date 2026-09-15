"""add reporte_token

Revision ID: ea6700f2e3e9
Revises: 18dfade2ee24
Create Date: 2026-09-15 11:25:02.898589

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ea6700f2e3e9'
down_revision: Union[str, None] = '18dfade2ee24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('reporte_token',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('usuario_whatsapp_id', sa.Integer(), nullable=False),
    sa.Column('alcance', sa.Enum('completo', 'alumno', name='alcancereporteenum'), nullable=False),
    sa.Column('alumno_id', sa.Integer(), nullable=True),
    sa.Column('creado', sa.DateTime(), nullable=False),
    sa.Column('expira', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['alumno_id'], ['alumno.id'], ),
    sa.ForeignKeyConstraint(['usuario_whatsapp_id'], ['usuario_whatsapp.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_index(op.f('ix_reporte_token_token'), 'reporte_token', ['token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_reporte_token_token'), table_name='reporte_token')
    op.drop_table('reporte_token')
