"""Vuelca el esquema OpenAPI de la API a la salida estándar, con las claves ordenadas::

    uv run python -m asistente.plataforma.cli.openapi > frontend/openapi.json

Importa la aplicación y llama a ``app.openapi()``: no arranca el servidor ni carga modelos. El frontend genera
sus tipos a partir de ese fichero (``npm run tipos``) y ``make tipos-check`` falla si alguno se queda atrás.

Un campo con valor por defecto siempre sale en la respuesta (la API no usa ``response_model_exclude_*``), pero
Pydantic lo marca como opcional en el esquema de salida salvo que se active
``json_schema_serialization_defaults_required``. Se activa aquí, para todos los modelos, antes de importar la
aplicación: así el esquema de salida dice lo que de verdad llega y el de entrada no cambia. Solo afecta a este
volcado; el ``/openapi.json`` que sirve la API sigue el criterio por defecto de Pydantic.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    from pydantic import BaseModel

    # Los modelos heredan la configuración de ``BaseModel`` al crearse, así que debe ir antes de importarlos.
    BaseModel.model_config["json_schema_serialization_defaults_required"] = True

    from asistente.plataforma.api.api import app

    esquema = app.openapi()
    sys.stdout.write(json.dumps(esquema, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
