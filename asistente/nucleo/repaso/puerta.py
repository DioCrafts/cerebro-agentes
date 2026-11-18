"""The deterministic gate on what the model wrote: nothing reaches the user that the points do not support.

The model received the ranked points as data and returned a headline and one item per point. This module decides,
without any model, whether that text may be shown. Every rule says WHY it exists:

* **every point, once, by its id** — the model selects how to say things, never what exists: an unknown id is an
  invented point, a missing one hides something the code ranked, a repeated one inflates it;
* **every number and date appears in that point's facts** (:class:`Permitidos`: its Decimal amounts, its dates,
  its counts, and the figures already written in its code-made titles and reasons), in any of the usual Spanish
  spellings (``1.234,56 €``, ``31,2``, ``20/11/2026``, ``20 de noviembre``, «diez días»). A figure the facts do not
  hold is invented — and a sum of two points' amounts is too, since it is in neither;
* **a proposal id is one the point carries** — the buttons come from the points; a text may not announce a
  proposal nobody made;
* **nothing claims to be done** («ya lo he pagado», «he enviado», «queda presentado»): the pass never acts, and
  every proposal waits for the user's confirmation;
* **the order follows the code's priority within ±1 position** — the model may smooth the reading, not re-rank;
* **plain text of bounded length** — no links, addresses, markup or redaction markers (a link would be invented,
  markup could hide content).

:func:`revisar` returns a :class:`Veredicto` with the violations (Spanish, for the repair message and the trace)
and which parts passed, so the caller can keep the items that passed when a repair also fails.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from asistente.nucleo.repaso.priorizacion import Punto

MIN_TITULAR, MAX_TITULAR = 10, 220
MIN_TEXTO, MAX_TEXTO = 10, 420
MIN_POR_QUE, MAX_POR_QUE = 5, 300
# How far the model may move a point from the code's rank.
TOLERANCIA_ORDEN = 1

OrigenTexto = Literal["modelo", "determinista"]
OrigenRedaccion = Literal["modelo", "mixto", "determinista"]
TipoViolacion = Literal[
    "estructura",
    "punto_desconocido",
    "punto_repetido",
    "punto_ausente",
    "cifra_inventada",
    "fecha_inventada",
    "propuesta_inventada",
    "afirma_hecho",
    "orden",
    "longitud",
    "formato",
]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemRedactado(_Modelo):
    punto_id: str
    texto: str
    por_que: str
    propuesta_id: str | None = None
    origen: OrigenTexto = "modelo"


class Redaccion(_Modelo):
    """The text of a digest: a headline and one item per point, in the order shown."""

    titular: str
    items: list[ItemRedactado]
    origen: OrigenRedaccion
    titular_origen: OrigenTexto = "modelo"


class Violacion(_Modelo):
    tipo: TipoViolacion
    punto_id: str | None = None
    # Spanish: what is wrong (sent back to the model in the repair).
    detalle: str = Field(max_length=500)


@dataclass
class Veredicto:
    violaciones: list[Violacion] = field(default_factory=list)
    titular_ok: bool = False
    # Items that passed their own checks, by point id.
    items_ok: dict[str, ItemRedactado] = field(default_factory=dict)
    # The model's order is acceptable (every point present once, within the tolerance).
    orden_ok: bool = False
    orden: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violaciones


# --------------------------------------------------------------------------- #
# Spanish numbers and dates
# --------------------------------------------------------------------------- #

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
# Spelled-out numbers the gate reads when a unit follows («diez días», «treinta euros»). «un/una» is left out:
# «una semana», «un aviso» are not figures.
PALABRAS_NUMERO: dict[str, int] = {
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "veintiuno": 21,
    "veintiun": 21,
    "veintidos": 22,
    "veintitres": 23,
    "veinticuatro": 24,
    "veinticinco": 25,
    "veintiseis": 26,
    "veintisiete": 27,
    "veintiocho": 28,
    "veintinueve": 29,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "sesenta": 60,
    "setenta": 70,
    "ochenta": 80,
    "noventa": 90,
    "cien": 100,
    "ciento": 100,
    "doscientos": 200,
    "trescientos": 300,
    "quinientos": 500,
    "mil": 1000,
}
_UNIDADES = r"(?:\s+(?:euros?|dias|semanas|meses|anos|veces|correos|mensajes|avisos|datos|pasos|horas)\b|\s*€)"
_PALABRAS = "|".join(sorted(PALABRAS_NUMERO, key=len, reverse=True))
# «treinta y un días»: after «y», «un/una/uno» is a figure.
_UNO = {"un": 1, "una": 1, "uno": 1}
_RE_PALABRAS = re.compile(r"\b(" + _PALABRAS + r")(?:\s+y\s+(" + _PALABRAS + r"|una|uno|un))?" + _UNIDADES)
_RE_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_RE_FECHA_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}|\d{2}))?\b")
_RE_FECHA_TEXTO = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")(?:\s+(?:de|del)\s+(\d{4}))?\b", re.IGNORECASE
)
_RE_NUMERO = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*(?![\w])")


def plano(texto: str) -> str:
    """Lower case without accents (for the word rules)."""
    descompuesto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def _norm(valor: Decimal) -> Decimal:
    return abs(valor).normalize()


def interpretaciones(token: str) -> set[Decimal]:
    """Every value a written number may mean in Spanish or English notation (``1.234`` is 1234 or 1.234)."""
    salida: set[Decimal] = set()
    try:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", token):
            salida.add(Decimal(token.replace(".", "").replace(",", ".")))
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
            salida.add(Decimal(token.replace(",", "")))
        if re.fullmatch(r"\d+,\d+", token):
            salida.add(Decimal(token.replace(",", ".")))
        if re.fullmatch(r"\d+\.\d+", token):
            salida.add(Decimal(token))
        if re.fullmatch(r"\d+", token):
            salida.add(Decimal(token))
    except InvalidOperation:
        return set()
    return {_norm(v) for v in salida}


@dataclass
class Leido:
    """The numbers and dates written in a text."""

    numeros: list[tuple[str, set[Decimal]]] = field(default_factory=list)
    # (literal, full date or None, (day, month))
    fechas: list[tuple[str, date | None, tuple[int, int]]] = field(default_factory=list)


def _fecha(dia: int, mes: int, anio: int | None) -> tuple[date | None, tuple[int, int]] | None:
    if not (1 <= mes <= 12 and 1 <= dia <= 31):
        return None
    if anio is not None and anio < 100:
        anio += 2000
    try:
        return (date(anio, mes, dia) if anio is not None else None), (dia, mes)
    except ValueError:
        return None


def leer(texto: str) -> Leido:
    """Dates first (and blanked out), then the remaining numbers, then spelled-out numbers before a unit."""
    leido = Leido()
    resto = texto

    def quitar(m: re.Match[str]) -> str:
        return " " * len(m.group(0))

    def fecha_iso(m: re.Match[str]) -> str:
        valor = _fecha(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if valor is None:
            return m.group(0)
        leido.fechas.append((m.group(0), *valor))
        return quitar(m)

    def fecha_texto(m: re.Match[str]) -> str:
        valor = _fecha(int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3)) if m.group(3) else None)
        if valor is None:
            return m.group(0)
        leido.fechas.append((m.group(0), *valor))
        return quitar(m)

    def fecha_num(m: re.Match[str]) -> str:
        # «31,20» is money, never a date: only «/», «-» and «.» separate the parts of a date, and a «.» date
        # needs its year (otherwise «1.5» could be read as 1 May).
        if "." in m.group(0) and not m.group(3):
            return m.group(0)
        valor = _fecha(int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        if valor is None:
            return m.group(0)
        leido.fechas.append((m.group(0), *valor))
        return quitar(m)

    resto = _RE_FECHA_ISO.sub(fecha_iso, resto)
    resto = _RE_FECHA_TEXTO.sub(fecha_texto, resto)
    resto = _RE_FECHA_NUM.sub(fecha_num, resto)
    for m in _RE_NUMERO.finditer(resto):
        token = m.group(0).rstrip(".,")
        valores = interpretaciones(token)
        if valores:
            leido.numeros.append((token, valores))
    for m in _RE_PALABRAS.finditer(plano(resto)):
        segundo = m.group(2)
        valor = PALABRAS_NUMERO[m.group(1)] + ((PALABRAS_NUMERO.get(segundo) or _UNO[segundo]) if segundo else 0)
        leido.numeros.append((m.group(0), {_norm(Decimal(valor))}))
    return leido


@dataclass
class Permitidos:
    """The numbers and dates a text about some points may contain."""

    numeros: set[Decimal] = field(default_factory=set)
    fechas: set[date] = field(default_factory=set)

    def anadir_numero(self, valor: Decimal | int | None) -> None:
        if valor is not None:
            self.numeros.add(_norm(Decimal(valor)))

    def anadir_fecha(self, dia: date | None) -> None:
        if dia is None:
            return
        self.fechas.add(dia)
        # «el día 20», «en 2026»: the parts of an allowed date are allowed numbers.
        for parte in (dia.day, dia.month, dia.year):
            self.anadir_numero(parte)

    def anadir_texto(self, texto: str | None) -> None:
        """The figures already written (by code) in a title, a reason or a proposal's summary."""
        if not texto:
            return
        leido = leer(texto)
        for _literal, valores in leido.numeros:
            self.numeros |= valores
        for _literal, completa, (dia, mes) in leido.fechas:
            if completa is not None:
                self.anadir_fecha(completa)
            else:
                self.anadir_numero(dia)
                self.anadir_numero(mes)

    def unir(self, otro: Permitidos) -> Permitidos:
        return Permitidos(numeros=self.numeros | otro.numeros, fechas=self.fechas | otro.fechas)

    def admite_numero(self, valores: Iterable[Decimal]) -> bool:
        return any(v in self.numeros for v in valores)

    def admite_fecha(self, completa: date | None, dia_mes: tuple[int, int]) -> bool:
        if completa is not None:
            return completa in self.fechas
        return any((f.day, f.month) == dia_mes for f in self.fechas)


def permitidos_de(punto: Punto) -> Permitidos:
    """Everything one point's text may state: its signals' facts and the figures in its code-made texts."""
    p = Permitidos()
    for senal in punto.senales:
        h = senal.hechos
        p.anadir_numero(h.importe)
        if h.dias is not None:
            p.anadir_numero(abs(h.dias))
        for valor in h.numeros.values():
            p.anadir_numero(valor)
        for dia in (h.vence, h.fecha, *h.fechas.values()):
            p.anadir_fecha(dia)
        p.anadir_texto(senal.titulo)
        p.anadir_texto(senal.consecuencia)
        if senal.propuesta is not None:
            p.anadir_texto(senal.propuesta.etiqueta)
            p.anadir_texto(senal.propuesta.resumen)
    p.anadir_texto(punto.titulo)
    for motivo in punto.por_que:
        p.anadir_texto(motivo)
    return p


# --------------------------------------------------------------------------- #
# The rules on words
# --------------------------------------------------------------------------- #

# Participles of what the pass could falsely claim was done, as stems (any gender and number: «pagada», «hechos»).
_RAICES = (
    "enviad|pagad|presentad|contratad|cancelad|confirmad|aceptad|rechazad|solicitad|cambiad|firmad|tramitad|reservad|"
    "comprad|domiciliad|renovad|registrad|guardad|borrad|eliminad|ejecutad|realizad|completad|hech|resuelt|solucionad|"
    "arreglad|reclamad|pedid|transferid|abonad|liquidad|programad|devuelt|recurrid"
)
_PARTICIPIOS = r"(?:(?:" + _RAICES + r")(?:o|a|os|as)|dad(?:o|a|os|as)\s+de\s+(?:baja|alta))"
_RE_HECHO = re.compile(
    r"\b(?:ya\s+)?(?:lo\s+|la\s+|los\s+|las\s+|te\s+|se\s+lo\s+|se\s+la\s+)?"
    r"(?:he|hemos|se\s+ha|se\s+han|ha\s+quedado|han\s+quedado)\s+(?:" + _PARTICIPIOS + r")\b"
    r"|\bya\s+(?:esta|estan|quedo|queda|quedan)\s+(?:" + _PARTICIPIOS + r"|list[oa]s?)\b"
    r"|\b(?:queda|quedan|quedo)\s+(?:" + _PARTICIPIOS + r")\b"
    r"|\bacabo\s+de\s+(?:enviar|pagar|presentar|contratar|cancelar|confirmar|aceptar|solicitar|cambiar|firmar|"
    r"tramitar|reservar|comprar|renovar|registrar|guardar|borrar|ejecutar|hacer|resolver|reclamar)\b"
    r"|\bhe\s+procedido\s+a\b"
)
_RE_NEGADO = re.compile(r"\b(?:no|nunca|nada|todavia\s+no|aun\s+no)\s+(?:\w+\s+){0,2}$")
_RE_FORMATO = re.compile(r"https?://|www\.|\w@\w|\*\*|\[|\]|<|>|`|#|REDACTED", re.IGNORECASE)


def afirma_hecho(texto: str) -> str | None:
    """The phrase that claims something was done (not negated), or None."""
    p = plano(texto)
    for m in _RE_HECHO.finditer(p):
        if _RE_NEGADO.search(p[max(0, m.start() - 24) : m.start()]):
            continue
        return m.group(0)
    return None


def _formato(texto: str) -> str | None:
    m = _RE_FORMATO.search(texto)
    return m.group(0) if m else None


def _cifras(texto: str, permitidos: Permitidos) -> list[Violacion]:
    leido = leer(texto)
    salida: list[Violacion] = []
    for literal, valores in leido.numeros:
        if not permitidos.admite_numero(valores):
            salida.append(Violacion(tipo="cifra_inventada", detalle=f"La cifra «{literal}» no está en los datos."))
    for literal, completa, dia_mes in leido.fechas:
        if not permitidos.admite_fecha(completa, dia_mes):
            salida.append(Violacion(tipo="fecha_inventada", detalle=f"La fecha «{literal}» no está en los datos."))
    return salida


def revisar_texto(texto: str, permitidos: Permitidos, *, campo: str, minimo: int, maximo: int) -> list[Violacion]:
    """The rules every written field follows (length, format, figures, «done» claims)."""
    salida: list[Violacion] = []
    limpio = " ".join(str(texto or "").split())
    if not minimo <= len(limpio) <= maximo:
        salida.append(Violacion(tipo="longitud", detalle=f"«{campo}» debe tener entre {minimo} y {maximo} caracteres."))
    marca = _formato(limpio)
    if marca:
        salida.append(
            Violacion(tipo="formato", detalle=f"«{campo}» lleva «{marca}»: solo texto, sin enlaces ni formato.")
        )
    hecho = afirma_hecho(limpio)
    if hecho:
        salida.append(
            Violacion(
                tipo="afirma_hecho",
                detalle=f"«{campo}» dice «{hecho}»: no se ha hecho nada; las propuestas esperan la confirmación.",
            )
        )
    for violacion in _cifras(limpio, permitidos):
        salida.append(violacion.model_copy(update={"detalle": f"«{campo}»: {violacion.detalle}"}))
    return salida


# --------------------------------------------------------------------------- #
# The whole output
# --------------------------------------------------------------------------- #


def permitidos_titular(puntos: Sequence[Punto], *, extra: Permitidos | None = None, restantes: int = 0) -> Permitidos:
    """The headline may state any point's figures, how many points there are, and the period's dates."""
    total = Permitidos()
    for punto in puntos:
        total = total.unir(permitidos_de(punto))
    total.anadir_numero(len(puntos))
    total.anadir_numero(sum(1 for p in puntos if p.urgente) or None)
    if restantes:
        total.anadir_numero(restantes)
    return total.unir(extra) if extra is not None else total


def revisar(
    salida: Mapping[str, Any] | None,
    puntos: Sequence[Punto],
    *,
    extra_titular: Permitidos | None = None,
    restantes: int = 0,
) -> Veredicto:
    """Check a model output against the points it was given (see the module docstring for each rule)."""
    veredicto = Veredicto()
    if not isinstance(salida, Mapping) or not isinstance(salida.get("puntos"), list):
        veredicto.violaciones.append(
            Violacion(tipo="estructura", detalle="La respuesta no tiene «titular» y «puntos».")
        )
        return veredicto
    por_id = {p.id: p for p in puntos}
    titular = salida.get("titular")
    if not isinstance(titular, str):
        veredicto.violaciones.append(Violacion(tipo="estructura", detalle="Falta el «titular»."))
    else:
        del_titular = revisar_texto(
            titular,
            permitidos_titular(puntos, extra=extra_titular, restantes=restantes),
            campo="titular",
            minimo=MIN_TITULAR,
            maximo=MAX_TITULAR,
        )
        veredicto.violaciones.extend(del_titular)
        veredicto.titular_ok = not del_titular

    vistos: list[str] = []
    for item in salida["puntos"]:
        if not isinstance(item, Mapping):
            veredicto.violaciones.append(Violacion(tipo="estructura", detalle="Un punto no es un objeto."))
            continue
        punto_id = item.get("punto_id")
        if not isinstance(punto_id, str) or punto_id not in por_id:
            veredicto.violaciones.append(
                Violacion(tipo="punto_desconocido", detalle=f"El punto «{punto_id}» no existe en los datos.")
            )
            continue
        if punto_id in vistos:
            veredicto.violaciones.append(
                Violacion(tipo="punto_repetido", punto_id=punto_id, detalle=f"El punto «{punto_id}» está repetido.")
            )
            continue
        vistos.append(punto_id)
        punto = por_id[punto_id]
        permitidos = permitidos_de(punto)
        problemas: list[Violacion] = []
        texto, por_que, propuesta_id = item.get("texto"), item.get("por_que"), item.get("propuesta_id")
        if not isinstance(texto, str) or not isinstance(por_que, str):
            problemas.append(Violacion(tipo="estructura", detalle="Faltan «texto» o «por_que»."))
        else:
            problemas += revisar_texto(texto, permitidos, campo="texto", minimo=MIN_TEXTO, maximo=MAX_TEXTO)
            problemas += revisar_texto(por_que, permitidos, campo="por_que", minimo=MIN_POR_QUE, maximo=MAX_POR_QUE)
        if propuesta_id is not None and propuesta_id not in {p.id for p in punto.propuestas}:
            problemas.append(
                Violacion(
                    tipo="propuesta_inventada",
                    detalle=f"La propuesta «{propuesta_id}» no es una de las de este punto.",
                )
            )
        for problema in problemas:
            veredicto.violaciones.append(
                problema.model_copy(update={"punto_id": punto_id, "detalle": f"[{punto_id}] {problema.detalle}"})
            )
        if not problemas:
            assert isinstance(texto, str) and isinstance(por_que, str)
            veredicto.items_ok[punto_id] = ItemRedactado(
                punto_id=punto_id,
                texto=" ".join(texto.split()),
                por_que=" ".join(por_que.split()),
                propuesta_id=propuesta_id,
            )

    ausentes = [p.id for p in puntos if p.id not in vistos]
    for punto_id in ausentes:
        veredicto.violaciones.append(
            Violacion(tipo="punto_ausente", punto_id=punto_id, detalle=f"Falta el punto «{punto_id}».")
        )
    veredicto.orden = vistos
    fuera = [pid for posicion, pid in enumerate(vistos) if abs(posicion - (por_id[pid].rango - 1)) > TOLERANCIA_ORDEN]
    if fuera:
        veredicto.violaciones.append(
            Violacion(
                tipo="orden",
                detalle="El orden se aleja de la prioridad calculada (máximo un puesto): "
                + ", ".join(fuera)
                + ". Mantén el orden de los datos.",
            )
        )
    veredicto.orden_ok = not ausentes and not fuera and len(vistos) == len(puntos)
    return veredicto


__all__ = [
    "MAX_TEXTO",
    "MAX_TITULAR",
    "TOLERANCIA_ORDEN",
    "ItemRedactado",
    "Leido",
    "Permitidos",
    "Redaccion",
    "Veredicto",
    "Violacion",
    "afirma_hecho",
    "interpretaciones",
    "leer",
    "permitidos_de",
    "permitidos_titular",
    "revisar",
    "revisar_texto",
]
