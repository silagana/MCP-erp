"""Dashboard de KPIs — la página HTML detrás de los links que manda la tool
`generar_dashboard` (ver app/mcp_server.py). Montado sobre el mismo servicio
de WhatsApp (app/whatsapp_webhook.py incluye este router), así el link
funciona sin importar por qué canal se pidió.

Cada link es un token random en `reporte_token` con vencimiento (24hs) que
graba el alcance (completo vs. solo-su-cuenta) que tenía el que lo pidió en
ESE momento — el control de acceso vive acá igual que en las tools del MCP,
no es "quien tenga el link ve todo".

Identidad visual tomada de docs/BRAND.md (colores/tipografía reales de
stclarescenter.com.ar). Gráficos con Chart.js vía CDN, sin agregar
dependencias de Python.
"""
import json
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.models import (
    Alumno, AlcanceReporteEnum, Aula, Cuota, Curso, EstadoAlumnoEnum,
    EstadoCuotaEnum, Grupo, Inscripcion, Pago, ReporteToken, Sede, UsuarioWhatsapp,
)

router = APIRouter()

TOKEN_VALIDEZ_HORAS = 24


# ── Generación / validación de tokens ───────────────────────────────────────

def crear_token_reporte(session: Session, usuario: UsuarioWhatsapp) -> str:
    """Crea un token de dashboard con el alcance correspondiente al rol
    actual del usuario. No recibe el rol como parámetro — lo saca de la
    base en este momento, para que no se pueda falsear."""
    alcance = (
        AlcanceReporteEnum.alumno
        if usuario.rol.value == "alumno"
        else AlcanceReporteEnum.completo
    )
    token = ReporteToken(
        token=secrets.token_urlsafe(32),
        usuario_whatsapp_id=usuario.id,
        alcance=alcance,
        alumno_id=usuario.alumno_id if alcance == AlcanceReporteEnum.alumno else None,
        expira=datetime.utcnow() + timedelta(hours=TOKEN_VALIDEZ_HORAS),
    )
    session.add(token)
    session.flush()
    return token.token


def crear_token_horario(session: Session, usuario: UsuarioWhatsapp, sede_id: int | None = None) -> str:
    """Token de dashboard para el grid de horarios — mismo mecanismo que
    crear_token_reporte, alcance separado (no expone KPIs de
    ingresos/morosidad a un profesor). sede_id=None: la página trae todas
    las sedes con un selector adentro, en vez de un link por sede."""
    token = ReporteToken(
        token=secrets.token_urlsafe(32),
        usuario_whatsapp_id=usuario.id,
        alcance=AlcanceReporteEnum.horarios,
        sede_id=sede_id,
        expira=datetime.utcnow() + timedelta(hours=TOKEN_VALIDEZ_HORAS),
    )
    session.add(token)
    session.flush()
    return token.token


def crear_token_listados(session: Session, usuario: UsuarioWhatsapp, sede_id: int) -> str:
    """Token de dashboard para el listado de alumnos por curso/comisión de
    una sede — mismo mecanismo que crear_token_reporte/crear_token_horario."""
    token = ReporteToken(
        token=secrets.token_urlsafe(32),
        usuario_whatsapp_id=usuario.id,
        alcance=AlcanceReporteEnum.listados,
        sede_id=sede_id,
        expira=datetime.utcnow() + timedelta(hours=TOKEN_VALIDEZ_HORAS),
    )
    session.add(token)
    session.flush()
    return token.token


def _resolver_token(session: Session, token: str) -> ReporteToken | None:
    fila = session.query(ReporteToken).filter(ReporteToken.token == token).first()
    if fila is None or fila.expira < datetime.utcnow():
        return None
    return fila


# ── Consultas de KPIs ────────────────────────────────────────────────────────

def _d(x) -> float:
    return float(x) if x is not None else 0.0


def kpis_alumnos(session: Session) -> dict:
    activos = (
        session.query(Alumno)
        .filter(Alumno.estado == EstadoAlumnoEnum.activo)
        .count()
    )

    por_sede: dict[str, int] = defaultdict(int)
    for _, sede_nombre in (
        session.query(Alumno, Sede.nombre)
        .join(Sede, Alumno.sede_id == Sede.id)
        .filter(Alumno.estado == EstadoAlumnoEnum.activo)
        .all()
    ):
        por_sede[sede_nombre] += 1

    por_curso: dict[str, int] = defaultdict(int)
    for _, curso_nombre in (
        session.query(Inscripcion, Curso.nombre)
        .join(Curso, Inscripcion.curso_id == Curso.id)
        .filter(Inscripcion.activa == True, Inscripcion.provisional == False)
        .all()
    ):
        por_curso[curso_nombre] += 1

    hace_30d = date.today() - timedelta(days=30)
    inscripciones_nuevas = (
        session.query(Inscripcion)
        .filter(Inscripcion.fecha_inscripcion >= hace_30d, Inscripcion.provisional == False)
        .count()
    )
    bajas_recientes = (
        session.query(Alumno)
        .filter(Alumno.estado == EstadoAlumnoEnum.baja, Alumno.fecha_estado >= hace_30d)
        .count()
    )

    top_cursos = dict(sorted(por_curso.items(), key=lambda kv: kv[1], reverse=True)[:8])

    return {
        "activos": activos,
        "por_sede": dict(por_sede),
        "top_cursos": top_cursos,
        "inscripciones_nuevas_30d": inscripciones_nuevas,
        "bajas_30d": bajas_recientes,
    }


def kpis_ingresos(session: Session) -> dict:
    hoy = date.today()
    desde = (hoy.replace(day=1) - timedelta(days=170)).replace(day=1)  # ~6 meses atrás

    pagos = session.query(Pago).filter(Pago.fecha >= desde).all()

    por_mes: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    por_medio: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for p in pagos:
        por_mes[p.fecha.strftime("%Y-%m")] += p.monto
        por_medio[p.medio.value] += p.monto

    mes_actual = hoy.strftime("%Y-%m")
    total_mes_actual = por_mes.get(mes_actual, Decimal("0.00"))

    return {
        "total_mes_actual": _d(total_mes_actual),
        "por_mes": {k: _d(v) for k, v in sorted(por_mes.items())},
        "por_medio": {k: _d(v) for k, v in por_medio.items()},
    }


def kpis_morosidad(session: Session) -> dict:
    hoy = date.today()
    filas = (
        session.query(Cuota, Alumno, Sede)
        .join(Inscripcion, Cuota.inscripcion_id == Inscripcion.id)
        .join(Alumno, Inscripcion.alumno_id == Alumno.id)
        .join(Sede, Alumno.sede_id == Sede.id)
        .filter(
            Cuota.estado.in_([EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial]),
            Cuota.fecha_vencimiento < hoy,
            Inscripcion.activa == True,
        )
        .all()
    )

    total_adeudado = Decimal("0.00")
    por_sede: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    alumnos_morosos: set[int] = set()
    for cuota, alumno, sede in filas:
        total_adeudado += cuota.saldo_pendiente
        por_sede[sede.nombre] += cuota.saldo_pendiente
        alumnos_morosos.add(alumno.id)

    return {
        "total_adeudado": _d(total_adeudado),
        "cantidad_alumnos": len(alumnos_morosos),
        "por_sede": {k: _d(v) for k, v in por_sede.items()},
    }


def kpis_alumno_propio(session: Session, alumno_id: int) -> dict:
    alumno = session.get(Alumno, alumno_id)
    if alumno is None:
        return {"alumno": None, "cuotas": [], "total_pendiente": 0.0}

    cuotas = []
    total_pendiente = Decimal("0.00")
    for inscripcion in alumno.inscripciones:
        if not inscripcion.activa:
            continue
        for cuota in inscripcion.cuotas:
            cuotas.append({
                "curso": inscripcion.curso.nombre,
                "tipo": cuota.tipo.value,
                "periodo": cuota.periodo,
                "fecha_vencimiento": cuota.fecha_vencimiento.isoformat(),
                "monto": _d(cuota.monto_actualizado),
                "saldo_pendiente": _d(cuota.saldo_pendiente),
                "estado": cuota.estado.value,
            })
            if cuota.estado in (EstadoCuotaEnum.pendiente, EstadoCuotaEnum.parcial):
                total_pendiente += cuota.saldo_pendiente

    return {
        "alumno": f"{alumno.apellido}, {alumno.nombre}",
        "cuotas": cuotas,
        "total_pendiente": _d(total_pendiente),
    }


_DIAS_ORDEN = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
_DIAS_LABEL = {
    "lunes": "Lunes", "martes": "Martes", "miercoles": "Miércoles", "jueves": "Jueves",
    "viernes": "Viernes", "sabado": "Sábado", "domingo": "Domingo",
}
_PALETA_PROFESORES = [
    "#F8AF44", "#0D8657", "#3B8E2C", "#C0392B", "#2980B9",
    "#8E44AD", "#D35400", "#16A085", "#7F8C8D", "#C71585",
]
_SLOT_MINUTOS = 30


def _minutos(t) -> int:
    return t.hour * 60 + t.minute


def _minutos_a_hora(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


_PARES_DIAS = [
    ("lym", "Lunes y Miércoles", {"lunes", "miercoles"}),
    ("myj", "Martes y Jueves", {"martes", "jueves"}),
]


def _par_de_dias(dias_grupo: set[str]) -> str:
    """Los cursos acá siempre se dictan en el mismo par de días (lunes+miércoles
    o martes+jueves) — así estaban armadas las hojas "AULAS LYM"/"AULAS MYJ"
    del Excel original, cada una con un solo grid (no uno por día suelto,
    porque el horario/aula/profesor es idéntico ambos días del par). Lo que
    no cae en ninguno de los dos pares (viernes, sábado, clases sueltas)
    queda agrupado aparte en "Otros días"."""
    for clave, _, dias_pares in _PARES_DIAS:
        if dias_grupo & dias_pares:
            return clave
    return "otros"


def _armar_grid(grupos: list[Grupo], aulas: list[Aula], color_por_profesor: dict[int, str]) -> dict:
    """color_por_profesor se pasa desde afuera (compartido entre todos los
    pares de días de una misma sede) para que un mismo profesor tenga
    siempre el mismo color en Lunes y Miércoles y en Martes y Jueves —
    reiniciarlo por grid haría que el primer profesor de cada grid se
    lleve el primer color de la paleta, aunque sean personas distintas."""
    inicio_min = (min(_minutos(g.hora_inicio) for g in grupos) // _SLOT_MINUTOS) * _SLOT_MINUTOS
    fin_min = -(-max(_minutos(g.hora_fin) for g in grupos) // _SLOT_MINUTOS) * _SLOT_MINUTOS
    total_filas = (fin_min - inicio_min) // _SLOT_MINUTOS

    aula_index = {a.id: i for i, a in enumerate(aulas)}
    tiene_sin_aula = any(g.aula_id not in aula_index for g in grupos)
    columnas = [a.nombre for a in aulas] + (["Sin aula"] if tiene_sin_aula else [])

    def color_de(g: Grupo) -> str:
        if g.aula and g.aula.color:
            return g.aula.color
        if g.profesor_id is None:
            return "#B7B7B7"
        if g.profesor_id not in color_por_profesor:
            color_por_profesor[g.profesor_id] = _PALETA_PROFESORES[len(color_por_profesor) % len(_PALETA_PROFESORES)]
        return color_por_profesor[g.profesor_id]

    bloques = []
    for g in grupos:
        columna = aula_index.get(g.aula_id, len(aulas))
        bloques.append({
            "columna": columna,
            "fila_desde": (_minutos(g.hora_inicio) - inicio_min) // _SLOT_MINUTOS,
            "fila_hasta": -(-(_minutos(g.hora_fin) - inicio_min) // _SLOT_MINUTOS),
            "etiqueta": g.nombre or g.curso.nombre,
            "curso": g.curso.nombre,
            "profesor": f"{g.profesor.nombre} {g.profesor.apellido}" if g.profesor else None,
            "horario": f"{g.hora_inicio.strftime('%H:%M')}–{g.hora_fin.strftime('%H:%M')}",
            "inscriptos": len(g.inscripciones),
            "color": color_de(g),
        })

    profesor_ids_en_este_grid = {g.profesor_id for g in grupos if g.profesor_id is not None}
    leyenda = [
        {
            "profesor": next(f"{g.profesor.nombre} {g.profesor.apellido}" for g in grupos if g.profesor_id == pid),
            "color": color_por_profesor[pid],
        }
        for pid in profesor_ids_en_este_grid
    ]

    return {
        "columnas": columnas,
        "bloques": bloques,
        "etiquetas_horas": [_minutos_a_hora(inicio_min + i * _SLOT_MINUTOS) for i in range(total_filas)],
        "total_filas": total_filas,
        "leyenda_profesores": leyenda,
    }


def datos_horarios(session: Session, sede_id: int | None = None) -> dict:
    """Arma el grid semanal aula×horario de una o todas las sedes activas
    (sede_id=None), agrupado en "Lunes y Miércoles" / "Martes y Jueves" —
    misma idea que las hojas "AULAS LYM"/"AULAS MYJ" del Excel de
    secretaría: filas = franjas de _SLOT_MINUTOS, columnas = aulas, cada
    grupo ocupa un bloque según su duración, coloreado por profesor (o por
    Aula.color si está seteado). Grupos sin aula asignada van en una
    columna "Sin aula" aparte. Sedes sin ningún grupo cargado no aparecen."""
    sedes_query = session.query(Sede).filter(Sede.activa == True)
    if sede_id is not None:
        sedes_query = sedes_query.filter(Sede.id == sede_id)
    sedes = sedes_query.order_by(Sede.nombre).all()

    resultado_sedes = []
    for sede in sedes:
        aulas = (
            session.query(Aula)
            .filter(Aula.sede_id == sede.id, Aula.activa == True)
            .order_by(Aula.nombre)
            .all()
        )
        grupos = (
            session.query(Grupo)
            .join(Curso, Grupo.curso_id == Curso.id)
            .filter(Curso.sede_id == sede.id, Grupo.activo == True)
            .all()
        )
        if not grupos:
            continue

        por_par: dict[str, list[Grupo]] = {clave: [] for clave, _, _ in _PARES_DIAS}
        por_par["otros"] = []
        for g in grupos:
            dias_g = {gd.dia_semana.value for gd in g.dias}
            por_par[_par_de_dias(dias_g)].append(g)

        color_por_profesor: dict[int, str] = {}
        pares_out = []
        for clave, label, _ in [*_PARES_DIAS, ("otros", "Otros días", set())]:
            grupos_par = por_par.get(clave, [])
            if not grupos_par:
                continue
            pares_out.append({
                "clave": clave, "label": label,
                **_armar_grid(grupos_par, aulas, color_por_profesor),
            })

        if not pares_out:
            continue

        resultado_sedes.append({"id": sede.id, "nombre": sede.nombre, "pares": pares_out})

    return {"sedes": resultado_sedes}


def datos_listados(session: Session, sede_id: int) -> dict:
    """Arma el listado de alumnos por curso/comisión de una sede, misma
    idea que la hoja "LISTAS" del Excel de secretaría: un bloque por grupo
    (curso + día/horario/aula/profesor si tiene comisión asignada, ver
    Grupo) con la tabla de sus alumnos debajo — sin matrícula ni saldo, esto
    es solo para ver quién está en cada curso (para eso está
    generar_dashboard_listados/generar_dashboard, sección de morosidad).
    No reproduce las columnas "Libro" ni "Certificado" del Excel — no hay
    campo equivalente en el modelo."""
    sede = session.get(Sede, sede_id)
    cursos = (
        session.query(Curso)
        .filter(Curso.sede_id == sede_id, Curso.activo == True)
        .order_by(Curso.nombre)
        .all()
    )

    bloques = []
    for curso in cursos:
        inscripciones = [i for i in curso.inscripciones if i.activa and not i.provisional]
        if not inscripciones:
            continue

        por_grupo: dict[int | None, list[Inscripcion]] = {}
        for ins in inscripciones:
            por_grupo.setdefault(ins.grupo_id, []).append(ins)

        grupos_cache = {gid: session.get(Grupo, gid) for gid in por_grupo if gid is not None}

        def _orden_grupo(item, cache=grupos_cache):
            gid, _ = item
            return (1, "") if gid is None else (0, cache[gid].hora_inicio.strftime("%H:%M"))

        for grupo_id, inscrs in sorted(por_grupo.items(), key=_orden_grupo):
            grupo = grupos_cache.get(grupo_id)
            alumnos_rows = []
            for ins in sorted(inscrs, key=lambda i: (i.alumno.apellido, i.alumno.nombre)):
                alumno = ins.alumno
                referente = next((r for r in alumno.referentes if r.es_default), None) or (
                    alumno.referentes[0] if alumno.referentes else None
                )
                alumnos_rows.append({
                    "nombre_completo": f"{alumno.apellido}, {alumno.nombre}",
                    "fecha_nacimiento": alumno.fecha_nacimiento.strftime("%d/%m/%Y") if alumno.fecha_nacimiento else None,
                    "referente": referente.nombre_completo if referente else None,
                    "telefono": alumno.telefono,
                    "email": alumno.email,
                })

            dias_label = None
            horario_label = None
            if grupo:
                dias_label = ", ".join(
                    _DIAS_LABEL[gd.dia_semana.value]
                    for gd in sorted(grupo.dias, key=lambda gd: _DIAS_ORDEN.index(gd.dia_semana.value))
                )
                horario_label = f"{grupo.hora_inicio.strftime('%H:%M')} a {grupo.hora_fin.strftime('%H:%M')}"

            bloques.append({
                "curso": curso.nombre,
                "nivel": curso.nivel,
                "grupo_nombre": grupo.nombre if grupo else None,
                "dias": dias_label,
                "horario": horario_label,
                "profesor": f"{grupo.profesor.nombre} {grupo.profesor.apellido}" if grupo and grupo.profesor else None,
                "aula": grupo.aula.nombre if grupo and grupo.aula else None,
                "precio_cuota": _d(curso.monto_cuota_mensual),
                "alumnos": alumnos_rows,
            })

    return {"sede": sede.nombre if sede else "", "bloques": bloques}


# ── HTML ─────────────────────────────────────────────────────────────────────

_ESTILOS = """
:root {
  --verde: #0D8657;
  --verde-sec: #3B8E2C;
  --naranja: #F8AF44;
  --rojo: #C0392B;
  --texto: #333333;
  --texto-suave: #777777;
  --fondo-claro: #F8F9F9;
  --blanco: #FFFFFF;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--fondo-claro);
  color: var(--texto);
  font-family: "Open Sans", Helvetica, Arial, sans-serif;
}
header {
  background: var(--blanco);
  padding: 24px 32px;
  border-bottom: 3px solid var(--verde);
}
header h1 {
  font-family: Raleway, Helvetica, Arial, sans-serif;
  font-weight: 800;
  text-transform: uppercase;
  color: var(--texto);
  font-size: 22px;
  margin: 0;
}
header p { color: var(--texto-suave); margin: 4px 0 0; font-size: 13px; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }
section { margin-bottom: 40px; }
section h2 {
  font-family: Raleway, Helvetica, Arial, sans-serif;
  font-weight: 800;
  text-transform: uppercase;
  font-size: 18px;
  color: var(--texto);
  border-left: 4px solid var(--verde);
  padding-left: 10px;
  margin: 0 0 16px;
}
.tarjetas { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 20px; }
.tarjeta {
  background: var(--blanco);
  border-radius: 6px;
  padding: 18px 20px;
  min-width: 160px;
  flex: 1 1 160px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.tarjeta .valor { font-size: 30px; font-weight: 800; color: var(--verde); }
.tarjeta .valor.alerta { color: var(--rojo); }
.tarjeta .etiqueta { font-size: 12px; color: var(--texto-suave); text-transform: uppercase; margin-top: 4px; }
.graficos { display: flex; flex-wrap: wrap; gap: 16px; }
.grafico-caja {
  background: var(--blanco);
  border-radius: 6px;
  padding: 16px;
  flex: 1 1 380px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
table { width: 100%; border-collapse: collapse; background: var(--blanco); border-radius: 6px; overflow: hidden; }
th, td { text-align: left; padding: 10px 12px; font-size: 13px; border-bottom: 1px solid var(--fondo-claro); }
th { background: var(--fondo-claro); color: var(--texto-suave); text-transform: uppercase; font-size: 11px; }
.estado-pagada { color: var(--verde); font-weight: 600; }
.estado-pendiente, .estado-parcial { color: var(--rojo); font-weight: 600; }
footer { text-align: center; color: var(--texto-suave); font-size: 12px; padding: 24px; }
"""

_ESTILOS_HORARIOS = """
.horario-leyenda { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 20px; }
.horario-leyenda span { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--texto-suave); }
.horario-leyenda .punto { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.horario-dia { background: var(--blanco); border-radius: 6px; padding: 16px; margin-bottom: 28px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow-x: auto; }
.horario-dia h3 { font-family: Raleway, Helvetica, Arial, sans-serif; font-weight: 800; text-transform: uppercase; font-size: 14px; margin: 0 0 12px; color: var(--texto); }
.horario-grid { display: grid; border-top: 1px solid #eee; border-left: 1px solid #eee; min-width: 600px; }
.horario-col-header { background: #777777; color: #fff; font-size: 11px; font-weight: 600; text-align: center; padding: 6px 4px; border-right: 1px solid #fff; }
.horario-hora { font-size: 10px; color: var(--texto-suave); padding: 2px 6px; border-top: 1px solid #eee; border-right: 1px solid #eee; text-align: right; }
.horario-bloque { border-radius: 4px; padding: 3px 6px; margin: 1px; font-size: 11px; line-height: 1.3; overflow: hidden; color: #1a1a1a; border: 1px solid rgba(0,0,0,0.1); }
.horario-bloque b { display: block; font-size: 12px; }
"""

_ESTILOS_SELECTOR = """
.selector-caja { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }
.selector-caja label { font-size: 12px; text-transform: uppercase; color: var(--texto-suave); font-weight: 600; }
.selector-caja select {
  font-family: "Open Sans", Helvetica, Arial, sans-serif;
  font-size: 14px;
  padding: 8px 12px;
  border-radius: 6px;
  border: 1px solid #ddd;
  background: var(--blanco);
  color: var(--texto);
  min-width: 240px;
}
"""

_ESTILOS_LISTADOS = """
.listado-bloque { background: var(--blanco); border-radius: 6px; padding: 18px 20px; margin-bottom: 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.listado-bloque h2 { font-family: Raleway, Helvetica, Arial, sans-serif; font-weight: 800; text-transform: uppercase; font-size: 15px; margin: 0 0 4px; color: var(--texto); border-left: 4px solid var(--verde); padding-left: 10px; }
.listado-subtitulo { color: var(--texto-suave); font-size: 12px; margin: 0 0 14px 14px; }
"""


def _render_completo(datos_alumnos: dict, datos_ingresos: dict, datos_morosidad: dict, generado: str) -> str:
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard — St. Clare's</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Raleway:wght@800&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.1/chart.umd.min.js"></script>
<style>{_ESTILOS}</style>
</head>
<body>
<header>
  <h1>St. Clare's — Dashboard</h1>
  <p>Generado el {generado} · válido por {TOKEN_VALIDEZ_HORAS}hs</p>
</header>
<main>

<section>
  <h2>Alumnos</h2>
  <div class="tarjetas">
    <div class="tarjeta"><div class="valor">{datos_alumnos['activos']}</div><div class="etiqueta">Alumnos activos</div></div>
    <div class="tarjeta"><div class="valor">{datos_alumnos['inscripciones_nuevas_30d']}</div><div class="etiqueta">Inscripciones (30 días)</div></div>
    <div class="tarjeta"><div class="valor alerta">{datos_alumnos['bajas_30d']}</div><div class="etiqueta">Bajas (30 días)</div></div>
  </div>
  <div class="graficos">
    <div class="grafico-caja"><canvas id="chartAlumnosSede"></canvas></div>
    <div class="grafico-caja"><canvas id="chartAlumnosCurso"></canvas></div>
  </div>
</section>

<section>
  <h2>Ingresos</h2>
  <div class="tarjetas">
    <div class="tarjeta"><div class="valor">${datos_ingresos['total_mes_actual']:,.0f}</div><div class="etiqueta">Cobrado este mes</div></div>
  </div>
  <div class="graficos">
    <div class="grafico-caja"><canvas id="chartIngresosMes"></canvas></div>
    <div class="grafico-caja"><canvas id="chartIngresosMedio"></canvas></div>
  </div>
</section>

<section>
  <h2>Morosidad</h2>
  <div class="tarjetas">
    <div class="tarjeta"><div class="valor alerta">${datos_morosidad['total_adeudado']:,.0f}</div><div class="etiqueta">Total adeudado</div></div>
    <div class="tarjeta"><div class="valor alerta">{datos_morosidad['cantidad_alumnos']}</div><div class="etiqueta">Alumnos morosos</div></div>
  </div>
  <div class="graficos">
    <div class="grafico-caja"><canvas id="chartMorosidadSede"></canvas></div>
  </div>
</section>

</main>
<footer>MCP-erp · St. Clare's</footer>

<script>
const COLOR_VERDE = "#0D8657";
const COLOR_NARANJA = "#F8AF44";
const COLOR_ROJO = "#C0392B";
const PALETA = ["#0D8657", "#3B8E2C", "#F8AF44", "#777777", "#0D8657AA", "#3B8E2CAA", "#F8AF44AA", "#999999"];

new Chart(document.getElementById('chartAlumnosSede'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(list(datos_alumnos['por_sede'].keys()))},
    datasets: [{{ data: {json.dumps(list(datos_alumnos['por_sede'].values()))}, backgroundColor: PALETA }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Alumnos activos por sede' }} }} }}
}});

new Chart(document.getElementById('chartAlumnosCurso'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(list(datos_alumnos['top_cursos'].keys()))},
    datasets: [{{ label: 'Inscriptos', data: {json.dumps(list(datos_alumnos['top_cursos'].values()))}, backgroundColor: COLOR_VERDE }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Inscriptos por curso' }}, legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('chartIngresosMes'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(list(datos_ingresos['por_mes'].keys()))},
    datasets: [{{ label: 'Cobrado', data: {json.dumps(list(datos_ingresos['por_mes'].values()))}, borderColor: COLOR_VERDE, backgroundColor: COLOR_VERDE, tension: 0.2 }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Cobrado por mes' }} }} }}
}});

new Chart(document.getElementById('chartIngresosMedio'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(list(datos_ingresos['por_medio'].keys()))},
    datasets: [{{ data: {json.dumps(list(datos_ingresos['por_medio'].values()))}, backgroundColor: [COLOR_VERDE, COLOR_NARANJA] }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Por medio de pago' }} }} }}
}});

new Chart(document.getElementById('chartMorosidadSede'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(list(datos_morosidad['por_sede'].keys()))},
    datasets: [{{ label: 'Adeudado', data: {json.dumps(list(datos_morosidad['por_sede'].values()))}, backgroundColor: COLOR_ROJO }}]
  }},
  options: {{ indexAxis: 'y', plugins: {{ title: {{ display: true, text: 'Adeudado por sede' }}, legend: {{ display: false }} }} }}
}});
</script>
</body>
</html>"""


def _render_alumno(datos: dict, generado: str) -> str:
    filas_html = "".join(
        f"""<tr>
          <td>{c['curso']}</td><td>{c['tipo']}{' ' + c['periodo'] if c['periodo'] else ''}</td>
          <td>{c['fecha_vencimiento']}</td><td>${c['monto']:,.0f}</td>
          <td>${c['saldo_pendiente']:,.0f}</td>
          <td class="estado-{c['estado']}">{c['estado']}</td>
        </tr>"""
        for c in datos["cuotas"]
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mi cuenta — St. Clare's</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Raleway:wght@800&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>{_ESTILOS}</style>
</head>
<body>
<header>
  <h1>St. Clare's — Mi cuenta</h1>
  <p>{datos['alumno'] or ''} · Generado el {generado}</p>
</header>
<main>
<section>
  <div class="tarjetas">
    <div class="tarjeta"><div class="valor alerta">${datos['total_pendiente']:,.0f}</div><div class="etiqueta">Total pendiente</div></div>
  </div>
  <table>
    <thead><tr><th>Curso</th><th>Cuota</th><th>Vencimiento</th><th>Monto</th><th>Saldo</th><th>Estado</th></tr></thead>
    <tbody>{filas_html}</tbody>
  </table>
</section>
</main>
<footer>MCP-erp · St. Clare's</footer>
</body>
</html>"""


def _render_grid(par: dict) -> str:
    n_col = len(par["columnas"])
    header_cells = "".join(
        f'<div class="horario-col-header" style="grid-column:{i + 2};">{c}</div>'
        for i, c in enumerate(par["columnas"])
    )
    hora_cells = "".join(
        f'<div class="horario-hora" style="grid-row:{i + 2};grid-column:1;">{h}</div>'
        for i, h in enumerate(par["etiquetas_horas"])
    )
    bloques_html = "".join(
        f"""<div class="horario-bloque" style="grid-column:{b['columna'] + 2};grid-row:{b['fila_desde'] + 2}/{b['fila_hasta'] + 2};background:{b['color']};" title="{b['curso']} · {b['profesor'] or 'sin profesor'} · {b['horario']} · {b['inscriptos']} alumnos">
          <b>{b['etiqueta']}</b>{b['horario']}{' · ' + b['profesor'] if b['profesor'] else ''}
        </div>"""
        for b in par["bloques"]
    )
    return f"""<div class="horario-dia">
  <h3>{par['label']}</h3>
  <div class="horario-grid" style="grid-template-columns: 70px repeat({n_col}, minmax(120px, 1fr)); grid-template-rows: 26px repeat({par['total_filas']}, 22px);">
    <div class="horario-col-header" style="grid-column:1;">Hora</div>
    {header_cells}
    {hora_cells}
    {bloques_html}
  </div>
</div>"""


def _render_horarios(datos: dict, generado: str) -> str:
    if not datos["sedes"]:
        selector = ""
        cuerpo = "<p>No hay grupos con horario cargado todavía.</p>"
    else:
        opciones = []
        secciones_sede = []
        for i, sede in enumerate(datos["sedes"]):
            opciones.append(f'<option value="{i}">{sede["nombre"]}</option>')

            vistos = set()
            leyenda_items = []
            for par in sede["pares"]:
                for p in par["leyenda_profesores"]:
                    clave = (p["profesor"], p["color"])
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    leyenda_items.append(p)
            leyenda_html = "".join(
                f'<span><span class="punto" style="background:{p["color"]};"></span>{p["profesor"]}</span>'
                for p in leyenda_items
            )

            grids_html = "".join(_render_grid(par) for par in sede["pares"])
            estilo = "" if i == 0 else " style=\"display:none;\""
            secciones_sede.append(f"""<div class="sede-contenido" id="sede-{i}"{estilo}>
{f'<div class="horario-leyenda">{leyenda_html}</div>' if leyenda_html else ''}
{grids_html}
</div>""")

        selector = f"""<div class="selector-caja">
  <label for="sedeSelect">Sede</label>
  <select id="sedeSelect" onchange="mostrarSede(this.value)">{''.join(opciones)}</select>
</div>"""
        cuerpo = "".join(secciones_sede)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Horarios — St. Clare's</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Raleway:wght@800&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>{_ESTILOS}{_ESTILOS_SELECTOR}{_ESTILOS_HORARIOS}</style>
</head>
<body>
<header>
  <h1>St. Clare's — Horarios</h1>
  <p>Generado el {generado} · válido por {TOKEN_VALIDEZ_HORAS}hs</p>
</header>
<main style="max-width: 1400px;">
{selector}
{cuerpo}
</main>
<footer>MCP-erp · St. Clare's</footer>
<script>
function mostrarSede(i) {{
  document.querySelectorAll('.sede-contenido').forEach(function(el) {{ el.style.display = 'none'; }});
  var el = document.getElementById('sede-' + i);
  if (el) el.style.display = '';
}}
</script>
</body>
</html>"""


def _render_listados(datos: dict, generado: str) -> str:
    if not datos["bloques"]:
        selector = ""
        cuerpo = "<p>No hay alumnos inscriptos activos para mostrar.</p>"
    else:
        opciones = []
        secciones = []
        for i, b in enumerate(datos["bloques"]):
            partes_subtitulo = []
            if b["grupo_nombre"]:
                partes_subtitulo.append(b["grupo_nombre"])
            if b["dias"] and b["horario"]:
                partes_subtitulo.append(f"{b['dias']} · {b['horario']}")
            if b["profesor"]:
                partes_subtitulo.append(f"Prof. {b['profesor']}")
            if b["aula"]:
                partes_subtitulo.append(f"Aula {b['aula']}")
            subtitulo = " · ".join(partes_subtitulo) or "Sin comisión asignada"

            etiqueta_opcion = b["curso"] + (f" — {b['dias']} {b['horario']}" if b["dias"] and b["horario"] else "")
            opciones.append(f'<option value="{i}">{etiqueta_opcion}</option>')

            filas = "".join(
                f"""<tr>
                  <td>{a['nombre_completo']}</td>
                  <td>{a['fecha_nacimiento'] or '—'}</td>
                  <td>{a['referente'] or '—'}</td>
                  <td>{a['telefono'] or '—'}</td>
                  <td>{a['email'] or '—'}</td>
                </tr>"""
                for a in b["alumnos"]
            )
            estilo = "" if i == 0 else " style=\"display:none;\""
            secciones.append(f"""<section class="listado-bloque" id="bloque-{i}"{estilo}>
  <h2>{b['curso']}{(' · ' + b['nivel']) if b['nivel'] else ''}</h2>
  <p class="listado-subtitulo">{subtitulo} · ${b['precio_cuota']:,.0f}/mes · {len(b['alumnos'])} alumnos</p>
  <table>
    <thead><tr><th>Alumno</th><th>Nac.</th><th>Referente</th><th>Teléfono</th><th>Email</th></tr></thead>
    <tbody>{filas}</tbody>
  </table>
</section>""")
        selector = f"""<div class="selector-caja">
  <label for="cursoSelect">Curso</label>
  <select id="cursoSelect" onchange="mostrarBloque(this.value)">{''.join(opciones)}</select>
</div>"""
        cuerpo = "".join(secciones)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Listados — St. Clare's</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Raleway:wght@800&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>{_ESTILOS}{_ESTILOS_SELECTOR}{_ESTILOS_LISTADOS}</style>
</head>
<body>
<header>
  <h1>St. Clare's — Alumnos por curso{(' · ' + datos['sede']) if datos['sede'] else ''}</h1>
  <p>Generado el {generado} · válido por {TOKEN_VALIDEZ_HORAS}hs</p>
</header>
<main>
{selector}
{cuerpo}
</main>
<footer>MCP-erp · St. Clare's</footer>
<script>
function mostrarBloque(i) {{
  document.querySelectorAll('.listado-bloque').forEach(function(el) {{ el.style.display = 'none'; }});
  var el = document.getElementById('bloque-' + i);
  if (el) el.style.display = '';
}}
</script>
</body>
</html>"""


@router.get("/reportes/{token}", response_class=HTMLResponse)
def ver_reporte(token: str):
    from app.db import SessionLocal

    with SessionLocal() as session:
        fila = _resolver_token(session, token)
        if fila is None:
            return HTMLResponse("<h1>Link vencido o inválido</h1><p>Pedí uno nuevo por WhatsApp o Telegram.</p>", status_code=404)

        generado = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")

        if fila.alcance == AlcanceReporteEnum.alumno:
            datos = kpis_alumno_propio(session, fila.alumno_id)
            return HTMLResponse(_render_alumno(datos, generado))

        if fila.alcance == AlcanceReporteEnum.horarios:
            datos = datos_horarios(session, fila.sede_id)
            return HTMLResponse(_render_horarios(datos, generado))

        if fila.alcance == AlcanceReporteEnum.listados:
            datos = datos_listados(session, fila.sede_id)
            return HTMLResponse(_render_listados(datos, generado))

        datos_alumnos = kpis_alumnos(session)
        datos_ingresos = kpis_ingresos(session)
        datos_morosidad = kpis_morosidad(session)
        return HTMLResponse(_render_completo(datos_alumnos, datos_ingresos, datos_morosidad, generado))
