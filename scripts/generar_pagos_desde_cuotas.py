"""Genera los registros de Pago + Imputacion que le faltan a las cuotas que
ya están marcadas `pagada` pero nunca pasaron por conciliación real (el
import de ALUMNOS_2026.xlsx las marcó pagada directo, sin crear Pago —
"modo simple", decisión 2026-09-16). Sin esto, el dashboard de Ingresos
(que lee de la tabla Pago, no de Cuota) no ve nada de lo cobrado desde
que se usa el Excel como fuente — solo veía los pagos de la migración
original de st-clares-app (hasta mayo 2026).

Fecha del pago: se usa `cuota.fecha_vencimiento`, que para las cuotas que
vienen del Excel en realidad guarda la fecha real de pago (columna
"FECHA <mes>"), no un vencimiento — así quedó definido en
importar_excel_alumnos.py.

Medio de pago: no lo sabemos con certeza (el Excel no lo distingue sin
cruzar contra las hojas PAGOS <mes>, que decidimos no importar) — se
asume `transferencia` por default, la forma de pago ampliamente
predominante en los datos ya migrados. Se puede ajustar a mano después
para casos puntuales que se sepa que fueron en efectivo.

Idempotente: solo actúa sobre cuotas `pagada` que todavía no tienen
ninguna Imputacion — correr de nuevo no duplica nada.

Uso:
    DATABASE_URL=... python scripts/generar_pagos_desde_cuotas.py --dry-run
    DATABASE_URL=... python scripts/generar_pagos_desde_cuotas.py
"""
import argparse
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Cuota, EstadoCuotaEnum, Imputacion, MedioPagoEnum, Pago


def generar(database_url: str, dry_run: bool) -> None:
    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        cuotas = (
            session.query(Cuota)
            .filter(Cuota.estado == EstadoCuotaEnum.pagada)
            .all()
        )

        creados = 0
        monto_total = Decimal("0.00")
        por_periodo: dict[str, int] = {}

        for cuota in cuotas:
            if cuota.imputaciones:
                continue  # ya tiene un pago real detrás, no tocar

            pago = Pago(
                fecha=cuota.fecha_vencimiento,
                monto=cuota.monto_actualizado,
                medio=MedioPagoEnum.transferencia,
                observaciones="Generado desde cuota pagada (import de Excel, sin conciliación bancaria detallada)",
            )
            session.add(pago)
            session.flush()
            session.add(Imputacion(pago_id=pago.id, cuota_id=cuota.id, monto_imputado=cuota.monto_actualizado))

            creados += 1
            monto_total += cuota.monto_actualizado
            clave = cuota.periodo or cuota.tipo.value
            por_periodo[clave] = por_periodo.get(clave, 0) + 1

        print(f"Pagos generados: {creados}")
        print(f"Monto total: ${monto_total:,.2f}")
        print("Por período:")
        for periodo in sorted(por_periodo):
            print(f"  {periodo}: {por_periodo[periodo]}")

        if dry_run:
            session.rollback()
            print("\n[dry-run] No se guardó nada.")
        else:
            session.commit()
            print("\nGuardado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar el resumen, no guardar nada")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Falta DATABASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)

    generar(database_url, dry_run=args.dry_run)
