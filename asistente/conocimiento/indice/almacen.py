"""Almacén híbrido del corpus sobre SQLite: ``sqlite-vec`` (denso) + FTS5 (léxico) en el mismo fichero.

Tablas:

* ``fragmentos``: una fila por fragmento (``chunk_id`` estable, fichero de origen, texto y metadatos en JSON).
  Su ``rowid`` es la clave que comparten las otras dos tablas.
* ``vec_fragmentos`` (``vec0``): el vector del fragmento, ``float[dim]`` con métrica coseno. El embedder del
  proyecto devuelve vectores normalizados.
* ``fts_fragmentos`` (FTS5 de contenido externo sobre ``fragmentos.texto_lex``): el texto reducido a raíces
  del español (``lexico.lematizar``), con el tokenizador ``unicode61 remove_diacritics 2``, que corta por la
  puntuación y quita tildes y mayúsculas. «721.» casa con «721», «régimen» con «REGIMEN» y «deducción» con
  «deducciones». La consulta pasa por la misma reducción.
* ``preceptos``: los artículos y disposiciones de las normas que llegan por bloques del BOE, una fila por
  versión, con sus fechas de vigencia. No se vectorizan: son lo que se lee por identificador («artículo 68 de
  la LIRPF tal como estaba el 31 de diciembre de 2022») y de donde sale el índice de cada norma.
* ``indice_meta``: dimensión, métrica y versión del esquema.

Esquema 2 (noviembre de 2026): añade ``texto_lex`` y ``preceptos``. Un índice del esquema 1 se actualiza solo al
abrirlo para escribir (se calculan las raíces y se rehace FTS5; los vectores no se tocan); abierto en solo
lectura sigue funcionando con la búsqueda léxica por palabras, como antes.

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
import sqlite3
import threading
from typing import Any

from asistente.conocimiento.indice.lexico import lematizar, terminos

ESQUEMA_VERSION = "2"
METRICA = "cosine"
TOKENIZADOR_FTS = "unicode61 remove_diacritics 2"
RRF_K = 60
# vec0 no admite k mayor que este valor en una consulta KNN.
VEC0_K_MAX = 4096


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


@dataclass(frozen=True)
class Precepto:
    """Una versión de un artículo o disposición de una norma, tal como la guarda el índice."""

    norma_id: str  # BOE-A-2006-20764
    bloque_id: str  # a68
    titulo: str  # «Artículo 68»
    rotulo: str  # «Artículo 68. Deducciones.»
    texto: str
    vigente_desde: str
    vigente_hasta: str | None = None
    numero: str | None = None  # «68», «7 bis»
    sigla: str = ""
    norma_titulo: str = ""
    jerarquia: tuple[str, ...] = ()
    orden: int = 0
    id_norma: str = ""  # la norma que escribió esta versión
    notas: tuple[str, ...] = ()
    remisiones: tuple[tuple[str, str | None], ...] = ()  # (artículo, otra norma o None)
    sin_contenido: bool = False
    fuente: str | None = None

    @property
    def vigente(self) -> bool:
        return self.vigente_hasta is None


@dataclass(frozen=True)
class NormaIndexada:
    norma_id: str
    sigla: str
    titulo: str
    preceptos: int


def consulta_fts(texto: str, *, lematizado: bool = False) -> str | None:
    """Convierte texto libre en una consulta FTS5 segura: cada término entre comillas, unidos con OR, sin las
    palabras vacías del español (``PALABRAS_VACIAS``).

    Así ni los operadores de FTS5 (``AND``, ``NEAR``, ``*``, ``:``) ni las comillas del usuario rompen la
    consulta. Con ``lematizado`` los términos son raíces (lo que guarda un índice del esquema 2); sin él, las
    palabras tal cual, que el tokenizador de la tabla normaliza en tildes y mayúsculas.
    """
    palabras = terminos(texto, lematizado=lematizado)
    if not palabras:
        return None
    return " OR ".join(f'"{p}"' for p in palabras)


def _plano(texto: str) -> str:
    """Minúsculas, sin tildes y con los espacios colapsados: para comparar títulos y siglas."""
    from asistente.conocimiento.indice.lexico import sin_tildes

    return " ".join(sin_tildes(str(texto or "")).lower().split())


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
            self._conn.execute("CREATE TABLE IF NOT EXISTS indice_meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            if self._tiene_tabla("fragmentos") and "texto_lex" not in self._columnas("fragmentos"):
                self._actualizar_a_esquema_2()
            self._conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS fragmentos (
                    rowid INTEGER PRIMARY KEY,
                    chunk_id TEXT NOT NULL UNIQUE,
                    fuente TEXT,
                    texto TEXT NOT NULL,
                    metadatos TEXT NOT NULL DEFAULT '{{}}',
                    texto_lex TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS fragmentos_fuente ON fragmentos(fuente);
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_fragmentos USING fts5(
                    texto_lex, content='fragmentos', content_rowid='rowid', tokenize='{TOKENIZADOR_FTS}'
                );
                CREATE TABLE IF NOT EXISTS preceptos (
                    id INTEGER PRIMARY KEY,
                    fuente TEXT NOT NULL,
                    norma_id TEXT NOT NULL,
                    sigla TEXT NOT NULL DEFAULT '',
                    norma_titulo TEXT NOT NULL DEFAULT '',
                    bloque_id TEXT NOT NULL,
                    titulo TEXT NOT NULL DEFAULT '',
                    numero TEXT,
                    rotulo TEXT NOT NULL DEFAULT '',
                    jerarquia TEXT NOT NULL DEFAULT '[]',
                    orden INTEGER NOT NULL DEFAULT 0,
                    vigente_desde TEXT NOT NULL,
                    vigente_hasta TEXT,
                    id_norma TEXT NOT NULL DEFAULT '',
                    texto TEXT NOT NULL,
                    notas TEXT NOT NULL DEFAULT '[]',
                    remisiones TEXT NOT NULL DEFAULT '[]',
                    sin_contenido INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS preceptos_norma ON preceptos(norma_id, numero, vigente_desde);
                CREATE INDEX IF NOT EXISTS preceptos_bloque ON preceptos(norma_id, bloque_id, vigente_desde);
                CREATE INDEX IF NOT EXISTS preceptos_fuente ON preceptos(fuente);
                """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO indice_meta(clave, valor) VALUES ('esquema', ?), ('metrica', ?)",
                (ESQUEMA_VERSION, METRICA),
            )
            self._conn.commit()
        # Un índice del esquema 1 abierto en solo lectura guarda palabras, no raíces: se le pregunta igual.
        self._lematizado = self._tiene_tabla("fragmentos") and "texto_lex" in self._columnas("fragmentos")
        self._con_preceptos = self._tiene_tabla("preceptos")

    def _tiene_tabla(self, nombre: str) -> bool:
        fila = self._conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (nombre,)).fetchone()
        return fila is not None

    def _columnas(self, tabla: str) -> set[str]:
        return {str(f[1]) for f in self._conn.execute(f"PRAGMA table_info({tabla})").fetchall()}

    def _actualizar_a_esquema_2(self) -> None:
        """Esquema 1 → 2 en el sitio: raíces de cada fragmento y FTS5 rehecho sobre ellas. Los vectores no cambian."""
        with self._conn:
            self._conn.execute("ALTER TABLE fragmentos ADD COLUMN texto_lex TEXT NOT NULL DEFAULT ''")
            filas = self._conn.execute("SELECT rowid, texto FROM fragmentos").fetchall()
            self._conn.executemany(
                "UPDATE fragmentos SET texto_lex = ? WHERE rowid = ?", [(lematizar(f[1]), f[0]) for f in filas]
            )
            self._conn.execute("DROP TABLE IF EXISTS fts_fragmentos")
            self._conn.execute(
                f"CREATE VIRTUAL TABLE fts_fragmentos USING fts5("
                f"texto_lex, content='fragmentos', content_rowid='rowid', tokenize='{TOKENIZADOR_FTS}')"
            )
            self._conn.execute("INSERT INTO fts_fragmentos(fts_fragmentos) VALUES ('rebuild')")
            self._conn.execute(
                "INSERT INTO indice_meta(clave, valor) VALUES ('esquema', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                (ESQUEMA_VERSION,),
            )

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
            fila = self._conn.execute("SELECT texto_lex FROM fragmentos WHERE rowid = ?", (rowid,)).fetchone()
            if fila is None:
                continue
            # FTS5 de contenido externo: el borrado exige el texto que se indexó.
            self._conn.execute(
                "INSERT INTO fts_fragmentos(fts_fragmentos, rowid, texto_lex) VALUES ('delete', ?, ?)",
                (rowid, fila[0]),
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
            texto_lex = lematizar(fragmento.texto)
            cursor = self._conn.execute(
                "INSERT INTO fragmentos(chunk_id, fuente, texto, metadatos, texto_lex) VALUES (?, ?, ?, ?, ?)",
                (
                    fragmento.chunk_id,
                    None if fuente is None else str(fuente),
                    fragmento.texto,
                    json.dumps(fragmento.metadatos, ensure_ascii=False, default=str),
                    texto_lex,
                ),
            )
            rowid = cursor.lastrowid
            self._conn.execute("INSERT INTO fts_fragmentos(rowid, texto_lex) VALUES (?, ?)", (rowid, texto_lex))
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

    # ------------------------------------------------------------- preceptos

    def guardar_preceptos(self, fuente: str, preceptos: Sequence[Precepto]) -> None:
        """Sustituye todas las versiones de artículos que salieron de ``fuente`` (un fichero del corpus)."""
        with self._cerrojo, self._conn:
            self._conn.execute("DELETE FROM preceptos WHERE fuente = ?", (fuente,))
            self._conn.executemany(
                "INSERT INTO preceptos(fuente, norma_id, sigla, norma_titulo, bloque_id, titulo, numero, rotulo, "
                "jerarquia, orden, vigente_desde, vigente_hasta, id_norma, texto, notas, remisiones, sin_contenido) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        fuente,
                        p.norma_id,
                        p.sigla,
                        p.norma_titulo,
                        p.bloque_id,
                        p.titulo,
                        p.numero,
                        p.rotulo,
                        json.dumps(list(p.jerarquia), ensure_ascii=False),
                        p.orden,
                        p.vigente_desde,
                        p.vigente_hasta,
                        p.id_norma,
                        p.texto,
                        json.dumps(list(p.notas), ensure_ascii=False),
                        json.dumps([list(r) for r in p.remisiones], ensure_ascii=False),
                        int(p.sin_contenido),
                    )
                    for p in preceptos
                ],
            )

    def borrar_preceptos(self, fuente: str) -> int:
        with self._cerrojo, self._conn:
            return int(self._conn.execute("DELETE FROM preceptos WHERE fuente = ?", (fuente,)).rowcount)

    def fuentes_de(self, chunk_ids: Iterable[str]) -> set[str]:
        """Los ficheros de origen de unos fragmentos (para saber qué preceptos se quedan sin fragmentos)."""
        fuentes: set[str] = set()
        with self._cerrojo:
            for chunk_id in chunk_ids:
                fila = self._conn.execute("SELECT fuente FROM fragmentos WHERE chunk_id = ?", (chunk_id,)).fetchone()
                if fila is not None and fila[0]:
                    fuentes.add(str(fila[0]))
        return fuentes

    def fragmentos_de_fuente(self, fuente: str) -> int:
        with self._cerrojo:
            fila = self._conn.execute("SELECT count(*) FROM fragmentos WHERE fuente = ?", (fuente,)).fetchone()
        return int(fila[0])

    @staticmethod
    def _precepto(f: sqlite3.Row) -> Precepto:
        return Precepto(
            norma_id=f["norma_id"],
            bloque_id=f["bloque_id"],
            titulo=f["titulo"],
            rotulo=f["rotulo"],
            texto=f["texto"],
            vigente_desde=f["vigente_desde"],
            vigente_hasta=f["vigente_hasta"],
            numero=f["numero"],
            sigla=f["sigla"],
            norma_titulo=f["norma_titulo"],
            jerarquia=tuple(json.loads(f["jerarquia"])),
            orden=int(f["orden"]),
            id_norma=f["id_norma"],
            notas=tuple(json.loads(f["notas"])),
            remisiones=tuple((str(r[0]), r[1]) for r in json.loads(f["remisiones"])),
            sin_contenido=bool(f["sin_contenido"]),
            fuente=f["fuente"],
        )

    def normas(self) -> list[NormaIndexada]:
        """Las normas que el índice tiene por artículos, con cuántos preceptos vigentes guarda de cada una."""
        if not self._con_preceptos:
            return []
        with self._cerrojo:
            filas = self._conn.execute(
                "SELECT norma_id, max(sigla), max(norma_titulo), count(*) FROM preceptos "
                "WHERE vigente_hasta IS NULL GROUP BY norma_id ORDER BY norma_id"
            ).fetchall()
        return [NormaIndexada(str(f[0]), str(f[1] or ""), str(f[2] or ""), int(f[3])) for f in filas]

    def versiones(
        self, norma_id: str, *, numero: str | None = None, bloque_id: str | None = None, titulo: str | None = None
    ) -> list[Precepto]:
        """Todas las versiones de un precepto, de la más antigua a la vigente.

        Se localiza por número de artículo («68», «7 bis»), por el identificador del bloque en el BOE («a68») o
        por su título («Disposición adicional cuarta», sin distinguir tildes ni mayúsculas).
        """
        if not self._con_preceptos:
            return []
        if numero is not None:
            condicion, valor = "numero = ?", " ".join(numero.lower().split())
        elif bloque_id is not None:
            condicion, valor = "bloque_id = ?", bloque_id
        elif titulo is not None:
            condicion, valor = "titulo_plano(titulo) = ?", _plano(titulo)
        else:
            raise ValueError("Hace falta numero, bloque_id o titulo.")
        with self._cerrojo:
            self._conn.create_function("titulo_plano", 1, _plano, deterministic=True)
            filas = self._conn.execute(
                f"SELECT * FROM preceptos WHERE norma_id = ? AND {condicion} ORDER BY vigente_desde", (norma_id, valor)
            ).fetchall()
        return [self._precepto(f) for f in filas]

    def precepto(
        self,
        norma_id: str,
        *,
        numero: str | None = None,
        bloque_id: str | None = None,
        titulo: str | None = None,
        fecha: str | None = None,
    ) -> Precepto | None:
        """La versión vigente de un precepto o, con ``fecha`` (ISO), la que estaba en vigor ese día.

        ``None`` si la norma no tiene ese precepto o si aún no existía en esa fecha.
        """
        versiones = self.versiones(norma_id, numero=numero, bloque_id=bloque_id, titulo=titulo)
        if not versiones:
            return None
        if fecha is None:
            return versiones[-1]
        return next(
            (
                v
                for v in reversed(versiones)
                if v.vigente_desde <= fecha and (v.vigente_hasta is None or fecha < v.vigente_hasta)
            ),
            None,
        )

    def indice_norma(self, norma_id: str) -> list[Precepto]:
        """Los preceptos vigentes de una norma, en su orden, sin el texto (el índice de la norma)."""
        if not self._con_preceptos:
            return []
        with self._cerrojo:
            filas = self._conn.execute(
                "SELECT fuente, norma_id, sigla, norma_titulo, bloque_id, titulo, numero, rotulo, jerarquia, orden, "
                "vigente_desde, vigente_hasta, id_norma, '' AS texto, '[]' AS notas, '[]' AS remisiones, sin_contenido "
                "FROM preceptos WHERE norma_id = ? AND vigente_hasta IS NULL ORDER BY orden",
                (norma_id,),
            ).fetchall()
        return [self._precepto(f) for f in filas]

    def resolver_norma(self, referencia: str) -> str | None:
        """El identificador de la norma que nombra ``referencia``: su id del BOE, su sigla («LIRPF») o un trozo
        inequívoco de su título («Ley 35/2006»). ``None`` si no hay ninguna o si casan varias."""
        buscado = _plano(referencia)
        if not buscado:
            return None
        normas = self.normas()
        for n in normas:
            if buscado in (_plano(n.norma_id), _plano(n.sigla)):
                return n.norma_id
        candidatas = [n.norma_id for n in normas if buscado in _plano(n.titulo)]
        return candidatas[0] if len(candidatas) == 1 else None

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
        consulta = consulta_fts(texto, lematizado=self._lematizado)
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
