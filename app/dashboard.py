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
    Alumno, AlcanceReporteEnum, Cuota, Curso, EstadoAlumnoEnum,
    EstadoCuotaEnum, Inscripcion, Pago, ReporteToken, Sede, UsuarioWhatsapp,
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

        datos_alumnos = kpis_alumnos(session)
        datos_ingresos = kpis_ingresos(session)
        datos_morosidad = kpis_morosidad(session)
        return HTMLResponse(_render_completo(datos_alumnos, datos_ingresos, datos_morosidad, generado))
