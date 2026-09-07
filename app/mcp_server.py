"""Servidor MCP: expone las tools listadas en docs/ARCHITECTURE.md sección 5.

Cada tool debe resolver el rol por número de WhatsApp (tabla usuario_whatsapp,
todavía no creada) ANTES de tocar la base. Ver ARCHITECTURE.md sección 4.

Sin implementar todavía — placeholder de estructura.
"""
from mcp.server import Server

server = Server("st-clares-erp")

# TODO: tools de alumnos/inscripciones (envuelven services/enrollment.py de st-clares-app)
# TODO: tools de cuotas/precios/aumentos
# TODO: tools de cobros y conciliación (envuelven services/reconciliation.py, bank_import.py)
# TODO: comunicación saliente — diseñada pero EN PAUSA, no exponer todavía (ver ARCHITECTURE.md sección 5)
