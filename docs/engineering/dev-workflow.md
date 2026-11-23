# Developer Workflow — Calidad local

## Objetivo
Estandarizar un pipeline local de calidad para cambios en HaciendaGPT, con foco en compatibilidad incremental y trazabilidad.

## Herramientas habilitadas
- **Format**: `black`.
- **Lint**: `ruff` (incluye import sorting con reglas de isort).
- **Type-check**: `ty` (Astral) sobre `asistente`.
- **Test**: `pytest`.

## Comandos disponibles
Desde la raíz del repositorio:

```bash
make format
make lint
make type-check
make test
make quality
```

`make quality` ejecuta todo en secuencia (format + lint + type-check + test).

## Configuración en `pyproject.toml`
- `tool.black`: longitud de línea y versión objetivo.
- `tool.ruff`: reglas de lint y orden de imports.
- `tool.pytest.ini_options`: configuración base de test discovery.
- `tool.ty`: versión de Python y carpetas que comprueba el chequeo de tipos (`asistente`, sin `tests/`).

## Criterio recomendado para PR
1. Ejecutar `make quality` localmente.
2. Si falla, corregir antes de abrir PR.
3. Si existe limitación de entorno (dependencia externa, API key, etc.), documentar claramente el alcance del fallo y cómo reproducirlo.

## Decisiones de diseño
- Se adoptó una política de **endurecimiento gradual** para no romper la base actual:
  - ty resuelve las dependencias desde el entorno de `uv`; los `# type: ignore[...]` existentes se respetan,
  - chequeo de tipos sobre paquete principal,
  - tests descubiertos solo en `tests/`.
- `PYTHONPATH=.` en comandos para evitar errores de importación al ejecutar localmente sin instalación editable.
