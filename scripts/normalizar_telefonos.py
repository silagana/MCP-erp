"""Normaliza los teléfonos migrados de st-clares-app al formato que espera
WhatsApp Cloud API (E.164 sin "+": código de país + 9 + área + número, sin
espacios/guiones/paréntesis). Ej: "1164571140.0" -> "5491164571140".

Los datos migrados traen artefactos típicos de Excel: el string literal
"nan" en vez de vacío, y números con ".0" al final. Corregimos ambos.

Solo transforma casos donde el resultado es inequívoco (10 dígitos locales,
o ya viene con 54/549). Todo lo demás queda en NULL — preferible a adivinar
mal y terminar mandando un mensaje al número de otra persona.

Uso:
    DATABASE_URL=postgresql://...mcp-erp... python scripts/normalizar_telefonos.py --dry-run
    DATABASE_URL=postgresql://...mcp-erp... python scripts/normalizar_telefonos.py
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Alumno, Profesor, ReferentePago, Sede

# (modelo, nombre de la columna)
CAMPOS_TELEFONO = [
    (Alumno, "telefono"),
    (Sede, "encargada_telefono"),
    (ReferentePago, "telefono"),
    (Profesor, "telefono"),
]


def normalizar_telefono_ar(raw) -> str | None:
    """Devuelve el teléfono en formato WhatsApp AR, o None si no se puede
    normalizar con confianza."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("", "nan", "none", "null", "-"):
        return None
    if s.endswith(".0"):
        s = s[:-2]

    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    # Ya viene completo: 54 9 + 10 dígitos = 13
    if digits.startswith("549") and len(digits) == 13:
        return digits
    # Viene con 54 pero sin el 9 característico de celulares: 54 + 10 = 12
    if digits.startswith("54") and len(digits) == 12:
        return "549" + digits[2:]

    local = digits
    if local.startswith("0"):
        local = local[1:]
    # "15" insertado después del código de área (viejo prefijo de celular
    # que ya no corresponde en el formato internacional)
    if len(local) == 12 and local[2:4] == "15":
        local = local[:2] + local[4:]

    if len(local) == 10:
        return "549" + local

    return None  # ambiguo — mejor NULL que un número incorrecto


def normalizar(database_url: str, dry_run: bool) -> None:
    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        total_cambiados = total_sin_cambio = total_ambiguos = 0

        for modelo, campo in CAMPOS_TELEFONO:
            filas = session.query(modelo).all()
            for fila in filas:
                original = getattr(fila, campo)
                if original is None:
                    continue
                nuevo = normalizar_telefono_ar(original)
                if nuevo == original:
                    total_sin_cambio += 1
                    continue
                if nuevo is None:
                    total_ambiguos += 1
                    print(f"[{modelo.__tablename__}.{campo} id={fila.id}] '{original}' -> NULL (ambiguo o inválido)")
                else:
                    total_cambiados += 1
                    print(f"[{modelo.__tablename__}.{campo} id={fila.id}] '{original}' -> '{nuevo}'")
                if not dry_run:
                    setattr(fila, campo, nuevo)

        print(f"\nTotales: {total_cambiados} normalizados, {total_ambiguos} puestos en NULL, {total_sin_cambio} ya estaban bien.")

        if dry_run:
            print("[dry-run] No se guardó nada.")
        else:
            session.commit()
            print("Guardado.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar los cambios, no guardar")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Falta DATABASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)

    normalizar(database_url, dry_run=args.dry_run)
