# Identidad visual — St. Clare's Center

Extraído directo de [stclarescenter.com.ar](https://stclarescenter.com.ar/)
(CSS computado real, no aproximado) — para usar en el dashboard de KPIs y
cualquier otra pieza (PDFs, reportes) que represente al instituto.

## Colores

| Uso | Hex | RGB |
|---|---|---|
| **Verde primario** (logo, links, CTA principal, iconos) | `#0D8657` | rgb(13, 134, 87) |
| Verde secundario (variante, fondos alternativos) | `#3B8E2C` | rgb(59, 142, 44) |
| **Acento naranja/dorado** (highlights puntuales) | `#F8AF44` | rgb(248, 175, 68) |
| Texto oscuro (headings sobre blanco, footer bg) | `#333333` | rgb(51, 51, 51) |
| Texto body / gris medio | `#777777` | rgb(119, 119, 119) |
| Texto secundario / gris claro | `#999999` | rgb(153, 153, 153) |
| Texto sobre footer oscuro | `#BBBBBB` | rgb(187, 187, 187) |
| Fondo alternativo claro | `#F8F9F9` | rgb(248, 249, 249) |
| Blanco | `#FFFFFF` | rgb(255, 255, 255) |
| Overlay oscuro sobre fotos (hero) | `rgba(0,0,0,0.2–0.5)` | — |

**Uso típico:** fondo blanco, texto gris (#777) para body, verde (#0D8657)
para CTAs/links/acentos, texto casi negro (#333) para headings sobre
blanco. El naranja (#F8AF44) aparece como acento puntual, no como color
dominante — usarlo con moderación (ej. un ícono de alerta, un badge).

## Tipografía

- **Headings:** `Raleway`, peso **800** (extra bold), mayúsculas. Ej. H2 a
  35px. Da el look "título de campaña", grande y contundente.
- **Body / nav / botones:** `"Open Sans", Helvetica, Arial, sans-serif`,
  peso 400 regular.
- Ambas están en Google Fonts — cargar así en HTML:
  ```html
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Raleway:wght@800&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
  ```

## Botones

- Radio de borde chico: `2px` (casi recto, no pill-shaped).
- **Primario:** fondo `#0D8657`, texto blanco.
- **Secundario:** transparente, borde blanco/gris, texto blanco u oscuro
  según el fondo (outline style).

## Logo

`https://stclarescenter.com.ar/wp-content/uploads/2020/04/stclarescenter_ingles-buenos-aires-argentina-logo.png`
— wordmark en verde primario, tipografía manuscrita/cursiva para "St
Clare's" + "language center" en versalitas debajo.

## Tono visual general

- Fotos reales del instituto (aulas, sillas de colores, ambiente cálido)
  con overlay oscuro cuando hay texto grande encima.
- Layout limpio, mucho blanco, jerarquía clara (headings enormes en
  mayúscula, body chico y gris).
- Botón flotante de WhatsApp abajo a la derecha (verde, redondo) — coherente
  con que WhatsApp ya es un canal de contacto reconocido de la marca.

## Aplicación sugerida al dashboard de KPIs

- Fondo blanco / `#F8F9F9` para tarjetas.
- Headers de sección en Raleway 800, mayúscula, `#333`.
- Números grandes de KPI en verde `#0D8657` (positivo/neutral) — reservar
  el naranja `#F8AF44` para alertas suaves (ej. "cuotas por vencer") y un
  rojo aparte (no está en la marca, pero hace falta) para morosidad crítica
  — no forzar el naranja como color de alerta grave, es de marca, no de
  semántica de error.
- Botones/links con el mismo verde y radio de 2px, para que se sienta
  "de la casa" y no un dashboard genérico pegado con birome.
