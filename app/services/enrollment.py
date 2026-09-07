import calendar
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session

from app.models import (
    Curso, Cuota, EstadoCuotaEnum, Inscripcion,
    PrecioHistorico, TipoCuotaEnum,
)


def _apply_discounts(monto_base, descuento_pct, descuento_fijo) -> Decimal:
    result = Decimal(str(monto_base))
    if descuento_pct is not None:
        result = result * (1 - Decimal(str(descuento_pct)) / 100)
    if descuento_fijo is not None:
        result = result - Decimal(str(descuento_fijo))
    return max(result, Decimal("0.00")).quantize(Decimal("0.01"))


def _monto_imputado(cuota: Cuota) -> Decimal:
    return sum(
        Decimal(str(i.monto_imputado)) for i in cuota.imputaciones
    )


def generate_cuotas(
    session: Session,
    inscripcion: Inscripcion,
    cantidad_override: int | None = None,
    generar_matricula: bool = True,
) -> list[Cuota]:
    curso = inscripcion.curso
    sede = curso.sede
    dia_vto = sede.dia_vencimiento_default
    cantidad = cantidad_override if cantidad_override is not None else curso.cantidad_cuotas

    cuotas: list[Cuota] = []

    if generar_matricula:
        monto_mat = _apply_discounts(
            curso.monto_matricula,
            inscripcion.descuento_porcentaje,
            inscripcion.descuento_fijo,
        )
        cuotas.append(Cuota(
            inscripcion_id=inscripcion.id,
            tipo=TipoCuotaEnum.matricula,
            numero_cuota=0,
            periodo=None,
            fecha_vencimiento=inscripcion.fecha_inscripcion,
            monto_original=curso.monto_matricula,
            monto_actualizado=monto_mat,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto_mat,
        ))

    start = inscripcion.fecha_inscripcion.replace(day=1)
    for i in range(cantidad):
        periodo_date = start + relativedelta(months=i)
        last_day = calendar.monthrange(periodo_date.year, periodo_date.month)[1]
        fecha_vto = date(periodo_date.year, periodo_date.month, min(dia_vto, last_day))
        monto = _apply_discounts(
            curso.monto_cuota_mensual,
            inscripcion.descuento_porcentaje,
            inscripcion.descuento_fijo,
        )
        cuotas.append(Cuota(
            inscripcion_id=inscripcion.id,
            tipo=TipoCuotaEnum.mensual,
            numero_cuota=i + 1,
            periodo=periodo_date.strftime("%Y-%m"),
            fecha_vencimiento=fecha_vto,
            monto_original=curso.monto_cuota_mensual,
            monto_actualizado=monto,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto,
        ))

    for c in cuotas:
        session.add(c)

    return cuotas


def generate_matricula_only(
    session: Session,
    inscripcion: Inscripcion,
    fecha_vencimiento: date,
) -> Cuota | None:
    """Genera solo la cuota de matrícula para una inscripción. Retorna None si ya existe."""
    ya_existe = any(c.tipo == TipoCuotaEnum.matricula for c in inscripcion.cuotas)
    if ya_existe:
        return None

    monto_mat = _apply_discounts(
        inscripcion.curso.monto_matricula,
        inscripcion.descuento_porcentaje,
        inscripcion.descuento_fijo,
    )
    cuota = Cuota(
        inscripcion_id=inscripcion.id,
        tipo=TipoCuotaEnum.matricula,
        numero_cuota=0,
        periodo=None,
        fecha_vencimiento=fecha_vencimiento,
        monto_original=inscripcion.curso.monto_matricula,
        monto_actualizado=monto_mat,
        estado=EstadoCuotaEnum.pendiente,
        saldo_pendiente=monto_mat,
    )
    session.add(cuota)
    return cuota


def generate_cuotas_periodo(
    session: Session,
    inscripcion: Inscripcion,
    periodo_inicio: date,
    cantidad: int,
) -> list[Cuota]:
    """Genera cuotas mensuales para una inscripción específica desde un período dado.
    Omite períodos ya existentes."""
    curso = inscripcion.curso
    sede = curso.sede
    dia_vto = sede.dia_vencimiento_default
    start = periodo_inicio.replace(day=1)

    periodos_existentes = {c.periodo for c in inscripcion.cuotas if c.periodo}
    nums_existentes = [c.numero_cuota for c in inscripcion.cuotas if c.numero_cuota is not None]
    next_num = (max(nums_existentes) + 1) if nums_existentes else 1

    cuotas: list[Cuota] = []
    gen_count = 0
    for i in range(cantidad):
        periodo_date = start + relativedelta(months=i)
        periodo_str = periodo_date.strftime("%Y-%m")
        if periodo_str in periodos_existentes:
            continue
        last_day = calendar.monthrange(periodo_date.year, periodo_date.month)[1]
        fecha_vto = date(periodo_date.year, periodo_date.month, min(dia_vto, last_day))
        monto = _apply_discounts(
            curso.monto_cuota_mensual,
            inscripcion.descuento_porcentaje,
            inscripcion.descuento_fijo,
        )
        c = Cuota(
            inscripcion_id=inscripcion.id,
            tipo=TipoCuotaEnum.mensual,
            numero_cuota=next_num + gen_count,
            periodo=periodo_str,
            fecha_vencimiento=fecha_vto,
            monto_original=curso.monto_cuota_mensual,
            monto_actualizado=monto,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto,
        )
        session.add(c)
        cuotas.append(c)
        gen_count += 1

    return cuotas


def generate_cuotas_masivas(
    session: Session,
    curso_id: int,
    periodo_inicio: date,
    cantidad: int,
) -> tuple[int, int]:
    """Genera cuotas mensuales para todas las inscripciones activas de un curso.
    Omite períodos ya existentes. Retorna (cuotas_generadas, inscripciones_afectadas)."""
    curso = session.get(Curso, curso_id)
    sede = curso.sede
    dia_vto = sede.dia_vencimiento_default
    start = periodo_inicio.replace(day=1)

    inscripciones = (
        session.query(Inscripcion)
        .filter(
            Inscripcion.curso_id == curso_id,
            Inscripcion.activa == True,
            Inscripcion.provisional == False,
        )
        .all()
    )

    total_gen = 0
    inscripciones_afectadas = 0

    for ins in inscripciones:
        periodos_existentes = {c.periodo for c in ins.cuotas if c.periodo}
        nums_existentes = [c.numero_cuota for c in ins.cuotas if c.numero_cuota is not None]
        next_num = (max(nums_existentes) + 1) if nums_existentes else 1

        gen_count = 0
        for i in range(cantidad):
            periodo_date = start + relativedelta(months=i)
            periodo_str = periodo_date.strftime("%Y-%m")
            if periodo_str in periodos_existentes:
                continue
            last_day = calendar.monthrange(periodo_date.year, periodo_date.month)[1]
            fecha_vto = date(periodo_date.year, periodo_date.month, min(dia_vto, last_day))
            monto = _apply_discounts(
                curso.monto_cuota_mensual,
                ins.descuento_porcentaje,
                ins.descuento_fijo,
            )
            session.add(Cuota(
                inscripcion_id=ins.id,
                tipo=TipoCuotaEnum.mensual,
                numero_cuota=next_num + gen_count,
                periodo=periodo_str,
                fecha_vencimiento=fecha_vto,
                monto_original=curso.monto_cuota_mensual,
                monto_actualizado=monto,
                estado=EstadoCuotaEnum.pendiente,
                saldo_pendiente=monto,
            ))
            gen_count += 1

        if gen_count > 0:
            total_gen += gen_count
            inscripciones_afectadas += 1

    return total_gen, inscripciones_afectadas


def save_price_history(session: Session, curso: Curso, motivo: str) -> None:
    session.add(PrecioHistorico(
        curso_id=curso.id,
        vigencia_desde=date.today(),
        monto_cuota=curso.monto_cuota_mensual,
        monto_matricula=curso.monto_matricula,
        motivo=motivo,
    ))


def recalculate_pending_cuotas(session: Session, curso_id: int) -> int:
    """Recalcula montos en cuotas pendientes/parciales no vencidas al nuevo precio del curso.
    Respeta pagos parciales ya aplicados."""
    curso = session.get(Curso, curso_id)
    today = date.today()

    inscripciones = (
        session.query(Inscripcion)
        .filter(Inscripcion.curso_id == curso_id, Inscripcion.activa == True)
        .all()
    )

    updated = 0
    for ins in inscripciones:
        for cuota in ins.cuotas:
            if cuota.estado not in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                continue
            if cuota.fecha_vencimiento < today:
                continue
            if cuota.tipo == TipoCuotaEnum.mensual:
                base = curso.monto_cuota_mensual
            elif cuota.tipo == TipoCuotaEnum.matricula:
                base = curso.monto_matricula
            else:
                continue  # examen: precio fijo manual
            new_monto = _apply_discounts(base, ins.descuento_porcentaje, ins.descuento_fijo)
            ya_imputado = _monto_imputado(cuota)
            cuota.monto_original = base
            cuota.monto_actualizado = new_monto
            cuota.saldo_pendiente = max(new_monto - ya_imputado, Decimal("0.00"))
            updated += 1

    return updated


def recalculate_cuotas_by_inscripcion(session: Session, inscripcion_id: int) -> int:
    """Recalcula montos en cuotas pendientes/parciales no vencidas para una sola inscripción.
    Usa el precio actual del curso y el descuento vigente en la inscripción."""
    ins = session.get(Inscripcion, inscripcion_id)
    if not ins:
        return 0
    curso = ins.curso
    today = date.today()
    updated = 0

    for cuota in ins.cuotas:
        if cuota.estado not in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
            continue
        if cuota.fecha_vencimiento < today:
            continue
        if cuota.tipo == TipoCuotaEnum.mensual:
            base = curso.monto_cuota_mensual
        elif cuota.tipo == TipoCuotaEnum.matricula:
            base = curso.monto_matricula
        else:
            continue
        new_monto = _apply_discounts(base, ins.descuento_porcentaje, ins.descuento_fijo)
        ya_imputado = _monto_imputado(cuota)
        cuota.monto_original = base
        cuota.monto_actualizado = new_monto
        cuota.saldo_pendiente = max(new_monto - ya_imputado, Decimal("0.00"))
        updated += 1

    return updated


def dar_de_baja_inscripcion(
    session: Session,
    inscripcion_id: int,
    condonar_futuras: bool = True,
) -> int:
    """Desactiva una inscripción. Si condonar_futuras=True, condona cuotas pendientes
    con vencimiento >= hoy. Retorna cantidad de cuotas condonadas."""
    ins = session.get(Inscripcion, inscripcion_id)
    if not ins:
        return 0
    ins.activa = False
    today = date.today()
    condonadas = 0
    if condonar_futuras:
        for cuota in ins.cuotas:
            if cuota.estado not in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                continue
            if cuota.fecha_vencimiento < today:
                continue
            cuota.estado = EstadoCuotaEnum.condonada
            cuota.saldo_pendiente = Decimal("0.00")
            condonadas += 1
    return condonadas


def actualizar_monto_cuotas_alumno(session: Session, alumno_id: int, nuevo_monto: Decimal) -> int:
    """Set monto_actualizado to nuevo_monto for all pending/partial cuotas of alumno."""
    cuotas = (
        session.query(Cuota)
        .join(Inscripcion, Cuota.inscripcion_id == Inscripcion.id)
        .filter(
            Inscripcion.alumno_id == alumno_id,
            Inscripcion.activa == True,
            Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
        )
        .all()
    )
    for c in cuotas:
        ya_imp = _monto_imputado(c)
        c.monto_actualizado = nuevo_monto
        c.saldo_pendiente = max(nuevo_monto - ya_imp, Decimal("0.00"))
    return len(cuotas)


def actualizar_monto_cuota(session: Session, cuota_id: int, nuevo_monto: Decimal) -> None:
    """Update monto of a single cuota. Recalculates saldo_pendiente."""
    c = session.get(Cuota, cuota_id)
    if not c:
        return
    ya_imp = _monto_imputado(c)
    c.monto_actualizado = nuevo_monto
    c.saldo_pendiente = max(nuevo_monto - ya_imp, Decimal("0.00"))


def eliminar_cuota(session: Session, cuota_id: int) -> bool:
    """Delete a cuota only if it has no imputaciones. Returns True if deleted."""
    c = session.get(Cuota, cuota_id)
    if not c or c.imputaciones:
        return False
    session.delete(c)
    return True


def generate_exam_cuotas(session: Session, curso_id: int, monto: Decimal, anio: int | None = None) -> int:
    """Genera cuota de derecho de examen para todos los inscriptos activos del curso.
    Filtra duplicados por año para permitir generar en años sucesivos."""
    curso = session.get(Curso, curso_id)
    today = date.today()
    anio_ref = anio or today.year
    next_month = today.replace(day=1) + relativedelta(months=1)
    sede = curso.sede
    last_day = calendar.monthrange(next_month.year, next_month.month)[1]
    fecha_vto = date(next_month.year, next_month.month, min(sede.dia_vencimiento_default, last_day))

    inscripciones = (
        session.query(Inscripcion)
        .filter(
            Inscripcion.curso_id == curso_id,
            Inscripcion.activa == True,
            Inscripcion.provisional == False,
        )
        .all()
    )

    count = 0
    for ins in inscripciones:
        ya_tiene = any(
            c.tipo == TipoCuotaEnum.examen
            and c.periodo is not None
            and c.periodo.startswith(str(anio_ref))
            for c in ins.cuotas
        )
        if ya_tiene:
            continue
        monto_final = _apply_discounts(monto, ins.descuento_porcentaje, ins.descuento_fijo)
        session.add(Cuota(
            inscripcion_id=ins.id,
            tipo=TipoCuotaEnum.examen,
            numero_cuota=None,
            periodo=f"{anio_ref}-12",
            fecha_vencimiento=fecha_vto,
            monto_original=monto,
            monto_actualizado=monto_final,
            estado=EstadoCuotaEnum.pendiente,
            saldo_pendiente=monto_final,
        ))
        count += 1

    return count


def generate_inscripcion_provisional(
    session: Session,
    alumno_id: int,
    curso_id: int,
    monto_reserva: Decimal,
    fecha_vencimiento: date,
    anio_reserva: int,
) -> tuple[Inscripcion, Cuota]:
    """Crea inscripción provisional (reserva de cupo) con una cuota de matrícula.
    No genera cuotas mensuales. Retorna (inscripcion, cuota_matricula)."""
    ins = Inscripcion(
        alumno_id=alumno_id,
        curso_id=curso_id,
        fecha_inscripcion=date.today(),
        activa=True,
        provisional=True,
        anio_reserva=anio_reserva,
    )
    session.add(ins)
    session.flush()

    cuota = Cuota(
        inscripcion_id=ins.id,
        tipo=TipoCuotaEnum.matricula,
        numero_cuota=0,
        periodo=None,
        fecha_vencimiento=fecha_vencimiento,
        monto_original=monto_reserva,
        monto_actualizado=monto_reserva,
        estado=EstadoCuotaEnum.pendiente,
        saldo_pendiente=monto_reserva,
    )
    session.add(cuota)
    return ins, cuota


def confirmar_inscripcion_provisional(
    session: Session,
    inscripcion_id: int,
    fecha_inicio_cuotas: date,
    cantidad_cuotas: int | None = None,
) -> list[Cuota]:
    """Convierte una inscripción provisional en activa y genera las cuotas mensuales."""
    ins = session.get(Inscripcion, inscripcion_id)
    if not ins or not ins.provisional:
        return []
    ins.provisional = False
    ins.fecha_inscripcion = fecha_inicio_cuotas
    return generate_cuotas_periodo(session, ins, fecha_inicio_cuotas, cantidad_cuotas or ins.curso.cantidad_cuotas)
