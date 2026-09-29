"""Conexión a la Postgres propia de MCP-erp — independiente de st-clares-app
(ver docs/ARCHITECTURE.md, sección "Independencia de st-clares-app")."""
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def normalizar_database_url(url: str) -> str:
    """Fuerza el dialecto plano `postgresql://` (resuelve a psycopg2, la
    única lib de conexión que está en requirements.txt). Sin esto, dos
    variantes del DATABASE_URL que da Railway rompen el arranque:
    `postgres://` (esquema viejo, SQLAlchemy 2.x no lo reconoce — hace
    falta "postgresql") y `postgresql+psycopg://` (pide psycopg v3
    explícito, que no está instalado — "ModuleNotFoundError: No module
    named 'psycopg'", 2026-09-28). migrations/env.py usa esta misma
    función para que alembic no pise el mismo problema."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    return url


_raw_url = os.environ["DATABASE_URL"]
DATABASE_URL = normalizar_database_url(_raw_url)
# Diagnóstico temporal (2026-09-28): el arranque en Railway falla con
# "No module named 'psycopg'" incluso después de normalizar acá arriba —
# necesitamos ver el esquema REAL para saber qué formato manda Railway.
# Solo imprime lo que hay antes de "://" (nunca user/pass/host), sacar
# después de confirmar la causa.
print(f"[db] esquema DATABASE_URL crudo={_raw_url.split('://', 1)[0]!r} normalizado={DATABASE_URL.split('://', 1)[0]!r}", file=sys.stderr)

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
