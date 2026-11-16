"""Every action a workflow runs is pinned by full commit SHA, with the version it was in a comment
(docs/seguridad.md, S-13): a moved tag cannot change what runs with the repository's token."""

from __future__ import annotations

from pathlib import Path
import re

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)(.*)$", re.MULTILINE)


def test_every_action_is_pinned_by_commit_sha_with_its_version() -> None:
    vistas = 0
    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for accion, resto in _USES.findall(workflow.read_text(encoding="utf-8")):
            if accion.startswith("./"):  # an action of this repository
                continue
            vistas += 1
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", accion), f"{workflow.name}: {accion}"
            assert re.fullmatch(r"\s*#\s*v\d+(\.\d+)*\s*", resto), f"{workflow.name}: {accion} sin versión"
    assert vistas > 0
