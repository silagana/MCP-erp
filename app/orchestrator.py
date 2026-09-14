"""Arma el contexto de conversación, llama a Claude (claude-haiku-4-5) con las
tools del servidor MCP (app/mcp_server.py), y devuelve el texto de respuesta
para mandar por WhatsApp.

Flujo por mensaje entrante (procesar_mensaje):
1. Resuelve el rol del teléfono. Si no está registrado, responde sin llamar
   a Claude (ahorra costo y evita confundir a alguien sin acceso).
2. Carga las últimas MAX_HISTORIAL líneas de conversación con ese teléfono.
3. Llama a Claude con las tools del MCP (sin el parámetro `telefono` — eso
   se inyecta acá con el número real de quien escribió, nunca lo elige el
   modelo, para que no se pueda pedir una acción "en nombre de" otro número).
4. Si Claude pide usar una tool, la ejecuta contra app/mcp_server.py y le
   devuelve el resultado, hasta MAX_TOOL_ITERATIONS pasos o hasta que
   responda con texto final.
5. Guarda el turno (mensaje del usuario + respuesta final) en el historial.

Nota: las consultas a la base acá son síncronas (SQLAlchemy normal) dentro
de funciones async — bloquean el loop brevemente. Para el volumen de un
instituto chico no es un problema; si hace falta más concurrencia más
adelante, pasar a un engine async.
"""
import asyncio
import os

from anthropic import AsyncAnthropic

from app.db import SessionLocal
from app.mcp_server import server
from app.models import MensajeWhatsapp, RolMensajeEnum
from app.permissions import resolver_usuario
from mcp.server.mcpserver.exceptions import ToolError

MODEL = "claude-haiku-4-5"
MAX_HISTORIAL = 20
MAX_TOOL_ITERATIONS = 6

client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT_BASE = """Sos el asistente de WhatsApp de St. Clare's, instituto de inglés en Buenos Aires.
Respondés en español rioplatense, de forma clara y concisa (esto es WhatsApp, no un email formal).
Usá las tools disponibles para consultar o modificar datos reales — nunca inventes montos, fechas ni estados, ni asumas datos que no te dieron.
Antes de ejecutar una acción que no se puede deshacer fácilmente (dar de baja, aplicar una conciliación, cargar un aumento, eliminar una cuota), resumí en una línea lo que vas a hacer y pedí confirmación explícita antes de llamar a la tool.
Si el usuario pide algo para lo que no tiene permiso, o para lo que no hay una tool todavía, decíselo con claridad y sin detalles técnicos internos."""


async def _tools_para_claude() -> list[dict]:
    """Tools del servidor MCP en formato Anthropic, sin el parámetro `telefono`
    (se inyecta en _ejecutar_tool, nunca lo decide el modelo)."""
    mcp_tools = await server.list_tools()
    claude_tools = []
    for t in mcp_tools:
        schema = dict(t.input_schema)
        propiedades = dict(schema.get("properties", {}))
        propiedades.pop("telefono", None)
        schema["properties"] = propiedades
        schema["required"] = [r for r in schema.get("required", []) if r != "telefono"]
        claude_tools.append({
            "name": t.name,
            "description": t.description or "",
            "input_schema": schema,
        })
    return claude_tools


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
    """Punto de entrada único: llamado por app/whatsapp_webhook.py con el
    número del remitente y el texto del mensaje. Devuelve el texto a responder."""
    with SessionLocal() as session:
        usuario = resolver_usuario(session, telefono)
        rol_valor = usuario.rol.value if usuario else None

    if usuario is None:
        return (
            "Todavía no tenés acceso habilitado a este sistema. "
            "Pedile a la administración de St. Clare's que registre tu número."
        )

    mensajes = _cargar_historial(telefono) + [{"role": "user", "content": texto}]
    tools = await _tools_para_claude()
    system = f"{SYSTEM_PROMPT_BASE}\n\nRol del usuario actual: {rol_valor}."

    texto_final = "Perdón, tuve un problema procesando tu pedido. Probá de nuevo en un rato."

    for _ in range(MAX_TOOL_ITERATIONS):
        respuesta = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            tools=tools,
            messages=mensajes,
        )

        if respuesta.stop_reason != "tool_use":
            texto_final = "".join(
                bloque.text for bloque in respuesta.content if bloque.type == "text"
            ).strip() or "Listo."
            break

        respuesta_dict = respuesta.model_dump()
        mensajes.append({"role": "assistant", "content": respuesta_dict["content"]})

        tool_results = []
        for bloque in respuesta.content:
            if bloque.type != "tool_use":
                continue
            resultado_texto = await _ejecutar_tool(bloque.name, bloque.input, telefono)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": bloque.id,
                "content": resultado_texto,
            })
        mensajes.append({"role": "user", "content": tool_results})

    _guardar_turno(telefono, texto, texto_final)
    return texto_final
