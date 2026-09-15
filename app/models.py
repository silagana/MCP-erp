"""Modelos SQLAlchemy de MCP-erp.

Esquema propio, en su propia Postgres — MCP-erp ya no depende de
st-clares-app ni comparte su base de datos (ver docs/ARCHITECTURE.md,
sección "Independencia de st-clares-app").

Las tablas de dominio (Sede, Curso, Alumno, Cuota, etc.) se copiaron
tal cual desde https://github.com/silagana/st-clares-app/blob/master/db/models.py
para no perder la lógica de negocio ya validada. A partir de acá el
esquema evoluciona de forma independiente en este repo.

`UsuarioWhatsapp` es la única tabla nueva: resuelve el rol de cada
número de WhatsApp antes de ejecutar cualquier tool del servidor MCP.
"""
import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Enums ─────────────────────────────────────────────────────────────────────

class ModalidadEnum(str, enum.Enum):
    presencial = "presencial"
    online = "online"

class EstadoAlumnoEnum(str, enum.Enum):
    activo = "activo"
    suspendido = "suspendido"
    baja = "baja"

class VinculoEnum(str, enum.Enum):
    padre = "padre"
    madre = "madre"
    empresa = "empresa"
    otro = "otro"
    mismo_alumno = "mismo_alumno"

class TipoCuotaEnum(str, enum.Enum):
    matricula = "matricula"
    mensual = "mensual"
    examen = "examen"

class EstadoCuotaEnum(str, enum.Enum):
    pendiente = "pendiente"
    parcial = "parcial"
    pagada = "pagada"
    condonada = "condonada"

class MedioPagoEnum(str, enum.Enum):
    transferencia = "transferencia"
    efectivo = "efectivo"

class EstadoMovimientoEnum(str, enum.Enum):
    pendiente = "pendiente"
    conciliado = "conciliado"
    parcial = "parcial"
    ignorado_no_alumno = "ignorado_no_alumno"

class RolWhatsappEnum(str, enum.Enum):
    alumno = "alumno"
    profesor = "profesor"
    administrativo = "administrativo"
    owner = "owner"

class RolMensajeEnum(str, enum.Enum):
    user = "user"
    assistant = "assistant"


# ── Tablas (dominio, copiadas de st-clares-app) ────────────────────────────────

class Sede(Base):
    __tablename__ = "sede"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), nullable=False, unique=True)
    encargada_nombre = Column(String(120))
    encargada_telefono = Column(String(30))
    dia_vencimiento_default = Column(Integer, nullable=False, default=10)
    activa = Column(Boolean, nullable=False, default=True)

    cursos = relationship("Curso", back_populates="sede")
    alumnos = relationship("Alumno", back_populates="sede")


class Curso(Base):
    __tablename__ = "curso"

    id = Column(Integer, primary_key=True)
    sede_id = Column(Integer, ForeignKey("sede.id"), nullable=False)
    nombre = Column(String(120), nullable=False)
    nivel = Column(String(60))
    modalidad = Column(Enum(ModalidadEnum), nullable=False)
    monto_matricula = Column(Numeric(14, 2), nullable=False)
    monto_cuota_mensual = Column(Numeric(14, 2), nullable=False)
    cantidad_cuotas = Column(Integer, nullable=False)
    derecho_examen_monto = Column(Numeric(14, 2))
    activo = Column(Boolean, nullable=False, default=True)

    sede = relationship("Sede", back_populates="cursos")
    inscripciones = relationship("Inscripcion", back_populates="curso")
    precios_historicos = relationship("PrecioHistorico", back_populates="curso")


class PrecioHistorico(Base):
    __tablename__ = "precio_historico"

    id = Column(Integer, primary_key=True)
    curso_id = Column(Integer, ForeignKey("curso.id"), nullable=False)
    vigencia_desde = Column(Date, nullable=False)
    monto_cuota = Column(Numeric(14, 2), nullable=False)
    monto_matricula = Column(Numeric(14, 2), nullable=False)
    motivo = Column(String(255))

    curso = relationship("Curso", back_populates="precios_historicos")


class Alumno(Base):
    __tablename__ = "alumno"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    apellido = Column(String(80), nullable=False)
    dni = Column(String(15), nullable=False, unique=True)
    fecha_nacimiento = Column(Date)
    telefono = Column(String(30))
    email = Column(String(120))
    sede_id = Column(Integer, ForeignKey("sede.id"), nullable=False)
    estado = Column(Enum(EstadoAlumnoEnum), nullable=False, default=EstadoAlumnoEnum.activo)
    fecha_estado = Column(Date)
    motivo_estado = Column(String(255))
    observaciones = Column(Text)

    sede = relationship("Sede", back_populates="alumnos")
    inscripciones = relationship("Inscripcion", back_populates="alumno")
    referentes = relationship("ReferentePago", back_populates="alumno")
    aliases_cobro = relationship("AliasCobroAlumno", back_populates="alumno")


class ReferentePago(Base):
    __tablename__ = "referente_pago"

    id = Column(Integer, primary_key=True)
    alumno_id = Column(Integer, ForeignKey("alumno.id"), nullable=False)
    nombre_completo = Column(String(160), nullable=False)
    cuit_cuil = Column(String(20), nullable=False)
    telefono = Column(String(30))
    vinculo = Column(Enum(VinculoEnum), nullable=False)
    es_default = Column(Boolean, nullable=False, default=False)

    # Sin UniqueConstraint en cuit_cuil: un padre puede ser referente de varios alumnos
    alumno = relationship("Alumno", back_populates="referentes")


class Inscripcion(Base):
    __tablename__ = "inscripcion"

    id = Column(Integer, primary_key=True)
    alumno_id = Column(Integer, ForeignKey("alumno.id"), nullable=False)
    curso_id = Column(Integer, ForeignKey("curso.id"), nullable=False)
    fecha_inscripcion = Column(Date, nullable=False)
    descuento_porcentaje = Column(Numeric(5, 2))   # 10.00 = 10%
    descuento_fijo = Column(Numeric(14, 2))
    activa = Column(Boolean, nullable=False, default=True)
    provisional = Column(Boolean, nullable=False, default=False)  # reserva de cupo año siguiente
    anio_reserva = Column(Integer)  # año para el que se reserva el cupo

    alumno = relationship("Alumno", back_populates="inscripciones")
    curso = relationship("Curso", back_populates="inscripciones")
    cuotas = relationship("Cuota", back_populates="inscripcion")


class Cuota(Base):
    __tablename__ = "cuota"

    id = Column(Integer, primary_key=True)
    inscripcion_id = Column(Integer, ForeignKey("inscripcion.id"), nullable=False)
    tipo = Column(Enum(TipoCuotaEnum), nullable=False)
    numero_cuota = Column(Integer)              # 0=matrícula, 1-N=mensual
    periodo = Column(String(7))                 # YYYY-MM, null para matrícula
    fecha_vencimiento = Column(Date, nullable=False)
    monto_original = Column(Numeric(14, 2), nullable=False)
    monto_actualizado = Column(Numeric(14, 2), nullable=False)
    estado = Column(Enum(EstadoCuotaEnum), nullable=False, default=EstadoCuotaEnum.pendiente)
    saldo_pendiente = Column(Numeric(14, 2), nullable=False)

    inscripcion = relationship("Inscripcion", back_populates="cuotas")
    imputaciones = relationship("Imputacion", back_populates="cuota")


class MovimientoBancario(Base):
    __tablename__ = "movimiento_bancario"

    id = Column(Integer, primary_key=True)
    fecha = Column(Date, nullable=False)
    concepto_raw = Column(Text, nullable=False)
    importe = Column(Numeric(14, 2), nullable=False)
    cuit_detectado = Column(String(20))
    nombre_pagador_detectado = Column(String(160))
    referencia_detectada = Column(String(160))
    subtipo = Column(String(10))                # var / cuo / hon / oih / fac
    tipo_movimiento = Column(String(120))
    hash_unico = Column(String(40), nullable=False, unique=True)
    estado = Column(Enum(EstadoMovimientoEnum), nullable=False, default=EstadoMovimientoEnum.pendiente)
    fecha_importacion = Column(DateTime, nullable=False, default=datetime.utcnow)

    pagos = relationship("Pago", back_populates="movimiento")


class Pago(Base):
    __tablename__ = "pago"

    id = Column(Integer, primary_key=True)
    fecha = Column(Date, nullable=False)
    monto = Column(Numeric(14, 2), nullable=False)
    medio = Column(Enum(MedioPagoEnum), nullable=False)
    movimiento_bancario_id = Column(Integer, ForeignKey("movimiento_bancario.id"))
    operador_efectivo = Column(String(80))
    observaciones = Column(Text)

    movimiento = relationship("MovimientoBancario", back_populates="pagos")
    imputaciones = relationship("Imputacion", back_populates="pago")


class Imputacion(Base):
    __tablename__ = "imputacion"

    id = Column(Integer, primary_key=True)
    pago_id = Column(Integer, ForeignKey("pago.id"), nullable=False)
    cuota_id = Column(Integer, ForeignKey("cuota.id"), nullable=False)
    monto_imputado = Column(Numeric(14, 2), nullable=False)

    pago = relationship("Pago", back_populates="imputaciones")
    cuota = relationship("Cuota", back_populates="imputaciones")


class AliasCobroAlumno(Base):
    __tablename__ = "alias_cobro_alumno"

    id = Column(Integer, primary_key=True)
    alumno_id = Column(Integer, ForeignKey("alumno.id"), nullable=False)
    alias = Column(String(160), nullable=False)
    __table_args__ = (UniqueConstraint("alumno_id", "alias"),)

    alumno = relationship("Alumno", back_populates="aliases_cobro")


class PatronNoAlumno(Base):
    __tablename__ = "patron_no_alumno"

    id = Column(Integer, primary_key=True)
    patron = Column(String(160), nullable=False, unique=True)
    descripcion = Column(String(255))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    accion = Column(String(60), nullable=False)
    entidad = Column(String(60), nullable=False)
    entidad_id = Column(Integer)
    detalle_json = Column(Text)


# ── Tablas nuevas de MCP-erp ────────────────────────────────────────────────────

class Profesor(Base):
    """No existía en st-clares-app (auth ahí era un solo admin). Se agrega acá
    porque el plan de tools del MCP necesita distinguir profesores como rol."""
    __tablename__ = "profesor"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(80), nullable=False)
    apellido = Column(String(80), nullable=False)
    telefono = Column(String(30))
    tarifa_hora = Column(Numeric(14, 2))
    activo = Column(Boolean, nullable=False, default=True)

    usuarios_whatsapp = relationship("UsuarioWhatsapp", back_populates="profesor")


class UsuarioWhatsapp(Base):
    """Resuelve el rol de cada número de WhatsApp. El servidor MCP consulta
    esta tabla ANTES de ejecutar cualquier tool — el control de acceso vive acá,
    no en el prompt del agente (ver docs/ARCHITECTURE.md, sección 4)."""
    __tablename__ = "usuario_whatsapp"

    id = Column(Integer, primary_key=True)
    telefono = Column(String(30), nullable=False, unique=True)
    rol = Column(Enum(RolWhatsappEnum), nullable=False)
    alumno_id = Column(Integer, ForeignKey("alumno.id"))
    profesor_id = Column(Integer, ForeignKey("profesor.id"))
    activo = Column(Boolean, nullable=False, default=True)
    fecha_alta = Column(DateTime, nullable=False, default=datetime.utcnow)
    telegram_chat_id = Column(String(30), unique=True)
    """Canal de prueba alternativo (ver app/telegram_webhook.py) — vincula este
    mismo usuario/rol a un chat de Telegram, sin tocar el canal de WhatsApp.
    Se puede dejar de usar (o borrar todos los valores) sin afectar WhatsApp."""

    alumno = relationship("Alumno")
    profesor = relationship("Profesor", back_populates="usuarios_whatsapp")


class MensajeWhatsapp(Base):
    """Historial de conversación por teléfono, para darle memoria al orchestrator
    entre mensajes (Claude no tiene estado propio entre llamadas a la API)."""
    __tablename__ = "mensaje_whatsapp"

    id = Column(Integer, primary_key=True)
    telefono = Column(String(30), nullable=False, index=True)
    rol = Column(Enum(RolMensajeEnum), nullable=False)
    contenido = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
