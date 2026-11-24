"""Almacén híbrido del corpus sobre SQLite: ``sqlite-vec`` (denso) + FTS5 (léxico) en el mismo fichero.

Tablas:

* ``fragmentos``: una fila por fragmento (``chunk_id`` estable, fichero de origen, texto y metadatos en JSON).
  Su ``rowid`` es la clave que comparten las otras dos tablas.
* ``vec_fragmentos`` (``vec0``): el vector del fragmento, ``float[dim]`` con métrica coseno. El embedder del
  proyecto devuelve vectores normalizados.
* ``fts_fragmentos`` (FTS5 de contenido externo sobre ``fragmentos.texto``): tokenizador
  ``unicode61 remove_diacritics 2``, que corta por la puntuación y quita tildes y mayúsculas, de modo que
  «721.» casa con «721» y «régimen» con «REGIMEN».
* ``indice_meta``: dimensión, métrica y versión del esquema.

No hay pickle en ningún punto: abrir el índice no ejecuta código de nadie.

Las altas, bajas y reemplazos van en una transacción cada uno. Un ``chunk_id`` repetido reemplaza al
anterior (los ids son deterministas por fichero e índice de fragmento), lo que mantiene el contrato de la
ingesta incremental: el manifiesto (sha256 + huella del pipeline) dice qué ids borrar y qué ficheros
reprocesar, y el almacén aplica ese plan.
"""

from __future__ import annotations

from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any
import unicodedata

ESQUEMA_VERSION = "1"
METRICA = "cosine"
TOKENIZADOR_FTS = "unicode61 remove_diacritics 2"
RRF_K = 60
# vec0 no admite k mayor que este valor en una consulta KNN.
VEC0_K_MAX = 4096

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


@dataclass(frozen=True)
class Fragmento:
    chunk_id: str
    texto: str
    metadatos: dict[str, Any] = field(default_factory=dict)
    fuente: str | None = None


@dataclass(frozen=True)
class Acierto:
    fragmento: Fragmento
    puntuacion: float  # distancia coseno (densa), bm25 de FTS5 (léxica, menor es mejor) o RRF (mayor es mejor)


def consulta_fts(texto: str) -> str | None:
    """Convierte texto libre en una consulta FTS5 segura: cada palabra entre comillas, unidas con OR, sin las
    palabras vacías del español (``PALABRAS_VACIAS``).

    Así ni los operadores de FTS5 (``AND``, ``NEAR``, ``*``, ``:``) ni las comillas del usuario rompen la
    consulta. El tokenizador de la tabla vuelve a normalizar tildes y mayúsculas en cada término.
    """
    palabras = list(dict.fromkeys(p.lower() for p in _PALABRA.findall(texto)))
    if not palabras:
        return None
    utiles = [p for p in palabras if _sin_tildes(p) not in PALABRAS_VACIAS]
    palabras = utiles or palabras
    return " OR ".join(f'"{p}"' for p in palabras)


def _sin_tildes(palabra: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", palabra) if not unicodedata.combining(c))


def fusion_rrf(
    rankings: Sequence[Sequence[str]], *, k: int = RRF_K, pesos: Sequence[float] | None = None
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: ``sum(peso / (k + posición))`` por id, de mayor a menor."""
    pesos = pesos or [1.0] * len(rankings)
    puntos: dict[str, float] = {}
    for ranking, peso in zip(rankings, pesos, strict=True):
        for posicion, ident in enumerate(ranking, start=1):
            puntos[ident] = puntos.get(ident, 0.0) + peso / (k + posicion)
    return sorted(puntos.items(), key=lambda par: (-par[1], par[0]))


def _serializar(vector: Sequence[float]) -> bytes:
    import sqlite_vec

    return bytes(sqlite_vec.serialize_float32(list(map(float, vector))))


def abrir_conexion(ruta: str | Path, *, solo_lectura: bool = False) -> sqlite3.Connection:
    """Conexión con WAL, ``busy_timeout`` y la extensión ``sqlite-vec`` cargada."""
    import sqlite_vec

    ruta = Path(ruta)
    if solo_lectura:
        conn = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=30.0, check_same_thread=False)
    else:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(ruta, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    conn.execute("PRAGMA busy_timeout=30000;")
    if not solo_lectura:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


class IndiceHibrido:
    """Índice del corpus en un fichero SQLite. Seguro entre hilos (un cerrojo por instancia)."""

    def __init__(self, ruta: str | Path, *, solo_lectura: bool = False) -> None:
        self.ruta = Path(ruta)
        self._conn = abrir_conexion(self.ruta, solo_lectura=solo_lectura)
        self._cerrojo = threading.RLock()
        if not solo_lectura:
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS indice_meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fragmentos (
                    rowid INTEGER PRIMARY KEY,
                    chunk_id TEXT NOT NULL UNIQUE,
                    fuente TEXT,
                    texto TEXT NOT NULL,
                    metadatos TEXT NOT NULL DEFAULT '{{}}'
                );
                CREATE INDEX IF NOT EXISTS fragmentos_fuente ON fragmentos(fuente);
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_fragmentos USING fts5(
                    texto, content='fragmentos', content_rowid='rowid', tokenize='{TOKENIZADOR_FTS}'
                );
                """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO indice_meta(clave, valor) VALUES ('esquema', ?), ('metrica', ?)",
                (ESQUEMA_VERSION, METRICA),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ estado

    def __enter__(self) -> IndiceHibrido:
        return self

    def __exit__(self, *_: object) -> None:
        self.cerrar()

    def cerrar(self) -> None:
        self._conn.close()

    def _meta(self, clave: str) -> str | None:
        fila = self._conn.execute("SELECT valor FROM indice_meta WHERE clave = ?", (clave,)).fetchone()
        return None if fila is None else fila[0]

    @property
    def dimension(self) -> int | None:
        valor = self._meta("dimension")
        return None if valor is None else int(valor)

    def __len__(self) -> int:
        return int(self._conn.execute("SELECT count(*) FROM fragmentos").fetchone()[0])

    def num_vectores(self) -> int:
        if self.dimension is None:
            return 0
        return int(self._conn.execute("SELECT count(*) FROM vec_fragmentos").fetchone()[0])

    def _asegurar_vectores(self, dimension: int) -> None:
        actual = self.dimension
        if actual is None:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_fragmentos USING vec0("
                f"embedding float[{int(dimension)}] distance_metric={METRICA})"
            )
            self._conn.execute("INSERT INTO indice_meta(clave, valor) VALUES ('dimension', ?)", (str(dimension),))
        elif actual != dimension:
            raise ValueError(
                f"El índice {self.ruta} guarda vectores de dimensión {actual} y llegan de {dimension}: "
                "otro embedder exige reconstruir el índice."
            )

    # ------------------------------------------------------------- escritura

    def _borrar_rowids(self, rowids: Iterable[int]) -> int:
        borrados = 0
        tiene_vec = self.dimension is not None
        for rowid in rowids:
            fila = self._conn.execute("SELECT texto FROM fragmentos WHERE rowid = ?", (rowid,)).fetchone()
            if fila is None:
                continue
            # FTS5 de contenido externo: el borrado exige el texto que se indexó.
            self._conn.execute(
                "INSERT INTO fts_fragmentos(fts_fragmentos, rowid, texto) VALUES ('delete', ?, ?)", (rowid, fila[0])
            )
            if tiene_vec:
                self._conn.execute("DELETE FROM vec_fragmentos WHERE rowid = ?", (rowid,))
            self._conn.execute("DELETE FROM fragmentos WHERE rowid = ?", (rowid,))
            borrados += 1
        return borrados

    def _rowids_de(self, chunk_ids: Iterable[str]) -> list[int]:
        rowids = []
        for chunk_id in chunk_ids:
            fila = self._conn.execute("SELECT rowid FROM fragmentos WHERE chunk_id = ?", (chunk_id,)).fetchone()
            if fila is not None:
                rowids.append(int(fila[0]))
        return rowids

    def _insertar(self, fragmentos: Sequence[Fragmento], vectores: Sequence[Sequence[float]]) -> None:
        if len(fragmentos) != len(vectores):
            raise ValueError(f"{len(fragmentos)} fragmentos y {len(vectores)} vectores: deben coincidir.")
        if not fragmentos:
            return
        self._asegurar_vectores(len(vectores[0]))
        # Un chunk_id repetido reemplaza al anterior (ids deterministas: reindexar un fichero lo sobrescribe).
        self._borrar_rowids(self._rowids_de(f.chunk_id for f in fragmentos))
        for fragmento, vector in zip(fragmentos, vectores, strict=True):
            fuente = fragmento.fuente or fragmento.metadatos.get("source_file") or fragmento.metadatos.get("source")
            cursor = self._conn.execute(
                "INSERT INTO fragmentos(chunk_id, fuente, texto, metadatos) VALUES (?, ?, ?, ?)",
                (
                    fragmento.chunk_id,
                    None if fuente is None else str(fuente),
                    fragmento.texto,
                    json.dumps(fragmento.metadatos, ensure_ascii=False, default=str),
                ),
            )
            rowid = cursor.lastrowid
            self._conn.execute("INSERT INTO fts_fragmentos(rowid, texto) VALUES (?, ?)", (rowid, fragmento.texto))
            self._conn.execute(
                "INSERT INTO vec_fragmentos(rowid, embedding) VALUES (?, ?)", (rowid, _serializar(vector))
            )

    def anadir(self, fragmentos: Sequence[Fragmento], vectores: Sequence[Sequence[float]]) -> None:
        """Alta (o reemplazo por ``chunk_id``) de fragmentos con sus vectores ya calculados, en una transacción."""
        with self._cerrojo, self._conn:
            self._insertar(fragmentos, vectores)

    def borrar(self, chunk_ids: Iterable[str]) -> int:
        """Baja por ``chunk_id`` (los que guarda el manifiesto). Devuelve cuántos había."""
        with self._cerrojo, self._conn:
            return self._borrar_rowids(self._rowids_de(chunk_ids))

    def borrar_fuente(self, fuente: str) -> int:
        """Baja de todos los fragmentos de un fichero de origen."""
        with self._cerrojo, self._conn:
            filas = self._conn.execute("SELECT rowid FROM fragmentos WHERE fuente = ?", (fuente,)).fetchall()
            return self._borrar_rowids(int(f[0]) for f in filas)

    def reemplazar_fuente(
        self, fuente: str, fragmentos: Sequence[Fragmento], vectores: Sequence[Sequence[float]]
    ) -> None:
        """Sustituye todos los fragmentos de un fichero por los nuevos, de forma atómica."""
        with self._cerrojo, self._conn:
            filas = self._conn.execute("SELECT rowid FROM fragmentos WHERE fuente = ?", (fuente,)).fetchall()
            self._borrar_rowids(int(f[0]) for f in filas)
            self._insertar([Fragmento(f.chunk_id, f.texto, f.metadatos, fuente) for f in fragmentos], vectores)

    def aplicar(
        self,
        *,
        borrar: Iterable[str] = (),
        fragmentos: Sequence[Fragmento] = (),
        vectores: Sequence[Sequence[float]] = (),
    ) -> None:
        """Bajas y altas de un lote de la ingesta en una sola transacción."""
        with self._cerrojo, self._conn:
            self._borrar_rowids(self._rowids_de(borrar))
            self._insertar(fragmentos, vectores)

    # --------------------------------------------------------------- lectura

    def _fragmentos_por_rowid(self, rowids: Sequence[int]) -> dict[int, Fragmento]:
        if not rowids:
            return {}
        marcas = ",".join("?" * len(rowids))
        filas = self._conn.execute(
            f"SELECT rowid, chunk_id, fuente, texto, metadatos FROM fragmentos WHERE rowid IN ({marcas})",
            list(rowids),
        ).fetchall()
        return {
            int(f["rowid"]): Fragmento(f["chunk_id"], f["texto"], json.loads(f["metadatos"]), f["fuente"])
            for f in filas
        }

    def recorrer(self, *, con_vectores: bool = False) -> Iterator[tuple[Fragmento, list[float] | None]]:
        """Todos los fragmentos, en orden de alta, y su vector si ``con_vectores``.

        Para quien necesita el corpus entero: el grafo de doctrina y el benchmark de recuperación.
        """
        con_vec = con_vectores and self.dimension is not None
        consulta = (
            "SELECT f.chunk_id, f.fuente, f.texto, f.metadatos, v.embedding FROM fragmentos f "
            "LEFT JOIN vec_fragmentos v ON v.rowid = f.rowid ORDER BY f.rowid"
            if con_vec
            else "SELECT chunk_id, fuente, texto, metadatos, NULL FROM fragmentos ORDER BY rowid"
        )
        with self._cerrojo:
            filas = self._conn.execute(consulta).fetchall()
        for f in filas:
            vector = array("f", f[4]).tolist() if f[4] is not None else None
            yield Fragmento(f[0], f[2], json.loads(f[3]), f[1]), vector

    def vector(self, chunk_id: str) -> list[float] | None:
        fila = self._conn.execute(
            "SELECT vec_to_json(v.embedding) FROM fragmentos f JOIN vec_fragmentos v ON v.rowid = f.rowid "
            "WHERE f.chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        return None if fila is None else json.loads(fila[0])

    def buscar_denso(self, vector: Sequence[float], *, k: int) -> list[Acierto]:
        if self.dimension is None or k <= 0:
            return []
        with self._cerrojo:
            filas = self._conn.execute(
                "SELECT rowid, distance FROM vec_fragmentos WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (_serializar(vector), min(int(k), VEC0_K_MAX)),
            ).fetchall()
            por_rowid = self._fragmentos_por_rowid([int(f[0]) for f in filas])
        return [Acierto(por_rowid[int(f[0])], float(f[1])) for f in filas if int(f[0]) in por_rowid]

    def buscar_lexico(self, texto: str, *, k: int) -> list[Acierto]:
        consulta = consulta_fts(texto)
        if consulta is None or k <= 0:
            return []
        with self._cerrojo:
            filas = self._conn.execute(
                "SELECT rowid, bm25(fts_fragmentos) AS rango FROM fts_fragmentos WHERE fts_fragmentos MATCH ? "
                "ORDER BY rango LIMIT ?",
                (consulta, int(k)),
            ).fetchall()
            por_rowid = self._fragmentos_por_rowid([int(f[0]) for f in filas])
        return [Acierto(por_rowid[int(f[0])], float(f[1])) for f in filas if int(f[0]) in por_rowid]

    def buscar_hibrido(
        self,
        texto: str,
        vector: Sequence[float],
        *,
        k: int,
        profundidad: int | None = None,
        peso_denso: float = 1.0,
        peso_lexico: float = 1.0,
    ) -> list[Acierto]:
        """RRF sobre el ranking denso y el léxico (cada uno hasta ``profundidad``); puntuación = RRF."""
        profundidad = max(k, profundidad or k * 4)
        densos = self.buscar_denso(vector, k=profundidad)
        lexicos = self.buscar_lexico(texto, k=profundidad)
        por_id = {a.fragmento.chunk_id: a.fragmento for a in (*densos, *lexicos)}
        fusion = fusion_rrf(
            [[a.fragmento.chunk_id for a in densos], [a.fragmento.chunk_id for a in lexicos]],
            pesos=[peso_denso, peso_lexico],
        )
        return [Acierto(por_id[ident], puntos) for ident, puntos in fusion[:k]]
