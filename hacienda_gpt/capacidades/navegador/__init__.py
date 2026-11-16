"""Browser agent: acts on official websites on the user's behalf, safely.

* ``guion.py`` — declarative, versioned scripts (selectors and steps as data) and their static safety checks;
* ``seguridad.py`` — network allow-list per run, element checks, redaction;
* ``ejecutor.py`` — runs a script in Chromium (Playwright) after the USER logged in;
* ``reparacion.py`` — optional LLM selector repair behind a deterministic gate;
* ``vigilancia.py`` — format-watch sources, repair proposals, redacted diagnoses;
* ``tareas.py`` — tasks: consent, kill switch, rate limits, results (luz import, pending actions);
* ``modulo.py`` — the ``navegador`` household module (its tools in the chat);
* ``capacidad.py`` — the browser as a CAPABILITY (``CAPACIDAD``): what a domain asks for with ``pedir``
  («descarga mi consumo»).

Moved here from ``hacienda_gpt/navegador`` (which stays as aliases of these modules).

Playwright is imported lazily: the API starts without it, and no browser starts unless ``NAVEGADOR_ACTIVO``.
"""

from hacienda_gpt.capacidades.navegador.capacidad import CAPACIDAD

__all__ = ["CAPACIDAD"]
