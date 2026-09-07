# MCP-erp

ERP conversacional por WhatsApp para St. Clare's (instituto de inglés, Buenos Aires).

Este repo es la capa nueva: servidor MCP + orchestrator + webhook de WhatsApp. **No duplica el esquema de datos ni la lógica de negocio** — se conecta a la misma base Postgres que usa [`st-clares-app`](https://github.com/silagana/st-clares-app) (el panel Streamlit ya existente) y reutiliza sus reglas (generación de cuotas, conciliación bancaria FIFO, etc.).

Ver el diseño completo en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Estado actual

Repo recién creado — scaffold inicial, sin lógica de negocio todavía. Próximos pasos en `docs/ARCHITECTURE.md`, sección "Plan".

## Estructura

```
app/
  mcp_server.py      # servidor MCP: expone las tools sobre la base de st-clares-app
  db.py              # conexión a la misma Postgres (DATABASE_URL)
  models.py          # modelos SQLAlchemy — copia sincronizada de st-clares-app/db/models.py
  whatsapp_webhook.py # recibe mensajes entrantes de WhatsApp Cloud API
  orchestrator.py    # arma el prompt, llama a Claude con las tools MCP, responde
docs/
  ARCHITECTURE.md    # diseño completo: esquema, roles, tools, plan
```

## Variables de entorno (`.env`, no versionado)

- `DATABASE_URL` — misma base que `st-clares-app`
- `ANTHROPIC_API_KEY`
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`

## Deploy

Pensado para Railway, mismo proyecto que `st-clares-app` (comparten la Postgres).
