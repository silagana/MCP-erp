# TODO (decisión pendiente, ver docs/ARCHITECTURE.md):
# Este archivo debe reflejar EXACTAMENTE los modelos de
# https://github.com/silagana/st-clares-app/blob/master/db/models.py
# porque ambos repos apuntan a la misma base Postgres.
#
# Opciones para no duplicar a mano:
#   1) Instalar st-clares-app como dependencia git en requirements.txt
#      (requiere agregarle un setup.py/pyproject.toml al otro repo).
#   2) Copiar db/models.py acá y mantenerlo sincronizado manualmente
#      cada vez que cambie el esquema del lado del Streamlit.
#
# Por ahora: sin implementar. No escribir tools que dependan de esto
# hasta resolver la sincronización.
