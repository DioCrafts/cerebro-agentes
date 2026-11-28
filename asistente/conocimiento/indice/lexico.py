"""The lexical side of the index speaks stems, not words.

FTS5 ships no Spanish stemmer, so «deducción» never matched «deducciones» nor «arrendamiento» «arrendamientos».
The index stores, next to each fragment's text, the same text reduced to Snowball stems (:func:`lematizar`), and
FTS5 indexes that column. A query goes through the same reduction (:func:`terminos`), so both sides meet.

Numbers and identifiers pass unchanged: «68», «303» and «2006» must match exactly.
"""

from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

_PALABRA = re.compile(r"\w+", re.UNICODE)

# Palabras vacías del español (artículos, preposiciones, pronombres, interrogativos y auxiliares). En una consulta
# con OR casan con casi todos los fragmentos y ensucian el ranking léxico; se quitan, salvo que no quede nada.
PALABRAS_VACIAS = frozenset(
    """
    a al algo algun alguna algunas alguno algunos ante antes aquel aquella aquello aqui asi aun bajo bien cada como
    con contra cual cuales cualquier cuando cuanta cuantas cuanto cuantos de del desde donde dos durante e el ella
    ellas ello ellos en entre era eran es esa esas ese eso esos esta estan estas este esto estos fue fueron ha han
    hasta hay la las le les lo los mas me mi mis mucho muy ni no nos nuestra nuestro o os otra otro para pero poco
    por porque puede pueden puedo que quien quienes se sea segun ser si sin sobre son su sus tambien tanto te tengo
    tiene tienen tu tus un una unas uno unos usted vosotros y ya yo
    """.split()
)


def sin_tildes(palabra: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", palabra) if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _raices():  # noqa: ANN202 — the stemmer class is private to the library
    import snowballstemmer

    return snowballstemmer.stemmer("spanish")


@lru_cache(maxsize=200_000)
def raiz(palabra: str) -> str:
    """The Snowball stem of one lower-case word, without accents (the stemmer needs them; the index does not)."""
    if palabra.isdigit() or not palabra.isalpha():
        return sin_tildes(palabra)
    return sin_tildes(_raices().stemWord(palabra))


def lematizar(texto: str) -> str:
    """``texto`` as the space-joined stems of its words, in order: what FTS5 indexes."""
    return " ".join(raiz(p.lower()) for p in _PALABRA.findall(texto))


def terminos(texto: str, *, lematizado: bool = True) -> list[str]:
    """The distinct terms of a free-text query, without Spanish stop words (kept when nothing else is left).

    ``lematizado=False`` returns the words as written, lower-cased: for an index built before stems were stored.
    """
    palabras = list(dict.fromkeys(p.lower() for p in _PALABRA.findall(texto)))
    utiles = [p for p in palabras if sin_tildes(p) not in PALABRAS_VACIAS] or palabras
    if not lematizado:
        return utiles
    return list(dict.fromkeys(raiz(p) for p in utiles))
