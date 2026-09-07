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

Scaffold + esquema + services copiados. Sin tools del servidor MCP todavía,
sin orchestrator ni webhook implementados. Próximos pasos en
`docs/ARCHITECTURE.md`, sección "Plan".

## Estructura

```
app/
  models.py            # esquema propio (copiado de st-clares-app + tabla usuario_whatsapp)
  db.py                # conexión a la Postgres propia (DATABASE_URL)
  services/
    enrollment.py       # copiado de st-clares-app: cuotas, precios, altas/bajas
    reconciliation.py   # copiado de st-clares-app: conciliación FIFO, matching
    bank_import.py       # copiado de st-clares-app: parseo de resumen bancario
    cash_payments.py     # stub — sin implementar (tampoco estaba en el origen)
  utils/
    audit.py             # copiado de st-clares-app
    formatters.py         # copiado de st-clares-app
  mcp_server.py          # servidor MCP: expone las tools (sin implementar)
  whatsapp_webhook.py     # recibe mensajes entrantes de WhatsApp Cloud API
  orchestrator.py         # arma el prompt, llama a Claude con las tools MCP
scripts/
  migrate_from_st_clares.py  # migración única de datos desde la Postgres de st-clares-app
docs/
  ARCHITECTURE.md       # diseño completo: esquema, roles, tools, plan
```

## Variables de entorno (`.env`, no versionado)

- `DATABASE_URL` — Postgres propia de MCP-erp
- `ANTHROPIC_API_KEY`
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`
- `SOURCE_DATABASE_URL` — solo para la migración única (`scripts/migrate_from_st_clares.py`)

## Deploy

Railway. Servicio Postgres propio (separado del de `st-clares-app`).
