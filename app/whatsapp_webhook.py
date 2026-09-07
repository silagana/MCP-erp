"""Recibe mensajes de WhatsApp Cloud API y los pasa al orchestrator.

Sin implementar todavía — placeholder de estructura.
"""
import os

from fastapi import FastAPI, Request

app = FastAPI()

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


@app.get("/webhook")
async def verify(request: Request):
    """Verificación inicial que exige Meta al configurar el webhook."""
    params = request.query_params
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    return {"error": "invalid verify token"}


@app.post("/webhook")
async def receive(request: Request):
    payload = await request.json()
    # TODO: extraer mensaje + número de teléfono, pasar a app/orchestrator.py
    return {"status": "received"}
