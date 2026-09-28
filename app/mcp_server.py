"""Servidor MCP: expone las tools listadas en docs/ARCHITECTURE.md sección 5.

Cada tool resuelve el rol por número de WhatsApp (tabla usuario_whatsapp)
ANTES de tocar la base, vía app/permissions.py — el control de acceso vive
acá, no en el prompt del agente.

Corre en el mismo proceso que app/orchestrator.py (un solo servicio en
Railway) — orchestrator llama a server.call_tool(...) directo, no hay un
transporte MCP (stdio/HTTP) separado corriendo.

Tools todavía sin implementar (quedan para más adelante, ver ARCHITECTURE.md
sección 9): registrar_pago_con_comprobante (necesita visión en el
orchestrator), importar_resumen_bancario (necesita manejo de archivos
adjuntos de WhatsApp), marcar_asistencia (necesita tabla nueva que no
existe todavía), y toda la comunicación saliente de whatsapp_text.py (en
pausa, ver sección 7).

Horarios (Aula/Grupo/GrupoDia, ver app/models.py): agregado 2026-09-28 a
partir de "LISTAS BOEDO 2026.xlsx" (hojas "AULAS LYM"/"AULAS MYJ"), un grid
semanal aula×horario con cada grupo coloreado por profesor. Curso sigue
siendo el nivel/programa (ej. "FCE"); Grupo es la comisión concreta con
día(s)/horario/aula/profesor — un Curso puede tener varios Grupos.
crear_grupo/mover_grupo validan choques de horario (services/horarios.py)
antes de guardar. generar_dashboard_horarios muestra el grid en HTML,
reusando el mecanismo de token de generar_dashboard. generar_dashboard_listados
hace lo mismo para la vista "alumnos por curso" (hoja "LISTAS" del mismo Excel).

Cobros y conciliación, rediseñado 2026-09-28: antes un Pago solo se creaba
DESDE un MovimientoBancario ya matcheado (conciliar = cobrar, en el mismo
paso). Ahora registrar_pago (services/cash_payments.py) carga el cobro
—efectivo o transferencia— en el momento, sin Pago.movimiento_bancario_id;
conciliar_movimientos_pendientes corre una vez al mes (después de importar
el resumen bancario) y cruza cada movimiento contra los pagos ya cargados
(reconciliation.conciliar_pendientes), vinculando sin crear nada nuevo.
sugerir_conciliacion/aplicar_conciliacion siguen existiendo para revisar a
mano los que no matchean solos, y aplicar_conciliacion todavía puede crear
un Pago nuevo en el momento (parámetro imputaciones) como excepción, para
movimientos sin pago pre-cargado.
"""
import os
import re
from datetime import date, time
from decimal import Decimal

from mcp.server.mcpserver import MCPServer
from sqlalchemy import or_

from app.dashboard import crear_token_horario, crear_token_listados, crear_token_reporte
from app.db import SessionLocal
from app.models import (
    Alumno, Aula, Cuota, Curso, DiaSemanaEnum, EstadoCuotaEnum, Grupo, GrupoDia,
    Inscripcion, MedioPagoEnum, Profesor, RolWhatsappEnum, Sede,
)
from app.permissions import (
    require_alumno_propio_o_administrativo, require_rol, resolver_usuario,
)
from app.permissions import AccesoDenegado
from app.services import cash_payments, enrollment, horarios, reconciliation

server = MCPServer("st-clares-erp")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def _limpiar_dni(dni: str) -> str:
    """Saca puntos/espacios/guiones — encontramos un caso real donde el
    modelo mandó '40.000.000' y quedó guardado con puntos, distinto al
    formato limpio (solo dígitos) que usa el resto de la base (venía de la
    migración del Excel) — eso rompía cualquier búsqueda posterior por
    DNI exacto."""
    return re.sub(r"\D", "", dni)


@server.tool()
def generar_dashboard(telefono: str) -> dict:
    """Genera un link a un dashboard con gráficos (KPIs de alumnos, ingresos
    y morosidad para administrativo/owner; resumen de la propia cuenta para
    un alumno). El link vence en 24hs y respeta el rol de quien lo pidió —
    no requiere ningún parámetro más. Cualquier usuario registrado puede pedirlo."""
    with SessionLocal() as session:
        usuario = resolver_usuario(session, telefono)
        if usuario is None:
            raise AccesoDenegado(f"El número {telefono} no está registrado.")
        token = crear_token_reporte(session, usuario)
        session.commit()
        if not PUBLIC_BASE_URL:
            return {"error": "Falta configurar PUBLIC_BASE_URL en el servidor."}
        return {"url": f"{PUBLIC_BASE_URL}/reportes/{token}", "valido_por_horas": 24}


@server.tool()
def listar_sedes(telefono: str) -> dict:
    """Lista las sedes activas con su id y nombre — usar esto para resolver
    a qué sede_id corresponde un nombre de sede (ej. "Caballito") antes de
    llamar a otra tool que pida sede_id. Cualquier usuario registrado puede usarla."""
    with SessionLocal() as session:
        if resolver_usuario(session, telefono) is None:
            raise AccesoDenegado(f"El número {telefono} no está registrado.")
        sedes = session.query(Sede).filter(Sede.activa == True).all()
        return {"sedes": [{"id": s.id, "nombre": s.nombre} for s in sedes]}


@server.tool()
def listar_cursos(telefono: str, sede_id: int | None = None) -> dict:
    """Lista los cursos activos con id, nombre, nivel, sede y precios vigentes
    (matrícula y cuota mensual) — usar para resolver curso_id a partir de un
    nombre antes de llamar a otra tool que lo pida. Filtro opcional por sede_id
    (ver listar_sedes). Cualquier usuario registrado puede usarla."""
    with SessionLocal() as session:
        if resolver_usuario(session, telefono) is None:
            raise AccesoDenegado(f"El número {telefono} no está registrado.")
        query = session.query(Curso).filter(Curso.activo == True)
        if sede_id is not None:
            query = query.filter(Curso.sede_id == sede_id)
        return {"cursos": [
            {
                "id": c.id,
                "nombre": c.nombre,
                "nivel": c.nivel,
                "sede_id": c.sede_id,
                "sede": c.sede.nombre,
                "monto_matricula": c.monto_matricula,
                "monto_cuota_mensual": c.monto_cuota_mensual,
            }
            for c in query.all()
        ]}


@server.tool()
def buscar_alumno(telefono: str, query: str) -> dict:
    """Busca alumnos por nombre, apellido o DNI (parcial, no distingue
    mayúsculas/minúsculas). Devuelve id, nombre completo, dni y sede — usar
    para resolver alumno_id antes de llamar a otra tool. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        patron = f"%{query.strip()}%"
        filtros = [Alumno.nombre.ilike(patron), Alumno.apellido.ilike(patron), Alumno.dni.ilike(patron)]
        # si la búsqueda tiene puntos/espacios (ej. "40.000.000"), matchear
        # también contra el DNI sin esos caracteres — el formato guardado
        # es solo dígitos, ver _limpiar_dni.
        query_digitos = _limpiar_dni(query)
        if query_digitos and query_digitos != query.strip():
            filtros.append(Alumno.dni.ilike(f"%{query_digitos}%"))
        alumnos = (
            session.query(Alumno)
            .filter(or_(*filtros))
            .limit(15)
            .all()
        )
        return {"alumnos": [
            {
                "id": a.id,
                "nombre_completo": f"{a.apellido}, {a.nombre}",
                "dni": a.dni,
                "sede": a.sede.nombre,
            }
            for a in alumnos
        ]}


# ── Alumnos e inscripciones (envuelve services/enrollment.py) ──────────────

@server.tool()
def registrar_alumno(
    telefono: str,
    nombre: str,
    apellido: str,
    dni: str,
    sede_id: int,
    fecha_nacimiento: str | None = None,
    telefono_alumno: str | None = None,
    email: str | None = None,
) -> dict:
    """Da de alta un alumno nuevo. Rol mínimo: administrativo.
    fecha_nacimiento en formato YYYY-MM-DD."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        alumno = Alumno(
            nombre=nombre,
            apellido=apellido,
            dni=_limpiar_dni(dni) if dni else None,
            sede_id=sede_id,
            fecha_nacimiento=date.fromisoformat(fecha_nacimiento) if fecha_nacimiento else None,
            telefono=telefono_alumno,
            email=email,
        )
        session.add(alumno)
        session.commit()
        return {"alumno_id": alumno.id, "nombre_completo": f"{alumno.nombre} {alumno.apellido}"}


@server.tool()
def inscribir_alumno(
    telefono: str,
    alumno_id: int,
    curso_id: int,
    fecha_inscripcion: str,
    grupo_id: int | None = None,
    descuento_porcentaje: str | None = None,
    descuento_fijo: str | None = None,
    generar_matricula: bool = True,
) -> dict:
    """Inscribe a un alumno en un curso y genera sus cuotas (matrícula + mensuales).
    grupo_id es opcional: asigna de una la comisión concreta (aula/horario/profesor,
    ver listar_grupos) — tiene que pertenecer al mismo curso_id, si no se rechaza
    sin crear nada. Rol mínimo: administrativo. fecha_inscripcion en formato YYYY-MM-DD."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        if grupo_id is not None:
            grupo = session.get(Grupo, grupo_id)
            if grupo is None:
                return {"ok": False, "motivo": "grupo no encontrado"}
            if grupo.curso_id != curso_id:
                return {"ok": False, "motivo": "el grupo pertenece a otro curso"}
        inscripcion = Inscripcion(
            alumno_id=alumno_id,
            curso_id=curso_id,
            grupo_id=grupo_id,
            fecha_inscripcion=date.fromisoformat(fecha_inscripcion),
            descuento_porcentaje=Decimal(descuento_porcentaje) if descuento_porcentaje else None,
            descuento_fijo=Decimal(descuento_fijo) if descuento_fijo else None,
            activa=True,
        )
        session.add(inscripcion)
        session.flush()
        cuotas = enrollment.generate_cuotas(session, inscripcion, generar_matricula=generar_matricula)
        session.commit()
        return {
            "inscripcion_id": inscripcion.id,
            "cuotas_generadas": len(cuotas),
        }


@server.tool()
def generar_matricula_pendiente(telefono: str, inscripcion_id: int, fecha_vencimiento: str) -> dict:
    """Genera solo la cuota de matrícula de una inscripción que todavía no la tiene.
    Rol mínimo: administrativo. fecha_vencimiento en formato YYYY-MM-DD."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        inscripcion = session.get(Inscripcion, inscripcion_id)
        if inscripcion is None:
            return {"ok": False, "motivo": "inscripcion no encontrada"}
        cuota = enrollment.generate_matricula_only(session, inscripcion, date.fromisoformat(fecha_vencimiento))
        session.commit()
        if cuota is None:
            return {"ok": False, "motivo": "la inscripción ya tiene cuota de matrícula"}
        return {"ok": True, "cuota_id": cuota.id, "monto": cuota.monto_actualizado}


@server.tool()
def dar_de_baja_alumno(telefono: str, inscripcion_id: int, condonar_futuras: bool = True) -> dict:
    """Da de baja una inscripción. Si condonar_futuras=True, condona las cuotas
    pendientes con vencimiento futuro. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        condonadas = enrollment.dar_de_baja_inscripcion(session, inscripcion_id, condonar_futuras)
        session.commit()
        return {"cuotas_condonadas": condonadas}


@server.tool()
def reservar_cupo_anio_siguiente(
    telefono: str,
    alumno_id: int,
    curso_id: int,
    monto_reserva: str,
    fecha_vencimiento: str,
    anio_reserva: int,
) -> dict:
    """Reserva un cupo para el año siguiente (inscripción provisional, solo
    cuota de matrícula, sin cuotas mensuales todavía). Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        inscripcion, cuota = enrollment.generate_inscripcion_provisional(
            session, alumno_id, curso_id, Decimal(monto_reserva),
            date.fromisoformat(fecha_vencimiento), anio_reserva,
        )
        session.commit()
        return {"inscripcion_id": inscripcion.id, "cuota_reserva_id": cuota.id}


@server.tool()
def confirmar_reserva(
    telefono: str,
    inscripcion_id: int,
    fecha_inicio_cuotas: str,
    cantidad_cuotas: int | None = None,
) -> dict:
    """Confirma una reserva de cupo provisional y genera las cuotas mensuales.
    Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        cuotas = enrollment.confirmar_inscripcion_provisional(
            session, inscripcion_id, date.fromisoformat(fecha_inicio_cuotas), cantidad_cuotas,
        )
        session.commit()
        return {"cuotas_generadas": len(cuotas)}


# ── Cuotas, precios y aumentos (envuelve services/enrollment.py) ───────────

@server.tool()
def generar_cuotas_mensuales_curso(
    telefono: str,
    curso_id: int,
    periodo_inicio: str,
    cantidad: int,
) -> dict:
    """Genera cuotas mensuales para todas las inscripciones activas de un curso,
    a partir de un período (YYYY-MM-01). Omite períodos ya existentes.
    Rol mínimo: administrativo (pensado para correr por cron, pero se puede disparar a mano)."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        total_gen, inscripciones_afectadas = enrollment.generate_cuotas_masivas(
            session, curso_id, date.fromisoformat(periodo_inicio), cantidad,
        )
        session.commit()
        return {"cuotas_generadas": total_gen, "inscripciones_afectadas": inscripciones_afectadas}


@server.tool()
def generar_derecho_examen(telefono: str, curso_id: int, monto: str, anio: int | None = None) -> dict:
    """Genera la cuota de derecho de examen para todos los inscriptos activos del curso.
    Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        count = enrollment.generate_exam_cuotas(session, curso_id, Decimal(monto), anio)
        session.commit()
        return {"cuotas_generadas": count}


@server.tool()
def cargar_aumento(telefono: str, curso_id: int, nuevo_monto_cuota: str, nuevo_monto_matricula: str, motivo: str) -> dict:
    """Carga un aumento de precio para un curso: actualiza el precio vigente,
    guarda el historial, y recalcula las cuotas pendientes/parciales no vencidas
    (no toca cuotas ya vencidas ni pagos parciales ya aplicados). Rol mínimo: owner."""
    with SessionLocal() as session:
        require_rol(session, telefono, set())  # solo owner (bypass en require_rol)
        curso = session.get(Curso, curso_id)
        if curso is None:
            return {"ok": False, "motivo": "curso no encontrado"}
        curso.monto_cuota_mensual = Decimal(nuevo_monto_cuota)
        curso.monto_matricula = Decimal(nuevo_monto_matricula)
        enrollment.save_price_history(session, curso, motivo)
        actualizadas = enrollment.recalculate_pending_cuotas(session, curso_id)
        session.commit()
        return {"ok": True, "cuotas_actualizadas": actualizadas}


@server.tool()
def ajustar_monto_cuota_individual(telefono: str, cuota_id: int, nuevo_monto: str) -> dict:
    """Ajusta el monto de una cuota puntual (recalcula su saldo pendiente
    respetando lo ya imputado). Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        enrollment.actualizar_monto_cuota(session, cuota_id, Decimal(nuevo_monto))
        session.commit()
        return {"ok": True}


@server.tool()
def eliminar_cuota(telefono: str, cuota_id: int) -> dict:
    """Elimina una cuota, solo si no tiene imputaciones (pagos) ya aplicados.
    Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        ok = enrollment.eliminar_cuota(session, cuota_id)
        session.commit()
        return {"ok": ok, "motivo": None if ok else "la cuota tiene imputaciones, no se puede eliminar"}


@server.tool()
def consultar_estado_cuenta(telefono: str, alumno_id: int | None = None) -> dict:
    """Consulta las cuotas de un alumno con su estado y saldo pendiente.
    Un alumno solo puede consultar la suya (ignora alumno_id si lo manda);
    administrativo/owner puede consultar cualquiera (alumno_id obligatorio)."""
    with SessionLocal() as session:
        _, alumno_id_efectivo = require_alumno_propio_o_administrativo(session, telefono, alumno_id)
        alumno = session.get(Alumno, alumno_id_efectivo)
        if alumno is None:
            return {"ok": False, "motivo": "alumno no encontrado"}
        cuotas = []
        total_pendiente = Decimal("0.00")
        for inscripcion in alumno.inscripciones:
            if not inscripcion.activa:
                continue
            for cuota in inscripcion.cuotas:
                cuotas.append({
                    "cuota_id": cuota.id,
                    "curso": inscripcion.curso.nombre,
                    "tipo": cuota.tipo.value,
                    "periodo": cuota.periodo,
                    "fecha_vencimiento": cuota.fecha_vencimiento,
                    "monto_actualizado": cuota.monto_actualizado,
                    "saldo_pendiente": cuota.saldo_pendiente,
                    "estado": cuota.estado.value,
                })
                if cuota.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                    total_pendiente += cuota.saldo_pendiente
        return {
            "alumno": f"{alumno.apellido}, {alumno.nombre}",
            "cuotas": cuotas,
            "total_pendiente": total_pendiente,
        }


@server.tool()
def consultar_morosos(telefono: str, sede_id: int | None = None, periodo: str | None = None) -> dict:
    """Reporte de cuotas vencidas sin pagar (pendiente o parcial, vencimiento
    ya pasado), opcionalmente filtrado por sede (usar listar_sedes para
    resolver el sede_id a partir del nombre) y/o por período específico
    (formato "YYYY-MM", ej. "2026-08" para agosto 2026 — filtra solo las
    cuotas mensuales de ese mes, no aplica a matrícula/examen). Rol mínimo:
    administrativo.

    Importante: esto solo puede mostrar cuotas que EXISTEN como pendientes
    en la base. Si un mes reciente todavía no tiene cuotas generadas para
    todos los alumnos (ej. porque la carga de ese período no se hizo
    todavía), esta tool no va a poder decir quién debía pagar y no pagó —
    solo lista cuotas que ya están cargadas como impagas."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        query = (
            session.query(Inscripcion)
            .join(Alumno, Inscripcion.alumno_id == Alumno.id)
            .filter(Inscripcion.activa == True)
        )
        if sede_id is not None:
            query = query.filter(Alumno.sede_id == sede_id)

        today = date.today()
        morosos: dict[int, dict] = {}
        for inscripcion in query.all():
            for cuota in inscripcion.cuotas:
                if cuota.estado not in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                    continue
                if cuota.fecha_vencimiento >= today:
                    continue
                if periodo is not None and cuota.periodo != periodo:
                    continue
                alumno = inscripcion.alumno
                entry = morosos.setdefault(alumno.id, {
                    "alumno": f"{alumno.apellido}, {alumno.nombre}",
                    "telefono": alumno.telefono,
                    "cuotas_vencidas": 0,
                    "total_adeudado": Decimal("0.00"),
                })
                entry["cuotas_vencidas"] += 1
                entry["total_adeudado"] += cuota.saldo_pendiente

        return {"morosos": list(morosos.values())}


# ── Cobros (registro directo, sin pasar por conciliación bancaria) ─────────

@server.tool()
def registrar_pago(
    telefono: str,
    alumno_id: int,
    monto: str,
    medio: str,
    fecha: str | None = None,
    cuota_id: int | None = None,
    operador_efectivo: str | None = None,
    observaciones: str | None = None,
) -> dict:
    """Registra un cobro (efectivo o transferencia) en el momento — no hace
    falta esperar el resumen bancario. La conciliación con el banco se hace
    después, una vez al mes, con conciliar_movimientos_pendientes, que
    cruza los movimientos contra los pagos ya cargados acá. Sin cuota_id,
    el monto se distribuye FIFO contra las cuotas impagas del alumno; con
    cuota_id, se imputa ahí directo. medio: "efectivo" o "transferencia".
    fecha en formato YYYY-MM-DD (default: hoy). Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        try:
            medio_enum = MedioPagoEnum(medio.strip().lower())
        except ValueError:
            return {"ok": False, "motivo": f"medio inválido: {medio!r}. Usar 'efectivo' o 'transferencia'."}
        if cuota_id is not None:
            cuota = session.get(Cuota, cuota_id)
            if cuota is None:
                return {"ok": False, "motivo": "cuota no encontrada"}
            if cuota.inscripcion.alumno_id != alumno_id:
                return {"ok": False, "motivo": "esa cuota no pertenece a ese alumno"}
        pago, excedente = cash_payments.registrar_pago(
            session, alumno_id, Decimal(monto), medio_enum,
            date.fromisoformat(fecha) if fecha else date.today(),
            cuota_id=cuota_id, operador_efectivo=operador_efectivo, observaciones=observaciones,
        )
        session.commit()
        return {"ok": True, "pago_id": pago.id, "excedente": excedente}


# ── Conciliación bancaria (envuelve services/reconciliation.py) ────────────
# A partir de 2026-09-28 es un proceso a POSTERIORI: los pagos ya están
# cargados (registrar_pago, o antes por aplicar_conciliacion) y esto solo
# los cruza contra el resumen bancario del mes — ya no crea el Pago en el
# momento de conciliar, salvo el modo excepcional descripto en aplicar_conciliacion.

@server.tool()
def sugerir_conciliacion(telefono: str, movimiento_id: int) -> dict:
    """Sugiere a qué pago (o, si no hay uno ya cargado, a qué alumno/cuotas)
    corresponde un movimiento bancario sin conciliar, por CUIT/alias/fuzzy
    matching. Si devuelve pago_existente_id, ya hay un pago cargado con
    registrar_pago que probablemente es este movimiento — pasarlo tal cual
    a aplicar_conciliacion (parámetro pago_id) para vincular sin crear nada
    nuevo. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        sugerencia = reconciliation.sugerir_conciliacion(session, movimiento_id)
        return {
            "movimiento_id": sugerencia.movimiento_id,
            "confianza": sugerencia.confianza,
            "metodo": sugerencia.metodo,
            "sin_match": sugerencia.sin_match,
            "es_no_alumno": sugerencia.es_no_alumno,
            "excedente": sugerencia.excedente,
            "pago_existente_id": sugerencia.pago_existente_id,
            "imputaciones": [
                {
                    "alumno_id": i.alumno_id,
                    "alumno_nombre": i.alumno_nombre,
                    "cuota_id": i.cuota_id,
                    "cuota_descripcion": i.cuota_descripcion,
                    "monto_a_imputar": i.monto_a_imputar,
                    "saldo_restante_cuota": i.saldo_restante_cuota,
                }
                for i in sugerencia.imputaciones
            ],
        }


@server.tool()
def aplicar_conciliacion(
    telefono: str,
    movimiento_id: int,
    pago_id: int | None = None,
    imputaciones: list[dict] | None = None,
) -> dict:
    """Confirma una conciliación. Uso normal: pasar pago_id (el
    pago_existente_id que devolvió sugerir_conciliacion) para vincular un
    pago ya registrado — no crea nada, solo confirma el cruce. Uso
    excepcional: pasar imputaciones ([{"cuota_id": int, "monto_imputado":
    str}]) para crear un Pago nuevo en el momento, para movimientos que no
    tienen un pago pre-cargado (ej. datos viejos, o un cobro que se saltó
    registrar_pago). Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        if pago_id is None and not imputaciones:
            return {"ok": False, "motivo": "hay que pasar pago_id o imputaciones"}
        pago_id_resultado = reconciliation.aplicar_conciliacion(
            session, movimiento_id, imputaciones=imputaciones, operador=telefono, pago_id=pago_id,
        )
        session.commit()
        return {"pago_id": pago_id_resultado}


@server.tool()
def conciliar_movimientos_pendientes(telefono: str) -> dict:
    """Corre la conciliación mensual: cruza todos los movimientos bancarios
    pendientes contra los pagos ya registrados (registrar_pago, o una
    conciliación manual previa) y vincula automáticamente los que matchean
    con confianza alta — no crea pagos nuevos, solo confirma el cruce.
    Devuelve 'vinculados' (los que se cerraron solos) y 'a_revisar' (sin
    match claro: puede ser un cobro que nunca se registró, o un movimiento
    que no es un pago de alumno) — esos se resuelven a mano con
    sugerir_conciliacion/aplicar_conciliacion. Pensado para correr una vez
    al mes, después de importar el resumen bancario. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        resultado = reconciliation.conciliar_pendientes(session)
        session.commit()
        return resultado


@server.tool()
def guardar_alias_cobro(telefono: str, alumno_id: int, alias: str) -> dict:
    """Guarda un alias de cobro para un alumno, para que la próxima transferencia
    con ese alias matchee sola. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        nuevo = reconciliation.guardar_alias_cobro(session, alumno_id, alias)
        session.commit()
        return {"nuevo": nuevo}


# ── Profesores ───────────────────────────────────────────────────────────

@server.tool()
def registrar_profesor(
    telefono: str,
    nombre: str,
    apellido: str,
    telefono_profesor: str | None = None,
    tarifa_hora: str | None = None,
) -> dict:
    """Da de alta un profesor. Rol mínimo: owner."""
    with SessionLocal() as session:
        require_rol(session, telefono, set())  # solo owner
        profesor = Profesor(
            nombre=nombre,
            apellido=apellido,
            telefono=telefono_profesor,
            tarifa_hora=Decimal(tarifa_hora) if tarifa_hora else None,
        )
        session.add(profesor)
        session.commit()
        return {"profesor_id": profesor.id}


# ── Aulas y horarios (grid semanal aula×horario, ver services/horarios.py) ──

def _parsear_hora(valor: str) -> time:
    return time.fromisoformat(valor)


def _parsear_dias(dias: list[str]) -> set[DiaSemanaEnum]:
    return {DiaSemanaEnum(d.strip().lower()) for d in dias}


@server.tool()
def crear_aula(telefono: str, sede_id: int, nombre: str, color: str | None = None) -> dict:
    """Da de alta un aula física (o virtual, ej. "Online") en una sede, para
    poder asignarle grupos con crear_grupo. color es un hex opcional
    (ej. "#0D8657"); si no se da, el grid de horarios colorea los grupos
    por profesor en vez de por aula. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        aula = Aula(sede_id=sede_id, nombre=nombre.strip(), color=color, activa=True)
        session.add(aula)
        session.commit()
        return {"aula_id": aula.id, "nombre": aula.nombre}


@server.tool()
def listar_aulas(telefono: str, sede_id: int | None = None) -> dict:
    """Lista las aulas activas, filtro opcional por sede_id (ver
    listar_sedes) — usar para resolver aula_id antes de crear/mover un
    grupo. Cualquier usuario registrado puede usarla."""
    with SessionLocal() as session:
        if resolver_usuario(session, telefono) is None:
            raise AccesoDenegado(f"El número {telefono} no está registrado.")
        query = session.query(Aula).filter(Aula.activa == True)
        if sede_id is not None:
            query = query.filter(Aula.sede_id == sede_id)
        return {"aulas": [
            {"id": a.id, "nombre": a.nombre, "sede_id": a.sede_id, "sede": a.sede.nombre}
            for a in query.all()
        ]}


@server.tool()
def listar_grupos(
    telefono: str,
    curso_id: int | None = None,
    sede_id: int | None = None,
    profesor_id: int | None = None,
) -> dict:
    """Lista los grupos (comisiones) activos con su día(s), horario, aula y
    profesor — usar para resolver grupo_id antes de asignar un alumno o
    mover un horario. Filtros opcionales por curso_id, sede_id o
    profesor_id. Cualquier usuario registrado puede usarla."""
    with SessionLocal() as session:
        if resolver_usuario(session, telefono) is None:
            raise AccesoDenegado(f"El número {telefono} no está registrado.")
        query = session.query(Grupo).join(Curso, Grupo.curso_id == Curso.id).filter(Grupo.activo == True)
        if curso_id is not None:
            query = query.filter(Grupo.curso_id == curso_id)
        if sede_id is not None:
            query = query.filter(Curso.sede_id == sede_id)
        if profesor_id is not None:
            query = query.filter(Grupo.profesor_id == profesor_id)
        return {"grupos": [
            {
                "id": g.id,
                "nombre": g.nombre,
                "curso_id": g.curso_id,
                "curso": g.curso.nombre,
                "sede": g.curso.sede.nombre,
                "dias": sorted(gd.dia_semana.value for gd in g.dias),
                "hora_inicio": g.hora_inicio.strftime("%H:%M"),
                "hora_fin": g.hora_fin.strftime("%H:%M"),
                "aula": g.aula.nombre if g.aula else None,
                "profesor": f"{g.profesor.nombre} {g.profesor.apellido}" if g.profesor else None,
                "cupo_maximo": g.cupo_maximo,
                "inscriptos": len(g.inscripciones),
            }
            for g in query.all()
        ]}


@server.tool()
def crear_grupo(
    telefono: str,
    curso_id: int,
    dias_semana: list[str],
    hora_inicio: str,
    hora_fin: str,
    aula_id: int | None = None,
    profesor_id: int | None = None,
    nombre: str | None = None,
    cupo_maximo: int | None = None,
) -> dict:
    """Crea una comisión (grupo) concreta de un curso: día(s) de semana +
    horario + aula + profesor. Un mismo curso puede tener varios grupos en
    distintos horarios. Antes de crear, valida que no choque con otro grupo
    activo en la misma aula o con el mismo profesor (mismo día, horario
    superpuesto) — si hay choque, no crea nada y devuelve el detalle en
    'conflictos'. dias_semana: lista con valores "lunes".."domingo".
    hora_inicio/hora_fin en formato HH:MM. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        try:
            dias = _parsear_dias(dias_semana)
            h_ini, h_fin = _parsear_hora(hora_inicio), _parsear_hora(hora_fin)
        except ValueError as exc:
            return {"ok": False, "motivo": f"Horario o día inválido: {exc}"}
        if not dias:
            return {"ok": False, "motivo": "dias_semana no puede estar vacío"}
        if h_fin <= h_ini:
            return {"ok": False, "motivo": "hora_fin tiene que ser posterior a hora_inicio"}

        conflictos = horarios.detectar_conflictos(session, dias, h_ini, h_fin, aula_id, profesor_id)
        if conflictos:
            return {"ok": False, "motivo": "Choque de horario con otro grupo", "conflictos": conflictos}

        grupo = Grupo(
            curso_id=curso_id, aula_id=aula_id, profesor_id=profesor_id,
            nombre=nombre, hora_inicio=h_ini, hora_fin=h_fin,
            cupo_maximo=cupo_maximo, activo=True,
        )
        session.add(grupo)
        session.flush()
        for d in dias:
            session.add(GrupoDia(grupo_id=grupo.id, dia_semana=d))
        session.commit()
        return {"ok": True, "grupo_id": grupo.id}


@server.tool()
def mover_grupo(
    telefono: str,
    grupo_id: int,
    aula_id: int | None = None,
    profesor_id: int | None = None,
    hora_inicio: str | None = None,
    hora_fin: str | None = None,
    dias_semana: list[str] | None = None,
) -> dict:
    """Cambia aula, profesor, horario y/o días de un grupo existente. Solo
    modifica los campos que se pasan — el resto queda igual. Valida choques
    de horario antes de aplicar, igual que crear_grupo; si hay choque, no
    modifica nada. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        grupo = session.get(Grupo, grupo_id)
        if grupo is None:
            return {"ok": False, "motivo": "grupo no encontrado"}

        try:
            dias = _parsear_dias(dias_semana) if dias_semana is not None else {gd.dia_semana for gd in grupo.dias}
            h_ini = _parsear_hora(hora_inicio) if hora_inicio else grupo.hora_inicio
            h_fin = _parsear_hora(hora_fin) if hora_fin else grupo.hora_fin
        except ValueError as exc:
            return {"ok": False, "motivo": f"Horario o día inválido: {exc}"}
        if h_fin <= h_ini:
            return {"ok": False, "motivo": "hora_fin tiene que ser posterior a hora_inicio"}

        aula_efectiva = aula_id if aula_id is not None else grupo.aula_id
        profesor_efectivo = profesor_id if profesor_id is not None else grupo.profesor_id

        conflictos = horarios.detectar_conflictos(
            session, dias, h_ini, h_fin, aula_efectiva, profesor_efectivo, excluir_grupo_id=grupo.id,
        )
        if conflictos:
            return {"ok": False, "motivo": "Choque de horario con otro grupo", "conflictos": conflictos}

        grupo.hora_inicio, grupo.hora_fin = h_ini, h_fin
        grupo.aula_id, grupo.profesor_id = aula_efectiva, profesor_efectivo
        if dias_semana is not None:
            grupo.dias.clear()
            for d in dias:
                session.add(GrupoDia(grupo_id=grupo.id, dia_semana=d))
        session.commit()
        return {"ok": True}


@server.tool()
def asignar_alumno_a_grupo(telefono: str, inscripcion_id: int, grupo_id: int) -> dict:
    """Asigna la inscripción de un alumno a un grupo (comisión) concreto —
    para saber en qué aula/horario/profesor específico está, más allá del
    curso general. El grupo tiene que ser del mismo curso que la
    inscripción. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        inscripcion = session.get(Inscripcion, inscripcion_id)
        if inscripcion is None:
            return {"ok": False, "motivo": "inscripcion no encontrada"}
        grupo = session.get(Grupo, grupo_id)
        if grupo is None:
            return {"ok": False, "motivo": "grupo no encontrado"}
        if grupo.curso_id != inscripcion.curso_id:
            return {"ok": False, "motivo": "el grupo pertenece a otro curso que la inscripción"}
        inscripcion.grupo_id = grupo.id
        session.commit()
        return {"ok": True}


@server.tool()
def generar_dashboard_horarios(telefono: str, sede_id: int | None = None) -> dict:
    """Genera un link al grid semanal de horarios (aulas × franja horaria,
    agrupado en "Lunes y Miércoles"/"Martes y Jueves", con cada grupo
    coloreado por profesor) — la misma visualización que usaba la
    secretaría en Excel, ahora servida desde la base. Sin sede_id, la
    página trae todas las sedes con un selector adentro; con sede_id, solo
    esa (ver listar_sedes). El link vence en 24hs. Rol mínimo: profesor."""
    with SessionLocal() as session:
        usuario = require_rol(session, telefono, {RolWhatsappEnum.profesor, RolWhatsappEnum.administrativo})
        token = crear_token_horario(session, usuario, sede_id)
        session.commit()
        if not PUBLIC_BASE_URL:
            return {"error": "Falta configurar PUBLIC_BASE_URL en el servidor."}
        return {"url": f"{PUBLIC_BASE_URL}/reportes/{token}", "valido_por_horas": 24}


@server.tool()
def generar_dashboard_listados(telefono: str, sede_id: int) -> dict:
    """Genera un link al listado de alumnos por curso/comisión de una sede
    (nombre, fecha de nacimiento, referente de pago, teléfono, email; sin
    matrícula ni saldo — para eso está generar_dashboard/consultar_estado_cuenta)
    — la misma vista que usaba la secretaría en la hoja "LISTAS" del Excel,
    con un selector de curso adentro, ahora servida desde la base. No se
    le da a profesor porque muestra los alumnos de TODOS los cursos de la
    sede, no solo los suyos (ver docs/ARCHITECTURE.md sección 4: profesor
    ve "nombres de su curso", no de toda la sede). El link vence en 24hs.
    Rol mínimo: administrativo."""
    with SessionLocal() as session:
        usuario = require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        token = crear_token_listados(session, usuario, sede_id)
        session.commit()
        if not PUBLIC_BASE_URL:
            return {"error": "Falta configurar PUBLIC_BASE_URL en el servidor."}
        return {"url": f"{PUBLIC_BASE_URL}/reportes/{token}", "valido_por_horas": 24}
