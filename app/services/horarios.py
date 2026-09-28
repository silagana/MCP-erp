"""Detección de choques de horario entre Grupos (comisiones) — mismo día de
semana + horario superpuesto + misma aula o mismo profesor. Usado por
crear_grupo y mover_grupo (app/mcp_server.py) para no permitir pisar un
horario ya ocupado."""
from datetime import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import DiaSemanaEnum, Grupo, GrupoDia


def _se_superponen(inicio_a: time, fin_a: time, inicio_b: time, fin_b: time) -> bool:
    return inicio_a < fin_b and inicio_b < fin_a


def detectar_conflictos(
    session: Session,
    dias_semana: set[DiaSemanaEnum],
    hora_inicio: time,
    hora_fin: time,
    aula_id: int | None,
    profesor_id: int | None,
    excluir_grupo_id: int | None = None,
) -> list[dict]:
    """Devuelve los grupos activos que chocan en aula y/o profesor (mismo
    día + horario superpuesto). Lista vacía si no hay conflicto. Sin
    aula_id ni profesor_id no hay nada contra qué chocar, devuelve vacío."""
    if aula_id is None and profesor_id is None:
        return []

    condiciones = []
    if aula_id is not None:
        condiciones.append(Grupo.aula_id == aula_id)
    if profesor_id is not None:
        condiciones.append(Grupo.profesor_id == profesor_id)

    query = (
        session.query(Grupo)
        .join(GrupoDia, GrupoDia.grupo_id == Grupo.id)
        .filter(Grupo.activo == True, GrupoDia.dia_semana.in_(dias_semana), or_(*condiciones))
    )
    if excluir_grupo_id is not None:
        query = query.filter(Grupo.id != excluir_grupo_id)

    conflictos = []
    for grupo in query.distinct():
        if not _se_superponen(hora_inicio, hora_fin, grupo.hora_inicio, grupo.hora_fin):
            continue
        tipo_choque = []
        if aula_id is not None and grupo.aula_id == aula_id:
            tipo_choque.append("aula")
        if profesor_id is not None and grupo.profesor_id == profesor_id:
            tipo_choque.append("profesor")
        dias_choque = sorted(gd.dia_semana.value for gd in grupo.dias if gd.dia_semana in dias_semana)
        conflictos.append({
            "grupo_id": grupo.id,
            "grupo_nombre": grupo.nombre or grupo.curso.nombre,
            "curso": grupo.curso.nombre,
            "tipo_choque": tipo_choque,
            "dias": dias_choque,
            "hora_inicio": grupo.hora_inicio.strftime("%H:%M"),
            "hora_fin": grupo.hora_fin.strftime("%H:%M"),
            "aula": grupo.aula.nombre if grupo.aula else None,
            "profesor": f"{grupo.profesor.nombre} {grupo.profesor.apellido}" if grupo.profesor else None,
        })
    return conflictos
