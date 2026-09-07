"""Carga (idempotente) de números de WhatsApp autorizados en usuario_whatsapp.

Uso:
    DATABASE_URL=postgresql://...mcp-erp... python scripts/seed_usuario_whatsapp.py

Editar la lista USUARIOS de abajo con los números reales antes de correr.
Correr de nuevo no duplica: si el teléfono ya existe, actualiza el rol en
vez de insertar una fila nueva.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import RolWhatsappEnum, UsuarioWhatsapp

# (telefono sin "+" ni espacios, rol)
USUARIOS = [
    ("5491141996958", RolWhatsappEnum.owner),
]


def seed(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        for telefono, rol in USUARIOS:
            existing = (
                session.query(UsuarioWhatsapp)
                .filter(UsuarioWhatsapp.telefono == telefono)
                .first()
            )
            if existing:
                existing.rol = rol
                existing.activo = True
                print(f"{telefono}: actualizado a rol={rol.value}")
            else:
                session.add(UsuarioWhatsapp(telefono=telefono, rol=rol, activo=True))
                print(f"{telefono}: creado con rol={rol.value}")
        session.commit()


if __name__ == "__main__":
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Falta DATABASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)
    seed(database_url)
