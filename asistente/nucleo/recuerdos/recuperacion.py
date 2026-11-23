"""Hybrid retrieval of what is relevant to one question, over ONE owner's memory.

Why not SQLite FTS5: the index would be global (every user's postings in one structure), so a query would scan
other users' entries before the owner filter and the BM25 statistics (IDF) would mix everybody's words — a weak but
real cross-user signal; erased words would linger in its segments until a merge; and the schema would depend on
how SQLite was built. One owner's memory is small and bounded (``MEMORIA_MAX_RECUERDOS`` items plus a summary per
conversation), so the lexical index is a BM25 computed here over the owner's rows only, from the normalised terms
stored with each row (``terminos``): per-user statistics, nothing shared, nothing left behind.

The score of a document for a query (each part in 0..1, so the relevance handed to the brain is calibrated):

* **lexical** — BM25 (k1=1.2, b=0.75) per query CONCEPT (a word and its synonyms), divided by the score a
  document matching every concept once at average length would get: the IDF-weighted share of the question the
  document covers (a concept the owner's memory never mentions counts as half a rare one). Spanish normalisation:
  lower case, no accents, stopwords out, a light stemmer (plural, gender, infinitive and gerund), question and time
  words at 0.3, and a small household synonym map (piso ↔ vivienda, nómina ↔ sueldo…) whose expansions count at
  0.6. A document must share at least one TOPIC word: sharing only «días» or «mañana» is not relevance;
* **semantic** (optional) — cosine of the project's embeddings, when ``MEMORIA_EMBEDDINGS`` is on and the row
  has a vector; blended 55/45 with the lexical part. Never required;
* **priors** — type (preferencia/dato 1.0, contexto 0.85, resumen 0.8), recency (half-life by type, never below
  60 %), a small bonus for what the user corrected or the brain used before;
* **explicit references** — «lo que te dije de la hipoteca», «¿qué sabes de mí?», «la última vez»: the items the
  query points at are always included;
* **MMR** (λ=0.75) over the term sets, so eight near-identical items never crowd out the rest.

Pure functions: the store hands the documents in, nothing here touches the database.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
import math
import re
from typing import Literal, Protocol
import unicodedata

TipoDocumento = Literal["dato", "preferencia", "contexto", "resumen"]

K1 = 1.2
B = 0.75
PESO_SINONIMO = 0.6
PESO_PREGUNTA = 0.3
UMBRAL = 0.12
ABSENTE = 0.5
LAMBDA_MMR = 0.75
PRIOR: dict[str, float] = {"preferencia": 1.0, "dato": 1.0, "contexto": 0.85, "resumen": 0.8}
VIDA_MEDIA_DIAS: dict[str, float] = {"preferencia": 540.0, "dato": 365.0, "contexto": 120.0, "resumen": 90.0}

# --------------------------------------------------------------------------------------------------------------
# Spanish text analysis
# --------------------------------------------------------------------------------------------------------------

_STOPWORDS = frozenset(
    """
    a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque bien cada casi como con contra cual
    cuales cuando de del desde donde dos e el ella ellas ello ellos en entre era eran es esa esas ese eso esos esta
    estaba estado estan estar estas este esto estos fue fueron ha habia han has hasta hay he la las le les lo los mas
    me mi mia mias mio mios mis mucho muy nada ni no nos nosotros o os otra otras otro otros para pero poco por porque
    que quien quienes se sea ser si sido siempre sin sobre solo son su sus tal tambien te tenia ti tu tus u un una
    uno unos usted ustedes vosotros y ya yo the of and to in is it
    """.split()
)
# Words that make a question but carry no topic: kept, at a low weight.
_DE_PREGUNTA = frozenset(
    """
    cuanto cuanta cuantos cuantas puedo podria podrias quiero querria saber hacer tengo tiene tienes tenemos deberia
    deberias conviene convendria mejor ahora hoy dime dimelo explica explicame ayuda ayudame gracias favor recuerdas
    recuerda acuerdas dije dijiste conte comente hablamos hablado sabes sobre pregunta preguntar vale bueno hola oye
    mira necesito necesitaria cosa cosas algo eso esto tema asunto quedo quedamos quedaste paso interesa
    interesaria compensa compensaria merece vale pena
    manana tarde noche dia dias semana semanas mes meses ano anos hoy ayer tiempo vez veces hora horas momento rato
    """.split()
)

# Household synonyms (stems after :func:`raiz`): each group expands to the others at PESO_SINONIMO.
_GRUPOS_SINONIMOS: tuple[tuple[str, ...], ...] = (
    ("piso", "vivienda", "casa", "hogar", "domicilio"),
    ("hipotec", "prestamo hipotecari"),
    ("coch", "vehicul", "auto", "turism", "moto"),
    ("hij", "nin", "niet", "crio", "pequen", "chaval"),
    ("mujer", "marid", "espos", "parej", "conyug", "novi"),
    ("luz", "electricidad", "electric", "factura de la luz", "comercializador"),
    ("nomin", "sueld", "salari", "cobr"),
    ("alquiler", "alquil", "arrend", "inquilin", "casero"),
    ("trabaj", "emple", "curro", "empres"),
    ("autonom", "freelance", "actividad economic"),
    ("banc", "entidad", "cuenta"),
    ("segur", "poliz", "aseguradora"),
    ("gas", "calefaccion", "caldera"),
    ("agua", "factura del agua"),
    ("movil", "telefon", "fibra", "internet", "tarifa del movil"),
    ("perr", "gat", "mascot"),
    ("renta", "irpf", "declaracion", "hacienda"),
    ("ahorr", "inversion", "invert", "fond", "deposit"),
    ("pension", "jubilacion", "jubil", "plan de pension"),
    ("viaj", "vacacion"),
    ("suscripcion", "netflix", "spotify", "plataforma"),
    ("ciudad", "viv", "resid", "barri"),
    ("respuest", "respond", "contest", "explicacion"),
    ("cort", "breve", "resumid", "concis"),
    ("comunidad", "comunidad de vecin", "vecin"),
    ("colegi", "escuela", "guarderi", "estudi"),
)


def _plano(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sin)


def raiz(palabra: str) -> str:
    """A light Spanish stemmer (plural and gender; Savoy's «light» idea): «hipotecas» → «hipotec»,
    «hija» → «hij», «coches» → «coch», «facturas» → «factur». Numbers and short words are kept."""
    if palabra.isdigit() or len(palabra) <= 3:
        return palabra
    # Verb forms that would not meet their nouns otherwise: «alquilando» / «alquilar» → «alquil» (= «alquiler»),
    # «revisar» → «revis» (= «revisó»), «cambiar» → «cambi» (= «cambio»).
    if len(palabra) >= 7 and palabra.endswith(("ando", "iendo")):
        return palabra[: -5 if palabra.endswith("iendo") else -4]
    if len(palabra) >= 6 and palabra.endswith(("ar", "er", "ir")):
        return palabra[:-2]
    if palabra.endswith("ces") and len(palabra) > 4:
        palabra = palabra[:-3] + "z"
    elif palabra.endswith("es") and len(palabra) > 5 and palabra[-3] not in "aeiou":
        palabra = palabra[:-2]
    elif palabra.endswith("s") and len(palabra) > 4:
        palabra = palabra[:-1]
    if len(palabra) >= 4 and palabra[-1] in "aeo":
        palabra = palabra[:-1]
    return palabra


def analizar(texto: str) -> list[str]:
    """The index terms of ``texto``: normalised, without stopwords, stemmed (repeats kept: term frequency)."""
    return [raiz(p) for p in _plano(texto).split() if p not in _STOPWORDS and (len(p) > 1 or p.isdigit())]


_SINONIMOS: dict[str, set[str]] = {}
for _grupo in _GRUPOS_SINONIMOS:
    _formas = [tuple(analizar(termino)) for termino in _grupo]
    for _forma in _formas:
        for _t in _forma:
            _SINONIMOS.setdefault(_t, set()).update(t for otra in _formas if otra != _forma for t in otra)
for _t, _otros in _SINONIMOS.items():
    _otros.discard(_t)


@dataclass(frozen=True)
class Ranura:
    """One concept of the query: its term (factor 1.0) and its synonyms (factor PESO_SINONIMO), with the
    concept's weight (1.0 for a topic word, PESO_PREGUNTA for a question word)."""

    peso: float
    terminos: dict[str, float]


def ranuras_de_consulta(consulta: str) -> list[Ranura]:
    """The concepts of ``consulta`` (repeated words count once)."""
    vistas: dict[str, Ranura] = {}
    for palabra in _plano(consulta).split():
        if palabra in _STOPWORDS:
            continue
        termino = raiz(palabra)
        if termino in vistas:
            continue
        if palabra in _DE_PREGUNTA:
            vistas[termino] = Ranura(PESO_PREGUNTA, {termino: 1.0})
            continue
        terminos = {termino: 1.0}
        for sinonimo in _SINONIMOS.get(termino, ()):
            terminos.setdefault(sinonimo, PESO_SINONIMO)
        vistas[termino] = Ranura(1.0, terminos)
    return list(vistas.values())


def terminos_de_consulta(consulta: str) -> dict[str, float]:
    """Every query term with its best weight (concept weight × synonym factor)."""
    pesos: dict[str, float] = {}
    for ranura in ranuras_de_consulta(consulta):
        for termino, factor in ranura.terminos.items():
            pesos[termino] = max(pesos.get(termino, 0.0), ranura.peso * factor)
    return pesos


# --------------------------------------------------------------------------------------------------------------
# Explicit references to the memory
# --------------------------------------------------------------------------------------------------------------

Referencia = Literal["tema", "general", "ultima"]

_REF_TEMA = re.compile(
    r"\b(?:lo que te (?:dije|conte|comente|explique|pregunte)|que te (?:dije|conte|comente|pregunte)|"
    r"te (?:dije|conte|comente|hable|pregunte)|(?:te )?acuerdas|recuerdas|como te dije|lo de)\b"
    r"(?:\s+(?:de|del|sobre|acerca de|con|que|lo de|la|el|los|las|mi|mis))*\s+(?P<tema>.+)$"
)
_REF_GENERAL = re.compile(
    r"\b(?:que (?:sabes|recuerdas) (?:de|sobre) mi|que sabes de mi|lo que sabes de mi|que recuerdas de mi|"
    r"que te he contado|que has guardado|que tienes (?:guardado|apuntado) (?:de|sobre) mi)\b"
)
_REF_ULTIMA = re.compile(
    r"\b(?:la ultima vez|la otra vez|el otro dia|la conversacion anterior|la ultima conversacion|"
    r"lo que hablamos|de lo que hablamos|seguimos con|retomando|como quedamos)\b"
)


# Words of a reference that name a time, not a topic («lo de la otra vez»).
_TIEMPO = frozenset(
    raiz(p)
    for p in "vez veces dia dias otra otro ultima ultimo anterior antes semana mes ano conversacion charla rato ayer".split()
)


@dataclass(frozen=True)
class ReferenciaExplicita:
    tipo: Referencia
    temas: frozenset[str] = frozenset()


def detectar_referencia(consulta: str) -> ReferenciaExplicita | None:
    """Whether the question points at the memory itself, and at what."""
    plano = " ".join(_plano(consulta).split())
    if _REF_GENERAL.search(plano):
        return ReferenciaExplicita("general")
    tema = _REF_TEMA.search(plano)
    if tema is not None:
        vacias = {raiz(p) for p in _DE_PREGUNTA} | _TIEMPO
        temas = frozenset(t for t in analizar(tema.group("tema")) if t not in vacias)
        if temas:
            return ReferenciaExplicita("tema", temas)
    if _REF_ULTIMA.search(plano):
        return ReferenciaExplicita("ultima")
    return None


# --------------------------------------------------------------------------------------------------------------
# Documents and ranking
# --------------------------------------------------------------------------------------------------------------


class Incrustador(Protocol):
    """The part of a LangChain ``Embeddings`` the memory uses (the project's factory returns one)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass
class Documento:
    """One retrievable item of an owner's memory (a remembered item, a conversation summary, or a recent user
    message of a conversation not summarised yet)."""

    id: str
    tipo: TipoDocumento
    terminos: list[str]
    fecha: date
    conversacion_id: str = ""
    corregido: bool = False
    usos: int = 0
    vector: list[float] | None = None
    # True for the summary-like items of conversations (``resumen``) and for unsummarised messages.
    es_conversacion: bool = False
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Puntuado:
    documento: Documento
    relevancia: float
    lexica: float
    semantica: float | None
    forzado: bool


def _coseno(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


def _recencia(doc: Documento, hoy: date) -> float:
    edad = max(0, (hoy - doc.fecha).days)
    vida = VIDA_MEDIA_DIAS.get(doc.tipo, 180.0)
    return 0.6 + 0.4 * math.pow(0.5, edad / vida)


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def puntuar(
    documentos: Sequence[Documento],
    consulta: str,
    *,
    hoy: date,
    vector_consulta: Sequence[float] | None = None,
) -> list[Puntuado]:
    """Every document with its calibrated relevance for ``consulta`` (unsorted, unfiltered)."""
    if not documentos:
        return []
    ranuras = ranuras_de_consulta(consulta)
    referencia = detectar_referencia(consulta)
    n = len(documentos)
    frecuencias = [Counter(d.terminos) for d in documentos]
    media = sum(len(d.terminos) for d in documentos) / n or 1.0
    # Document frequency per CONCEPT (a document with a synonym has the concept), hence its IDF.
    idf = []
    techo = []
    for ranura in ranuras:
        df = sum(1 for f in frecuencias if any(t in f for t in ranura.terminos))
        idf.append(math.log(1 + (n - df + 0.5) / (df + 0.5)))
        # A concept the owner's memory never mentions cannot be matched by any document: it counts in the
        # denominator only as half a rare term, so «¿me conviene cambiar la hipoteca a tipo fijo?» still finds
        # what the memory holds about the mortgage.
        techo.append(idf[-1] if df else ABSENTE * math.log(1 + (n - 0.5) / 1.5))
    maximo = sum(r.peso * t for r, t in zip(ranuras, techo, strict=True))
    ultima_conversacion = max((d for d in documentos if d.es_conversacion), key=lambda d: (d.fecha, d.id), default=None)

    salida: list[Puntuado] = []
    for doc, frecuencia in zip(documentos, frecuencias, strict=True):
        norma = K1 * (1 - B + B * (len(doc.terminos) or 1) / media)
        bm25 = 0.0
        con_tema = False
        for ranura, idf_ranura in zip(ranuras, idf, strict=True):
            mejor = 0.0
            for termino, factor in ranura.terminos.items():
                tf = frecuencia.get(termino, 0)
                if tf:
                    # Saturating term frequency, 1.0 for one occurrence at average length (capped at 1).
                    mejor = max(mejor, factor * min(1.0, tf * (K1 + 1) / (tf + norma) / ((K1 + 1) / (1 + K1))))
            bm25 += ranura.peso * idf_ranura * mejor
            con_tema = con_tema or (mejor > 0 and ranura.peso >= 1.0)
        if not con_tema:
            # Sharing only question or time words («¿cuántos días…?», «mañana») is not being about the same thing.
            bm25 = 0.0
        lexica = min(1.0, bm25 / maximo) if maximo > 0 else 0.0
        semantica: float | None = None
        base = lexica
        if vector_consulta is not None and doc.vector is not None:
            coseno = _coseno(vector_consulta, doc.vector)
            semantica = max(0.0, min(1.0, (coseno - 0.3) / 0.5))
            base = 0.55 * lexica + 0.45 * semantica
        forzado = False
        if referencia is not None:
            if referencia.tipo == "tema" and referencia.temas & set(doc.terminos):
                base, forzado = max(base, 0.5), True
            elif referencia.tipo == "general" and doc.tipo in ("dato", "preferencia"):
                base, forzado = max(base, 0.4), True
            elif referencia.tipo == "ultima" and doc is ultima_conversacion:
                base, forzado = max(base, 0.6), True
        bonus = 1.0 + (0.05 if doc.corregido else 0.0) + min(0.05, 0.01 * doc.usos)
        relevancia = base * PRIOR.get(doc.tipo, 0.8) * _recencia(doc, hoy) * bonus
        salida.append(Puntuado(doc, round(max(0.0, min(1.0, relevancia)), 4), lexica, semantica, forzado))
    return salida


def seleccionar(
    puntuados: Sequence[Puntuado], *, limite: int, umbral: float = UMBRAL, lambda_mmr: float = LAMBDA_MMR
) -> list[Puntuado]:
    """The ``limite`` best (forced references first), above ``umbral``, diversified with MMR."""
    candidatos = sorted(
        (p for p in puntuados if p.forzado or p.relevancia >= umbral),
        key=lambda p: (not p.forzado, -p.relevancia, p.documento.id),
    )
    elegidos: list[Puntuado] = []
    conjuntos = {id(p): set(p.documento.terminos) for p in candidatos}

    def similitud(a: Puntuado, b: Puntuado) -> float:
        va, vb = a.documento.vector, b.documento.vector
        if va is not None and vb is not None:
            return max(0.0, _coseno(va, vb))
        return _jaccard(conjuntos[id(a)], conjuntos[id(b)])

    forzados = [p for p in candidatos if p.forzado]
    resto = [p for p in candidatos if not p.forzado]
    for grupo in (forzados, resto):
        pendientes = list(grupo)
        while pendientes and len(elegidos) < limite:
            mejor = max(
                pendientes,
                key=lambda p: (
                    lambda_mmr * p.relevancia - (1 - lambda_mmr) * max((similitud(p, e) for e in elegidos), default=0.0)
                ),
            )
            elegidos.append(mejor)
            pendientes.remove(mejor)
    return elegidos


def recuperar(
    documentos: Sequence[Documento],
    consulta: str,
    *,
    limite: int,
    hoy: date,
    vector_consulta: Sequence[float] | None = None,
) -> list[Puntuado]:
    """Score and select in one go (what the service calls)."""
    return seleccionar(puntuar(documentos, consulta, hoy=hoy, vector_consulta=vector_consulta), limite=limite)


# --------------------------------------------------------------------------------------------------------------
# Retrieval quality (the offline benchmark in tests/recuerdos uses these)
# --------------------------------------------------------------------------------------------------------------


def recall_en(resultados: Sequence[str], relevantes: Iterable[str], k: int) -> float:
    esperados = set(relevantes)
    if not esperados:
        return 1.0
    return len(esperados & set(resultados[:k])) / len(esperados)


def rango_reciproco(resultados: Sequence[str], relevantes: Iterable[str]) -> float:
    esperados = set(relevantes)
    for posicion, ident in enumerate(resultados, start=1):
        if ident in esperados:
            return 1.0 / posicion
    return 0.0


def vector_de_bytes(datos: bytes | None) -> list[float] | None:
    """A stored embedding (float32, little endian) back to a list; None when absent or malformed."""
    if not datos:
        return None
    import array

    valores = array.array("f")
    try:
        valores.frombytes(datos)
    except ValueError:
        return None
    return list(valores)


def vector_a_bytes(vector: Sequence[float]) -> bytes:
    import array

    return array.array("f", vector).tobytes()


__all__ = [
    "LAMBDA_MMR",
    "PRIOR",
    "UMBRAL",
    "Documento",
    "Incrustador",
    "Puntuado",
    "Ranura",
    "ReferenciaExplicita",
    "analizar",
    "detectar_referencia",
    "puntuar",
    "raiz",
    "rango_reciproco",
    "recall_en",
    "ranuras_de_consulta",
    "recuperar",
    "seleccionar",
    "terminos_de_consulta",
    "vector_a_bytes",
    "vector_de_bytes",
]
