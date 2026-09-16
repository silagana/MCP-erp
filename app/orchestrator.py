"""Arma el contexto de conversación, llama al modelo (Qwen3 vía Groq, API
compatible con OpenAI) con las tools del servidor MCP (app/mcp_server.py),
y devuelve el texto de respuesta para mandar por WhatsApp/Telegram.

Cambio de proveedor (2026-09-16): estaba en Claude Haiku directo contra la
API de Anthropic, pero la cuenta quedó en revisión ("organization on hold")
sin ETA. Se migró a Groq (mismo SDK `openai`, apuntando a
https://api.groq.com/openai/v1) con `qwen/qwen3.8-27b` — modelo open-source
(Apache 2.0), tool-calling probado para workflows agénticos, sin el trámite
de alta que trabó a Anthropic. El diseño quedó desacoplado del proveedor:
como Groq es compatible con el formato de OpenAI, volver a Claude (vía
Anthropic directo, o Bedrock/Vertex) o probar otro modelo es cambiar
MODEL/BASE_URL/API_KEY acá, no reescribir el loop.

Flujo por mensaje entrante (procesar_mensaje):
1. Resuelve el rol del teléfono. Si no está registrado, responde sin llamar
   al modelo (ahorra costo y evita confundir a alguien sin acceso).
2. Carga las últimas MAX_HISTORIAL líneas de conversación con ese teléfono.
3. Llama al modelo con las tools del MCP (sin el parámetro `telefono` — eso
   se inyecta acá con el número real de quien escribió, nunca lo elige el
   modelo, para que no se pueda pedir una acción "en nombre de" otro número).
4. Si el modelo pide usar una tool, la ejecuta contra app/mcp_server.py y le
   devuelve el resultado, hasta MAX_TOOL_ITERATIONS pasos o hasta que
   responda con texto final.
5. Guarda el turno (mensaje del usuario + respuesta final) en el historial.

Nota: las consultas a la base acá son síncronas (SQLAlchemy normal) dentro
de funciones async — bloquean el loop brevemente. Para el volumen de un
instituto chico no es un problema; si hace falta más concurrencia más
adelante, pasar a un engine async.
"""
import json
import os

from openai import AsyncOpenAI

from app.db import SessionLocal
from app.mcp_server import server
from app.models import MensajeWhatsapp, RolMensajeEnum
from app.permissions import resolver_usuario
from mcp.server.mcpserver.exceptions import ToolError

MODEL = "qwen/qwen3.8-27b"
BASE_URL = "https://api.groq.com/openai/v1"
MAX_HISTORIAL = 20
MAX_TOOL_ITERATIONS = 6
# El tier gratuito/on-demand de Groq limita a 1000 tokens de SALIDA por
# minuto (OTPM) — pedir max_tokens=1024 supera ese límite en un solo
# request y Groq lo rechaza con 429 siempre, no importa cuánto tráfico
# haya. Tiene que quedar estrictamente por debajo de 1000.
MAX_TOKENS_RESPUESTA = 800

client = AsyncOpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=BASE_URL)

SYSTEM_PROMPT_BASE = """Sos el asistente de WhatsApp de St. Clare's, instituto de inglés en Buenos Aires.
Respondés en español rioplatense, de forma clara y concisa (esto es WhatsApp, no un email formal).
Usá las tools disponibles para consultar o modificar datos reales — nunca inventes montos, fechas ni estados, ni asumas datos que no te dieron.
Antes de ejecutar una acción que no se puede deshacer fácilmente (dar de baja, aplicar una conciliación, cargar un aumento, eliminar una cuota), resumí en una línea lo que vas a hacer y pedí confirmación explícita antes de llamar a la tool.
Si el usuario pide algo para lo que no tiene permiso, o para lo que no hay una tool todavía, decíselo con claridad y sin detalles técnicos internos."""


async def _tools_para_llm() -> list[dict]:
    """Tools del servidor MCP en formato de function-calling (OpenAI/Groq),
    sin el parámetro `telefono` (se inyecta en _ejecutar_tool, nunca lo
    decide el modelo)."""
    mcp_tools = await server.list_tools()
    tools_llm = []
    for t in mcp_tools:
        schema = dict(t.input_schema)
        propiedades = dict(schema.get("properties", {}))
        propiedades.pop("telefono", None)
        schema["properties"] = propiedades
        schema["required"] = [r for r in schema.get("required", []) if r != "telefono"]
        tools_llm.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": schema,
            },
        })
    return tools_llm


async def _ejecutar_tool(nombre: str, argumentos: dict, telefono: str) -> str:
    argumentos = dict(argumentos)
    argumentos["telefono"] = telefono
    try:
        resultado = await server.call_tool(nombre, argumentos)
    except ToolError as exc:
        return f"Error: {exc}"
    partes = [c.text for c in resultado.content if hasattr(c, "text")]
    return "\n".join(partes) if partes else "(sin resultado)"


def _cargar_historial(telefono: str) -> list[dict]:
    with SessionLocal() as session:
        mensajes = (
            session.query(MensajeWhatsapp)
            .filter(MensajeWhatsapp.telefono == telefono)
            .order_by(MensajeWhatsapp.timestamp.desc())
            .limit(MAX_HISTORIAL)
            .all()
        )
        return [{"role": m.rol.value, "content": m.contenido} for m in reversed(mensajes)]


def _guardar_turno(telefono: str, texto_usuario: str, texto_asistente: str) -> None:
    with SessionLocal() as session:
        session.add(MensajeWhatsapp(telefono=telefono, rol=RolMensajeEnum.user, contenido=texto_usuario))
        session.add(MensajeWhatsapp(telefono=telefono, rol=RolMensajeEnum.assistant, contenido=texto_asistente))
        session.commit()


async def procesar_mensaje(telefono: str, texto: str) -> str:
    """Punto de entrada único: llamado por app/whatsapp_webhook.py (o
    app/telegram_webhook.py) con el número del remitente y el texto del
    mensaje. Devuelve el texto a responder."""
    with SessionLocal() as session:
        usuario = resolver_usuario(session, telefono)
        rol_valor = usuario.rol.value if usuario else None

    if usuario is None:
        return (
            "Todavía no tenés acceso habilitado a este sistema. "
            "Pedile a la administración de St. Clare's que registre tu número."
        )

    system = f"{SYSTEM_PROMPT_BASE}\n\nRol del usuario actual: {rol_valor}."
    mensajes = [{"role": "system", "content": system}] + _cargar_historial(telefono) + [
        {"role": "user", "content": texto}
    ]
    tools = await _tools_para_llm()

    texto_final = "Perdón, tuve un problema procesando tu pedido. Probá de nuevo en un rato."

    for _ in range(MAX_TOOL_ITERATIONS):
        respuesta = await client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS_RESPUESTA,
            tools=tools,
            messages=mensajes,
        )
        mensaje = respuesta.choices[0].message

        if respuesta.choices[0].finish_reason != "tool_calls" or not mensaje.tool_calls:
            texto_final = (mensaje.content or "").strip() or "Listo."
            break

        mensajes.append({
            "role": "assistant",
            "content": mensaje.content,
            "tool_calls": [tc.model_dump() for tc in mensaje.tool_calls],
        })

        for tc in mensaje.tool_calls:
            try:
                argumentos = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                argumentos = {}
            resultado_texto = await _ejecutar_tool(tc.function.name, argumentos, telefono)
            mensajes.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": resultado_texto,
            })

    _guardar_turno(telefono, texto, texto_final)
    return texto_final
