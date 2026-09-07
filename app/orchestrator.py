"""Arma el contexto de conversación, llama a Claude (claude-haiku-4-5) con las
tools MCP registradas, y devuelve el texto de respuesta para mandar por WhatsApp.

Sin implementar todavía — placeholder de estructura.
"""
import os

from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = "claude-haiku-4-5"

# TODO: cargar historial de conversación por teléfono desde la base
# TODO: resolver rol del teléfono ANTES de llamar al modelo
# TODO: pasar las tools del servidor MCP (app/mcp_server.py)
