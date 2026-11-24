"""A thin client of Hindsight's REST API: only the calls the conversation memory makes.

It talks HTTP through the project's one client (:mod:`asistente.plataforma.http`), so timeouts, retries and the
User-Agent follow the same rules as every other outgoing request, and tests swap the transport for an
``httpx.MockTransport``. Every method raises :class:`HindsightError` on a transport error or an unexpected status;
the service (:mod:`asistente.nucleo.recuerdos.servicio`) decides what a failure means for the user's turn.

Endpoints used (Hindsight 1.2, ``/v1/default/banks/{bank}``): ``PUT`` the bank, ``PATCH .../config``, ``POST
.../memories`` (retain), ``POST .../memories/recall``, ``GET .../memories/list``, ``GET|PATCH .../memories/{id}``,
``GET .../documents``, ``GET|DELETE .../documents/{id}`` and ``DELETE`` the bank.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from asistente.plataforma.http import Cliente

# Hindsight's fact types: what the person said about the world, what happened to them, and what Hindsight
# consolidated from several facts.
TIPOS_HECHO = ("world", "experience")
TIPO_OBSERVACION = "observation"
PAGINA = 100


class HindsightError(RuntimeError):
    """Hindsight could not be reached or answered with an unexpected status."""


class NoEncontradoError(HindsightError):
    """The bank, memory or document does not exist (404)."""


class ClienteHindsight:
    def __init__(self, base_url: str, *, http: Cliente | None = None, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = http or Cliente(timeout=timeout, reintentos=1)
        self._timeout = timeout

    # -- plumbing ----------------------------------------------------------------------------------------------

    def _url(self, banco: str, *partes: str) -> str:
        ruta = "/".join(quote(p, safe="") for p in partes)
        return f"{self.base_url}/v1/default/banks/{quote(banco, safe='')}" + (f"/{ruta}" if ruta else "")

    def _pedir(
        self,
        metodo: str,
        url: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        timeout: float | None = None,
        admite_404: bool = False,
    ) -> Any:
        try:
            respuesta = self._http.peticion(
                metodo,
                url,
                json=json,
                params=params,
                accept="application/json",
                timeout=timeout or self._timeout,
                reintentar=metodo in ("GET", "PUT", "DELETE"),
            )
        except httpx.HTTPError as exc:
            raise HindsightError(f"Hindsight no responde ({type(exc).__name__})") from exc
        if respuesta.status_code == 404:
            if admite_404:
                return None
            raise NoEncontradoError(f"Hindsight: {metodo} {url} → 404")
        if respuesta.status_code >= 400:
            raise HindsightError(f"Hindsight: {metodo} {url} → {respuesta.status_code} {respuesta.text[:300]}")
        if not respuesta.content:
            return None
        try:
            return respuesta.json()
        except ValueError as exc:
            raise HindsightError(f"Hindsight: {metodo} {url} devolvió algo que no es JSON") from exc

    # -- banks -------------------------------------------------------------------------------------------------

    def crear_banco(self, banco: str, *, perfil: Mapping[str, Any], config: Mapping[str, Any]) -> None:
        """Create (or update) the bank with its missions, then set its configuration (memory defense…)."""
        self._pedir("PUT", self._url(banco), json=dict(perfil))
        if config:
            self._pedir("PATCH", self._url(banco, "config"), json={"updates": dict(config)})

    def borrar_banco(self, banco: str) -> None:
        """Delete a bank and everything in it. Idempotent: Hindsight answers the same whether it existed or not."""
        self._pedir("DELETE", self._url(banco), admite_404=True)

    # -- retain / recall -----------------------------------------------------------------------------------------

    def retener(
        self,
        banco: str,
        *,
        contenido: str,
        documento_id: str,
        fecha: datetime,
        contexto: str,
        etiquetas: Sequence[str],
        metadatos: Mapping[str, str],
        asincrono: bool = True,
    ) -> None:
        item = {
            "content": contenido,
            "timestamp": fecha.isoformat(),
            "context": contexto,
            "document_id": documento_id,
            "tags": list(etiquetas),
            "metadata": dict(metadatos),
        }
        self._pedir("POST", self._url(banco, "memories"), json={"items": [item], "async": asincrono})

    def recordar(
        self,
        banco: str,
        consulta: str,
        *,
        presupuesto: str,
        max_tokens: int,
        momento: datetime,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Recall: the ranked facts and observations, with the source chunks (the user's own words) included."""
        cuerpo = {
            "query": consulta,
            "budget": presupuesto,
            "max_tokens": max_tokens,
            "query_timestamp": momento.isoformat(),
            "include": {"chunks": {"max_tokens": 4000}},
        }
        datos = self._pedir("POST", self._url(banco, "memories", "recall"), json=cuerpo, timeout=timeout)
        return datos if isinstance(datos, dict) else {"results": []}

    # -- memories ----------------------------------------------------------------------------------------------

    def listar_memorias(self, banco: str, *, tipo: str | None = None, documento_id: str | None = None) -> list[dict]:
        """Every valid memory unit of the bank (paged through), optionally of one type or one document."""
        params: dict[str, Any] = {"state": "valid", "limit": PAGINA}
        if tipo:
            params["type"] = tipo
        if documento_id:
            params["document_id"] = documento_id
        return self._todas(self._url(banco, "memories", "list"), params)

    def memoria(self, banco: str, memoria_id: str) -> dict[str, Any] | None:
        """One memory unit, or None when it does not exist (Hindsight's ids are UUIDs: anything else is «not found»
        here instead of its 400)."""
        try:
            UUID(memoria_id)
        except ValueError:
            return None
        return self._pedir("GET", self._url(banco, "memories", memoria_id), admite_404=True)

    def invalidar(self, banco: str, memoria_id: str, *, motivo: str) -> None:
        cuerpo = {"state": "invalidated", "reason": motivo}
        self._pedir("PATCH", self._url(banco, "memories", memoria_id), json=cuerpo)

    # -- documents ---------------------------------------------------------------------------------------------

    def listar_documentos(self, banco: str, *, etiquetas: Sequence[str] = ()) -> list[dict]:
        params: dict[str, Any] = {"limit": PAGINA}
        if etiquetas:
            params["tags"] = list(etiquetas)
            params["tags_match"] = "all_strict"
        return self._todas(self._url(banco, "documents"), params, admite_404=True)

    def documento(self, banco: str, documento_id: str) -> dict[str, Any] | None:
        return self._pedir("GET", self._url(banco, "documents", documento_id), admite_404=True)

    def borrar_documento(self, banco: str, documento_id: str) -> int:
        """Delete a document and every memory extracted from it; how many memories went (0 if it did not exist)."""
        datos = self._pedir("DELETE", self._url(banco, "documents", documento_id), admite_404=True)
        return int((datos or {}).get("memory_units_deleted") or 0)

    # -- helpers -----------------------------------------------------------------------------------------------

    def _todas(self, url: str, params: dict[str, Any], *, admite_404: bool = True) -> list[dict]:
        filas: list[dict] = []
        offset = 0
        while True:
            datos = self._pedir("GET", url, params={**params, "offset": offset}, admite_404=admite_404)
            if datos is None:
                return filas
            pagina = [f for f in datos.get("items") or [] if isinstance(f, dict)]
            filas.extend(pagina)
            offset += len(pagina)
            if not pagina or offset >= int(datos.get("total") or 0):
                return filas

    def cerrar(self) -> None:
        self._http.cerrar()


__all__ = ["TIPOS_HECHO", "TIPO_OBSERVACION", "ClienteHindsight", "HindsightError", "NoEncontradoError"]
