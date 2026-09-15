"""Servidor MCP: expone las tools listadas en docs/ARCHITECTURE.md sección 5.

Cada tool resuelve el rol por número de WhatsApp (tabla usuario_whatsapp)
ANTES de tocar la base, vía app/permissions.py — el control de acceso vive
acá, no en el prompt del agente.

Corre en el mismo proceso que app/orchestrator.py (un solo servicio en
Railway) — orchestrator llama a server.call_tool(...) directo, no hay un
transporte MCP (stdio/HTTP) separado corriendo.

Tools todavía sin implementar (quedan para más adelante, ver ARCHITECTURE.md
sección 9): registrar_pago_con_comprobante (necesita visión en el
orchestrator), registrar_pago_efectivo (necesita completar
services/cash_payments.py), importar_resumen_bancario (necesita manejo de
archivos adjuntos de WhatsApp), marcar_asistencia/consultar_horarios
(necesitan tablas nuevas que no existen todavía), y toda la comunicación
saliente de whatsapp_text.py (en pausa, ver sección 7).
"""
import os
from datetime import date
from decimal import Decimal

from mcp.server.mcpserver import MCPServer

from app.dashboard import crear_token_reporte
from app.db import SessionLocal
from app.models import (
    Alumno, Curso, EstadoCuotaEnum, Inscripcion, Profesor, RolWhatsappEnum, Sede,
)
from app.permissions import (
    require_alumno_propio_o_administrativo, require_rol, resolver_usuario,
)
from app.permissions import AccesoDenegado
from app.services import enrollment, reconciliation

server = MCPServer("st-clares-erp")

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


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
        alumnos = (
            session.query(Alumno)
            .filter(
                (Alumno.nombre.ilike(patron))
                | (Alumno.apellido.ilike(patron))
                | (Alumno.dni.ilike(patron))
            )
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
            dni=dni,
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
    descuento_porcentaje: str | None = None,
    descuento_fijo: str | None = None,
    generar_matricula: bool = True,
) -> dict:
    """Inscribe a un alumno en un curso y genera sus cuotas (matrícula + mensuales).
    Rol mínimo: administrativo. fecha_inscripcion en formato YYYY-MM-DD."""
    with SessionLocal() as session:
        require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        inscripcion = Inscripcion(
            alumno_id=alumno_id,
            curso_id=curso_id,
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
def consultar_morosos(telefono: str, sede_id: int | None = None) -> dict:
    """Reporte de cuotas vencidas sin pagar (pendiente o parcial, vencimiento
    ya pasado), opcionalmente filtrado por sede (usar listar_sedes para
    resolver el sede_id a partir del nombre). Rol mínimo: administrativo."""
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


# ── Cobros y conciliación (envuelve services/reconciliation.py) ────────────

@server.tool()
def sugerir_conciliacion(telefono: str, movimiento_id: int) -> dict:
    """Sugiere a qué alumno/cuotas corresponde un movimiento bancario sin
    conciliar, por CUIT/alias/fuzzy matching, con nivel de confianza.
    Rol mínimo: administrativo."""
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
def aplicar_conciliacion(telefono: str, movimiento_id: int, imputaciones: list[dict]) -> dict:
    """Confirma una conciliación: crea el Pago y sus Imputaciones según lo
    sugerido (o ajustado a mano). `imputaciones` es una lista de
    {"cuota_id": int, "monto_imputado": str}. Rol mínimo: administrativo."""
    with SessionLocal() as session:
        usuario = require_rol(session, telefono, {RolWhatsappEnum.administrativo})
        pago_id = reconciliation.aplicar_conciliacion(session, movimiento_id, imputaciones, operador=telefono)
        session.commit()
        return {"pago_id": pago_id}


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
