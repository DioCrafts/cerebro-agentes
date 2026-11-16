# Imagen del backend de HaciendaGPT: la MISMA imagen sirve al proceso «api» y al proceso «worker»
# (docker-compose.yml elige la orden). Ejemplo: revísalo antes de usarlo en producción (docs/despliegue.md).
#
#   docker build -f docs/despliegue/Dockerfile -t haciendagpt-backend .
FROM python:3.13-slim

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
