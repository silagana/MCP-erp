"""Canal de prueba alternativo por Telegram — vive como un router más,
montado sobre el mismo servicio/proceso que app/whatsapp_webhook.py (igual
que app/dashboard.py). No hace falta un servicio de Railway aparte.

Reusa procesar_mensaje() de app/orchestrator.py tal cual — el resto del
sistema (permisos, tools, historial) no sabe ni le importa si el mensaje
vino de Telegram o WhatsApp, todo se resuelve por `telefono`.

Cómo "desactivar" este canal más adelante (sin tocar Railway ni WhatsApp):
llamar a `https://api.telegram.org/bot<TOKEN>/deleteWebhook` — Telegram
deja de mandar mensajes a /telegram/webhook. La ruta sigue existiendo en
el código pero no le pega nadie, no afecta en nada a WhatsApp.

Identidad: un chat de Telegram se vincula a un usuario_whatsapp YA
EXISTENTE por teléfono (no crea usuarios nuevos). El comando /vincular
<telefono> hace esa asociación. Es intencionalmente simple porque este
canal es solo para pruebas internas del equipo del instituto, no para
alumnos — no hay verificación de que quien escribe "es dueño" de ese
teléfono, a diferencia de WhatsApp donde el número mismo es la identidad.
"""
import os

import httpx
from fastapi import APIRouter, BackgroundTasks, Request

from app.db import SessionLocal
from app.orchestrator import procesar_mensaje
from app.permissions import resolver_telefono_por_telegram, vincular_telegram

router = APIRouter()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def _enviar_respuesta(chat_id: str, texto: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
        )
        resp.raise_for_status()


def _vincular(chat_id: str, telefono: str) -> str:
    with SessionLocal() as session:
        usuario = vincular_telegram(session, chat_id, telefono)
        if usuario is None:
            return f"No encontré ningún usuario con el teléfono {telefono}. Pedile a un owner que lo registre primero en usuario_whatsapp."
        session.commit()
        return f"Listo, vinculado como {usuario.rol.value}."


async def _procesar_y_responder(chat_id: str, texto: str) -> None:
    if texto.startswith("/vincular"):
        partes = texto.split(maxsplit=1)
        if len(partes) != 2:
            await _enviar_respuesta(chat_id, "Uso: /vincular <telefono> (ej. /vincular 5491141996958)")
            return
        respuesta = _vincular(chat_id, partes[1].strip())
        await _enviar_respuesta(chat_id, respuesta)
        return

    with SessionLocal() as session:
        telefono = resolver_telefono_por_telegram(session, chat_id)

    if telefono is None:
        await _enviar_respuesta(
            chat_id,
            "Tu chat de Telegram todavía no está vinculado. Mandá /vincular <tu_telefono> "
            "(el mismo que ya está registrado en usuario_whatsapp).",
        )
        return

    respuesta = await procesar_mensaje(telefono, texto)
    await _enviar_respuesta(chat_id, respuesta)


@router.post("/telegram/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    mensaje = payload.get("message") or payload.get("edited_message")
    if not mensaje:
        return {"status": "ignored"}

    chat_id = str(mensaje.get("chat", {}).get("id", ""))
    texto = (mensaje.get("text") or "").strip()
    if not chat_id or not texto:
        return {"status": "ignored"}

    background_tasks.add_task(_procesar_y_responder, chat_id, texto)
    return {"status": "received"}
