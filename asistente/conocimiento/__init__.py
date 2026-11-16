"""Legal knowledge: data read from the BOE with literal quotes, its verification and its maintenance.

One library for every legal figure the assistant uses, whatever module it belongs to:

* :mod:`~asistente.conocimiento.texto` — how a law's page is read and normalised (one normalisation for all);
* :mod:`~asistente.conocimiento.modelo` — the common shape of a group of legal data (figures, literal quotes,
  source, how each figure is spelled);
* :mod:`~asistente.conocimiento.comprobacion` — the one checker: quotes verbatim in the law (in the version they
  were read from), every figure inside its quote;
* :mod:`~asistente.conocimiento.registro` — where each module declares its legal data files, so the weekly BOE
  verification, the reading dates (and their automatic pull request) and the re-encoding loop cover them;
* :mod:`~asistente.conocimiento.recodificacion.de_modulos` — module groups in the agentic re-encoding loop.

The tax parameters keep their own loaders and the loop (``asistente.conocimiento.verificacion``,
``asistente.conocimiento.recodificacion``), which delegate their text handling and literal-quote checks to this package.
"""

from __future__ import annotations

from asistente.conocimiento.comprobacion import (
    MIN_CITA,
    SIN_TEXTO,
    Comprobacion,
    Resultado,
    cita_en_texto,
    citas_no_literales,
    comprobar_grupo,
    comprobar_grupos,
    es_literal,
    verificar,
)
from asistente.conocimiento.modelo import (
    FRONTERA_CIFRA,
    FRONTERA_LIBRE,
    FRONTERA_MIXTA,
    FRONTERA_PALABRA,
    CitaLegal,
    FuenteLegal,
    GrupoLegal,
    Ortografia,
    citas_de_evidencia,
    fuente_de,
    hojas,
)
from asistente.conocimiento.registro import (
    FicheroLegal,
    Recodificable,
    fichero_legal,
    ficheros_legales,
    lecturas_de_modulos,
    registrar,
    verificar_fichero,
    verificar_modulos,
)
from asistente.conocimiento.texto import (
    VERSION_DIARIO,
    TextoDe,
    comprimido,
    formas,
    leer_snapshot,
    texto_de_html,
    texto_plano,
    textos_de_directorio,
    url_de,
)

__all__ = [
    "FRONTERA_CIFRA",
    "FRONTERA_LIBRE",
    "FRONTERA_MIXTA",
    "FRONTERA_PALABRA",
    "MIN_CITA",
    "SIN_TEXTO",
    "VERSION_DIARIO",
    "CitaLegal",
    "Comprobacion",
    "FicheroLegal",
    "FuenteLegal",
    "GrupoLegal",
    "Ortografia",
    "Recodificable",
    "Resultado",
    "TextoDe",
    "cita_en_texto",
    "citas_de_evidencia",
    "citas_no_literales",
    "comprimido",
    "comprobar_grupo",
    "comprobar_grupos",
    "es_literal",
    "fichero_legal",
    "ficheros_legales",
    "formas",
    "fuente_de",
    "hojas",
    "lecturas_de_modulos",
    "leer_snapshot",
    "registrar",
    "texto_de_html",
    "texto_plano",
    "textos_de_directorio",
    "url_de",
    "verificar",
    "verificar_fichero",
    "verificar_modulos",
]
