"""Conexión a la Postgres propia de MCP-erp — independiente de st-clares-app
(ver docs/ARCHITECTURE.md, sección "Independencia de st-clares-app")."""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
