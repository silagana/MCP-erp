"""Registro directo de pagos (efectivo o transferencia) sin pasar primero
por conciliación bancaria.

Decisión 2026-09-28: se separa "cobrar" (este módulo) de "conciliar"
(reconciliation.py). Antes, un `Pago` solo se creaba DESDE un
`MovimientoBancario` ya matcheado — la secretaría tenía que esperar el
resumen bancario para que el cobro quedara registrado. Ahora se registra
en el momento (efectivo en mano, o viendo la notificación de la
transferencia) y la conciliación mensual (`reconciliation.conciliar_pendientes`)
cruza el resumen bancario contra estos pagos ya cargados — no crea el Pago
recién ahí, solo confirma el cruce vinculando `movimiento_bancario_id`."""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Cuota, EstadoCuotaEnum, Imputacion, MedioPagoEnum, Pago
from app.services.reconciliation import fifo_distribuir
from app.utils.audit import log_action


def registrar_pago(
    session: Session,
    alumno_id: int,
    monto: Decimal,
    medio: MedioPagoEnum,
    fecha: date,
    cuota_id: int | None = None,
    operador_efectivo: str | None = None,
    observaciones: str | None = None,
) -> tuple[Pago, Decimal]:
    """Crea un Pago SIN movimiento_bancario_id (se vincula después, si
    corresponde, en la conciliación mensual). Sin cuota_id, distribuye
    FIFO contra las cuotas impagas del alumno (igual que hacía la
    conciliación); con cuota_id, imputa ahí directo. Devuelve (pago,
    excedente) — excedente es lo que sobró sin poder imputar (alumno sin
    más deuda, o cuota puntual ya cubierta)."""
    pago = Pago(
        fecha=fecha,
        monto=monto,
        medio=medio,
        operador_efectivo=operador_efectivo,
        observaciones=observaciones,
    )
    session.add(pago)
    session.flush()

    if cuota_id is not None:
        cuota = session.get(Cuota, cuota_id)
        a_imputar = min(monto, cuota.saldo_pendiente)
        session.add(Imputacion(pago_id=pago.id, cuota_id=cuota.id, monto_imputado=a_imputar))
        nuevo_saldo = cuota.saldo_pendiente - a_imputar
        cuota.estado = EstadoCuotaEnum.pagada if nuevo_saldo <= Decimal("0.00") else EstadoCuotaEnum.parcial
        cuota.saldo_pendiente = max(nuevo_saldo, Decimal("0.00"))
        excedente = monto - a_imputar
    else:
        imputaciones, excedente = fifo_distribuir(session, [alumno_id], monto)
        for imp in imputaciones:
            cuota = session.get(Cuota, imp.cuota_id)
            session.add(Imputacion(pago_id=pago.id, cuota_id=imp.cuota_id, monto_imputado=imp.monto_a_imputar))
            nuevo_saldo = cuota.saldo_pendiente - imp.monto_a_imputar
            cuota.estado = EstadoCuotaEnum.pagada if nuevo_saldo <= Decimal("0.00") else EstadoCuotaEnum.parcial
            cuota.saldo_pendiente = max(nuevo_saldo, Decimal("0.00"))

    log_action(session, "REGISTRAR_PAGO", "pago", pago.id, {
        "alumno_id": alumno_id, "monto": str(monto), "medio": medio.value, "excedente": str(excedente),
    })
    return pago, excedente
