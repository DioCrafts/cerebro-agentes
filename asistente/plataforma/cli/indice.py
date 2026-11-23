"""Gestión del índice SQLite del corpus.

Uso::

    FAISS_TRUSTED_INDEX=true python -m asistente.plataforma.cli.indice migrar --desde .faiss --a .faiss/indice.sqlite

Copia vectores, textos y metadatos del índice FAISS sin recalcular embeddings, verifica recuentos y una
muestra de vecinos y sale con código 1 si algo no cuadra.
"""

from __future__ import annotations

import argparse
import logging
import sys


def _migrar(args: argparse.Namespace) -> int:
    from asistente.conocimiento.indice.migracion import cargar_faiss, migrar
    from asistente.plataforma import settings

    vectorstore = cargar_faiss(args.desde, confiar=settings.FAISS_TRUSTED_INDEX)
    informe = migrar(
        vectorstore,
        args.a,
        origen=args.desde,
        muestra=args.muestra,
        vecinos_k=args.k,
        solapamiento_minimo=args.solapamiento_minimo,
    )
    print(informe.texto())
    return 0 if informe.correcto else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asistente.plataforma.cli.indice", description=__doc__)
    sub = parser.add_subparsers(dest="orden", required=True)
    migrar = sub.add_parser("migrar", help="Copia un índice FAISS a SQLite (sqlite-vec + FTS5).")
    migrar.add_argument("--desde", required=True, help="Directorio del índice FAISS (index.faiss + index.pkl).")
    migrar.add_argument("--a", required=True, help="Fichero SQLite de destino; no debe existir.")
    migrar.add_argument("--muestra", type=int, default=50, help="Fragmentos para comparar vecinos.")
    migrar.add_argument("--k", type=int, default=10, help="Vecinos por consulta de la muestra.")
    migrar.add_argument("--solapamiento-minimo", type=float, default=0.9)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.orden == "migrar":
        return _migrar(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
