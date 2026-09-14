"""Resolución y verificación de rol por número de WhatsApp.

El control de acceso vive acá, no en el prompt del agente ni confiado al
modelo — cada tool de app/mcp_server.py llama a require_rol() (o
require_rol_o_propio()) antes de tocar la base. Ver docs/ARCHITECTURE.md,
sección 4.
"""
from sqlalchemy.orm import Session

from app.models import RolWhatsappEnum, UsuarioWhatsapp


class AccesoDenegado(Exception):
    """Número no registrado, o registrado con un rol sin permiso para la acción."""


def resolver_usuario(session: Session, telefono: str) -> UsuarioWhatsapp | None:
    return (
        session.query(UsuarioWhatsapp)
        .filter(UsuarioWhatsapp.telefono == telefono, UsuarioWhatsapp.activo == True)
        .first()
    )


def require_rol(
    session: Session,
    telefono: str,
    roles_permitidos: set[RolWhatsappEnum],
) -> UsuarioWhatsapp:
    """Exige que el número esté registrado y tenga uno de los roles dados.
    `owner` siempre pasa (tiene CRUD completo en todo, ver ARCHITECTURE.md sección 4)."""
    usuario = resolver_usuario(session, telefono)
    if usuario is None:
        raise AccesoDenegado(f"El número {telefono} no está registrado. Pedile a un owner que lo dé de alta.")
    if usuario.rol == RolWhatsappEnum.owner:
        return usuario
    if usuario.rol not in roles_permitidos:
        raise AccesoDenegado(f"Tu rol ({usuario.rol.value}) no tiene permiso para esta acción.")
    return usuario


def require_alumno_propio_o_administrativo(
    session: Session,
    telefono: str,
    alumno_id: int | None,
) -> tuple[UsuarioWhatsapp, int]:
    """Para tools tipo 'lectura de su propio estado' (alumno) vs 'cualquiera' (administrativo/owner).
    Devuelve (usuario, alumno_id_efectivo) — si es alumno, ignora el alumno_id pedido y
    fuerza el propio, para que no pueda consultar la ficha de otro."""
    usuario = resolver_usuario(session, telefono)
    if usuario is None:
        raise AccesoDenegado(f"El número {telefono} no está registrado.")
    if usuario.rol in (RolWhatsappEnum.owner, RolWhatsappEnum.administrativo):
        if alumno_id is None:
            raise AccesoDenegado("Falta indicar alumno_id.")
        return usuario, alumno_id
    if usuario.rol == RolWhatsappEnum.alumno:
        if usuario.alumno_id is None:
            raise AccesoDenegado("Tu número está registrado como alumno pero no está vinculado a ninguna ficha.")
        return usuario, usuario.alumno_id
    raise AccesoDenegado(f"Tu rol ({usuario.rol.value}) no tiene permiso para esta acción.")
