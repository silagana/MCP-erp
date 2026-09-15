"""Envía un resumen semanal de KPIs por Telegram a owner/administrativo.

Pensado para correr como Railway Cron Job (no queda prendido, se dispara
solo y se apaga — ver README, sección Deploy). Manda un texto corto con
los números clave + un link al dashboard completo.

Deliberadamente NO manda por WhatsApp: un mensaje que el sistema inicia
solo (no en respuesta a algo que escribió el usuario) es exactamente el
tipo de "comunicación saliente" que dejamos en pausa para WhatsApp — ver
docs/ARCHITECTURE.md sección 7 (cambio de tarifas de Meta desde el
1/10/2026, todavía sin confirmar el costo para Argentina). Cuando eso se
destrabe, extender este mismo script a WhatsApp con la misma lógica de
_armar_texto/kpis, solo cambia el transporte de envío.

Uso:
    DATABASE_URL=... TELEGRAM_BOT_TOKEN=... PUBLIC_BASE_URL=... \
        python scripts/enviar_resumen_semanal.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.dashboard import crear_token_reporte, kpis_alumnos, kpis_ingresos, kpis_morosidad
from app.models import RolWhatsappEnum, UsuarioWhatsapp

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _armar_texto(datos_alumnos: dict, datos_ingresos: dict, datos_morosidad: dict, url: str) -> str:
    return (
        "Resumen semanal - St. Clare's\n\n"
        f"Alumnos activos: {datos_alumnos['activos']}\n"
        f"Cobrado este mes: ${datos_ingresos['total_mes_actual']:,.0f}\n"
        f"Adeudado (morosos): ${datos_morosidad['total_adeudado']:,.0f} "
        f"({datos_morosidad['cantidad_alumnos']} alumnos)\n\n"
        f"Ver el dashboard completo: {url}"
    )


def enviar_telegram(chat_id: str, texto: str) -> None:
    resp = httpx.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": texto},
        timeout=20,
    )
    resp.raise_for_status()


def main() -> None:
    if not TELEGRAM_BOT_TOKEN or not PUBLIC_BASE_URL:
        print("Faltan TELEGRAM_BOT_TOKEN y/o PUBLIC_BASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Falta DATABASE_URL en el entorno.", file=sys.stderr)
        sys.exit(1)

    engine = create_engine(database_url, future=True)
    with Session(engine) as session:
        datos_alumnos = kpis_alumnos(session)
        datos_ingresos = kpis_ingresos(session)
        datos_morosidad = kpis_morosidad(session)

        destinatarios = (
            session.query(UsuarioWhatsapp)
            .filter(
                UsuarioWhatsapp.rol.in_([RolWhatsappEnum.owner, RolWhatsappEnum.administrativo]),
                UsuarioWhatsapp.activo == True,
                UsuarioWhatsapp.telegram_chat_id.isnot(None),
            )
            .all()
        )

        if not destinatarios:
            print("Nadie con rol owner/administrativo tiene telegram_chat_id vinculado todavía — nada para enviar.")
            return

        for usuario in destinatarios:
            token = crear_token_reporte(session, usuario)
            url = f"{PUBLIC_BASE_URL}/reportes/{token}"
            texto = _armar_texto(datos_alumnos, datos_ingresos, datos_morosidad, url)
            try:
                enviar_telegram(usuario.telegram_chat_id, texto)
                print(f"Enviado a {usuario.telefono} (chat_id={usuario.telegram_chat_id})")
            except Exception as exc:
                print(f"Error enviando a {usuario.telefono}: {exc}", file=sys.stderr)

        session.commit()


if __name__ == "__main__":
    main()
