"""Continuous evaluation of the central assistant: run the suite, gate prompt/model changes, review cases.

    # Model-free part (every pull request): schema, expected figures, reference scripts replayed.
    uv run python -m hacienda_gpt.cli.evaluacion validar
    uv run python -m hacienda_gpt.cli.evaluacion run --reproduccion

    # With a model (needs a key; asks before spending): the active variant, or a candidate.
    uv run python -m hacienda_gpt.cli.evaluacion run --out reports/evaluacion/base.json
    uv run python -m hacienda_gpt.cli.evaluacion run --config variantes/candidata.json --baseline reports/evaluacion/base.json

    # Gate: baseline vs candidate; writes the active variant only if the candidate improves.
    uv run python -m hacienda_gpt.cli.evaluacion promote --config variantes/candidata.json

    # Users' feedback: automatic signals, review queue, promotion of a reviewed case to the suite.
    uv run python -m hacienda_gpt.cli.evaluacion senales
    uv run python -m hacienda_gpt.cli.evaluacion casos pendientes
    uv run python -m hacienda_gpt.cli.evaluacion casos borrador fb_… --out /tmp/borrador.json
    uv run python -m hacienda_gpt.cli.evaluacion casos promover fb_… --desde /tmp/borrador.json \\
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

from hacienda_gpt import settings
from hacienda_gpt.evaluacion import suite as suite_mod
from hacienda_gpt.evaluacion.modelos import CasoEvaluacion

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
    from hacienda_gpt.evaluacion.variantes import comprobar_integrados, desajustes, variante_activa

    informe = suite_mod.validar(casos_dir)
    for aviso in desajustes(variante_activa()):
        click.echo(f"Aviso: {aviso}")
    problemas = list(informe.errores) + comprobar_integrados()
    click.echo(
        f"{informe.casos} casos ({informe.deterministicos} sin juez, {informe.con_guion} con guion de referencia)."
    )
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
    from hacienda_gpt.evaluacion.runner import (
        Entorno,
        buscador_por_defecto,
        proveedor_real,
        proveedor_reproduccion,
    )
    from hacienda_gpt.ops.consumo import ConsumoStore, Presupuesto, almacen_por_defecto

    if reproduccion:
        temporal = Path(tempfile.mkdtemp(prefix="evaluacion-")) / "consumo.sqlite3"
        return Entorno(proveedor=proveedor_reproduccion, consumo=ConsumoStore(str(temporal)), modo="reproduccion")
    from hacienda_gpt.agent.search import CorpusSearcher
    from hacienda_gpt.evaluacion.cifras import SinCorpus

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
        from hacienda_gpt.agent.providers import create_provider

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
    from hacienda_gpt.evaluacion.variantes import VarianteInvalidaError, cargar_variante, variante_activa

    if config is None:
        return variante_activa()
    try:
        return cargar_variante(config)
    except VarianteInvalidaError as exc:
        raise click.ClickException(str(exc)) from exc


def _progreso(caso: CasoEvaluacion, resultado) -> None:  # type: ignore[no-untyped-def]
    marca = "✓" if resultado.aprobado else "✗"
    click.echo(f"  {marca} {caso.id} [{caso.categoria}] {resultado.ejecucion.latencia_s:.1f} s")


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
) -> None:
    """Ejecuta el asistente central con una variante sobre la suite y escribe el informe."""
    from hacienda_gpt.evaluacion.runner import cargar_informe, comparar, evaluar, formatear, guardar

    casos = _seleccion(casos_dir, categorias, ids)
    variante = _variante(config)
    if not reproduccion and not yes:
        click.confirm(
            f"Se ejecutarán {len(casos)} casos × {repeticiones} con la variante «{variante.id}» contra el modelo "
            "(varias llamadas por caso: agente, especialistas, verificador y juez). ¿Continuar?",
            abort=True,
        )
    entorno = _entorno(reproduccion, presupuesto=presupuesto_usd, sin_corpus=sin_corpus, sin_juez=sin_juez)
    informe = evaluar(
        casos,
        variante,
        entorno,
        config=suite_mod.cargar_config(casos_dir),
        suite_huella=suite_mod.huella_suite(casos_dir),
        repeticiones=repeticiones,
        progreso=_progreso,
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
def promote(
    casos_dir: Path,
    config: Path,
    repeticiones: int,
    presupuesto_usd: Decimal | None,
    out_dir: Path,
    sin_corpus: bool,
    yes: bool,
) -> None:
    """Base frente a candidata: aplica la candidata solo si mejora (y no empeora ninguna categoría crítica)."""
    from hacienda_gpt.evaluacion.gate import promover
    from hacienda_gpt.evaluacion.runner import formatear, guardar
    from hacienda_gpt.evaluacion.variantes import ruta_activa, variante_activa

    candidata = _variante(config)
    base = variante_activa()
    casos = suite_mod.cargar_casos(casos_dir)
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
        suite_huella=suite_mod.huella_suite(casos_dir),
        base=base,
        repeticiones=repeticiones,
        progreso=lambda etiqueta, caso, resultado: _progreso(caso, resultado),
    )
    marca = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    guardar(decision.base, out_dir / f"promote-{marca}-base-{base.id}.json")
    guardar(decision.candidata, out_dir / f"promote-{marca}-candidata-{candidata.id}.json")
    click.echo(formatear(decision.base))
    click.echo("")
    click.echo(formatear(decision.candidata))
    click.echo("")
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
    from hacienda_gpt.evaluacion.variantes import (
        INTEGRADOS,
        comprobar_integrados,
        desajustes,
        ruta_activa,
        variante_activa,
    )

    activa = variante_activa()
    click.echo(f"Variante activa: {activa.id} ({ruta_activa()}{'' if ruta_activa().exists() else ', sin fichero'})")
    click.echo(f"  {activa.descripcion}")
    for rol, version in activa.prompts.items():
        click.echo(f"  prompt {rol}: {version}" + (" (integrado)" if version == INTEGRADOS[rol].version else ""))
    click.echo(f"  proveedor: {activa.proveedor or settings.LLM_PROVIDER}; modelo: {activa.modelo or 'el de settings'}")
    for problema in comprobar_integrados():
        click.echo(f"Error: {problema}", err=True)
    for aviso in desajustes(activa):
        click.echo(f"Aviso: {aviso}")


@cli.group()
def prompts() -> None:
    """Versiones registradas de los prompts integrados (hacienda_gpt/agent/prompts.py)."""


@prompts.command("estado")
def prompts_estado() -> None:
    """Versión vigente de cada prompt y si el texto de prompts.py coincide con ella."""
    from hacienda_gpt.evaluacion.variantes import ROLES, cargar_registro, comprobar_integrados, huella, texto_integrado

    registro = cargar_registro()
    for rol in ROLES:
        ultima = registro[rol][-1]
        estado = "coincide" if huella(texto_integrado(rol)) == ultima.huella else "HA CAMBIADO"
        click.echo(f"{rol}: {ultima.version} ({ultima.registrado_el}) — {estado}")
    problemas = comprobar_integrados()
    if problemas:
        for problema in problemas:
            click.echo(f"Error: {problema}", err=True)
        sys.exit(1)


@prompts.command("registrar")
@click.option("--rol", required=True, type=click.Choice(["agente", "modulos", "especialista"]))
@click.option(
    "--informe",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Informe (run --baseline o promote) de una candidata con el texto nuevo, que mejora sin empeorar lo crítico.",
)
@click.option("--sin-evaluar", "motivo", help="Registrar sin evaluación; el motivo queda escrito en el registro.")
def prompts_registrar(rol: str, informe: Path | None, motivo: str | None) -> None:
    """Registra como versión nueva el texto actual de un prompt de prompts.py."""
    from hacienda_gpt.evaluacion.runner import cargar_informe
    from hacienda_gpt.evaluacion.variantes import huella, registrar_version, texto_integrado

    if (informe is None) == (motivo is None):
        raise click.ClickException('Indica --informe <informe.json> o --sin-evaluar "<motivo>" (uno de los dos).')
    evaluacion = None
    if informe is not None:
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
    from hacienda_gpt.evaluacion.variantes import INTEGRADOS, ROLES, huella, registrar_version, texto_integrado

    cambiados = [rol for rol in ROLES if huella(texto_integrado(rol)) != INTEGRADOS[rol].huella]
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
    from hacienda_gpt.evaluacion.importar import importar_dgt

    escritos = importar_dgt(dataset, destino, sobrescribir=sobrescribir)
    click.echo(f"{len(escritos)} casos escritos en {destino}.")


# --------------------------------------------------------------------------- #
# Feedback: signals and the review queue
# --------------------------------------------------------------------------- #


@cli.command()
@_DB
def senales(db_path: str) -> None:
    """Señales automáticas de todos los expedientes (solo recuentos, sin contenido)."""
    from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
    from hacienda_gpt.evaluacion.senales import agregar, senales_de_auditoria

    cases = SQLiteCaseStateStore(db_path)
    todas = []
    for case in cases.list_all_cases():
        todas.extend(senales_de_auditoria(cases.list_audit_events(case.case_id)))
    click.echo(json.dumps(agregar(todas), ensure_ascii=False, indent=2))


@cli.group()
def casos() -> None:
    """Revisión de los casos que los usuarios han guardado para evaluación (nunca automática)."""


def _stores(db_path: str):  # type: ignore[no-untyped-def]
    from hacienda_gpt.decision.state_store_sqlite import SQLiteCaseStateStore
    from hacienda_gpt.evaluacion.captura import FeedbackStore

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
    from hacienda_gpt.evaluacion.captura import borrador_de_caso

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
    """Incorpora a la suite un caso revisado y anonimizado (categoría y expectativas del fichero)."""
    from hacienda_gpt.evaluacion.captura import comprobar_anonimizado, referencia_anonima, registrar_revision
    from hacienda_gpt.evaluacion.modelos import Origen

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
    destino = casos_dir / caso.categoria / f"{caso.id}.json"
    if destino.exists():
        raise click.ClickException(f"Ya existe {destino}.")
    existentes = {c.id for c in suite_mod.cargar_casos(casos_dir)}
    if caso.id in existentes:
        raise click.ClickException(f"Ya hay un caso con id «{caso.id}».")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(caso.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    registro.estado = "promovido"
    registro.revisado_el = datetime.now(UTC)
    registro.caso_suite = caso.id
    store.actualizar(registro)
    registrar_revision(cases, registro)
    click.echo(f"Caso {caso.id} incorporado a la suite: {destino}")


@casos.command()
@_DB
@click.argument("feedback_id")
def descartar(db_path: str, feedback_id: str) -> None:
    """Descarta un caso guardado (su contenido sigue en el expediente del usuario hasta que lo borre)."""
    from hacienda_gpt.evaluacion.captura import registrar_revision

    cases, store = _stores(db_path)
    registro = _registro(store, feedback_id)
    registro.estado = "descartado"
    registro.revisado_el = datetime.now(UTC)
    store.actualizar(registro)
    registrar_revision(cases, registro)
    click.echo(f"Valoración {feedback_id} descartada.")


if __name__ == "__main__":
    cli()
