"""Deep research from the command line (``asistente/investigador``, docs/cerebro/investigador.md).

    python -m asistente.plataforma.cli.investigar "¿Qué deducciones autonómicas por alquiler hay en Madrid?" \
        [--max-tokens 200000] [--max-euros 1.0] [--profundidad 2] [--marcar] [--json]

Progress (plan, sources, findings, critiques) goes to stderr as it happens; the report (Markdown, or JSON with
``--json``) to stdout. Model from ``INVESTIGADOR_MODELO`` (``proveedor/modelo``, e.g. ``anthropic/claude-…``;
without it, the configured provider).
"""

from __future__ import annotations

import json

import click

from asistente.investigador import Evento, Presupuesto, investigar


def _progreso(evento: Evento) -> None:
    click.echo(f"[{evento.tipo}] {evento.mensaje}", err=True)


@click.command()
@click.argument("pregunta")
@click.option("--max-tokens", default=200_000, show_default=True, type=int)
@click.option("--max-euros", default=1.0, show_default=True, type=float)
@click.option("--profundidad", default=2, show_default=True, type=int, help="Rondas máximas de investigación.")
@click.option("--subpreguntas", default=5, show_default=True, type=int)
@click.option("--marcar", is_flag=True, help="Marcar las afirmaciones sin fuente en vez de eliminarlas.")
@click.option("--json", "como_json", is_flag=True, help="Informe en JSON.")
def main(pregunta: str, max_tokens: int, max_euros: float, profundidad: int, subpreguntas: int, marcar: bool,
         como_json: bool) -> None:
    presupuesto = Presupuesto(max_tokens=max_tokens, max_euros=max_euros, max_profundidad=profundidad,
                              max_subpreguntas=subpreguntas)
    informe = investigar(pregunta, presupuesto, al_progresar=_progreso, sin_fuente="marcar" if marcar else "eliminar")
    click.echo(json.dumps(informe.a_dict(), ensure_ascii=False, default=str, indent=2) if como_json else informe.markdown())


if __name__ == "__main__":
    main()
