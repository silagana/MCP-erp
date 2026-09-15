"""Recibe mensajes de WhatsApp Cloud API y los pasa al orchestrator.

GET /webhook: verificación que exige Meta al configurar el webhook la
primera vez (compara hub.verify_token contra WHATSAPP_VERIFY_TOKEN).

POST /webhook: mensajes entrantes. Responde 200 de inmediato (Meta
reintenta el envío si no responde rápido o si no es 200) y procesa el
mensaje en background — llama al orchestrator y manda la respuesta por la
API de WhatsApp. Solo procesa mensajes de texto; otros tipos (imagen,
audio, ubicación, etc.) se ignoran por ahora.
"""
import os

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from app.dashboard import router as dashboard_router
from app.orchestrator import procesar_mensaje

app = FastAPI()
# app.include_router(dashboard_router) tiene un bug de cacheo en la versión
# de FastAPI de este entorno (0.141.1): el router incluido queda envuelto en
# un _IncludedRouter cuyo effective_candidates nunca se recalcula, y la ruta
# nunca matchea un request real aunque esté bien definida (confirmado:
# dashboard_router.routes la tiene). Workaround: agregar los APIRoute del
# router directo a app.router.routes, igual que quedan las rutas @app.get
# de este mismo archivo — se confirmó que esas sí funcionan.
for _route in dashboard_router.routes:
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
