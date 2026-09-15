# MCP-erp

ERP conversacional por WhatsApp para St. Clare's (instituto de inglés, Buenos Aires).

Servidor MCP + orchestrator + webhook de WhatsApp. **Independiente de
[`st-clares-app`](https://github.com/silagana/st-clares-app)** (el panel
Streamlit original): tiene su propia base Postgres y su propio esquema. El
esquema y los services (`enrollment.py`, `reconciliation.py`, `bank_import.py`)
se copiaron desde `st-clares-app` para no perder la lógica ya validada, pero
de acá en adelante evolucionan por separado — ver
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), sección "Independencia de
st-clares-app".

## Estado actual

Funcionando de punta a punta: esquema propio + datos reales migrados, 20
tools del servidor MCP, orchestrator con `claude-haiku-4-5`, webhook de
WhatsApp — validado con un teléfono real vía el número de prueba de Meta.
Falta un número de WhatsApp de producción (el real del instituto sigue en
uso diario, se migra más adelante) y completar `cash_payments.py`. Detalle
completo del avance en `docs/ARCHITECTURE.md`, sección "Plan".

## Estructura

```
app/
  models.py            # esquema propio (copiado de st-clares-app + usuario_whatsapp, mensaje_whatsapp)
  db.py                # conexión a la Postgres propia (DATABASE_URL)
  permissions.py        # resolución de rol por teléfono — el control de acceso vive acá
  services/
    enrollment.py       # copiado de st-clares-app: cuotas, precios, altas/bajas
    reconciliation.py   # copiado de st-clares-app: conciliación FIFO, matching
    bank_import.py       # copiado de st-clares-app: parseo de resumen bancario
    cash_payments.py     # stub — sin implementar (tampoco estaba en el origen)
  utils/
    audit.py             # copiado de st-clares-app
    formatters.py         # copiado de st-clares-app
  mcp_server.py          # servidor MCP: 20 tools (alumnos, cuotas, conciliación, referencia)
  whatsapp_webhook.py     # canal principal: recibe mensajes de WhatsApp Cloud API
  telegram_webhook.py     # canal de prueba alternativo (ver sección Deploy) — separado a propósito
  orchestrator.py         # arma el prompt, llama a Claude con las tools MCP
migrations/
  versions/              # migraciones de Alembic
scripts/
  migrate_from_st_clares.py     # migración única de datos desde la Postgres de st-clares-app
  normalizar_telefonos.py       # normaliza teléfonos migrados al formato WhatsApp (549 + 10 dígitos)
  seed_usuario_whatsapp.py      # carga (idempotente) de números autorizados
docs/
  ARCHITECTURE.md       # diseño completo: esquema, roles, tools, plan
```

## Variables de entorno (`.env`, no versionado)

- `DATABASE_URL` — Postgres propia de MCP-erp
- `ANTHROPIC_API_KEY`
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` — canal principal
- `TELEGRAM_BOT_TOKEN` — solo si se usa el canal de prueba de Telegram
- `SOURCE_DATABASE_URL` — solo para la migración única (`scripts/migrate_from_st_clares.py`)

## Deploy

Railway. Tres servicios en el mismo proyecto:

1. **Postgres** — propia, separada de la de `st-clares-app`.
2. **MCP-erp** (canal WhatsApp) — `startCommand` en `railway.toml`:
   `uvicorn app.whatsapp_webhook:app --host 0.0.0.0 --port $PORT`.
3. **MCP-erp-telegram** (canal de prueba, opcional) — mismo repo, pero con
   un **Custom Start Command propio** seteado a mano en Railway (Settings →
   Deploy → Start Command, sobreescribe el de `railway.toml` para ese
   servicio):
   `uvicorn app.telegram_webhook:app --host 0.0.0.0 --port $PORT`.
   Necesita su propia variable `TELEGRAM_BOT_TOKEN` (se crea gratis con
   [@BotFather](https://t.me/BotFather) en Telegram). Una vez desplegado,
   registrar el webhook con:
   ```
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<dominio-de-ese-servicio>/telegram/webhook"
   ```
   **Para desactivarlo**: parar o eliminar este servicio en Railway. No
   afecta al servicio de WhatsApp ni a la base — comparten la misma
   Postgres y el mismo código, pero corren como procesos independientes.
