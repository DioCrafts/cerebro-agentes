"""Capabilities any domain can use: mail, browser, documents, research. See ``contrato.py``.

* ``contrato.py`` — the contract (operations, effects, consent, untrusted results) and ``pedir``;
* ``registro.py`` — discovery and validation of the packages ``hacienda_gpt.capacidades.<clave>``;
* ``peticion.py`` — :func:`pedir`: the only way a domain uses a capability (consent, effect, schemas, metering);
* ``consentimiento.py`` — the user's consent per capability (stored with the modules' choices).
"""
