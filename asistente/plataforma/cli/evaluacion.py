"""Continuous evaluation of the central assistant: run the suite, gate prompt/model changes, review cases.

    # Model-free part (every pull request): schema, expected figures, reference scripts replayed.
    uv run python -m asistente.plataforma.cli.evaluacion validar
    uv run python -m asistente.plataforma.cli.evaluacion run --reproduccion

    # With a model (needs a key; asks before spending): the active variant, or a candidate.
    uv run python -m asistente.plataforma.cli.evaluacion run --out reports/evaluacion/base.json
    uv run python -m asistente.plataforma.cli.evaluacion run --config variantes/candidata.json --baseline reports/evaluacion/base.json

    # Gate: baseline vs candidate; writes the active variant only if the candidate improves.
    uv run python -m asistente.plataforma.cli.evaluacion promote --config variantes/candidata.json

    # Learning from real mistakes: the review queue of candidate cases (masked), accept / reject.
    uv run python -m asistente.plataforma.cli.evaluacion casos detectar
    uv run python -m asistente.plataforma.cli.evaluacion casos cola
    uv run python -m asistente.plataforma.cli.evaluacion casos candidato cand_…
    uv run python -m asistente.plataforma.cli.evaluacion casos aceptar cand_… --desde edicion.json
    uv run python -m asistente.plataforma.cli.evaluacion casos rechazar cand_… --motivo "…"

    # Accepted real cases (private database, never in git): list, negative control, replay against a variant.
    uv run python -m asistente.plataforma.cli.evaluacion casos-reales listar
    uv run python -m asistente.plataforma.cli.evaluacion casos-reales comprobar
    uv run python -m asistente.plataforma.cli.evaluacion casos-reales reproducir --config candidata.json \\
        --modelo-real --presupuesto-usd 2

    # Older review flow of a stored feedback (the case also goes to the private database).
    uv run python -m asistente.plataforma.cli.evaluacion casos pendientes
    uv run python -m asistente.plataforma.cli.evaluacion casos borrador fb_… --out /tmp/borrador.json
    uv run python -m asistente.plataforma.cli.evaluacion casos promover fb_… --desde /tmp/borrador.json \\
        --confirmo-anonimizado --revisor "Nombre"
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile

import click

from asistente.nucleo.evaluacion import suite as suite_mod
from asistente.nucleo.evaluacion.modelos import CasoEvaluacion
from asistente.plataforma import settings

_CASOS = click.option(
    "--casos",
    "casos_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=str(suite_mod.CASOS_DIR),
    show_default=True,
    help="Directorio de la suite.",
)
_DB = click.option(
    "--db",
    "db_path",
    default=lambda: settings.DECISION_STATE_DB_PATH,
    show_default="DECISION_STATE_DB_PATH",
    help="Base de datos de los expedientes (donde están las valoraciones).",
)


@click.group()
def cli() -> None:
    """Evaluación continua del asistente central y de sus módulos."""


# --------------------------------------------------------------------------- #
# Suite: validation, runs, gate
# --------------------------------------------------------------------------- #


@cli.command()
@_CASOS
@click.option("--estricto", is_flag=True, help="Falla también si alguna cifra registrada ha cambiado (deriva).")
def validar(casos_dir: Path, estricto: bool) -> None:
    """Comprueba la suite sin modelo: esquema, categorías, módulos y que cada cifra esperada se calcula."""
    from asistente.nucleo.evaluacion.variantes import comprobar_integrados, desajustes, variante_activa

    informe = suite_mod.validar(casos_dir)
    for aviso in desajustes(variante_activa()):
        click.echo(f"Aviso: {aviso}")
    problemas = list(informe.errores) + comprobar_integrados()
    click.echo(
        f"{informe.casos} casos ({informe.deterministicos} sin juez, {informe.con_guion} con guion de referencia)."
    )
    if informe.con_requisitos:
        from asistente.nucleo.evaluacion.ejecutores import pendiente

        for requisito, n in sorted(informe.con_requisitos.items()):
            motivo = pendiente(requisito)
            click.echo(f"  {n} requieren «{requisito}»: " + (f"se omitirán ({motivo})" if motivo else "disponible"))
    for deriva in informe.derivas:
        click.echo(f"Deriva: {deriva}")
    for problema in problemas:
        click.echo(f"Error: {problema}", err=True)
    if problemas or (estricto and informe.derivas):
        sys.exit(1)
    click.echo("Suite válida.")


def _seleccion(casos_dir: Path, categorias: tuple[str, ...], ids: tuple[str, ...]) -> list[CasoEvaluacion]:
    casos = suite_mod.cargar_casos(casos_dir)
    if categorias:
        casos = [c for c in casos if c.categoria in categorias]
    if ids:
        casos = [c for c in casos if c.id in ids]
    if not casos:
        raise click.ClickException("Ningún caso coincide con la selección.")
    return casos


def _entorno(reproduccion: bool, *, presupuesto: Decimal | None, sin_corpus: bool, sin_juez: bool):  # type: ignore[no-untyped-def]
    from asistente.nucleo.evaluacion.runner import (
        Entorno,
        buscador_por_defecto,
        proveedor_real,
        proveedor_reproduccion,
    )
    from asistente.plataforma.ops.consumo import ConsumoStore, Presupuesto, almacen_por_defecto

    if reproduccion:
        temporal = Path(tempfile.mkdtemp(prefix="evaluacion-")) / "consumo.sqlite3"
        return Entorno(proveedor=proveedor_reproduccion, consumo=ConsumoStore(str(temporal)), modo="reproduccion")
    from asistente.nucleo.agent.search import CorpusSearcher
    from asistente.nucleo.evaluacion.cifras import SinCorpus

    avisos: list[str] = []
    buscador: CorpusSearcher
    if sin_corpus:
        buscador = SinCorpus()
        avisos.append("Ejecutado sin corpus (--sin-corpus).")
    else:
        buscador, aviso = buscador_por_defecto()
        if aviso:
            avisos.append(aviso)

    def juez():  # type: ignore[no-untyped-def]
        # The judge is the configured model, the same for baseline and candidate.
        from asistente.nucleo.agent.providers import create_provider

        return create_provider()

    consumo = almacen_por_defecto()
    return Entorno(
        proveedor=proveedor_real,
        consumo=consumo,
        # The service's daily limits apply to evaluation spend too; --presupuesto-usd caps the run.
        limites=Presupuesto.desde_settings(consumo),
        modo="modelo",
        buscador=buscador,
        juez=None if sin_juez else juez,
        presupuesto_usd=presupuesto,
        avisos=avisos,
    )


def _variante(config: Path | None):  # type: ignore[no-untyped-def]
    from asistente.nucleo.evaluacion.variantes import VarianteInvalidaError, cargar_variante, variante_activa

    if config is None:
        return variante_activa()
    try:
        return cargar_variante(config)
    except VarianteInvalidaError as exc:
        raise click.ClickException(str(exc)) from exc


def _progreso(caso: CasoEvaluacion, resultado) -> None:  # type: ignore[no-untyped-def]
    if resultado.omitido:
        click.echo(f"  – {caso.id} [{caso.categoria}] omitido: {resultado.motivo_omision}")
        return
    marca = "✓" if resultado.aprobado else "✗"
    click.echo(f"  {marca} {caso.id} [{caso.categoria}] {resultado.ejecucion.latencia_s:.1f} s")


_CASOS_REALES_OPCIONES = (
    click.option(
        "--con-casos-reales/--sin-casos-reales",
        "con_reales",
        default=None,
        help="Incluir los casos reales aceptados (base privada). Por defecto: sí, si la base existe.",
    ),
    click.option(
        "--casos-reales-db",
        "reales_db",
        default=None,
        help="Base privada de casos reales (por defecto EVALUACION_CASOS_REALES_DB_PATH o junto a la de expedientes).",
    ),
)


def _componer(*opciones):  # type: ignore[no-untyped-def]
    def decorar(funcion):  # type: ignore[no-untyped-def]
        for opcion in reversed(opciones):
            funcion = opcion(funcion)
        return funcion

    return decorar


_CASOS_REALES = _componer(*_CASOS_REALES_OPCIONES)


def _ruta_reales(reales_db: str | None) -> Path:
    from asistente.nucleo.evaluacion.privado import ruta_por_defecto

    return Path(reales_db) if reales_db else ruta_por_defecto()


def _casos_reales(
    con_reales: bool | None, reales_db: str | None, categorias: tuple[str, ...] = (), ids: tuple[str, ...] = ()
) -> list[CasoEvaluacion]:
    from asistente.nucleo.evaluacion.privado import casos_vigentes

    if con_reales is False:
        return []
    ruta = _ruta_reales(reales_db)
    if not ruta.exists():
        if con_reales:
            raise click.ClickException(f"No existe la base privada de casos reales {ruta}.")
        return []
    reales = casos_vigentes(ruta)
    if categorias:
        reales = [c for c in reales if c.categoria in categorias]
    if ids:
        reales = [c for c in reales if c.id in ids]
    if reales:
        click.echo(f"Con {len(reales)} casos reales aceptados de {ruta}.")
    return reales


def _huella(casos_dir: Path, reales: list[CasoEvaluacion]) -> str:
    """The suite's fingerprint, extended with the real cases run (both runs of a promotion must match)."""
    import hashlib

    base = suite_mod.huella_suite(casos_dir)
    if not reales:
        return base
    digest = hashlib.sha256(base.encode())
    for caso in sorted(reales, key=lambda c: c.id):
        digest.update(caso.model_dump_json().encode())
    return digest.hexdigest()


def _salida_por_defecto(variante_id: str) -> Path:
    marca = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("reports") / "evaluacion" / f"{variante_id}-{marca}.json"


@cli.command()
@_CASOS
@click.option("--config", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Variante (JSON).")
@click.option("--reproduccion", is_flag=True, help="Sin modelo: reproduce los guiones de referencia (CI).")
@click.option("--categoria", "categorias", multiple=True, help="Solo estas categorías.")
@click.option("--caso", "ids", multiple=True, help="Solo estos casos.")
@click.option("--repeticiones", default=1, show_default=True, type=click.IntRange(1, 20))
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), help="Informe JSON.")
@click.option("--baseline", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Informe base.")
@click.option("--presupuesto-usd", type=Decimal, help="Detiene la ejecución al superar este coste.")
@click.option("--sin-corpus", is_flag=True, help="No carga el índice FAISS.")
@click.option("--sin-juez", is_flag=True, help="No evalúa las rúbricas (quedan como aviso en el informe).")
@click.option("--exigir-criticas", is_flag=True, help="Termina con error si falla algún caso de una categoría crítica.")
@click.option("--yes", is_flag=True, help="No pide confirmación antes de llamar al modelo.")
@click.option(
    "--motor",
    type=click.Choice(["auto", "agente", "cerebro"]),
    default="auto",
    show_default=True,
    help="auto: el cerebro del hogar solo en los casos que lo requieren; cerebro: todos los casos por el cerebro.",
)
@_CASOS_REALES
def run(
    casos_dir: Path,
    config: Path | None,
    reproduccion: bool,
    categorias: tuple[str, ...],
    ids: tuple[str, ...],
    repeticiones: int,
    out_path: Path | None,
    baseline: Path | None,
    presupuesto_usd: Decimal | None,
    sin_corpus: bool,
    sin_juez: bool,
    exigir_criticas: bool,
    yes: bool,
    motor: str,
    con_reales: bool | None,
    reales_db: str | None,
) -> None:
    """Ejecuta el asistente central con una variante sobre la suite y escribe el informe."""
    from asistente.nucleo.evaluacion.runner import cargar_informe, comparar, evaluar, formatear, guardar

    casos = _seleccion(casos_dir, categorias, ids)
    reales = [] if reproduccion else _casos_reales(con_reales, reales_db, categorias, ids)
    casos = casos + reales
    variante = _variante(config)
    if not reproduccion and not yes:
        click.confirm(
            f"Se ejecutarán {len(casos)} casos × {repeticiones} con la variante «{variante.id}» contra el modelo "
            "(varias llamadas por caso: agente, especialistas, verificador y juez). ¿Continuar?",
            abort=True,
        )
    entorno = _entorno(reproduccion, presupuesto=presupuesto_usd, sin_corpus=sin_corpus, sin_juez=sin_juez)
    entorno.motor = motor  # type: ignore[assignment]
    informe = evaluar(
        casos,
        variante,
        entorno,
        config=suite_mod.cargar_config(casos_dir),
        suite_huella=_huella(casos_dir, reales),
        repeticiones=repeticiones,
        progreso=_progreso,
        casos_reales=len(reales),
    )
    if baseline is not None:
        informe.comparacion = comparar(informe, cargar_informe(baseline))
    destino = out_path or (None if reproduccion else _salida_por_defecto(variante.id))
    if destino is not None:
        guardar(informe, destino)
        click.echo(f"Informe: {destino}")
    click.echo(formatear(informe))
    if reproduccion and informe.resumen.aprobados < informe.resumen.total:
        sys.exit(1)
    if reales:
        click.echo(f"Aviso: el informe incluye {len(reales)} casos reales (privados): no lo subas al repositorio.")
    if exigir_criticas and any(r.critica and not r.aprobado for r in informe.casos):
        click.echo("Falla algún caso de una categoría crítica.", err=True)
        sys.exit(1)


@cli.command()
@_CASOS
@click.option("--config", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--repeticiones", default=1, show_default=True, type=click.IntRange(1, 20))
@click.option("--presupuesto-usd", type=Decimal, help="Para cada una de las dos ejecuciones.")
@click.option("--out-dir", type=click.Path(file_okay=False, path_type=Path), default=Path("reports/evaluacion"))
@click.option("--sin-corpus", is_flag=True)
@click.option("--yes", is_flag=True)
@click.option(
    "--min-casos", type=click.IntRange(1, 10_000), help="Casos comparables mínimos (EVALUACION_GATE_MIN_CASOS)."
)
@_CASOS_REALES
def promote(
    casos_dir: Path,
    config: Path,
    repeticiones: int,
    presupuesto_usd: Decimal | None,
    out_dir: Path,
    sin_corpus: bool,
    yes: bool,
    min_casos: int | None,
    con_reales: bool | None,
    reales_db: str | None,
) -> None:
    """Base frente a candidata: aplica la candidata solo si NO empeora (con estadística pareada y sin regresiones
    críticas) y cabe en el presupuesto de coste y latencia. Escribe la decisión y su evidencia (JSON y resumen)."""
    from asistente.nucleo.evaluacion.gate import Umbrales, guardar_informe, promover, resumen_es
    from asistente.nucleo.evaluacion.runner import formatear, guardar
    from asistente.nucleo.evaluacion.variantes import ruta_activa, variante_activa

    candidata = _variante(config)
    base = variante_activa()
    reales = _casos_reales(con_reales, reales_db)
    casos = suite_mod.cargar_casos(casos_dir) + reales
    umbrales = Umbrales.desde_settings()
    if min_casos is not None:
        umbrales = umbrales.model_copy(update={"min_casos": min_casos})
    if not yes:
        click.confirm(
            f"Se ejecutará la suite ({len(casos)} casos × {repeticiones}) dos veces: base «{base.id}» y candidata "
            f"«{candidata.id}», contra el modelo. ¿Continuar?",
            abort=True,
        )
    decision = promover(
        candidata,
        _entorno(False, presupuesto=presupuesto_usd, sin_corpus=sin_corpus, sin_juez=False),
        casos,
        config=suite_mod.cargar_config(casos_dir),
        suite_huella=_huella(casos_dir, reales),
        base=base,
        repeticiones=repeticiones,
        progreso=lambda etiqueta, caso, resultado: _progreso(caso, resultado),
        umbrales=umbrales,
        casos_reales=len(reales),
    )
    marca = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    guardar(decision.base, out_dir / f"promote-{marca}-base-{base.id}.json")
    guardar(decision.candidata, out_dir / f"promote-{marca}-candidata-{candidata.id}.json")
    click.echo(formatear(decision.base))
    click.echo("")
    click.echo(formatear(decision.candidata))
    click.echo("")
    if decision.informe is not None:
        json_path, texto_path = guardar_informe(decision.informe, out_dir, marca=marca)
        click.echo(resumen_es(decision.informe))
        click.echo(f"Decisión y evidencia: {json_path} ({texto_path})")
    else:
        for motivo in decision.motivos:
            click.echo(("Aplicada: " if decision.aplicada else "Rechazada: ") + motivo)
    if decision.aplicada:
        click.echo(f"Variante activa escrita en {decision.ruta_activa or ruta_activa()}.")
        if decision.regresiones:
            click.echo("Regresiones en categorías no críticas: " + ", ".join(decision.regresiones))
        return
    sys.exit(1)


@cli.command()
def variante() -> None:
    """Muestra la variante activa y la integridad de los prompts registrados."""
    from asistente.nucleo.evaluacion.variantes import (
        comprobar_integrados,
        desajustes,
        es_integrada,
        rol_disponible,
        ruta_activa,
        variante_activa,
    )

    activa = variante_activa()
    click.echo(f"Variante activa: {activa.id} ({ruta_activa()}{'' if ruta_activa().exists() else ', sin fichero'})")
    click.echo(f"  {activa.descripcion}")
    for rol, version in activa.prompts.items():
        marca = " (integrado)" if es_integrada(rol, version) else ""
        click.echo(f"  prompt {rol}: {version}{marca}" + ("" if rol_disponible(rol) else " — no disponible aquí"))
    click.echo(f"  proveedor: {activa.proveedor or settings.LLM_PROVIDER}; modelo: {activa.modelo or 'el de settings'}")
    for problema in comprobar_integrados():
        click.echo(f"Error: {problema}", err=True)
    for aviso in desajustes(activa):
        click.echo(f"Aviso: {aviso}")


@cli.group()
def prompts() -> None:
    """Versiones registradas de los prompts integrados (agent/prompts.py y los del cerebro, el repaso y la memoria)."""


@prompts.command("estado")
def prompts_estado() -> None:
    """Versión vigente de cada prompt y si el texto del código coincide con ella."""
    from asistente.nucleo.evaluacion.variantes import (
        ROLES,
        RolNoDisponibleError,
        cargar_registro,
        comprobar_integrados,
        huella,
        texto_integrado,
    )

    registro = cargar_registro()
    for rol in ROLES:
        try:
            actual = huella(texto_integrado(rol))
        except RolNoDisponibleError:
            click.echo(f"{rol}: no disponible aquí (su paquete no está instalado)")
            continue
        if not registro[rol]:
            click.echo(f"{rol}: SIN REGISTRAR")
            continue
        ultima = registro[rol][-1]
        estado = "coincide" if actual == ultima.huella else "HA CAMBIADO"
        click.echo(f"{rol}: {ultima.version} ({ultima.registrado_el}) — {estado}")
    problemas = comprobar_integrados()
    if problemas:
        for problema in problemas:
            click.echo(f"Error: {problema}", err=True)
        sys.exit(1)


def _roles() -> list[str]:
    from asistente.nucleo.evaluacion.variantes import ROLES

    return list(ROLES)


@prompts.command("registrar")
@click.option("--rol", required=True, type=click.Choice(_roles()))
@click.option(
    "--informe",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Decisión de promote (…-decision.json) o informe de una candidata con el texto nuevo que no empeora.",
)
@click.option("--sin-evaluar", "motivo", help="Registrar sin evaluación; el motivo queda escrito en el registro.")
def prompts_registrar(rol: str, informe: Path | None, motivo: str | None) -> None:
    """Registra como versión nueva el texto actual de un prompt de prompts.py."""
    from asistente.nucleo.evaluacion.runner import cargar_informe
    from asistente.nucleo.evaluacion.variantes import huella, registrar_version, texto_integrado

    if (informe is None) == (motivo is None):
        raise click.ClickException('Indica --informe <informe.json> o --sin-evaluar "<motivo>" (uno de los dos).')
    evaluacion = None
    if informe is not None and informe.name.endswith("-decision.json"):
        from asistente.nucleo.evaluacion.gate import InformePromocion

        decision = InformePromocion.model_validate_json(informe.read_text(encoding="utf-8"))
        if not decision.promovida:
            raise click.ClickException("La decisión no promovió la candidata: no respalda el cambio.")
        candidata = informe.with_name(informe.name.replace("-decision.json", f"-candidata-{decision.candidata}.json"))
        if not candidata.exists():
            raise click.ClickException(f"Falta el informe de la candidata junto a la decisión ({candidata.name}).")
        if cargar_informe(candidata).huellas_prompts.get(rol) != huella(texto_integrado(rol)):  # type: ignore[arg-type]
            raise click.ClickException("El informe no evaluó este texto del prompt (la huella no coincide).")
        evaluacion = {
            "variante": decision.candidata,
            "frente_a": decision.base,
            "suite_version": decision.suite_version,
            "suite_huella": decision.suite_huella,
            "decision": decision.decision,
            "n_pares": decision.estadisticos.n_pares,
            "ic": [decision.estadisticos.ic_inferior, decision.estadisticos.ic_superior],
        }
    elif informe is not None:
        datos = cargar_informe(informe)
        actual = huella(texto_integrado(rol))  # type: ignore[arg-type]
        if datos.huellas_prompts.get(rol) != actual:
            raise click.ClickException("El informe no evaluó este texto del prompt (la huella no coincide).")
        if datos.modo != "modelo" or datos.incompleto or datos.comparacion is None:
            raise click.ClickException("El informe debe ser de una ejecución completa con modelo y con comparación.")
        empeoradas = [
            nombre
            for nombre, cat in datos.resumen.por_categoria.items()
            if cat.critica and datos.comparacion.delta_por_categoria.get(nombre, 0.0) < 0
        ]
        if empeoradas or datos.comparacion.delta_tasa <= 0:
            raise click.ClickException(
                "El informe no respalda el cambio: "
                + (f"empeora {', '.join(empeoradas)}" if empeoradas else "no mejora en conjunto")
                + "."
            )
        evaluacion = {
            "variante": datos.variante,
            "frente_a": datos.comparacion.frente_a,
            "suite_version": datos.suite_version,
            "suite_huella": datos.suite_huella,
            "tasa": datos.resumen.tasa,
            "delta_tasa": datos.comparacion.delta_tasa,
            "modelo": f"{datos.proveedor}/{datos.modelo}",
        }
    try:
        nueva = registrar_version(
            rol,  # type: ignore[arg-type]
            nota=motivo or f"Evaluado: {informe}",
            evaluacion=evaluacion,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Registrada {nueva.version} ({nueva.huella[:12]}).")


@prompts.command("sincronizar")
@click.option("--motivo", required=True, help="Por qué se registra sin evaluación (queda escrito en el registro).")
def prompts_sincronizar(motivo: str) -> None:
    """Registra como versión nueva cada prompt de prompts.py cuyo texto ya no coincide con el registro."""
    from asistente.nucleo.evaluacion.variantes import pendientes_de_registro, registrar_version

    cambiados = pendientes_de_registro()
    if not cambiados:
        click.echo("El registro y prompts.py ya coinciden.")
        return
    for rol in cambiados:
        nueva = registrar_version(rol, nota=f"Sin evaluar: {motivo}")
        click.echo(f"Registrada {nueva.version} ({nueva.huella[:12]}).")


@cli.command("importar-dgt")
@click.option("--dataset", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--destino",
    type=click.Path(file_okay=False, path_type=Path),
    default=suite_mod.CASOS_DIR / "dgt",
    show_default=True,
)
@click.option("--sobrescribir", is_flag=True)
def importar_dgt_cmd(dataset: Path, destino: Path, sobrescribir: bool) -> None:
    """Convierte el conjunto de la DGT (eval_agent build) en casos de la suite (categoría «dgt»)."""
    from asistente.nucleo.evaluacion.importar import importar_dgt

    escritos = importar_dgt(dataset, destino, sobrescribir=sobrescribir)
    click.echo(f"{len(escritos)} casos escritos en {destino}.")


# --------------------------------------------------------------------------- #
# Feedback: signals and the review queue
# --------------------------------------------------------------------------- #


@cli.command()
@_DB
def senales(db_path: str) -> None:
    """Señales automáticas de todos los expedientes (solo recuentos, sin contenido)."""
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.evaluacion.senales import agregar, senales_de_auditoria

    cases = SQLiteCaseStateStore(db_path)
    todas = []
    for case in cases.list_all_cases():
        todas.extend(senales_de_auditoria(cases.list_audit_events(case.case_id)))
    click.echo(json.dumps(agregar(todas), ensure_ascii=False, indent=2))


@cli.group()
def casos() -> None:
    """Revisión de los casos que los usuarios han guardado para evaluación (nunca automática)."""


def _stores(db_path: str):  # type: ignore[no-untyped-def]
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.evaluacion.captura import FeedbackStore

    cases = SQLiteCaseStateStore(db_path)
    return cases, FeedbackStore(db_path)


@casos.command()
@_DB
def estadisticas(db_path: str) -> None:
    """Recuentos de las valoraciones (sin contenido)."""
    _cases, store = _stores(db_path)
    click.echo(json.dumps(store.estadisticas(), ensure_ascii=False, indent=2))


@casos.command()
@_DB
def pendientes(db_path: str) -> None:
    """Casos guardados con consentimiento que esperan revisión."""
    _cases, store = _stores(db_path)
    filas = store.pendientes()
    if not filas:
        click.echo("No hay casos pendientes de revisión.")
    for registro in filas:
        click.echo(
            f"{registro.id}  {registro.creado_el:%Y-%m-%d}  {registro.tipo or '—'}  "
            f"etiquetas: {', '.join(registro.etiquetas) or '—'}"
        )


def _registro(store, feedback_id: str):  # type: ignore[no-untyped-def]
    registro = store.get(feedback_id)
    if registro is None:
        raise click.ClickException(f"No existe la valoración {feedback_id} (o el usuario la retiró).")
    return registro


@casos.command()
@_DB
@click.argument("feedback_id")
def ver(db_path: str, feedback_id: str) -> None:
    """Muestra un caso guardado (pregunta, respuesta, corrección, traza e instantánea)."""
    _cases, store = _stores(db_path)
    registro = _registro(store, feedback_id)
    contenido = store.contenido(registro)
    click.echo(registro.model_dump_json(indent=2))
    if contenido is None:
        click.echo("Sin contenido guardado (solo valoración, o retirado por el usuario).")
        return
    click.echo(json.dumps(contenido, ensure_ascii=False, indent=2))


@casos.command()
@_DB
@click.argument("feedback_id")
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
def borrador(db_path: str, feedback_id: str, out_path: Path) -> None:
    """Escribe un borrador de caso para revisar y editar (no entra en la suite)."""
    from asistente.nucleo.evaluacion.captura import borrador_de_caso

    _cases, store = _stores(db_path)
    registro = _registro(store, feedback_id)
    contenido = store.contenido(registro)
    if registro.estado != "pendiente" or contenido is None:
        raise click.ClickException("Solo se puede preparar un borrador de un caso pendiente con contenido guardado.")
    caso, notas = borrador_de_caso(registro, contenido)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(caso.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    click.echo(f"Borrador en {out_path}. Revísalo antes de promoverlo:")
    for nota in notas:
        click.echo(f"  - {nota}")


@casos.command()
@_DB
@_CASOS
@click.argument("feedback_id")
@click.option("--desde", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--revisor", required=True, help="Quién ha revisado el caso.")
@click.option("--confirmo-anonimizado", is_flag=True, help="Confirmo que el caso no contiene datos personales.")
def promover(
    db_path: str, casos_dir: Path, feedback_id: str, desde: Path, revisor: str, confirmo_anonimizado: bool
) -> None:
    """Acepta un caso revisado y anonimizado (categoría y expectativas del fichero) en la base PRIVADA de casos
    reales (nunca en el repositorio): se evalúa en cada ejecución y se borra con la cuenta del usuario."""
    from asistente.nucleo.evaluacion.captura import comprobar_anonimizado, referencia_anonima, registrar_revision
    from asistente.nucleo.evaluacion.modelos import Origen

    if not confirmo_anonimizado:
        raise click.ClickException("Hace falta --confirmo-anonimizado: revisa que el caso no identifica a nadie.")
    cases, store = _stores(db_path)
    registro = _registro(store, feedback_id)
    if registro.estado != "pendiente":
        raise click.ClickException(f"La valoración está «{registro.estado}», no pendiente.")
    try:
        caso = CasoEvaluacion.model_validate_json(desde.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise click.ClickException(f"El caso no es válido: {exc}") from exc
    problemas = comprobar_anonimizado(caso)
    if problemas:
        raise click.ClickException("El caso parece contener datos personales:\n- " + "\n- ".join(problemas))
    caso = caso.model_copy(
        update={
            "origen": Origen(
                tipo="feedback",
                referencia=referencia_anonima(registro.id),
                etiquetas=list(registro.etiquetas),
                revisado_por=revisor,
                revisado_el=date.today(),
            )
        }
    )
    config = suite_mod.cargar_config(casos_dir)
    if caso.categoria not in config.categorias:
        raise click.ClickException(f"Categoría desconocida «{caso.categoria}».")
    validacion = suite_mod.validar(casos_dir, casos=[caso])
    if not validacion.ok:
        raise click.ClickException("El caso no se puede evaluar:\n- " + "\n- ".join(validacion.errores))
    existentes = {c.id for c in suite_mod.cargar_casos(casos_dir)}
    if caso.id in existentes:
        raise click.ClickException(f"Ya hay un caso con id «{caso.id}».")
    # A real case never enters the repository: it goes to the private database, with the user it came from.
    from asistente.nucleo.evaluacion import privado, revision

    reales = privado.store_de(db_path)
    if reales.get(caso.id) is not None:
        raise click.ClickException(f"Ya hay un caso real aceptado con id «{caso.id}».")
    case = cases.get_case(registro.case_id) if registro.case_id else None
    candidatos = revision.candidatos_para(db_path)
    de_feedback = candidatos.de_feedback(registro.id)
    reales.guardar(
        privado.CasoReal(
            id=caso.id,
            case_id=registro.case_id,
            user_id=case.user_id if case is not None else None,
            candidato_id=de_feedback[0].id if de_feedback else registro.id,
            grupo=de_feedback[0].grupo if de_feedback else referencia_anonima(registro.id),
            feedback_id=registro.id,
            caso=caso,
            aceptado_el=datetime.now(UTC),
            aceptado_por="operador",
        )
    )
    for candidato in de_feedback:
        if candidato.estado == "pendiente":
            candidato.estado, candidato.caso_aceptado = "aceptado", caso.id
            candidato.revisado_el, candidato.revisado_por = datetime.now(UTC), "operador"
            candidatos.actualizar(candidato)
    registro.estado = "promovido"
    registro.revisado_el = datetime.now(UTC)
    registro.caso_suite = caso.id
    store.actualizar(registro)
    registrar_revision(cases, registro)
    click.echo(f"Caso {caso.id} aceptado en la base privada de casos reales: {reales.db_path}")


@casos.command()
@_DB
@click.argument("feedback_id")
def descartar(db_path: str, feedback_id: str) -> None:
    """Descarta un caso guardado (su contenido sigue en el expediente del usuario hasta que lo borre)."""
    from asistente.nucleo.evaluacion.captura import registrar_revision

    cases, store = _stores(db_path)
    registro = _registro(store, feedback_id)
    registro.estado = "descartado"
    registro.revisado_el = datetime.now(UTC)
    store.actualizar(registro)
    registrar_revision(cases, registro)
    click.echo(f"Valoración {feedback_id} descartada.")


# --------------------------------------------------------------------------- #
# The review queue (candidate cases, masked): the operator's side of /evaluacion/revision
# --------------------------------------------------------------------------- #


def _candidatos(db_path: str):  # type: ignore[no-untyped-def]
    from asistente.nucleo.decision.state_store_sqlite import SQLiteCaseStateStore
    from asistente.nucleo.evaluacion.revision import CandidatoStore

    return SQLiteCaseStateStore(db_path), CandidatoStore(db_path)


@casos.command()
@_DB
def detectar(db_path: str) -> None:
    """Convierte en candidatos las señales débiles de los expedientes cuyo dueño lo ha consentido."""
    from asistente.nucleo.evaluacion.revision import detectar_en_caso

    cases, store = _candidatos(db_path)
    nuevos = 0
    for consentimiento in store.consentimientos():
        case = cases.get_case(consentimiento.case_id)
        if case is not None:
            nuevos += len(detectar_en_caso(store, cases, case, user_id=case.user_id))
    click.echo(f"{nuevos} candidatos nuevos de {len(store.consentimientos())} expedientes con aprendizaje activado.")


@casos.command()
@_DB
@click.option("--estado", type=click.Choice(["pendiente", "aceptado", "rechazado"]), default="pendiente")
def cola(db_path: str, estado: str) -> None:
    """La cola de revisión: candidatos agrupados (sin duplicados), por prioridad."""
    from asistente.nucleo.evaluacion.revision import ETIQUETAS_FALLO, agrupar

    _cases, store = _candidatos(db_path)
    grupos = agrupar(store.todos(), estado=estado)  # type: ignore[arg-type]
    if not grupos:
        click.echo(f"No hay casos candidatos en estado «{estado}».")
    for g in grupos:
        r = g.representante
        click.echo(
            f"{r.id}  prioridad {g.prioridad:>6.1f}  ×{g.frecuencia}  {ETIQUETAS_FALLO[r.tipo_fallo]}  "
            f"[{r.categoria_sugerida}{', crítica' if r.critica else ''}]  {r.origen}"
        )
        click.echo(f"    «{r.contenido.pregunta[:120]}»")
        click.echo(f"    {'; '.join(g.motivos_prioridad)}")


def _candidato(store, candidato_id: str):  # type: ignore[no-untyped-def]
    candidato = store.get(candidato_id)
    if candidato is None:
        raise click.ClickException(f"No existe el caso candidato {candidato_id}.")
    return candidato


@casos.command()
@_DB
@click.argument("candidato_id")
def candidato(db_path: str, candidato_id: str) -> None:
    """Muestra un candidato (enmascarado): conversación, plan del cerebro, qué falló y qué se ocultó."""
    _cases, store = _candidatos(db_path)
    click.echo(_candidato(store, candidato_id).model_dump_json(indent=2))


@casos.command()
@_DB
@click.argument("candidato_id")
@click.option(
    "--desde",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Edición (JSON): categoria, esperado (especialistas, citas, cifras prohibidas, cifra esperada…), hechos…",
)
@click.option(
    "--aunque-no-reproduzca", is_flag=True, help="Aceptar aunque la respuesta original pase las expectativas."
)
def aceptar(db_path: str, candidato_id: str, desde: Path, aunque_no_reproduzca: bool) -> None:
    """Acepta un candidato como caso real (base privada) con el comportamiento esperado del fichero."""
    from asistente.nucleo.evaluacion.redaccion import nombres_de_perfil
    from asistente.nucleo.evaluacion.revision import AceptacionRechazadaError, EdicionCaso
    from asistente.nucleo.evaluacion.revision import aceptar as aceptar_candidato

    cases, store = _candidatos(db_path)
    elegido = _candidato(store, candidato_id)
    try:
        edicion = EdicionCaso.model_validate_json(desde.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise click.ClickException(f"La edición no es válida: {exc}") from exc
    if aunque_no_reproduzca:
        edicion = edicion.model_copy(update={"aunque_no_reproduzca": True})
    case = cases.get_case(elegido.case_id) if elegido.case_id else None
    nombres = nombres_de_perfil([(f.name, f.value) for f in case.facts] if case is not None else [])
    try:
        real = aceptar_candidato(store, elegido, edicion, revisor="operador", cases=cases, nombres=nombres)
    except AceptacionRechazadaError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Aceptado como {real.id} ({real.caso.categoria}) en la base privada de casos reales.")
    for aviso in real.avisos:
        click.echo(f"Aviso: {aviso}")


@casos.command()
@_DB
@click.argument("candidato_id")
@click.option("--motivo", required=True, help="Por qué no es un error del asistente (o no sirve como caso).")
def rechazar(db_path: str, candidato_id: str, motivo: str) -> None:
    """Rechaza un candidato (y sus duplicados) con un motivo."""
    from asistente.nucleo.evaluacion.revision import AceptacionRechazadaError
    from asistente.nucleo.evaluacion.revision import rechazar as rechazar_candidato

    cases, store = _candidatos(db_path)
    try:
        cambiados = rechazar_candidato(store, _candidato(store, candidato_id), motivo, revisor="operador", cases=cases)
    except AceptacionRechazadaError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{len(cambiados)} candidato(s) rechazado(s).")


# --------------------------------------------------------------------------- #
# Accepted real cases (private): list, negative control, replay against a variant
# --------------------------------------------------------------------------- #


@cli.group("casos-reales")
def casos_reales() -> None:
    """Casos reales aceptados (base privada, nunca en el repositorio)."""


def _reales_de(reales_db: str | None, desde: Path | None):  # type: ignore[no-untyped-def]
    """Accepted real cases from the private database, or from a JSON list (synthetic fixtures in CI)."""
    from asistente.nucleo.evaluacion.privado import CasoReal, store_para

    if desde is not None:
        datos = json.loads(desde.read_text(encoding="utf-8"))
        return [CasoReal.model_validate(d) for d in datos]
    ruta = _ruta_reales(reales_db)
    if not ruta.exists():
        raise click.ClickException(f"No existe la base privada de casos reales {ruta}.")
    return store_para(ruta).vigentes()


_DESDE_REALES = click.option(
    "--desde",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Lista JSON de casos reales (p. ej. los sintéticos de las pruebas) en lugar de la base privada.",
)


@casos_reales.command("listar")
@click.option("--casos-reales-db", "reales_db", default=None)
def reales_listar(reales_db: str | None) -> None:
    """Los casos reales aceptados (sin el contenido)."""
    from asistente.nucleo.evaluacion.privado import store_para

    ruta = _ruta_reales(reales_db)
    if not ruta.exists():
        click.echo("No hay casos reales aceptados.")
        return
    for real in store_para(ruta).todos():
        estado = "vigente" if real.vigente else f"retirado ({real.motivo_baja})"
        click.echo(f"{real.id}  {real.caso.categoria}  {real.aceptado_el:%Y-%m-%d}  {real.aceptado_por}  {estado}")


@casos_reales.command("comprobar")
@click.option("--casos-reales-db", "reales_db", default=None)
@_DESDE_REALES
@_CASOS
def reales_comprobar(reales_db: str | None, desde: Path | None, casos_dir: Path) -> None:
    """Sin modelo: cada caso real debe seguir detectando el error que se dio (reproduce lo capturado)."""
    from asistente.nucleo.evaluacion.reproduccion import reproducir_capturas
    from asistente.nucleo.evaluacion.runner import formatear

    reales = _reales_de(reales_db, desde)
    sin_captura = [r.id for r in reales if r.guion_capturado is None]
    informe, siguen = reproducir_capturas(
        [(r.caso, r.guion_capturado) for r in reales],
        config=suite_mod.cargar_config(casos_dir),
        suite_huella="casos-reales",
    )
    click.echo(formatear(informe))
    for caso_id in sin_captura:
        click.echo(f"Aviso: {caso_id} no guarda la respuesta original: no se puede reproducir.")
    if siguen:
        click.echo("Ya no detectan el error que se dio (aprueban lo capturado): " + ", ".join(siguen), err=True)
        sys.exit(1)
    click.echo(f"{len(reales) - len(sin_captura)} casos reales siguen detectando su error.")


@casos_reales.command("reproducir")
@click.option("--config", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Variante candidata.")
@click.option("--casos-reales-db", "reales_db", default=None)
@_DESDE_REALES
@_CASOS
@click.option("--modelo-real", is_flag=True, help="Ejecutar contra el modelo (cuesta dinero; exige --presupuesto-usd).")
@click.option("--presupuesto-usd", type=Decimal, help="Tope de gasto de la ejecución (obligatorio con --modelo-real).")
@click.option("--sin-corpus", is_flag=True)
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--yes", is_flag=True)
def reales_reproducir(
    config: Path | None,
    reales_db: str | None,
    desde: Path | None,
    casos_dir: Path,
    modelo_real: bool,
    presupuesto_usd: Decimal | None,
    sin_corpus: bool,
    out_path: Path | None,
    yes: bool,
) -> None:
    """Vuelve a ejecutar los casos reales con una variante: contra el modelo solo con --modelo-real y un tope en USD."""
    from asistente.nucleo.evaluacion.runner import evaluar, formatear, guardar

    if not modelo_real:
        raise click.ClickException(
            "Sin --modelo-real no se llama a ningún modelo: usa `casos-reales comprobar` (reproducción sin modelo)."
        )
    if presupuesto_usd is None or presupuesto_usd <= 0:
        raise click.ClickException("Con --modelo-real hace falta --presupuesto-usd (un tope de gasto positivo).")
    reales = _reales_de(reales_db, desde)
    variante = _variante(config)
    if not yes:
        click.confirm(
            f"Se ejecutarán {len(reales)} casos reales con la variante «{variante.id}» contra el modelo "
            f"(tope {presupuesto_usd} USD). ¿Continuar?",
            abort=True,
        )
    informe = evaluar(
        [r.caso for r in reales],
        variante,
        _entorno(False, presupuesto=presupuesto_usd, sin_corpus=sin_corpus, sin_juez=False),
        config=suite_mod.cargar_config(casos_dir),
        suite_huella=_huella(casos_dir, [r.caso for r in reales]),
        progreso=_progreso,
        casos_reales=len(reales),
    )
    if out_path is not None:
        guardar(informe, out_path)
        click.echo(f"Informe: {out_path}")
    click.echo(formatear(informe))


if __name__ == "__main__":
    cli()
