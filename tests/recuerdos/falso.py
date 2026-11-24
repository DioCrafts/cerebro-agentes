"""An in-memory Hindsight for the tests: the REST endpoints the memory uses, behind an ``httpx.MockTransport``.

The real :class:`~asistente.nucleo.recuerdos.cliente.ClienteHindsight` talks to it, so the client's requests are
exercised too. «Extraction» is deterministic: each retained item becomes one ``world`` fact whose text is the
content itself (Hindsight's model would rewrite it; the tests only need a fact with its source chunk). Recall ranks
the bank's valid facts by shared words with the query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import httpx

from asistente.nucleo.recuerdos.cliente import ClienteHindsight
from asistente.plataforma.http import Cliente

BASE = "http://hindsight.test"


@dataclass
class Banco:
    perfil: dict[str, Any]
    config: dict[str, Any] = field(default_factory=dict)
    documentos: dict[str, dict[str, Any]] = field(default_factory=dict)
    memorias: dict[str, dict[str, Any]] = field(default_factory=dict)
    trozos: dict[str, str] = field(default_factory=dict)


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"\w+", texto.lower()) if len(p) > 3}


class HindsightFalso:
    def __init__(self) -> None:
        self.bancos: dict[str, Banco] = {}
        self.peticiones: list[tuple[str, str, Any]] = []
        self.caido = False

    # -- the client the service gets ---------------------------------------------------------------------------

    def cliente(self) -> ClienteHindsight:
        http = Cliente(transport=httpx.MockTransport(self._atender), dormir=lambda _s: None, reintentos=0)
        return ClienteHindsight(BASE, http=http)

    # -- helpers for assertions --------------------------------------------------------------------------------

    def textos(self, banco: str) -> list[str]:
        b = self.bancos.get(banco)
        return [d["original_text"] for d in b.documentos.values()] if b else []

    def hechos(self, banco: str) -> list[dict[str, Any]]:
        b = self.bancos.get(banco)
        return [m for m in b.memorias.values() if m["state"] == "valid"] if b else []

    # -- the REST API ------------------------------------------------------------------------------------------

    def _atender(self, peticion: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("Hindsight caído", request=peticion)
        cuerpo = json.loads(peticion.content) if peticion.content else None
        ruta = [unquote(p) for p in peticion.url.path.split("/") if p]
        self.peticiones.append((peticion.method, "/".join(ruta), cuerpo))
        if ruta[:3] != ["v1", "default", "banks"] or len(ruta) < 4:
            return httpx.Response(404)
        nombre, resto, metodo = ruta[3], ruta[4:], peticion.method
        if not resto:
            if metodo == "PUT":
                banco = self.bancos.setdefault(nombre, Banco(perfil={}))
                banco.perfil.update(cuerpo or {})
                return httpx.Response(200, json={"bank_id": nombre})
            if metodo == "DELETE":  # like Hindsight: the same answer whether the bank existed or not
                self.bancos.pop(nombre, None)
                return httpx.Response(200, json={"success": True, "deleted_count": 0})
        banco = self.bancos.get(nombre)
        if banco is None:
            return httpx.Response(404, json={"detail": "bank not found"})
        params = peticion.url.params
        if resto == ["config"] and metodo == "PATCH":
            banco.config.update((cuerpo or {}).get("updates") or {})
            return httpx.Response(200, json={"config": banco.config})
        if resto == ["memories"] and metodo == "POST":
            return self._retener(banco, cuerpo or {})
        if resto == ["memories", "recall"] and metodo == "POST":
            return self._recordar(banco, cuerpo or {})
        if resto == ["memories", "list"] and metodo == "GET":
            filas = [
                m
                for m in banco.memorias.values()
                if m["state"] == (params.get("state") or m["state"])
                and (not params.get("type") or m["fact_type"] == params["type"])
                and (not params.get("document_id") or m["document_id"] == params["document_id"])
            ]
            return self._pagina(filas, params)
        if len(resto) == 2 and resto[0] == "memories":
            if not re.fullmatch(r"[0-9a-f-]{32,36}", resto[1]):
                return httpx.Response(400, json={"detail": "Invalid memory_id"})
            memoria = banco.memorias.get(resto[1])
            if memoria is None:
                return httpx.Response(404)
            if metodo == "GET":
                return httpx.Response(200, json=memoria)
            if metodo == "PATCH":
                if memoria["fact_type"] == "observation":
                    return httpx.Response(400)
                memoria.update({k: v for k, v in (cuerpo or {}).items() if k in ("state", "text")})
                return httpx.Response(200, json=memoria)
        if resto == ["documents"] and metodo == "GET":
            etiquetas = params.get_list("tags")
            filas = [d for d in banco.documentos.values() if set(etiquetas) <= set(d["tags"])]
            return self._pagina([{k: v for k, v in d.items() if k != "original_text"} for d in filas], params)
        if len(resto) == 2 and resto[0] == "documents":
            documento = banco.documentos.get(resto[1])
            if documento is None:
                return httpx.Response(404)
            if metodo == "GET":
                return httpx.Response(200, json=documento)
            if metodo == "DELETE":
                del banco.documentos[resto[1]]
                borradas = [i for i, m in banco.memorias.items() if m["document_id"] == resto[1]]
                for i in borradas:
                    del banco.memorias[i]
                return httpx.Response(200, json={"success": True, "memory_units_deleted": len(borradas)})
        return httpx.Response(404)

    def _retener(self, banco: Banco, cuerpo: dict[str, Any]) -> httpx.Response:
        for item in cuerpo.get("items") or []:
            documento_id = item.get("document_id") or uuid4().hex
            ahora = datetime.now(UTC).isoformat()
            banco.documentos[documento_id] = {
                "id": documento_id,
                "original_text": item["content"],
                "created_at": item.get("timestamp") or ahora,
                "updated_at": ahora,
                "tags": item.get("tags") or [],
                "document_metadata": item.get("metadata") or {},
                "memory_unit_count": 1,
            }
            trozo = uuid4().hex
            banco.trozos[trozo] = item["content"]
            memoria_id = str(uuid4())
            banco.memorias[memoria_id] = {
                "id": memoria_id,
                "text": item["content"],
                "fact_type": "world",
                "document_id": documento_id,
                "chunk_id": trozo,
                "mentioned_at": item.get("timestamp"),
                "tags": item.get("tags") or [],
                "metadata": item.get("metadata") or {},
                "state": "valid",
            }
        return httpx.Response(200, json={"success": True, "items_count": len(cuerpo.get("items") or [])})

    def _recordar(self, banco: Banco, cuerpo: dict[str, Any]) -> httpx.Response:
        pregunta = _palabras(cuerpo.get("query") or "")
        validas = [m for m in banco.memorias.values() if m["state"] == "valid"]
        puntuadas = sorted(
            ((len(pregunta & _palabras(m["text"])), m) for m in validas), key=lambda par: par[0], reverse=True
        )
        resultados = [dict(m, type=m["fact_type"]) for puntos, m in puntuadas if puntos > 0]
        trozos = {
            m["chunk_id"]: {"id": m["chunk_id"], "text": banco.trozos[m["chunk_id"]], "chunk_index": 0}
            for m in resultados
            if m.get("chunk_id") in banco.trozos
        }
        return httpx.Response(200, json={"results": resultados, "chunks": trozos})

    @staticmethod
    def _pagina(filas: list[dict[str, Any]], params: httpx.QueryParams) -> httpx.Response:
        offset, limite = int(params.get("offset") or 0), int(params.get("limit") or 100)
        return httpx.Response(
            200, json={"items": filas[offset : offset + limite], "total": len(filas), "limit": limite, "offset": offset}
        )
