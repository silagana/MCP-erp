"""add mensaje_whatsapp

Revision ID: cef3a182a043
Revises: bc81eb3b5c09
Create Date: 2026-09-14 19:49:09.964061

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'cef3a182a043'
down_revision: Union[str, None] = 'bc81eb3b5c09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('mensaje_whatsapp',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telefono', sa.String(length=30), nullable=False),
    sa.Column('rol', sa.Enum('user', 'assistant', name='rolmensajeenum'), nullable=False),
    sa.Column('contenido', sa.Text(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mensaje_whatsapp_telefono'), 'mensaje_whatsapp', ['telefono'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_mensaje_whatsapp_telefono'), table_name='mensaje_whatsapp')
    op.drop_table('mensaje_whatsapp')
