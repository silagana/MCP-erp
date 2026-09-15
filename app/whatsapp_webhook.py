"""Punto de entrada único del servicio: WhatsApp (este archivo), el
dashboard de KPIs (app/dashboard.py) y el canal de prueba de Telegram
(app/telegram_webhook.py) corren todos acá, en el mismo proceso — un solo
servicio de Railway. Ver el docstring de cada módulo para desactivar su
parte sin tocar las demás (ninguna requiere un servicio separado).

GET /webhook: verificación que exige Meta al configurar el webhook la
primera vez (compara hub.verify_token contra WHATSAPP_VERIFY_TOKEN).

POST /webhook: mensajes entrantes de WhatsApp. Responde 200 de inmediato
(Meta reintenta el envío si no responde rápido o si no es 200) y procesa
el mensaje en background — llama al orchestrator y manda la respuesta por
la API de WhatsApp. Solo procesa mensajes de texto; otros tipos (imagen,
audio, ubicación, etc.) se ignoran por ahora.
"""
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from app.dashboard import router as dashboard_router
from app.orchestrator import procesar_mensaje
from app.telegram_webhook import router as telegram_router

app = FastAPI()
# app.include_router(...) tiene un bug de cacheo en la versión de FastAPI de
# este entorno (0.141.1): el router incluido queda envuelto en un
# _IncludedRouter cuyo effective_candidates nunca se recalcula, y sus rutas
# nunca matchean un request real aunque estén bien definidas (confirmado:
# router.routes las tiene bien). Workaround: agregar los APIRoute de cada
# router directo a app.router.routes, igual que quedan las rutas @app.get
# de este mismo archivo — se confirmó que esas sí funcionan.
for _router in (dashboard_router, telegram_router):
    for _route in _router.routes:
        app.router.routes.append(_route)

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")

GRAPH_API_URL = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"


@app.get("/webhook")
async def verify(request: Request):
    """Verificación inicial que exige Meta al configurar el webhook."""
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    return {"error": "invalid verify token"}


async def _enviar_respuesta(telefono: str, texto: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            GRAPH_API_URL,
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"},
            json={
                "messaging_product": "whatsapp",
                "to": telefono,
                "type": "text",
                "text": {"body": texto},
            },
        )
        resp.raise_for_status()


async def _procesar_y_responder(telefono: str, texto: str) -> None:
    respuesta = await procesar_mensaje(telefono, texto)
    await _enviar_respuesta(telefono, respuesta)


@app.post("/webhook")
async def receive(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for mensaje in value.get("messages", []):
                telefono = mensaje.get("from")
                if not telefono or mensaje.get("type") != "text":
                    continue
                texto = mensaje.get("text", {}).get("body", "").strip()
                if not texto:
                    continue
                background_tasks.add_task(_procesar_y_responder, telefono, texto)

    return {"status": "received"}
