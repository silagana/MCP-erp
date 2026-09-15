"""add telegram_chat_id to usuario_whatsapp

Revision ID: 18dfade2ee24
Revises: cef3a182a043
Create Date: 2026-09-15 08:51:27.472209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '18dfade2ee24'
down_revision: Union[str, None] = 'cef3a182a043'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('usuario_whatsapp', sa.Column('telegram_chat_id', sa.String(length=30), nullable=True))
    op.create_unique_constraint(op.f('uq_usuario_whatsapp_telegram_chat_id'), 'usuario_whatsapp', ['telegram_chat_id'])


def downgrade() -> None:
    op.drop_constraint(op.f('uq_usuario_whatsapp_telegram_chat_id'), 'usuario_whatsapp', type_='unique')
    op.drop_column('usuario_whatsapp', 'telegram_chat_id')
