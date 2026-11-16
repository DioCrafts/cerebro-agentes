"""Operator commands of the mail module.

``python -m hacienda_gpt.modulos.correo sincronizar`` — sync every connected case whose owner has the module
on (for cron, e.g. every 30 minutes). ``nueva-clave`` — print a new Fernet key for ``CORREO_CLAVE_CIFRADO``.
``rotar-clave`` — re-encrypt every stored token with the first key of ``CORREO_CLAVE_CIFRADO`` (put the new
key first and keep the old one after a comma until this has run).
"""

from __future__ import annotations

import sys

from hacienda_gpt.modulos.correo import almacen, cifrado
from hacienda_gpt.modulos.correo.http import CorreoError


def _casos_conectados() -> list[str]:
    with almacen.conectar() as conn:
        return [f["case_id"] for f in conn.execute("SELECT case_id FROM correo_conexiones WHERE estado = 'conectada'")]


def sincronizar_todo() -> int:
    from hacienda_gpt.memoria import reloj
    from hacienda_gpt.modulos.correo.sincronizacion import sincronizar
    from hacienda_gpt.modulos.preferencias import modulos_activos_para_caso

    fallos = 0
    for case_id in _casos_conectados():
        if "correo" not in {m.clave for m in modulos_activos_para_caso(case_id)}:
            continue
        try:
            resultado = sincronizar(case_id, hoy=reloj.hoy())
            print(f"{case_id}: {resultado.nuevos} nuevo(s) ({resultado.modo})")
        except CorreoError as exc:
            fallos += 1
            print(f"{case_id}: {exc}")
    return 1 if fallos else 0


def rotar_clave() -> int:
    with almacen.conectar() as conn:
        filas = conn.execute("SELECT case_id, token_cifrado FROM correo_conexiones").fetchall()
        for fila in filas:
            conn.execute(
                "UPDATE correo_conexiones SET token_cifrado = ? WHERE case_id = ?",
                (cifrado.recifrar(fila["token_cifrado"]), fila["case_id"]),
            )
    print(f"{len(filas)} token(s) recifrado(s) con la primera clave.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    orden = args[0] if args else ""
    if orden == "sincronizar":
        return sincronizar_todo()
    if orden == "nueva-clave":
        print(cifrado.nueva_clave())
        return 0
    if orden == "rotar-clave":
        return rotar_clave()
    print("uso: python -m hacienda_gpt.modulos.correo {sincronizar|nueva-clave|rotar-clave}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
