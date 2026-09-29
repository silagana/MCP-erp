"""Conexión a la Postgres propia de MCP-erp — independiente de st-clares-app
(ver docs/ARCHITECTURE.md, sección "Independencia de st-clares-app")."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def normalizar_database_url(url: str) -> str:
    """Railway a veces todavía manda el esquema viejo `postgres://`, que
    SQLAlchemy 2.x no reconoce (hace falta "postgresql"). migrations/env.py
    usa esta misma función para que alembic no pise el mismo problema."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


DATABASE_URL = normalizar_database_url(os.environ["DATABASE_URL"])

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
