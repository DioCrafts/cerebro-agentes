# Imagen del backend de HaciendaGPT: la MISMA imagen sirve al proceso «api» y al proceso «worker»
# (docker-compose.yml elige la orden). Ejemplo: revísalo antes de usarlo en producción (docs/despliegue.md).
#
#   docker build -f docs/despliegue/Dockerfile -t haciendagpt-backend .
#
# Desde la raíz del repositorio: /.dockerignore deja fuera .git, los entornos virtuales, data/, los
# modelos y los secretos (.env), así el contexto es pequeño y no se cuela nada privado en una capa.
#
# Linux no tiene el embedder MLX: los modelos *-mlx (EMBEDDING_MODEL, RERANKER_MODEL,
# CONTEXTUAL_MODEL_PATH…) solo funcionan en Apple Silicon. En esta imagen la búsqueda funciona sin ellos
# (léxica) y MEMORIA_EMBEDDINGS debe quedarse en false; la indexación con embeddings se hace en un Mac.
#
# Datos actualizables dentro del código: hipoteca-indices escribe en asistente/ambitos/hipoteca/data
# (ruta fija en asistente/ambitos/hipoteca/indices.py, DATA_DIR). Dentro del contenedor esa escritura se
# pierde al reconstruir la imagen. Sacarla a un volumen exige que indices.py lea el directorio de una
# variable de entorno (pendiente en Python); hasta entonces la actualización llega con la imagen nueva.
#
# Imagen fijada a una versión exacta. Para fijarla además por digest (recomendado en producción):
#   docker buildx imagetools inspect python:3.14.10-slim     → FROM python:3.14.10-slim@sha256:…
FROM python:3.14.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /srv/hacienda-asesor
# Primero solo las dependencias (capa que se reutiliza mientras uv.lock no cambie).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY asistente ./asistente
# The compatibility package (old module paths, one release): cron lines and scripts still naming hacienda_gpt.
COPY hacienda_gpt ./hacienda_gpt
COPY rules ./rules
COPY README.md ./
RUN uv sync --frozen --no-dev

# Usuario sin privilegios; ./data (bases de datos, estados, diagnósticos) es un volumen.
RUN useradd --system --uid 10001 --home-dir /srv/hacienda-asesor hacienda \
    && mkdir -p /srv/hacienda-asesor/data \
    && chown -R hacienda /srv/hacienda-asesor/data
USER hacienda
VOLUME ["/srv/hacienda-asesor/data"]

EXPOSE 8000
# Proceso «api». El proxy inverso quita el prefijo /api y pasa X-Forwarded-*; uvicorn los cree porque solo
# el proxy llega al puerto 8000 (red interna de compose, sin «ports»). Un solo proceso: ver «Procesos».
CMD ["uvicorn", "asistente.plataforma.api.api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
