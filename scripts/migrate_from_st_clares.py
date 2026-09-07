"""Migración única de datos: st-clares-app (Postgres origen) -> MCP-erp (Postgres propia).

Se corre UNA sola vez, antes de dar de baja st-clares-app (ver docs/ARCHITECTURE.md,
sección "Independencia de st-clares-app"). Copia todas las tablas de dominio en
orden que respeta foreign keys. No migra `Profesor` ni `UsuarioWhatsapp`: son
tablas nuevas de MCP-erp que no existen del lado de origen.

Uso:
    SOURCE_DATABASE_URL=postgresql://...st-clares-app... \
    DATABASE_URL=postgresql://...mcp-erp... \
    python scripts/migrate_from_st_clares.py [--dry-run]

Requiere que el esquema de destino ya exista (correr las migraciones/Base.metadata.create_all
de app/models.py antes de esto).
"""
import argparse
import os
import sys

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

# Orden de migración: padres antes que hijos, respeta foreign keys.
TABLE_ORDER = [
    "sede",
    "curso",
    "precio_historico",
    "alumno",
    "referente_pago",
    "inscripcion",
    "cuota",
    "movimiento_bancario",
    "pago",
    "imputacion",
    "alias_cobro_alumno",
    "patron_no_alumno",
    "audit_log",
]


def migrate(source_url: str, dest_url: str, dry_run: bool = False) -> None:
    src_engine = create_engine(source_url, future=True)
    dst_engine = create_engine(dest_url, future=True)

    src_meta = MetaData()
    dst_meta = MetaData()

    with src_engine.connect() as src_conn, dst_engine.connect() as dst_conn:
        for table_name in TABLE_ORDER:
            src_table = Table(table_name, src_meta, autoload_with=src_engine)
            dst_table = Table(table_name, dst_meta, autoload_with=dst_engine)

            rows = [dict(r._mapping) for r in src_conn.execute(select(src_table))]

            if dry_run:
                print(f"[dry-run] {table_name}: {len(rows)} filas a migrar")
                continue

            if not rows:
                print(f"{table_name}: 0 filas, nada que migrar")
                continue

            dst_conn.execute(dst_table.insert(), rows)
            dst_conn.commit()
            print(f"{table_name}: {len(rows)} filas migradas")

        if not dry_run:
            # Resincroniza las secuencias de PK autoincremental (Postgres) para
            # que las próximas altas en MCP-erp no choquen con los IDs migrados.
            for table_name in TABLE_ORDER:
                dst_conn.execute(
                    __import__("sqlalchemy").text(
                        f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1))"
                    )
                )
            dst_conn.commit()
            print("Secuencias de PK resincronizadas.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo cuenta filas, no escribe nada")
    args = parser.parse_args()

    source_url = os.environ.get("SOURCE_DATABASE_URL")
    dest_url = os.environ.get("DATABASE_URL")

    if not source_url or not dest_url:
        print("Faltan SOURCE_DATABASE_URL y/o DATABASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)

    migrate(source_url, dest_url, dry_run=args.dry_run)
