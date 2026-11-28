"""Browser agent: acts on official websites on the user's behalf, safely.

* ``guion.py`` — declarative, versioned scripts (selectors and steps as data) and their static safety checks;
* ``seguridad.py`` — network allow-list per run, element checks, redaction;
* ``ejecutor.py`` — runs a script in Chromium (Playwright) after the USER logged in;
* ``reparacion.py`` — optional LLM selector repair behind a deterministic gate;
* ``sitio.py`` — sites a mission may run on: origins, login, masks; no steps;
* ``instantanea.py`` — the page's ARIA snapshot as a model may see it: masked, cut, with its valid targets;
* ``agente.py`` — missions: a model chooses each action over the snapshot, behind deterministic gates;
* ``confinamiento.py`` — the Chromium process below the browser's own policy (name resolution, sandbox-exec);
* ``vigilancia.py`` — format-watch sources, repair proposals, redacted diagnoses;
* ``tareas.py`` — tasks: consent, kill switch, rate limits, results (luz import, pending actions);
* ``modulo.py`` — the ``navegador`` household module (its tools in the chat);
* ``capacidad.py`` — the browser as a CAPABILITY (``CAPACIDAD``): what a domain asks for with ``pedir``
  («descarga mi consumo»).

Moved here from ``hacienda_gpt/navegador`` (an alias in the compatibility package ``hacienda_gpt``). ``MODULO`` is
the chat front the module registry discovers here (the old ``hacienda_gpt/modulos/navegador`` discovery shim).

Playwright is imported lazily: the API starts without it, and no browser starts unless ``NAVEGADOR_ACTIVO``.
"""

from asistente.capacidades.navegador.capacidad import CAPACIDAD
from asistente.capacidades.navegador.modulo import MODULO

__all__ = ["CAPACIDAD", "MODULO"]
