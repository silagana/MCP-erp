import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import EstadoMovimientoEnum, MovimientoBancario
from app.utils.formatters import parse_importe_ar


# ── Extractors ─────────────────────────────────────────────────────────────

def _strip_nul(s: str | None) -> str | None:
    return s.replace('\x00', '') if s else s


def _extract_tipo(concepto: str) -> str:
    """Extracts movement type prefix (everything before pagador marker)."""
    # Split before "De " or "Id debin" markers
    m = re.split(r'\s+-\s+(?=[Dd]e\s+|[Ii][Dd]\s+[Dd]ebin)', concepto, maxsplit=1)
    if len(m) > 1:
        return m[0].strip()
    return concepto.split(' - ')[0].strip()


def _extract_cuit(concepto: str) -> str | None:
    """Extracts CUIT: explicit 'cuit XXXXXXXXXXX' or last 11-digit sequence."""
    m = re.search(r'\bcuit\s+(\d{11})\b', concepto, re.IGNORECASE)
    if m:
        return m.group(1)
    matches = re.findall(r'\b(\d{11})\b', concepto)
    return matches[-1] if matches else None


def _extract_nombre_ref_subtipo(concepto: str) -> tuple[str | None, str | None, str | None]:
    """
    Extracts (nombre_pagador, referencia, subtipo) from concepto.
    Works on the lowercase concepto string.
    """
    m = re.search(r'[Dd]e\s+(.+)', concepto)
    if not m:
        return None, None, None

    after_de = m.group(1)

    # Strip trailing "/ CUIT" so it doesn't pollute referencia
    after_de = re.sub(r'\s*/\s*\d{11}\s*$', '', after_de).strip()

    # Split nombre from rest at first whitespace-surrounded slash
    # This lets "acosta/matias" stay intact (no surrounding spaces)
    parts = re.split(r'\s+/\s+', after_de, maxsplit=1)
    if len(parts) == 2:
        nombre_raw, rest = parts
    else:
        # Fallback: bare slash (e.g. "chir/ mercado pago /cuit")
        slash_parts = after_de.split('/', 1)
        nombre_raw = slash_parts[0]
        rest = slash_parts[1] if len(slash_parts) > 1 else ""

    nombre = nombre_raw.strip() or None
    referencia = rest.strip() or None
    subtipo: str | None = None

    if referencia:
        # subtipo is a 3-letter lowercase code at end: "vittoria - var"
        s_m = re.search(r'\s*-\s*([a-z]{3})\s*$', referencia)
        if s_m:
            subtipo = s_m.group(1)
            referencia = referencia[:s_m.start()].strip() or None

    return nombre, referencia, subtipo


# ── Parser ─────────────────────────────────────────────────────────────────

def parse_tsv(content: bytes) -> list[dict]:
    """
    Parse bank statement bytes (TSV, latin-1 encoded, CRLF).
    Returns list of dicts ready for DB insertion.
    """
    text = content.decode("latin-1").lstrip("﻿")
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    reader = csv.DictReader(io.StringIO(text), delimiter='\t')
    rows: list[dict] = []

    for raw in reader:
        fecha_str = (raw.get("Fecha") or "").strip()
        concepto_raw = _strip_nul((raw.get("Concepto") or "").strip())
        importe_str = (raw.get("Importe") or "").strip()

        if not fecha_str or not concepto_raw or not importe_str:
            continue

        # Parse date: D/M/YY or D/M/YYYY
        try:
            d_s, m_s, y_s = fecha_str.split("/")
            y_val = int(y_s)
            if y_val < 100:
                y_val += 2000
            fecha = date(y_val, int(m_s), int(d_s))
        except Exception:
            continue

        try:
            importe = parse_importe_ar(importe_str)
        except Exception:
            continue

        c = concepto_raw.lower()
        tipo = _strip_nul(_extract_tipo(c))
        nombre, referencia, subtipo = _extract_nombre_ref_subtipo(c)
        nombre = _strip_nul(nombre)
        referencia = _strip_nul(referencia)
        cuit = _extract_cuit(c)

        # SHA1 of raw fields for idempotency
        hash_unico = hashlib.sha1(
            f"{fecha_str}|{concepto_raw}|{importe_str}".encode("utf-8")
        ).hexdigest()

        rows.append({
            "fecha": fecha,
            "concepto_raw": concepto_raw,
            "importe": importe,
            "tipo_movimiento": tipo,
            "nombre_pagador_detectado": nombre,
            "referencia_detectada": referencia,
            "subtipo": subtipo,
            "cuit_detectado": cuit,
            "hash_unico": hash_unico,
            "is_proveedor_candidate": "pago a proveedores" in (tipo or ""),
        })

    return rows


# ── PDF Parser ─────────────────────────────────────────────────────────────
#
# Santander PDF column layout (x-positions empirically verified):
#   Fecha text     :  x < 65
#   Comprobante    : 65 ≤ x < 115  (5+ digit number)
#   Movimiento text: 115 ≤ x < 340
#   Débito  $      : 340 ≤ x < 420
#   Crédito $      : 420 ≤ x < 510
#   Saldo   $      : x ≥ 510

_PDF_DEBITO_X_MAX = 420
_PDF_CREDITO_X_MAX = 510

# Credit transaction types whose pagador is NOT a student
_PROVEEDOR_CREDITO = re.compile(r'pago\s+a\s+proveedores\s+recibido', re.I)


def _parse_monto_ar(s: str) -> Decimal | None:
    """'140.000,00' → Decimal('140000.00'), None on failure or zero."""
    if not s:
        return None
    s = s.strip().lstrip("$").strip()
    s = s.replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        v = Decimal(s)
        return v if v > 0 else None
    except InvalidOperation:
        return None


def _fecha_str_to_date(fecha_str: str) -> date | None:
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', fecha_str.strip())
    if not m:
        return None
    try:
        y_val = int(m.group(3))
        if y_val < 100:
            y_val += 2000
        return date(y_val, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _pdf_bucket_words(words: list) -> list[list]:
    """Group word dicts by y-proximity (10 pt tolerance) → list of rows."""
    buckets: list[list] = []
    for w in sorted(words, key=lambda x: x['top']):
        placed = False
        for bucket in reversed(buckets):
            if abs(w['top'] - bucket[0]['top']) <= 10:
                bucket.append(w)
                placed = True
                break
        if not placed:
            buckets.append([w])
    return buckets


def _pdf_row_amounts(bucket: list) -> tuple[Decimal | None, Decimal | None]:
    """Return (debito, credito) amounts from a word-bucket row."""
    debito = credito = None
    bsorted = sorted(bucket, key=lambda w: w['x0'])
    i = 0
    while i < len(bsorted):
        w = bsorted[i]
        if w['text'] == '$' and i + 1 < len(bsorted):
            amt = _parse_monto_ar(bsorted[i + 1]['text'])
            if amt is not None:
                x = w['x0']
                if x < _PDF_DEBITO_X_MAX:
                    debito = amt
                elif x < _PDF_CREDITO_X_MAX:
                    credito = amt
                # else: saldo, ignore
            i += 2
        else:
            i += 1
    return debito, credito


def _pdf_finalize(pending: dict, rows: list) -> None:
    """Build final row dict from pending credit and append to rows."""
    concepto_raw = _strip_nul(pending['concepto_raw'])
    c = concepto_raw.lower()
    tipo = _strip_nul(_extract_tipo(c))
    nombre, referencia, subtipo = _extract_nombre_ref_subtipo(c)
    nombre = _strip_nul(nombre)
    referencia = _strip_nul(referencia)
    cuit = _extract_cuit(c)
    is_prov = bool(_PROVEEDOR_CREDITO.search(concepto_raw))
    hash_unico = hashlib.sha1(
        f"{pending['_fecha_str']}|{concepto_raw}|{pending['importe']}".encode("utf-8")
    ).hexdigest()
    rows.append({
        "fecha": pending["fecha"],
        "concepto_raw": concepto_raw,
        "importe": pending["importe"],
        "tipo_movimiento": tipo,
        "nombre_pagador_detectado": nombre,
        "referencia_detectada": referencia,
        "subtipo": subtipo,
        "cuit_detectado": cuit,
        "hash_unico": hash_unico,
        "is_proveedor_candidate": is_prov,
    })


def parse_pdf(content: bytes) -> list[dict]:
    """
    Parse Santander bank statement PDF (Resumen de cuenta mensual).
    Uses word x-positions to distinguish Débito vs Crédito columns.
    Imports only credit movements (incoming transfers from students).
    """
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber requerido. Instalar: pip install pdfplumber")

    rows: list[dict] = []
    current_fecha: date | None = None
    current_fecha_str: str = ""
    pending: dict | None = None   # current credit transaction being built

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            word_list = page.extract_words(x_tolerance=3, y_tolerance=3)
            if not word_list:
                continue

            buckets = _pdf_bucket_words(word_list)

            for bucket in buckets:
                bucket.sort(key=lambda w: w['x0'])

                # Detect date words (x < 65, DD/MM/YY format)
                date_words = [
                    w for w in bucket
                    if re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', w['text'])
                    and w['x0'] < 65
                ]
                # Detect comprobante (5+ digit number, x 65–115)
                comp_words = [
                    w for w in bucket
                    if re.match(r'^\d{5,}$', w['text']) and 65 <= w['x0'] < 115
                ]
                # Movimiento text words (x 115–340)
                mov_words = [w for w in bucket if 115 <= w['x0'] < 340]

                debito_amt, credito_amt = _pdf_row_amounts(bucket)

                # Update current date when found
                if date_words:
                    current_fecha = _fecha_str_to_date(date_words[0]['text'])
                    current_fecha_str = date_words[0]['text']

                if comp_words:
                    # ── Main transaction row ──────────────────────────────
                    # Finalize previous pending credit
                    if pending is not None:
                        _pdf_finalize(pending, rows)
                        pending = None

                    if current_fecha is None or not mov_words:
                        continue

                    if credito_amt is not None:
                        concepto_tipo = " ".join(w['text'] for w in mov_words).strip()
                        pending = {
                            "fecha": current_fecha,
                            "_fecha_str": current_fecha_str,
                            "concepto_raw": concepto_tipo,
                            "importe": credito_amt,
                        }
                    # Debits: silently skipped

                elif not date_words and mov_words and pending is not None:
                    # ── Detail / continuation row ─────────────────────────
                    # e.g. "De martinez / emiliaramos - cuo / 27243122525"
                    detail = " ".join(w['text'] for w in mov_words).strip()
                    if detail:
                        first = detail.split()[0].lower()
                        if first in ("de", "a", "resp:", "id"):
                            pending['concepto_raw'] += ' - ' + detail

        # Flush pending at end of last page
        if pending is not None:
            _pdf_finalize(pending, rows)

    return rows


# ── DB import ──────────────────────────────────────────────────────────────

def import_movimientos(session: Session, content: bytes, filename: str = "") -> tuple[int, int]:
    """
    Import parsed movements into DB. Idempotent via hash_unico.
    Auto-detects PDF vs TSV/XLS from content magic bytes or filename.
    Returns (imported, skipped_duplicates).
    """
    is_pdf = content[:4] == b"%PDF" or filename.lower().endswith(".pdf")
    rows = parse_pdf(content) if is_pdf else parse_tsv(content)
    imported = skipped = 0
    seen: set[str] = set()  # hashes added in this call (autoflush=False no los ve el query)

    for row in rows:
        h = row["hash_unico"]
        if h in seen:
            skipped += 1
            continue
        exists = session.query(MovimientoBancario).filter(
            MovimientoBancario.hash_unico == h
        ).first()
        if exists:
            skipped += 1
            continue

        estado = (
            EstadoMovimientoEnum.ignorado_no_alumno
            if row["is_proveedor_candidate"]
            else EstadoMovimientoEnum.pendiente
        )

        session.add(MovimientoBancario(
            fecha=row["fecha"],
            concepto_raw=row["concepto_raw"],
            importe=row["importe"],
            cuit_detectado=row["cuit_detectado"],
            nombre_pagador_detectado=row["nombre_pagador_detectado"],
            referencia_detectada=row["referencia_detectada"],
            subtipo=row["subtipo"],
            tipo_movimiento=row["tipo_movimiento"],
            hash_unico=h,
            estado=estado,
            fecha_importacion=datetime.utcnow(),
        ))
        seen.add(h)
        imported += 1

    return imported, skipped
