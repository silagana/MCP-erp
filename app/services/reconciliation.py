from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.models import (
    AliasCobroAlumno, Alumno, Cuota, EstadoAlumnoEnum, EstadoCuotaEnum,
    EstadoMovimientoEnum, Imputacion, Inscripcion,
    MedioPagoEnum, MovimientoBancario, Pago,
    PatronNoAlumno, ReferentePago,
)
from app.utils.audit import log_action

FUZZY_THRESHOLD_NOMBRE = 80
FUZZY_THRESHOLD_REFERENCIA = 75


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class SugerenciaImputacion:
    alumno_id: int
    alumno_nombre: str
    cuota_id: int
    cuota_descripcion: str
    monto_a_imputar: Decimal
    saldo_restante_cuota: Decimal


@dataclass
class SugerenciaConciliacion:
    movimiento_id: int
    confianza: str          # 'alto' | 'medio' | 'bajo' | ''
    metodo: str             # 'pago_existente' | 'cuit' | 'cuit_multiple' | 'fuzzy_nombre' | 'fuzzy_referencia' | 'patron_no_alumno' | ''
    imputaciones: list[SugerenciaImputacion] = field(default_factory=list)
    sin_match: bool = False
    es_no_alumno: bool = False
    excedente: Decimal = field(default_factory=lambda: Decimal("0.00"))
    pago_existente_id: int | None = None
    """Si está seteado, ya hay un Pago cargado (vía registrar_pago o una
    conciliación anterior) que probablemente es este movimiento — aplicar_conciliacion
    con este id solo VINCULA, no crea Pago/Imputacion nuevas. Ver
    conciliar_pendientes, que es el flujo mensual pensado para esto."""


# ── FIFO ───────────────────────────────────────────────────────────────────

def _cuotas_impagas(session: Session, alumno_id: int) -> list[Cuota]:
    return (
        session.query(Cuota)
        .join(Inscripcion, Cuota.inscripcion_id == Inscripcion.id)
        .filter(
            Inscripcion.alumno_id == alumno_id,
            Inscripcion.activa == True,
            Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
        )
        .order_by(Cuota.fecha_vencimiento, Cuota.id)
        .all()
    )


def fifo_distribuir(
    session: Session,
    alumno_ids: list[int],
    monto_disponible: Decimal,
) -> tuple[list[SugerenciaImputacion], Decimal]:
    """
    Distribute monto_disponible across unpaid cuotas of given alumnos (FIFO).
    Returns (imputaciones, excedente).
    """
    pares: list[tuple[Alumno, Cuota]] = []
    for al_id in alumno_ids:
        alumno = session.get(Alumno, al_id)
        for cuota in _cuotas_impagas(session, al_id):
            pares.append((alumno, cuota))
    pares.sort(key=lambda x: (x[1].fecha_vencimiento, x[1].id))

    imputaciones: list[SugerenciaImputacion] = []
    restante = monto_disponible

    for alumno, cuota in pares:
        if restante <= Decimal("0.00"):
            break
        a_imputar = min(restante, cuota.saldo_pendiente)
        tipo_labels = {"matricula": "Matrícula", "mensual": f"Cuota {cuota.numero_cuota}", "examen": "Examen"}
        desc = tipo_labels.get(cuota.tipo.value, cuota.tipo.value)
        if cuota.periodo:
            desc += f" {cuota.periodo}"
        imputaciones.append(SugerenciaImputacion(
            alumno_id=alumno.id,
            alumno_nombre=f"{alumno.apellido}, {alumno.nombre}",
            cuota_id=cuota.id,
            cuota_descripcion=desc,
            monto_a_imputar=a_imputar,
            saldo_restante_cuota=cuota.saldo_pendiente - a_imputar,
        ))
        restante -= a_imputar

    return imputaciones, restante


# ── Matching ───────────────────────────────────────────────────────────────

def _buscar_pago_existente(session: Session, mov: MovimientoBancario, alumno_ids: list[int]) -> Pago | None:
    """Busca, entre los pagos ya cargados (registrar_pago) y todavía sin
    vincular a ningún movimiento, uno que probablemente sea este mismo
    cobro: mismo importe, medio transferencia, imputado a alguno de los
    alumnos candidatos. Con más de un candidato, se queda con el de fecha
    más cercana al movimiento — no es perfecto, pero es el caso raro
    (dos pagos del mismo importe al mismo alumno el mismo mes)."""
    candidatos = (
        session.query(Pago)
        .join(Imputacion, Imputacion.pago_id == Pago.id)
        .join(Cuota, Imputacion.cuota_id == Cuota.id)
        .join(Inscripcion, Cuota.inscripcion_id == Inscripcion.id)
        .filter(
            Pago.movimiento_bancario_id.is_(None),
            Pago.medio == MedioPagoEnum.transferencia,
            Pago.monto == mov.importe,
            Inscripcion.alumno_id.in_(alumno_ids),
        )
        .distinct()
        .all()
    )
    if not candidatos:
        return None
    candidatos.sort(key=lambda p: abs((p.fecha - mov.fecha).days))
    return candidatos[0]


def _describir_pago(pago: Pago) -> list[SugerenciaImputacion]:
    """Traduce las Imputaciones de un Pago ya existente al mismo formato
    que devuelve fifo_distribuir, para mostrar en la sugerencia qué es lo
    que se está proponiendo vincular (no volver a imputar)."""
    out = []
    for imp in pago.imputaciones:
        cuota = imp.cuota
        alumno = cuota.inscripcion.alumno
        tipo_labels = {"matricula": "Matrícula", "mensual": f"Cuota {cuota.numero_cuota}", "examen": "Examen"}
        desc = tipo_labels.get(cuota.tipo.value, cuota.tipo.value)
        if cuota.periodo:
            desc += f" {cuota.periodo}"
        out.append(SugerenciaImputacion(
            alumno_id=alumno.id,
            alumno_nombre=f"{alumno.apellido}, {alumno.nombre}",
            cuota_id=cuota.id,
            cuota_descripcion=desc,
            monto_a_imputar=imp.monto_imputado,
            saldo_restante_cuota=cuota.saldo_pendiente,
        ))
    return out


def _sugerir_para_alumnos(
    session: Session,
    mov: MovimientoBancario,
    alumno_ids: list[int],
    confianza: str,
    metodo: str,
) -> SugerenciaConciliacion:
    """Con un alumno (o varios) ya identificado como probable pagador,
    prioriza vincular un Pago ya registrado (flujo normal, a posteriori,
    ver conciliar_pendientes) — solo si no hay ninguno cae al viejo
    comportamiento de proponer crear un Pago nuevo con FIFO."""
    pago_existente = _buscar_pago_existente(session, mov, alumno_ids)
    if pago_existente:
        return SugerenciaConciliacion(
            movimiento_id=mov.id, confianza="alto", metodo="pago_existente",
            imputaciones=_describir_pago(pago_existente), pago_existente_id=pago_existente.id,
        )
    imps, exc = fifo_distribuir(session, alumno_ids, Decimal(str(mov.importe)))
    return SugerenciaConciliacion(
        movimiento_id=mov.id, confianza=confianza, metodo=metodo,
        imputaciones=imps, excedente=exc,
    )


def sugerir_conciliacion(
    session: Session,
    movimiento_id: int,
) -> SugerenciaConciliacion:
    mov = session.get(MovimientoBancario, movimiento_id)
    if not mov:
        raise ValueError(f"Movimiento {movimiento_id} no encontrado")

    # ── 1. CUIT match ──────────────────────────────────────────────────────
    if mov.cuit_detectado:
        refs = (
            session.query(ReferentePago)
            .filter(ReferentePago.cuit_cuil == mov.cuit_detectado)
            .all()
        )
        al_ids = list({r.alumno_id for r in refs})

        if len(al_ids) == 1:
            return _sugerir_para_alumnos(session, mov, al_ids, "alto", "cuit")

        if len(al_ids) > 1:
            return _sugerir_para_alumnos(session, mov, al_ids, "medio", "cuit_multiple")

    # ── 1.5. Alias cobro guardado por el usuario ───────────────────────────
    if mov.nombre_pagador_detectado:
        query_lower = mov.nombre_pagador_detectado.lower()
        aliases = session.query(AliasCobroAlumno).all()
        best_score = 0
        best_alias: AliasCobroAlumno | None = None
        for a in aliases:
            score = fuzz.token_sort_ratio(query_lower, a.alias.lower())
            if score > best_score:
                best_score = score
                best_alias = a
        if best_alias and best_score >= 85:
            return _sugerir_para_alumnos(session, mov, [best_alias.alumno_id], "alto", "alias_cobro")

    # ── 2. Fuzzy nombre_pagador vs referente.nombre_completo ───────────────
    if mov.nombre_pagador_detectado:
        query = mov.nombre_pagador_detectado.lower()
        refs_all = session.query(ReferentePago).all()

        matches = sorted(
            [(ref, fuzz.token_sort_ratio(query, ref.nombre_completo.lower()))
             for ref in refs_all],
            key=lambda x: x[1], reverse=True,
        )
        matches = [(ref, score) for ref, score in matches if score >= FUZZY_THRESHOLD_NOMBRE]

        if matches:
            al_ids = list({ref.alumno_id for ref, _ in matches})
            confianza = "medio" if len(al_ids) == 1 else "bajo"
            # Use top match alumno only
            best_al_id = matches[0][0].alumno_id
            return _sugerir_para_alumnos(session, mov, [best_al_id], confianza, "fuzzy_nombre")

    # ── 3. Fuzzy referencia_detectada vs alumno nombre+apellido ───────────
    if mov.referencia_detectada:
        query = mov.referencia_detectada.lower()
        alumnos = (
            session.query(Alumno)
            .filter(Alumno.estado == EstadoAlumnoEnum.activo)
            .all()
        )
        matches = sorted(
            [(al, fuzz.token_sort_ratio(query, f"{al.nombre} {al.apellido}".lower()))
             for al in alumnos],
            key=lambda x: x[1], reverse=True,
        )
        matches = [(al, score) for al, score in matches if score >= FUZZY_THRESHOLD_REFERENCIA]

        if matches:
            best_id = matches[0][0].id
            return _sugerir_para_alumnos(session, mov, [best_id], "bajo", "fuzzy_referencia")

    # ── 4. Patron no-alumno ────────────────────────────────────────────────
    check_str = (mov.nombre_pagador_detectado or mov.concepto_raw or "").lower()
    for patron in session.query(PatronNoAlumno).all():
        if patron.patron.lower() in check_str:
            return SugerenciaConciliacion(
                movimiento_id=movimiento_id, confianza="alto",
                metodo="patron_no_alumno", es_no_alumno=True,
            )

    return SugerenciaConciliacion(
        movimiento_id=movimiento_id, confianza="", metodo="", sin_match=True,
    )


# ── Aplicar / Revertir ─────────────────────────────────────────────────────

def aplicar_conciliacion(
    session: Session,
    movimiento_id: int,
    imputaciones: list[dict] | None = None,   # [{"cuota_id": int, "monto_imputado": Decimal}]
    operador: str | None = None,
    pago_id: int | None = None,
) -> int:
    """Confirma una conciliación. Dos modos:
    - pago_id: vincula un Pago YA registrado (flujo normal desde 2026-09-28,
      ver conciliar_pendientes / registrar_pago) — no crea Pago ni
      Imputacion, ya existen; solo setea movimiento_bancario_id.
    - imputaciones: crea un Pago nuevo en el momento (excepción — para
      movimientos sin un pago pre-cargado). Comportamiento previo a
      2026-09-28, se mantiene como fallback manual.
    Devuelve el pago_id resultante."""
    mov = session.get(MovimientoBancario, movimiento_id)

    if pago_id is not None:
        pago = session.get(Pago, pago_id)
        pago.movimiento_bancario_id = movimiento_id
        mov.estado = EstadoMovimientoEnum.conciliado
        log_action(session, "CONCILIAR_VINCULAR", "movimiento_bancario", movimiento_id, {"pago_id": pago.id})
        return pago.id

    pago = Pago(
        fecha=mov.fecha,
        monto=mov.importe,
        medio=MedioPagoEnum.transferencia,
        movimiento_bancario_id=movimiento_id,
        observaciones=operador,
    )
    session.add(pago)
    session.flush()

    total_imputado = Decimal("0.00")
    for imp in imputaciones or []:
        cuota = session.get(Cuota, imp["cuota_id"])
        monto_imp = Decimal(str(imp["monto_imputado"]))
        session.add(Imputacion(
            pago_id=pago.id,
            cuota_id=imp["cuota_id"],
            monto_imputado=monto_imp,
        ))
        nuevo_saldo = cuota.saldo_pendiente - monto_imp
        if nuevo_saldo <= Decimal("0.00"):
            cuota.estado = EstadoCuotaEnum.pagada
            cuota.saldo_pendiente = Decimal("0.00")
        else:
            cuota.estado = EstadoCuotaEnum.parcial
            cuota.saldo_pendiente = nuevo_saldo
        total_imputado += monto_imp

    mov.estado = (
        EstadoMovimientoEnum.conciliado
        if total_imputado >= Decimal(str(mov.importe))
        else EstadoMovimientoEnum.parcial
    )

    log_action(session, "CONCILIAR", "movimiento_bancario", movimiento_id, {
        "pago_id": pago.id, "total_imputado": str(total_imputado),
    })
    return pago.id


def conciliar_pendientes(session: Session) -> dict:
    """Flujo mensual: corre sugerir_conciliacion sobre todos los
    MovimientoBancario pendientes y vincula automáticamente los que
    matchean con confianza alta contra un Pago ya cargado (registrar_pago,
    o una conciliación manual anterior) — no crea nada nuevo, solo
    confirma el cruce. Pensado para correr después de importar el resumen
    bancario del mes, con los cobros del mes ya registrados de antemano.
    Lo que no matchea (confianza baja/media, o sin pago existente) queda
    pendiente para revisar a mano con sugerir_conciliacion/aplicar_conciliacion
    — puede ser un pago que nunca se cargó, o un movimiento que no es un
    cobro de alumno."""
    pendientes = (
        session.query(MovimientoBancario)
        .filter(MovimientoBancario.estado == EstadoMovimientoEnum.pendiente)
        .all()
    )

    vinculados = []
    a_revisar = []
    for mov in pendientes:
        sugerencia = sugerir_conciliacion(session, mov.id)
        if sugerencia.pago_existente_id and sugerencia.confianza == "alto":
            aplicar_conciliacion(session, mov.id, pago_id=sugerencia.pago_existente_id)
            vinculados.append({"movimiento_id": mov.id, "pago_id": sugerencia.pago_existente_id})
        else:
            a_revisar.append({
                "movimiento_id": mov.id,
                "importe": mov.importe,
                "fecha": mov.fecha.isoformat(),
                "confianza": sugerencia.confianza,
                "metodo": sugerencia.metodo,
                "sin_match": sugerencia.sin_match,
            })

    return {"vinculados": vinculados, "a_revisar": a_revisar}


def guardar_alias_cobro(session: Session, alumno_id: int, alias: str) -> bool:
    """Save a payment alias for future matching. Returns True if new."""
    alias_clean = alias.strip().lower()
    if not alias_clean:
        return False
    existing = session.query(AliasCobroAlumno).filter(
        AliasCobroAlumno.alumno_id == alumno_id,
        AliasCobroAlumno.alias == alias_clean,
    ).first()
    if existing:
        return False
    session.add(AliasCobroAlumno(alumno_id=alumno_id, alias=alias_clean))
    return True


def revertir_conciliacion(session: Session, movimiento_id: int) -> None:
    """Delete Pago+Imputaciones, restore Cuota states, set movimiento to pendiente."""
    mov = session.get(MovimientoBancario, movimiento_id)

    for pago in list(mov.pagos):
        for imp in list(pago.imputaciones):
            cuota = session.get(Cuota, imp.cuota_id)
            cuota.saldo_pendiente += imp.monto_imputado
            cuota.estado = (
                EstadoCuotaEnum.pendiente
                if cuota.saldo_pendiente >= cuota.monto_actualizado
                else EstadoCuotaEnum.parcial
            )
            session.delete(imp)
        session.flush()
        session.delete(pago)

    mov.estado = EstadoMovimientoEnum.pendiente
    log_action(session, "REVERTIR", "movimiento_bancario", movimiento_id, {})
