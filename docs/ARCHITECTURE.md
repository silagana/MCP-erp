# ERP conversacional por WhatsApp — St. Clare's (instituto de inglés, Buenos Aires)

## 1. Concepto

Un ERP donde la única puerta de entrada y salida es WhatsApp. No hay panel web separado para el día a día: alumnos, profesores y administración interactúan con el mismo número, y el sistema resuelve qué puede ver y hacer cada uno según su rol. La complejidad tradicional de un ERP (esquema de datos + carga + visualización) se reemplaza por un agente que conoce el esquema, ejecuta operaciones acotadas por rol, y genera la vista adecuada (texto, WhatsApp Flow o archivo) según lo que se pide.

**Importante — decisión revisada (ver sección 1.1):** existía una base real —
el repo `silagana/st-clares-app` (Streamlit + SQLAlchemy + Alembic). El plan
original era que MCP-erp se conectara a esa misma Postgres. Se decidió en
cambio que **MCP-erp sea independiente**: tiene su propia Postgres y su
propio esquema, copiado de `st-clares-app` como punto de partida. El
Streamlit se da de baja una vez migrados los datos — MCP-erp pasa a ser el
único sistema.

## 1.1 Independencia de st-clares-app (decisión, 2026-09-07)

El plan original (`app/models.py` importando o sincronizado a mano con
`st-clares-app/db/models.py`, misma Postgres) se descartó. En su lugar:

- **Esquema propio:** `app/models.py` es una copia de
  `st-clares-app/db/models.py` (tablas de dominio idénticas: `Sede`, `Curso`,
  `PrecioHistorico`, `Alumno`, `ReferentePago`, `Inscripcion`, `Cuota`,
  `MovimientoBancario`, `Pago`, `Imputacion`, `AliasCobroAlumno`,
  `PatronNoAlumno`, `AuditLog`), más dos tablas nuevas que no existían del
  lado del Streamlit: `Profesor` y `UsuarioWhatsapp`. De acá en adelante el
  esquema evoluciona solo en este repo — no hay sincronización manual
  continua con `st-clares-app`.
- **Services propios:** `enrollment.py`, `reconciliation.py`, `bank_import.py`
  se copiaron tal cual a `app/services/` (imports adaptados a `app.models` en
  vez de `db.models`). `cash_payments.py` sigue como stub — tampoco estaba
  implementado en el origen.
- **Postgres propia:** servicio Railway separado del que usa `st-clares-app`.
- **Migración única de datos:** `scripts/migrate_from_st_clares.py` copia los
  datos existentes (alumnos, cursos, cuotas, pagos, etc.) de la Postgres de
  `st-clares-app` a la Postgres de MCP-erp, tabla por tabla respetando
  foreign keys. Se corre una sola vez, con el esquema de destino ya creado.
- **st-clares-app se abandona:** una vez migrados los datos y validado
  MCP-erp, el panel Streamlit deja de usarse. No hay sincronización
  bidireccional ni convivencia a largo plazo entre las dos apps.

## 2. Arquitectura

```
Alumno o staff (WhatsApp)
        |  mensaje
        v
WhatsApp Cloud API (único canal in/out)
        |  webhook
        v
Orchestrator (Railway) — Claude Haiku 4.5 + cliente MCP
        |  tool call
        v
Servidor MCP -> Postgres propia de MCP-erp (Railway, independiente de st-clares-app)
```

La respuesta vuelve por el mismo camino. Reportes pesados o de tablas complejas se generan como imagen/PDF y se mandan como archivo; para altas y cobros se usa WhatsApp Flow (formulario nativo dentro del chat) en vez de forzar todo a texto libre.

**Stack:**

- **Canal:** WhatsApp Cloud API (Meta), acceso directo sin BSP intermedio (ver costos en sección 8).
- **Hosting:** Railway — ya está en `railway.toml` del repo. Orchestrator + servidor MCP se suman al mismo proyecto/base.
- **Modelo:** `claude-haiku-4-5` como default (USD 1/M input, USD 5/M output). Escalar a Sonnet 5 solo en razonamiento ambiguo (ej. disputas de pago).

## 3. Esquema propio (copiado de st-clares-app como punto de partida)

`app/models.py` de este repo:

- `Sede`, `Curso` (con `monto_matricula`, `monto_cuota_mensual`, `cantidad_cuotas`, `derecho_examen_monto`), `PrecioHistorico` (versionado de precios — esto YA resuelve el tema de aumentos).
- `Alumno`, `ReferentePago` (padre/madre/empresa con su propio CUIT — clave para facturar bien).
- `Inscripcion` (incluye `provisional` + `anio_reserva`, para reserva de cupo del año siguiente).
- `Cuota` (`tipo`: matricula/mensual/examen; `monto_original` vs `monto_actualizado`; `saldo_pendiente`).
- `MovimientoBancario`, `Pago`, `Imputacion` (distribución FIFO de pagos contra cuotas impagas), `AliasCobroAlumno` (alias guardado por alumno para matchear transferencias), `PatronNoAlumno`.
- `AuditLog`.
- **Nuevas, no existían en st-clares-app:** `Profesor`, `UsuarioWhatsapp`.

Services ya copiados a `app/services/` y funcionando (misma lógica que el origen): `enrollment.py` (generación de cuotas, recálculo de precios respetando pagos parciales, altas/bajas, inscripción provisional), `reconciliation.py` (matching por CUIT/alias/fuzzy con `rapidfuzz`, FIFO), `bank_import.py` (parseo de resumen bancario PDF/TSV).

Services que siguen como **stub, sin implementar** (ya lo estaban en el origen): `cash_payments.py`. `whatsapp_text.py` y `reports.py` no se copiaron todavía — se escriben nuevos acá cuando llegue su etapa (ver sección 5 y 9). No hay auth por usuario todavía del lado de MCP-erp — la resuelve `UsuarioWhatsapp` por rol, no un login (ver sección 4).

## 4. Roles por número de WhatsApp (a construir)

| Maestro | Alumno | Profesor | Administrativo | Owner |
|---|---|---|---|---|
| Alumnos | solo su propia ficha | ve nombres de su curso, no pagos | CRUD completo | CRUD completo |
| Cursos | lectura de los propios | lectura de los propios | CRUD completo | CRUD completo |
| Cuotas / pagos | lectura de su propio estado | sin acceso | CRUD completo | CRUD + reportes globales |
| Profesores | sin acceso | solo su propia ficha | lectura | CRUD completo (altas, tarifas) |

**Regla dura:** una tabla nueva `usuario_whatsapp(telefono, rol, alumno_id, profesor_id)` que el servidor MCP resuelve antes de ejecutar cualquier tool. El control de acceso vive en el servidor MCP, no en el prompt del agente.

## 5. Plan completo de tools del servidor MCP

Organizado por si ya existe la lógica de base (se envuelve) o hay que crearla.

### Alumnos e inscripciones (envuelve `services/enrollment.py`)
| Tool | Rol mínimo | Función real que envuelve |
|---|---|---|
| `registrar_alumno` | administrativo | alta directa en `Alumno` |
| `inscribir_alumno` | administrativo | crea `Inscripcion` + llama `generate_cuotas` |
| `generar_matricula_pendiente` | administrativo | `generate_matricula_only` |
| `dar_de_baja_alumno` | administrativo | `dar_de_baja_inscripcion` (condona cuotas futuras) |
| `reservar_cupo_anio_siguiente` | administrativo | `generate_inscripcion_provisional` |
| `confirmar_reserva` | administrativo | `confirmar_inscripcion_provisional` |

### Cuotas, precios y aumentos (envuelve `services/enrollment.py`)
| Tool | Rol mínimo | Función real que envuelve |
|---|---|---|
| `generar_cuotas_mensuales_curso` | sistema (cron mensual) | `generate_cuotas_masivas` |
| `generar_derecho_examen` | administrativo | `generate_exam_cuotas` |
| `cargar_aumento` | owner | `save_price_history` + `recalculate_pending_cuotas` (aumento no toca cuotas ya vencidas ni pagos parciales) |
| `ajustar_monto_cuota_individual` | administrativo | `actualizar_monto_cuota` |
| `eliminar_cuota` | administrativo | `eliminar_cuota` (solo si no tiene imputaciones) |
| `consultar_estado_cuenta` | alumno (propio) / administrativo (cualquiera) | lectura de `Cuota` + `Imputacion` |
| `consultar_morosos` | administrativo | reporte agregado de `Cuota.estado in (pendiente, parcial)` vencidas |

### Cobros y conciliación (envuelve `services/reconciliation.py`, `bank_import.py`; crea `cash_payments.py`)
| Tool | Rol mínimo | Qué hace |
|---|---|---|
| `importar_resumen_bancario` | administrativo | `import_movimientos` — sube el archivo del banco, no por WhatsApp sino por carga periódica |
| `sugerir_conciliacion` | administrativo | `sugerir_conciliacion` — devuelve matches por CUIT/alias/fuzzy con nivel de confianza |
| `aplicar_conciliacion` | administrativo | `aplicar_conciliacion` — confirma el match y crea `Pago` + `Imputacion` |
| `registrar_pago_con_comprobante` **(nuevo)** | administrativo | secretaría manda imagen de transferencia por WhatsApp; Claude (visión nativa) extrae monto/fecha/alias; propone alumno; **pide confirmación antes de aplicar** — atajo rápido, la conciliación de fondo sigue siendo el resumen bancario completo |
| `registrar_pago_efectivo` **(nuevo, completa el stub `cash_payments.py`)** | administrativo | registra `Pago(medio=efectivo)` + `operador_efectivo`, imputa FIFO con `fifo_distribuir` |
| `guardar_alias_cobro` | administrativo | `guardar_alias_cobro` — para que la próxima transferencia de ese alumno matchee sola |

### Comunicación saliente (completa el stub `whatsapp_text.py`) — **diseñada ahora, NO se activa en este piloto**
| Tool | Rol mínimo | Qué hace |
|---|---|---|
| `generar_aviso_morosos` **(nuevo, en pausa)** | administrativo | genera y envía el texto de recordatorio a los alumnos con cuota vencida, por sede |
| `recordatorio_vencimiento_proximo` **(nuevo, en pausa)** | sistema (cron) | avisa unos días antes del vencimiento, no solo cuando ya está vencida |
| `enviar_factura_mensual` **(nuevo, en pausa)** | sistema (cron mensual) | genera el comprobante del período (PDF, o CAE si ya está AFIP) y lo manda por WhatsApp junto con el monto y el link/alias de pago |

**Por qué en pausa:** el 1/10/2026 cambia el costo de los mensajes de servicio en WhatsApp (sección 7), así que conviene definir bien el volumen y el costo real antes de activar cualquier mensaje que salga del sistema sin que el usuario escriba primero. La lógica se diseña y se deja lista en el servidor MCP, pero las tools quedan sin exponer (o detrás de un flag `ACTIVO=false`) hasta decidir prender esto.

**Diseño de la funcionalidad, para cuando se active:**
- **Recordatorio de vencimiento:** dispara unos días antes de `Cuota.fecha_vencimiento` (configurable por sede, ya existe `dia_vencimiento_default` en `Sede`). Un solo mensaje por alumno con el resumen de lo que vence, no una plantilla separada por cuota.
- **Aviso de mora:** dispara cuando `Cuota.estado` pasa a vencida sin pago. Agrupa todas las cuotas vencidas de ese alumno en un solo mensaje, no uno por cuota.
- **Envío de factura mensual:** al generar las cuotas del mes (`generate_cuotas_masivas`), se arma automáticamente el comprobante correspondiente y se manda por WhatsApp al referente de pago (`ReferentePago`), no al alumno si es menor — usa el vínculo ya modelado. Antes de tener AFIP integrado, el comprobante es un PDF simple con los datos del instituto; cuando AFIP esté, se reemplaza por el comprobante fiscal con CAE.
- Todo esto queda gateado por rol `sistema` (cron), nunca disparado por texto libre de un usuario, para que no dependa de que alguien se acuerde de pedirlo.

### Profesores y horarios
| Tool | Rol mínimo | Qué hace |
|---|---|---|
| `registrar_profesor` | owner | alta, tarifa por hora |
| `marcar_asistencia` | profesor (solo sus cohortes) | requiere sumar tabla de asistencias, no está en el modelo actual |
| `consultar_horarios` | todos, filtrado | requiere sumar tabla de horarios, no está en el modelo actual |

### Futuro
| Tool | Rol mínimo | Qué hace |
|---|---|---|
| `emitir_factura_afip` | administrativo | toma un `Pago`, arma comprobante vía AFIP SDK/WSFEv1, guarda CAE |
| `emitir_constancia` | administrativo | genera PDF de alumno regular/certificado |

## 6. AFIP (a integrar más adelante)

- Webservice oficial: **WSFEv1**, requiere certificado digital + autenticación WSAA (token de 12hs) antes de pedir el CAE con `FECAESolicitar`. Manual: `afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG-v4-0.pdf`.
- Alternativa más simple: **AFIP SDK** (`docs.afipsdk.com`), expone lo mismo por REST/JSON.
- Falta definir antes de implementar: condición fiscal del instituto (monotributo / responsable inscripto / exento).

## 7. Costos de WhatsApp Business API (Meta Cloud API) — chequeado en vivo

- **La API en sí es gratis** — sin licencia, accedés directo vía `business.facebook.com` sin pasar por un BSP (Twilio, 360dialog, etc.) que te cobre markup.
- **Hoy (hasta el 30/9/2026):** las conversaciones de "servicio" (el cliente te escribe primero, vos respondés dentro de la ventana de 24hs) son gratis y sin límite. Eso cubre casi todo el uso conversacional del instituto (alumno o secretaria escribiendo).
- **⚠️ Cambio importante, ya confirmado, entra en vigencia el 1/10/2026:** Meta empieza a cobrar también por los mensajes de servicio dentro de esa ventana de 24hs — hasta ahora gratis desde julio 2025. Esto te afecta directo: el modelo de costo cambia justo cuando estarías por lanzar el piloto.
- Los mensajes que **vos iniciás** (recordatorio de vencimiento, aviso de mora) son plantillas "utility", y esas se cobran por mensaje desde el primer envío — no hay franquicia gratuita mensual para plantillas desde 2025. El costo por mensaje varía mucho por país (en EE.UU. ronda USD 0,004–0,014 por utility/authentication); no tengo la tarifa específica para Argentina confirmada, conviene chequearla en el rate card de Meta antes de lanzar.
- Con el volumen de un instituto chico (recordatorios de cuota, avisos de mora), el costo mensual debería ser bajo igual, pero ya no es "gratis" a partir de octubre — hay que presupuestarlo, aunque sea modesto.
- Alternativa que existe pero que dejo solo mencionada: librerías no oficiales (ej. Baileys) que usan un número de WhatsApp personal sin pasar por la API de Meta, sin costo por mensaje — pero violan los términos de servicio de WhatsApp y arriesgan el baneo del número. No la recomiendo para algo que maneja cobros reales de alumnos.

## 8. Funcionalidades adicionales recomendadas

1. Recordatorio automático de vencimiento de cuota (push, no reactivo).
2. Aviso a administración si un alumno falta 2 clases seguidas (requiere sumar asistencias al modelo).
3. Alta de prospecto vía WhatsApp Flow, con conversión a alumno al confirmar.
4. Alerta de cupo por agotarse, para abrir nueva cohorte.

## 9. Plan de pasos a seguir

| # | Etapa | Estado |
|---|---|---|
| 0.1 | Scaffold del repo (README, ARCHITECTURE.md, esqueletos de `app/`) recuperado y pusheado a `github.com/silagana/MCP-erp` | ✅ hecho |
| 0.2 | Esquema propio: `app/models.py` copiado de `st-clares-app/db/models.py`, agregadas `Profesor` y `UsuarioWhatsapp` | ✅ hecho |
| 0.3 | Services copiados a `app/services/` (`enrollment.py`, `reconciliation.py`, `bank_import.py`) con imports adaptados; `cash_payments.py` como stub | ✅ hecho |
| 0.4 | Script de migración única `scripts/migrate_from_st_clares.py` (esqueleto funcional, copia tabla por tabla respetando FKs) | ✅ hecho |
| 1 | Crear el servicio Postgres propio de MCP-erp en Railway (separado del de `st-clares-app`). También se desplegó el servicio `MCP-erp` en sí (conectado al repo de GitHub), aunque el código todavía no hace nada útil | ✅ hecho |
| 2 | Generar el esquema en la Postgres nueva vía Alembic: migración inicial generada y aplicada (`alembic upgrade head`) desde la consola de Railway, contra el Postgres real. Migración commiteada en `migrations/versions/bc81eb3b5c09_initial_schema.py` | ✅ hecho |
| 3 | Correr `scripts/migrate_from_st_clares.py` contra los datos reales de `st-clares-app` (requiere `SOURCE_DATABASE_URL` de producción) — probar primero con `--dry-run` | pendiente |
| 4 | Cargar `usuario_whatsapp` con los números reales — `scripts/seed_usuario_whatsapp.py` (idempotente), owner cargado (`5491141996958`) | ✅ hecho |
| 5 | Implementar `app/mcp_server.py`: registrar las tools de alumnos/inscripciones/cuotas/conciliación (sección 5), cada una resolviendo el rol por `UsuarioWhatsapp` antes de tocar la base | pendiente |
| 6 | Completar `services/cash_payments.py` (cobros en efectivo) | pendiente |
| 7 | Implementar `app/orchestrator.py`: historial de conversación por teléfono, resolución de rol, carga de tools MCP, llamada a `claude-haiku-4-5` | pendiente |
| 8 | Implementar `app/whatsapp_webhook.py`: extraer mensaje + teléfono del payload de Meta, pasar al orchestrator, responder | pendiente |
| 9 | Deploy en Railway (webhook + orchestrator + Postgres nueva), configurar el webhook en Meta, probar primer WhatsApp Flow (alta/cobro) | pendiente |
| 10 | Prueba con una semana real de actividad; ajustar tools según malentendidos del agente | pendiente |
| 11 | Validar MCP-erp en uso real → dar de baja `st-clares-app` (Streamlit) | pendiente |
| 12 | `whatsapp_text.py` (recordatorios, avisos de mora, factura mensual): confirmar tarifa de WhatsApp para Argentina post 1/10/2026 y activar | en pausa (ver sección 7) |
| 13 | AFIP (cuando esté definida la condición fiscal del instituto) | futuro |

**Próximo paso inmediato:** etapa 5 — implementar las tools del servidor MCP.
