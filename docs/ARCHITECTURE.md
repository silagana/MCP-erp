# ERP conversacional por WhatsApp — St. Clare's (instituto de inglés, Buenos Aires)

## 1. Concepto

Un ERP donde la única puerta de entrada y salida es WhatsApp. No hay panel web separado para el día a día: alumnos, profesores y administración interactúan con el mismo número, y el sistema resuelve qué puede ver y hacer cada uno según su rol. La complejidad tradicional de un ERP (esquema de datos + carga + visualización) se reemplaza por un agente que conoce el esquema, ejecuta operaciones acotadas por rol, y genera la vista adecuada (texto, WhatsApp Flow o archivo) según lo que se pide.

**Importante:** ya existe una base real — el repo `silagana/st-clares-app` (Streamlit + SQLAlchemy + Alembic, pensado para Railway). No se reescribe el esquema ni la lógica de negocio: el MCP se conecta a esa misma base y reutiliza esos services. El Streamlit queda como panel de administración/conciliación pesada; WhatsApp se suma como segunda puerta, liviana, sobre la misma lógica.

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
Servidor MCP -> misma Postgres que usa el Streamlit (Railway)
```

La respuesta vuelve por el mismo camino. Reportes pesados o de tablas complejas se generan como imagen/PDF y se mandan como archivo; para altas y cobros se usa WhatsApp Flow (formulario nativo dentro del chat) en vez de forzar todo a texto libre.

**Stack:**

- **Canal:** WhatsApp Cloud API (Meta), acceso directo sin BSP intermedio (ver costos en sección 8).
- **Hosting:** Railway — ya está en `railway.toml` del repo. Orchestrator + servidor MCP se suman al mismo proyecto/base.
- **Modelo:** `claude-haiku-4-5` como default (USD 1/M input, USD 5/M output). Escalar a Sonnet 5 solo en razonamiento ambiguo (ej. disputas de pago).

## 3. Base real ya existente (no se duplica)

Repo: `silagana/st-clares-app`. Modelos en `db/models.py` (SQLAlchemy + Alembic):

- `Sede`, `Curso` (con `monto_matricula`, `monto_cuota_mensual`, `cantidad_cuotas`, `derecho_examen_monto`), `PrecioHistorico` (versionado de precios — esto YA resuelve el tema de aumentos).
- `Alumno`, `ReferentePago` (padre/madre/empresa con su propio CUIT — clave para facturar bien).
- `Inscripcion` (incluye `provisional` + `anio_reserva`, para reserva de cupo del año siguiente).
- `Cuota` (`tipo`: matricula/mensual/examen; `monto_original` vs `monto_actualizado`; `saldo_pendiente`).
- `MovimientoBancario`, `Pago`, `Imputacion` (distribución FIFO de pagos contra cuotas impagas), `AliasCobroAlumno` (alias guardado por alumno para matchear transferencias), `PatronNoAlumno`.
- `AuditLog`.

Services ya funcionando: `enrollment.py` (generación de cuotas, recálculo de precios respetando pagos parciales, altas/bajas, inscripción provisional), `reconciliation.py` (matching por CUIT/alias/fuzzy con `rapidfuzz`, FIFO), `bank_import.py` (parseo de resumen bancario PDF/TSV).

Services que están como **stub, sin implementar** (marcados `"Implementado en M5/M6"` en el propio código): `whatsapp_text.py`, `cash_payments.py`, `reports.py`. Auth actual (`utils/auth.py`) es un solo usuario admin/contraseña — no hay roles por persona todavía, hay que sumarlo para WhatsApp.

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

## 9. Plan (revisado — ya no se arranca de cero)

| Etapa | Entregable |
|---|---|
| 1 | Tabla `usuario_whatsapp` + resolución de rol en el servidor MCP |
| 2 | MCP envolviendo lo que ya existe: alumnos, inscripciones, cuotas, conciliación (secciones con ✅ arriba) |
| 3 | Completar `cash_payments.py` (cobros). `whatsapp_text.py` se diseña pero **no se activa** — queda gateado (ver sección 5) |
| 4 | Integración WhatsApp Cloud API + primer WhatsApp Flow (alta/cobro) — solo mensajería entrante/respuesta, nada de saliente automático todavía |
| 5 | Prueba con una semana real de actividad; ajustar tools según malentendidos del agente |
| 6 | Confirmar tarifa de WhatsApp para Argentina post 1/10 y activar recordatorios + envío de factura mensual |
| 7 | AFIP (cuando esté definida la condición fiscal) |
