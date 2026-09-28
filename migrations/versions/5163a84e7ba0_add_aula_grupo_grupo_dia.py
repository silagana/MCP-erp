"""add aula, grupo, grupo_dia (horarios por aula/profesor)

Revision ID: 5163a84e7ba0
Revises: 6ed41e645d7a
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5163a84e7ba0'
down_revision: Union[str, None] = '6ed41e645d7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('aula',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sede_id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=80), nullable=False),
    sa.Column('color', sa.String(length=7), nullable=True),
    sa.Column('activa', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['sede_id'], ['sede.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sede_id', 'nombre')
    )

    op.create_table('grupo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('curso_id', sa.Integer(), nullable=False),
    sa.Column('aula_id', sa.Integer(), nullable=True),
    sa.Column('profesor_id', sa.Integer(), nullable=True),
    sa.Column('nombre', sa.String(length=120), nullable=True),
    sa.Column('hora_inicio', sa.Time(), nullable=False),
    sa.Column('hora_fin', sa.Time(), nullable=False),
    sa.Column('cupo_maximo', sa.Integer(), nullable=True),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['aula_id'], ['aula.id'], ),
    sa.ForeignKeyConstraint(['curso_id'], ['curso.id'], ),
    sa.ForeignKeyConstraint(['profesor_id'], ['profesor.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('grupo_dia',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('grupo_id', sa.Integer(), nullable=False),
    sa.Column('dia_semana', sa.Enum('lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo', name='diasemanaenum'), nullable=False),
    sa.ForeignKeyConstraint(['grupo_id'], ['grupo.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('grupo_id', 'dia_semana')
    )

    op.add_column('inscripcion', sa.Column('grupo_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_inscripcion_grupo_id', 'inscripcion', 'grupo', ['grupo_id'], ['id'])

    op.add_column('reporte_token', sa.Column('sede_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_reporte_token_sede_id', 'reporte_token', 'sede', ['sede_id'], ['id'])

    # Agrega el valor nuevo al enum ya existente (alcancereporteenum). No se
    # puede usar dentro de la misma transacción en algunas versiones viejas
    # de Postgres, pero como acá solo se agrega la columna y no se inserta
    # ningún dato con ese valor todavía, no hace falta autocommit aparte.
    op.execute("ALTER TYPE alcancereporteenum ADD VALUE IF NOT EXISTS 'horarios'")


def downgrade() -> None:
    # Nota: Postgres no permite quitar un valor de un enum sin recrear el
    # tipo; se deja 'horarios' en alcancereporteenum aunque se revierta el
    # resto (mismo criterio pragmático que el resto de las migraciones acá).
    op.drop_constraint('fk_reporte_token_sede_id', 'reporte_token', type_='foreignkey')
    op.drop_column('reporte_token', 'sede_id')

    op.drop_constraint('fk_inscripcion_grupo_id', 'inscripcion', type_='foreignkey')
    op.drop_column('inscripcion', 'grupo_id')

    op.drop_table('grupo_dia')
    op.drop_table('grupo')
    op.drop_table('aula')

    sa.Enum(name='diasemanaenum').drop(op.get_bind(), checkfirst=True)
