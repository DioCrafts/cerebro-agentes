"""Loading the versioned suite and checking a run against its deterministic expectations.

The checks of the household brain (``especialistas``, ``intencion``, ``ruta``, ``flujo_dato``, ``puerta``,
``cifra_atribuida``, ``recuerdos``) read the turn's :class:`~asistente.nucleo.cerebro.contrato.InformeCerebro`
(``Ejecucion.cerebro``), never the answer's prose: what the brain planned, who it asked, what the figures gate
said. ``repaso`` reads the proactive pass's structured output. ``dictamen`` and ``invarianza`` read the household
council's normalised ruling (``Ejecucion.consejo``, :mod:`.consejo_eval`). All of them are deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from pydantic import ValidationError

from asistente.nucleo.cerebro.contrato import InformeCerebro
from asistente.nucleo.evaluacion.cifras import CalculoError, aparece, calcular_cifra, numeros_en, valor_de_cifra
from asistente.nucleo.evaluacion.modelos import (
    CasoEvaluacion,
    ConfigSuite,
    Ejecucion,
    ExpAccionPendiente,
    ExpCifra,
    ExpCifraAtribuida,
    ExpCifraAusente,
    ExpCitas,
    ExpEspecialistas,
    ExpFlujoDato,
    ExpHerramientaLlamada,
    ExpHerramientaNoLlamada,
    ExpIntencion,
    ExpModo,
    ExpModuloConsultado,
    ExpModuloNoConsultado,
    ExpPuerta,
    ExpRecuerdos,
    ExpRepaso,
    ExpRuta,
    ExpSinAccionPendiente,
    ExpSumaAusente,
    ExpTextoAusente,
    ExpTextoPresente,
)
from asistente.nucleo.evaluacion.modelos_consejo import ExpDictamen, ExpInvarianza
from asistente.nucleo.evaluacion.modulo_prueba import MODULOS_SINTETICOS
from asistente.nucleo.modulos.contrato import Modulo

CASOS_DIR = Path(__file__).resolve().parent / "casos"
CONFIG = "suite.json"
# Origins of real cases: they live in the private store (privado.py), never in the versioned suite.
ORIGENES_PRIVADOS = frozenset({"feedback", "senal"})
_MARCADOR_RE = re.compile(r"\{(cifra|fragmento):([A-Za-z0-9_]+)\}")


class SuiteInvalidaError(ValueError):
    """The suite cannot be loaded (bad JSON, duplicate id, unknown category or module). Spanish message."""


def cargar_config(directorio: Path = CASOS_DIR) -> ConfigSuite:
    ruta = directorio / CONFIG
    try:
        return ConfigSuite.model_validate_json(ruta.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise SuiteInvalidaError(f"No se puede leer la configuración de la suite {ruta}: {exc}") from exc


def ficheros_de_casos(directorio: Path = CASOS_DIR) -> list[Path]:
    return sorted(p for p in directorio.rglob("*.json") if p.name != CONFIG)


def cargar_casos(directorio: Path = CASOS_DIR, *, config: ConfigSuite | None = None) -> list[CasoEvaluacion]:
    """Every case of the suite, by id. Raises :class:`SuiteInvalidaError` with every problem found."""
    config = config or cargar_config(directorio)
    casos: list[CasoEvaluacion] = []
    problemas: list[str] = []
    vistos: dict[str, Path] = {}
    for ruta in ficheros_de_casos(directorio):
        try:
            caso = CasoEvaluacion.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            problemas.append(f"{ruta.name}: {exc}")
            continue
        if caso.id in vistos:
            problemas.append(f"{ruta.name}: id «{caso.id}» repetido (también en {vistos[caso.id].name})")
            continue
        vistos[caso.id] = ruta
        if caso.categoria not in config.categorias:
            problemas.append(f"{ruta.name}: categoría desconocida «{caso.categoria}»")
        if caso.origen.tipo in ORIGENES_PRIVADOS and directorio.resolve() == CASOS_DIR.resolve():
            problemas.append(
                f"{ruta.name}: un caso real («{caso.origen.tipo}») no puede estar en el repositorio: va a la base "
                "privada de evaluación (casos aceptados)"
            )
        try:
            modulos_de(caso)
        except SuiteInvalidaError as exc:
            problemas.append(f"{ruta.name}: {exc}")
        casos.append(caso)
    if problemas:
        raise SuiteInvalidaError("La suite tiene errores:\n- " + "\n- ".join(problemas))
    return sorted(casos, key=lambda c: c.id)


def huella_suite(directorio: Path = CASOS_DIR) -> str:
    """SHA-256 over every file of the suite: two reports are comparable only on the same suite."""
    digest = hashlib.sha256()
    for ruta in [directorio / CONFIG, *ficheros_de_casos(directorio)]:
        digest.update(ruta.relative_to(directorio).as_posix().encode())
        digest.update(b"\0")
        digest.update(ruta.read_bytes())
    return digest.hexdigest()


def modulos_de(caso: CasoEvaluacion) -> list[Modulo]:
    """The modules a case switches on (registered or synthetic). Raises :class:`SuiteInvalidaError`."""
    from asistente.nucleo.modulos.registro import registro

    modulos: list[Modulo] = []
    for clave in caso.modulos:
        modulo = MODULOS_SINTETICOS.get(clave) or registro().get(clave)
        if modulo is None:
            raise SuiteInvalidaError(f"módulo «{clave}» desconocido o desactivado")
        modulos.append(modulo)
    return modulos


def modulos_sinteticos_de(caso: CasoEvaluacion) -> list[Modulo]:
    return [MODULOS_SINTETICOS[c] for c in caso.modulos if c in MODULOS_SINTETICOS]


# --------------------------------------------------------------------------- #
# Expected figures of a case
# --------------------------------------------------------------------------- #


@dataclass
class CifrasEsperadas:
    cifras: dict[str, str] = field(default_factory=dict)
    # Sums that must not appear, one per ``suma_ausente`` expectation (None when not computable).
    sumas: list[Decimal | None] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    derivas: list[str] = field(default_factory=list)


def cifras_esperadas(caso: CasoEvaluacion) -> CifrasEsperadas:
    """Run every calculator the case's expectations name, on its snapshot. No model involved."""
    extra = modulos_sinteticos_de(caso)
    resultado = CifrasEsperadas()
    for expectativa in caso.expectativas:
        if isinstance(expectativa, ExpCifra):
            try:
                cifra = calcular_cifra(expectativa.calculo, caso.expediente, modulos_extra=extra)
            except CalculoError as exc:
                resultado.errores.append(f"cifra «{expectativa.nombre}»: {exc}")
                continue
            resultado.cifras[expectativa.nombre] = cifra
            registrada = expectativa.calculo.registrada
            if registrada is not None and registrada != cifra:
                resultado.derivas.append(
                    f"cifra «{expectativa.nombre}»: la calculadora da ahora {cifra} y al escribir el caso daba "
                    f"{registrada} (¿ha cambiado un parámetro o la calculadora?)."
                )
        elif isinstance(expectativa, ExpSumaAusente):
            total = Decimal(0)
            calculable = True
            for calculo in expectativa.calculos:
                try:
                    valor = valor_de_cifra(calcular_cifra(calculo, caso.expediente, modulos_extra=extra))
                except CalculoError as exc:
                    resultado.errores.append(f"suma ausente: {exc}")
                    calculable = False
                    break
                if valor is None:
                    resultado.errores.append(f"suma ausente: la cifra de {calculo.herramienta} no es un importe")
                    calculable = False
                    break
                total += valor
            resultado.sumas.append(total if calculable else None)
    return resultado


def problemas_de_guion(caso: CasoEvaluacion) -> list[str]:
    """Placeholders of the script that point at nothing."""
    if caso.guion is None:
        return []
    nombres = {e.nombre for e in caso.expectativas if isinstance(e, ExpCifra)}
    problemas: list[str] = []
    for pasos in caso.guion.values():
        for paso in pasos:
            for tipo, nombre in _MARCADOR_RE.findall(paso.texto or ""):
                if tipo == "cifra" and nombre not in nombres:
                    problemas.append(f"el guion usa {{cifra:{nombre}}} y no hay ninguna cifra esperada con ese nombre")
    return problemas


# --------------------------------------------------------------------------- #
# Checking a run
# --------------------------------------------------------------------------- #


def comprobar(caso: CasoEvaluacion, ejecucion: Ejecucion, esperadas: CifrasEsperadas) -> list[str]:
    """Failed deterministic expectations, in Spanish (empty = the run satisfies them all)."""
    fallos: list[str] = []
    if ejecucion.error:
        fallos.append(f"la ejecución falló: {ejecucion.error}")
    fallos.extend(f"no se pudo calcular lo esperado ({error})" for error in esperadas.errores)
    respuesta = ejecucion.respuesta
    llamadas = ejecucion.herramientas
    sumas = iter(esperadas.sumas)

    cerebro = ejecucion.cerebro

    def consultado(modulo: str) -> bool:
        if any(c.herramienta == f"consultar_{modulo}" and c.agente == "asesor" for c in llamadas):
            return True
        return cerebro is not None and modulo in especialistas_consultados(cerebro)

    for exp in caso.expectativas:
        if isinstance(exp, ExpHerramientaLlamada):
            veces = sum(
                1
                for c in llamadas
                if c.herramienta == exp.herramienta
                and not c.es_error
                and (exp.agente is None or c.agente == exp.agente)
            )
            if veces < exp.min_veces:
                quien = f" por {exp.agente}" if exp.agente else ""
                fallos.append(f"debía llamar a {exp.herramienta}{quien} (al menos {exp.min_veces}; llamadas: {veces})")
        elif isinstance(exp, ExpHerramientaNoLlamada):
            if any(c.herramienta == exp.herramienta for c in llamadas):
                fallos.append(f"no debía llamar a {exp.herramienta}")
        elif isinstance(exp, ExpModuloConsultado):
            if not consultado(exp.modulo):
                fallos.append(f"debía consultar al especialista «{exp.modulo}»")
        elif isinstance(exp, ExpModuloNoConsultado):
            if consultado(exp.modulo):
                fallos.append(f"no debía consultar al especialista «{exp.modulo}»")
        elif isinstance(exp, ExpCifra):
            cifra = esperadas.cifras.get(exp.nombre)
            if cifra is not None and not aparece(cifra, respuesta):
                fallos.append(f"falta la cifra «{exp.nombre}» que da la calculadora: {cifra}")
        elif isinstance(exp, ExpSumaAusente):
            total = next(sumas, None)
            if total is not None and total in numeros_en(respuesta):
                fallos.append(
                    f"suma cifras que ninguna herramienta ha combinado ({total}): {exp.motivo or 'no se suman'}"
                )
        elif isinstance(exp, ExpTextoAusente):
            encontrado = re.search(exp.patron, respuesta, flags=re.IGNORECASE)
            if encontrado:
                fallos.append(f"afirma lo que no debe («{encontrado.group(0)}»): {exp.motivo or exp.patron}")
        elif isinstance(exp, ExpTextoPresente):
            if not re.search(exp.patron, respuesta, flags=re.IGNORECASE):
                fallos.append(f"falta en la respuesta: {exp.motivo or exp.patron}")
        elif isinstance(exp, ExpCitas):
            citas = [c for c in ejecucion.citas if exp.autoridad is None or c.autoridad == exp.autoridad]
            if len(citas) < exp.minimo:
                de = f" de {exp.autoridad}" if exp.autoridad else ""
                fallos.append(f"debía citar al menos {exp.minimo} fuente(s){de} (cita {len(citas)})")
        elif isinstance(exp, ExpModo):
            if ejecucion.modo not in exp.permitidos:
                fallos.append(f"modo «{ejecucion.modo}» no admitido ({', '.join(exp.permitidos)})")
        elif isinstance(exp, ExpAccionPendiente):
            if exp.herramienta not in ejecucion.acciones_pendientes:
                fallos.append(f"{exp.herramienta} debía quedar como acción pendiente de confirmación")
        elif isinstance(exp, ExpSinAccionPendiente):
            if ejecucion.acciones_pendientes:
                fallos.append(f"no debía proponer acciones ({', '.join(ejecucion.acciones_pendientes)})")
        elif isinstance(exp, ExpCifraAusente):
            valor = valor_de_cifra(exp.valor)
            if valor is not None and valor in numeros_en(respuesta):
                fallos.append(f"da una cifra que no debe ({exp.valor}): {exp.motivo or 'es la cifra equivocada'}")
        elif isinstance(exp, ExpRepaso):
            fallos.extend(comprobar_repaso(exp, ejecucion.repaso, caso))
        elif isinstance(exp, ExpCifraAtribuida):
            fallos.extend(_comprobar_atribuida(exp, cerebro, esperadas, respuesta))
        elif isinstance(exp, ExpEspecialistas | ExpIntencion | ExpRuta | ExpFlujoDato | ExpPuerta | ExpRecuerdos):
            if cerebro is None:
                fallos.append(f"«{exp.tipo}»: la ejecución no trae el informe del cerebro (¿se ejecutó sin él?)")
                continue
            fallos.extend(comprobar_cerebro(exp, cerebro))
        elif isinstance(exp, ExpDictamen | ExpInvarianza):
            from asistente.nucleo.evaluacion.consejo_eval import comprobar_consejo

            fallos.extend(comprobar_consejo(exp, caso, ejecucion))
    return fallos


# --------------------------------------------------------------------------- #
# The household brain's report
# --------------------------------------------------------------------------- #


def especialistas_del_plan(cerebro: InformeCerebro) -> list[str]:
    return cerebro.intencion.especialistas()


def especialistas_consultados(cerebro: InformeCerebro) -> list[str]:
    """Specialists that actually reported (plan encargos and requests through the brain)."""
    vistos: list[str] = []
    for informe in cerebro.informes:
        if informe.especialista not in vistos:
            vistos.append(informe.especialista)
    for peticion in cerebro.peticiones:
        if peticion.destinatario not in vistos:
            vistos.append(peticion.destinatario)
    return vistos


def _dependencias(cerebro: InformeCerebro) -> set[tuple[str, str]]:
    por_id = {e.id: e.especialista for e in cerebro.intencion.encargos}
    return {(e.especialista, por_id[d]) for e in cerebro.intencion.encargos for d in e.depende_de if d in por_id}


def comprobar_cerebro(
    exp: ExpEspecialistas | ExpIntencion | ExpRuta | ExpFlujoDato | ExpPuerta | ExpRecuerdos, cerebro: InformeCerebro
) -> list[str]:
    """Failures of one brain expectation against the turn's report (Spanish)."""
    fallos: list[str] = []
    if isinstance(exp, ExpEspecialistas):
        quienes = especialistas_del_plan(cerebro) if exp.fuente == "plan" else especialistas_consultados(cerebro)
        donde = "en su plan" if exp.fuente == "plan" else "entre los informes"
        faltan = [e for e in exp.incluye if e not in quienes]
        sobran = [e for e in exp.excluye if e in quienes]
        if faltan:
            fallos.append(f"debía contar con {', '.join(faltan)} {donde} (tiene: {', '.join(quienes) or 'nadie'})")
        if sobran:
            fallos.append(f"no debía contar con {', '.join(sobran)} {donde}")
        if exp.exactamente and set(quienes) != set(exp.incluye):
            extra = sorted(set(quienes) - set(exp.incluye))
            if extra:
                fallos.append(
                    f"solo debía contar con {', '.join(exp.incluye) or 'nadie'} {donde} (también: {', '.join(extra)})"
                )
    elif isinstance(exp, ExpIntencion):
        intencion = cerebro.intencion
        if exp.tipos and intencion.tipo not in exp.tipos:
            fallos.append(f"entendió la consulta como «{intencion.tipo}» (se esperaba {' o '.join(exp.tipos)})")
        pregunta = intencion.aclaracion is not None and bool(intencion.aclaracion.strip())
        if exp.aclaracion is True and not (pregunta and not intencion.encargos):
            fallos.append("debía pedir una aclaración (la consulta es ambigua) en vez de suponer")
        if exp.aclaracion is False and pregunta:
            fallos.append("no debía pedir aclaración: la consulta se entiende")
        dependencias = _dependencias(cerebro)
        if exp.paralelo is True and (len(intencion.encargos) < 2 or dependencias):
            fallos.append("debía repartir la consulta en encargos independientes, en paralelo")
        if exp.paralelo is False and not dependencias:
            fallos.append("un encargo debía esperar al resultado de otro (no son independientes)")
        for espera, de in exp.dependencias:
            if (espera, de) not in dependencias:
                fallos.append(f"el encargo de {espera} debía esperar al de {de}")
    elif isinstance(exp, ExpRuta):
        if cerebro.ruta not in exp.permitidas:
            fallos.append(f"respondió por la ruta «{cerebro.ruta}» (se esperaba {' o '.join(exp.permitidas)})")
    elif isinstance(exp, ExpFlujoDato):
        por_peticion = any(p.solicitante == exp.a and p.destinatario == exp.de for p in [*cerebro.peticiones])
        por_dependencia = (exp.a, exp.de) in _dependencias(cerebro)
        valido = {"peticion": por_peticion, "dependencia": por_dependencia}.get(
            exp.via, por_peticion or por_dependencia
        )
        if not valido:
            como = {"peticion": "con una petición", "dependencia": "esperando a su encargo"}.get(
                exp.via, "a través del cerebro"
            )
            fallos.append(f"el dato de {exp.de} debía llegar a {exp.a} {como}")
    elif isinstance(exp, ExpPuerta):
        puerta = cerebro.puerta
        if puerta is None:
            fallos.append("la respuesta no pasó por la puerta de cifras")
        else:
            if exp.sin_violaciones and puerta.violaciones:
                tipos = ", ".join(sorted({v.tipo for v in puerta.violaciones}))
                fallos.append(f"la puerta de cifras encontró {len(puerta.violaciones)} problema(s): {tipos}")
            prohibidas = [v for v in puerta.violaciones if v.tipo in exp.tipos_prohibidos]
            if prohibidas and not exp.sin_violaciones:
                fallos.append(
                    "la puerta de cifras encontró " + ", ".join(f"{v.tipo} («{v.literal}»)" for v in prohibidas)
                )
            if len(puerta.atribuciones) < exp.min_atribuciones:
                fallos.append(
                    f"debía atribuir al menos {exp.min_atribuciones} cifra(s) a su origen (atribuye {len(puerta.atribuciones)})"
                )
    elif isinstance(exp, ExpRecuerdos):
        usados = set(cerebro.recuerdos_usados)
        faltan = [r for r in exp.usados_incluye if r not in usados]
        indebidos = [r for r in exp.usados_excluye if r in usados]
        if faltan:
            fallos.append(f"debía usar lo que recuerda: {', '.join(faltan)}")
        if indebidos:
            fallos.append(f"no debía usar estos recuerdos: {', '.join(indebidos)}")
        if exp.ninguno and usados:
            fallos.append(f"no debía usar ningún recuerdo (usó {', '.join(sorted(usados))})")
    return fallos


def _comprobar_atribuida(
    exp: ExpCifraAtribuida, cerebro: InformeCerebro | None, esperadas: CifrasEsperadas, respuesta: str
) -> list[str]:
    cifra = esperadas.cifras.get(exp.nombre)
    if cifra is None:
        return []  # not computable: already reported as such
    if not aparece(cifra, respuesta):
        return [f"falta la cifra «{exp.nombre}» ({cifra}) que debía atribuir a {exp.especialista}"]
    if cerebro is None or cerebro.puerta is None:
        return [f"«cifra_atribuida»: sin informe de la puerta de cifras para atribuir {cifra}"]
    valor = valor_de_cifra(cifra)
    for atribucion in cerebro.puerta.atribuciones:
        if valor is not None and abs(atribucion.valor) == valor and atribucion.especialista == exp.especialista:
            return []
    quien = next(
        (
            a.especialista or "el usuario"
            for a in cerebro.puerta.atribuciones
            if valor is not None and abs(a.valor) == valor
        ),
        None,
    )
    if quien is None:
        return [f"la cifra {cifra} no está atribuida a nadie (debía venir de {exp.especialista})"]
    return [f"la cifra {cifra} se atribuye a {quien} y debía venir de {exp.especialista}"]


def comprobar_repaso(exp: ExpRepaso, salida: dict | None, caso: CasoEvaluacion) -> list[str]:  # type: ignore[type-arg]
    """The proactive pass's output (``{"texto", "afirmaciones": [{"texto", "senales"}], "propuestas": [{"texto",
    "senal"}]}``) against its input signals."""
    if salida is None:
        return ["«repaso»: la ejecución no trae la salida del repaso"]
    entrada = caso.entrada_repaso
    conocidas = {s.id for s in entrada.senales} if entrada is not None else set()
    fallos: list[str] = []
    afirmaciones = [a for a in salida.get("afirmaciones") or [] if isinstance(a, dict)]
    propuestas = [p for p in salida.get("propuestas") or [] if isinstance(p, dict)]
    if exp.afirmaciones_fundadas:
        for afirmacion in afirmaciones:
            senales = [str(x) for x in afirmacion.get("senales") or []]
            if not senales or any(x not in conocidas for x in senales):
                fallos.append(f"afirmación sin señal que la respalde: «{str(afirmacion.get('texto') or '')[:80]}»")
    if exp.propuestas_fundadas:
        for propuesta in propuestas:
            senal = propuesta.get("senal")
            if senal is None or str(senal) not in conocidas:
                fallos.append(
                    f"propuesta inventada (no sale de ninguna señal): «{str(propuesta.get('texto') or '')[:80]}»"
                )
    if exp.propuestas_solo_de is not None:
        for propuesta in propuestas:
            if str(propuesta.get("senal")) not in exp.propuestas_solo_de:
                fallos.append(f"no debía proponer nada sobre «{propuesta.get('senal')}»")
    if exp.max_propuestas is not None and len(propuestas) > exp.max_propuestas:
        fallos.append(f"demasiadas propuestas ({len(propuestas)}; como mucho {exp.max_propuestas})")
    cubiertas = {str(x) for a in afirmaciones for x in a.get("senales") or []} | {
        str(p.get("senal")) for p in propuestas if p.get("senal") is not None
    }
    for senal in exp.menciona:
        if senal not in cubiertas:
            fallos.append(f"el resumen debía tratar la señal «{senal}»")
    for senal in exp.no_menciona:
        if senal in cubiertas:
            fallos.append(f"el resumen no debía tratar la señal «{senal}»")
    return fallos


@dataclass
class InformeValidacion:
    casos: int = 0
    deterministicos: int = 0
    con_guion: int = 0
    # Cases that need code of the household brain (cerebro, repaso, recuerdos), by requirement.
    con_requisitos: dict[str, int] = field(default_factory=dict)
    errores: list[str] = field(default_factory=list)
    derivas: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def validar(directorio: Path = CASOS_DIR, *, casos: Sequence[CasoEvaluacion] | None = None) -> InformeValidacion:
    """Check the suite without any model: schema, categories, modules, every expected figure computable."""
    informe = InformeValidacion()
    try:
        lista = list(casos) if casos is not None else cargar_casos(directorio)
    except SuiteInvalidaError as exc:
        informe.errores.append(str(exc))
        return informe
    informe.casos = len(lista)
    for caso in lista:
        informe.deterministicos += caso.deterministico
        informe.con_guion += caso.guion is not None
        for requisito in caso.requiere:
            informe.con_requisitos[requisito] = informe.con_requisitos.get(requisito, 0) + 1
        if not caso.requiere and caso.guion is None and caso.origen.tipo == "sintetico":
            informe.errores.append(
                f"{caso.id}: un caso sintético sin «requiere» necesita un guion de referencia (CI lo reproduce sin modelo)"
            )
        esperadas = cifras_esperadas(caso)
        informe.errores.extend(f"{caso.id}: {e}" for e in esperadas.errores)
        informe.errores.extend(f"{caso.id}: {e}" for e in problemas_de_guion(caso))
        if caso.consejo is not None or "consejo" in caso.requiere:
            from asistente.nucleo.evaluacion.consejo_eval import problemas_consejo

            informe.errores.extend(f"{caso.id}: {e}" for e in problemas_consejo(caso))
        informe.derivas.extend(f"{caso.id}: {d}" for d in esperadas.derivas)
    return informe


__all__ = [
    "CASOS_DIR",
    "ORIGENES_PRIVADOS",
    "comprobar_cerebro",
    "comprobar_repaso",
    "especialistas_consultados",
    "especialistas_del_plan",
    "CifrasEsperadas",
    "SuiteInvalidaError",
    "InformeValidacion",
    "cargar_casos",
    "cargar_config",
    "cifras_esperadas",
    "comprobar",
    "ficheros_de_casos",
    "huella_suite",
    "modulos_de",
    "problemas_de_guion",
    "validar",
]
