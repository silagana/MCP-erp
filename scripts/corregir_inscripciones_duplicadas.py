"""Corrige un bug de scripts/importar_excel_alumnos.py: para alumnos que ya
existían (migrados de st-clares-app), el import de ALUMNOS_2026.xlsx creó
una inscripción NUEVA por cada fila (matcheando por alumno_id + curso_id)
en vez de reusar la inscripción vieja — porque el nombre del curso en el
Excel no coincidía textualmente con el de la migración original (ej.
"Adulto" vs "TMC SENIOR (Jue 10hs)"). Resultado: esos alumnos quedaron con
DOS inscripciones activas en paralelo, y la vieja siguió teniendo cuotas
"pendiente" que nunca se iban a cobrar — eso infló el número de morosidad
del dashboard.

Decisión (2026-09-16): el Excel es la fuente de verdad. La inscripción
que NO haya sido creada por el import de hoy queda **desactivada**, y sus
cuotas pendientes/parciales se **condonan** (se anulan, NO se marcan como
pagadas).

Cómo se identifica cuál es "la de hoy": el intento original usaba
`fecha_inscripcion == 2026-03-01` como marca, pero resultó que MUCHAS
inscripciones viejas (de la migración de st-clares-app) también tienen
esa fecha — no sirve para distinguir. En cambio, el **id** de la fila sí
sirve: es un contador que solo crece, y el import de hoy corrió una sola
vez, después de que existiera todo lo demás — así que para cualquier
alumno con inscripciones duplicadas, la de mayor id es siempre la de hoy.

Solo actúa sobre alumnos con EXACTAMENTE 2 inscripciones activas (el caso
que se repite en la práctica). Si un alumno tiene 3 o más, se lista aparte
para revisión manual — ahí sí podría haber una inscripción vieja
legítimamente separada (ej. dos cursos en paralelo) y no quiero adivinar.

Uso:
    DATABASE_URL=... python scripts/corregir_inscripciones_duplicadas.py --dry-run
    DATABASE_URL=... python scripts/corregir_inscripciones_duplicadas.py
"""
import argparse
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Alumno, Cuota, EstadoCuotaEnum, Inscripcion


def corregir(database_url: str, dry_run: bool) -> None:
    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        por_alumno: dict[int, list[Inscripcion]] = {}
        for ins in session.query(Inscripcion).filter(Inscripcion.activa == True).all():
            por_alumno.setdefault(ins.alumno_id, []).append(ins)

        con_duplicado = {aid: inss for aid, inss in por_alumno.items() if len(inss) > 1}

        alumnos_corregidos = 0
        inscripciones_desactivadas = 0
        cuotas_condonadas = 0
        monto_condonado = Decimal("0.00")
        casos_ambiguos: list[tuple[Alumno, list[Inscripcion]]] = []

        for alumno_id, inscripciones in con_duplicado.items():
            if len(inscripciones) != 2:
                casos_ambiguos.append((session.get(Alumno, alumno_id), inscripciones))
                continue

            inscripciones_ordenadas = sorted(inscripciones, key=lambda i: i.id)
            viejas = inscripciones_ordenadas[:-1]  # todas menos la de mayor id

            for ins_vieja in viejas:
                ins_vieja.activa = False
                inscripciones_desactivadas += 1
                for cuota in ins_vieja.cuotas:
                    if cuota.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                        monto_condonado += cuota.saldo_pendiente
                        cuota.estado = EstadoCuotaEnum.condonada
                        cuota.saldo_pendiente = Decimal("0.00")
                        cuotas_condonadas += 1
            alumnos_corregidos += 1

        print(f"Alumnos con inscripción duplicada detectados: {len(con_duplicado)}")
        print(f"Alumnos corregidos (1 nueva + 1+ viejas, caso claro): {alumnos_corregidos}")
        print(f"Inscripciones viejas desactivadas: {inscripciones_desactivadas}")
        print(f"Cuotas condonadas: {cuotas_condonadas}")
        print(f"Monto total condonado: ${monto_condonado:,.2f}")

        if casos_ambiguos:
            print(f"\nCasos ambiguos, NO tocados ({len(casos_ambiguos)}) — revisar a mano:")
            for alumno, inss in casos_ambiguos:
                detalle = [(i.id, i.curso.nombre, i.fecha_inscripcion.isoformat()) for i in inss]
                print(f"  - {alumno.apellido}, {alumno.nombre}: {detalle}")

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

    corregir(database_url, dry_run=args.dry_run)
