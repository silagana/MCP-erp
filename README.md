# MCP-erp

ERP conversacional por WhatsApp para St. Clare's (instituto de inglés, Buenos Aires).

Servidor MCP + orchestrator + webhooks (WhatsApp y Telegram) + dashboard de
KPIs, todo en un solo servicio/proceso. **Independiente de
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
  mcp_server.py          # servidor MCP: 21 tools (alumnos, cuotas, conciliación, referencia, dashboard)
  dashboard.py            # KPIs + página HTML del dashboard (/reportes/<token>)
  whatsapp_webhook.py     # entrypoint del proceso: monta dashboard.py y telegram_webhook.py, expone /webhook (WhatsApp)
  telegram_webhook.py     # router del canal de prueba de Telegram (/telegram/webhook), montado en whatsapp_webhook.py
  orchestrator.py         # arma el prompt, llama a Claude con las tools MCP
migrations/
  versions/              # migraciones de Alembic
scripts/
  migrate_from_st_clares.py     # migración única de datos desde la Postgres de st-clares-app
  normalizar_telefonos.py       # normaliza teléfonos migrados al formato WhatsApp (549 + 10 dígitos)
  seed_usuario_whatsapp.py      # carga (idempotente) de números autorizados
  enviar_resumen_semanal.py     # cron semanal: manda KPIs por Telegram (ver Deploy) — no por WhatsApp todavía
docs/
  ARCHITECTURE.md       # diseño completo: esquema, roles, tools, plan
  BRAND.md               # identidad visual de St. Clare's (colores/tipografía reales del sitio)
```

## Variables de entorno (`.env`, no versionado)

- `DATABASE_URL` — Postgres propia de MCP-erp
- `ANTHROPIC_API_KEY`
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` — canal principal
- `TELEGRAM_BOT_TOKEN` — solo si se usa el canal de prueba de Telegram (mismo servicio, misma variable list)
- `PUBLIC_BASE_URL` — URL pública del servicio, sin barra final (para armar los links de `/reportes/<token>`)
- `SOURCE_DATABASE_URL` — solo para la migración única (`scripts/migrate_from_st_clares.py`)

## Deploy

Railway. **Importante: un solo servicio de aplicación** (`MCP-erp`) además
de Postgres — WhatsApp, Telegram y el dashboard corren todos en el mismo
proceso/deploy. **No crear un segundo servicio para Telegram** — es un
error fácil de cometer (pasó una vez) que además deja el `Custom Start
Command` del servicio principal apuntando a `telegram_webhook` en vez de
`whatsapp_webhook`, cortando WhatsApp sin querer.

1. **Postgres** — propia, separada de la de `st-clares-app`.
2. **MCP-erp** (único servicio de app) — `startCommand` en `railway.toml`,
   **no lo toques**: `uvicorn app.whatsapp_webhook:app --host 0.0.0.0 --port $PORT`.
   Variables acá: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `WHATSAPP_TOKEN`,
   `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `TELEGRAM_BOT_TOKEN`,
   `PUBLIC_BASE_URL` — todas juntas en este mismo servicio.

   **Activar Telegram**: crear el bot con [@BotFather](https://t.me/BotFather),
   cargar `TELEGRAM_BOT_TOKEN`, y registrar el webhook (apuntando al mismo
   dominio de este servicio):
   ```
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<dominio-de-MCP-erp>/telegram/webhook"
   ```
   **Desactivar Telegram**: `curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`
   — un solo comando, no hace falta tocar Railway ni el código.
3. **MCP-erp-resumen-semanal** (cron, opcional) — mismo repo, pero como
   tipo de servicio **"Cron Job"** en vez de un servidor web:
   - **Cron Schedule**: ej. `0 12 * * 1` (lunes 12:00 UTC = 9:00 hora
     Argentina).
   - **Start Command**: `python scripts/enviar_resumen_semanal.py`.
   - Variables: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `PUBLIC_BASE_URL`
     (las mismas que los otros servicios).
   - Manda el resumen **solo por Telegram** a los usuarios con rol
     owner/administrativo que ya vincularon `telegram_chat_id` (con
     `/vincular` en el bot) — deliberadamente no manda por WhatsApp
     todavía, ver el docstring del script.
