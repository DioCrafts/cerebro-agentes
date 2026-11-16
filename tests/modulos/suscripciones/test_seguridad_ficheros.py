"""Untrusted statement files (docs/seguridad.md): small inputs that would blow up in memory or time are refused.

Before the security review a 500-byte XLSX could ask for a list of 10^12 cells, a 70 KB one decompressed to
50 MB and built 8 million rows (1.3 GB of memory), and a DOCTYPE behind a long comment slipped past the check
of the first kilobytes, letting a 15 MB camt.053 expand to a gigabyte of text.
"""

from __future__ import annotations

import io
import resource
import struct
import zipfile

import pytest

from hacienda_gpt.modulos.suscripciones import tablas
from hacienda_gpt.modulos.suscripciones.camt import leer_camt
from hacienda_gpt.modulos.suscripciones.modelos import ImportacionError
from hacienda_gpt.modulos.suscripciones.tablas import filas_xlsx
from hacienda_gpt.utils.xml_seguro import XmlInseguroError, parse_seguro, rechazar_dtd

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
WORKBOOK = f'<workbook xmlns="{NS}"><sheets><sheet name="a" sheetId="1"/></sheets></workbook>'


def _xlsx(sheet: str | bytes, *, extra: dict[str, str | bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zona:
        zona.writestr("xl/workbook.xml", WORKBOOK)
        zona.writestr("xl/worksheets/sheet1.xml", sheet)
        for nombre, contenido in (extra or {}).items():
            zona.writestr(nombre, contenido)
    return buffer.getvalue()


def _sheet(rows: str) -> str:
    return f'<worksheet xmlns="{NS}"><sheetData>{rows}</sheetData></worksheet>'


def _laughs(depth: int = 9) -> str:
    entidades = '<!ENTITY a0 "xxxxxxxxxx">' + "".join(
        f'<!ENTITY a{i} "' + f"&a{i - 1};" * 10 + '">' for i in range(1, depth + 1)
    )
    return f"<!DOCTYPE w [{entidades}]>"


@pytest.fixture
def memory_cap():
    """Run the parser under a 1 GB address-space cap, so a regression fails instead of exhausting the host."""
    antes = resource.getrlimit(resource.RLIMIT_AS)
    try:  # the address space already mapped (libraries, arenas) plus 1 GB
        with open("/proc/self/status", encoding="ascii") as estado:
            mapeado = next(int(linea.split()[1]) * 1024 for linea in estado if linea.startswith("VmSize:"))
    except (OSError, StopIteration):
        yield
        return
    tope = mapeado + 1024**3
    if antes[1] != resource.RLIM_INFINITY and antes[1] < tope:
        yield
        return
    resource.setrlimit(resource.RLIMIT_AS, (tope, antes[1]))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_AS, antes)


def test_a_cell_beyond_the_last_column_is_refused(memory_cap) -> None:
    for referencia in ("ZZZZ1", "ZZZZZZZZZ1", "XFE1"):
        datos = _xlsx(_sheet(f'<row><c r="{referencia}" t="inlineStr"><is><t>x</t></is></c></row>'))
        with pytest.raises(ImportacionError, match="fuera de la hoja"):
            filas_xlsx(datos)
    # The last real column still works.
    filas = filas_xlsx(_xlsx(_sheet('<row><c r="XFD1" t="inlineStr"><is><t>x</t></is></c></row>')))
    assert len(filas[0]) == 16_384 and filas[0][-1] == "x"


def test_a_doctype_behind_a_long_comment_is_still_refused_in_xlsx(memory_cap) -> None:
    sheet = '<?xml version="1.0"?><!--' + "p" * 20_000 + "-->" + _laughs() + _sheet("<row/>")
    with pytest.raises(ImportacionError, match="DOCTYPE"):
        filas_xlsx(_xlsx(sheet))


def test_a_doctype_behind_a_long_comment_is_still_refused_in_camt(memory_cap) -> None:
    documento = (
        "<!--" + "p" * 20_000 + "-->" + _laughs(5).replace("DOCTYPE w", "DOCTYPE Document") + "<Document "
        'xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"><BkToCstmrStmt><Stmt><Ntry>'
        "<AddtlNtryInf>&a5;</AddtlNtryInf></Ntry></Stmt></BkToCstmrStmt></Document>"
    )
    with pytest.raises(ImportacionError, match="DOCTYPE"):
        leer_camt(documento)


def test_too_many_rows_stop_early_and_in_bounded_memory(memory_cap) -> None:
    filas = "<row/>" * (tablas._MAX_FILAS + 1)
    with pytest.raises(ImportacionError, match="filas"):
        filas_xlsx(_xlsx(_sheet(filas)))


def test_elements_outside_rows_do_not_accumulate(memory_cap) -> None:
    # 2 million siblings that are not rows: detached as they end, never held together.
    basura = "<x/>" * 2_000_000
    assert filas_xlsx(_xlsx(_sheet(basura + "<row/>"))) == [[]]


def test_an_oversized_row_or_deep_nesting_is_refused(memory_cap) -> None:
    ancha = "<row>" + "<c/>" * (tablas._MAX_ELEMENTOS_POR_FILA + 1) + "</row>"
    with pytest.raises(ImportacionError, match="demasiado grande"):
        filas_xlsx(_xlsx(_sheet(ancha)))
    profunda = "<a>" * 100 + "</a>" * 100
    with pytest.raises(ImportacionError, match="anidado"):
        filas_xlsx(_xlsx(_sheet(profunda)))


def test_a_part_that_lies_about_its_size_cannot_decompress_past_the_limit(memory_cap, monkeypatch) -> None:
    monkeypatch.setattr(tablas, "_MAX_XML", 100_000)
    datos = bytearray(_xlsx(_sheet("<row/>" * 200_000)))
    # Rewrite the uncompressed size the central directory declares for the sheet (offset 24).
    inicio = 0
    while (inicio := datos.find(b"PK\x01\x02", inicio)) != -1:
        nombre_len = struct.unpack_from("<H", datos, inicio + 28)[0]
        if datos[inicio + 46 : inicio + 46 + nombre_len] == b"xl/worksheets/sheet1.xml":
            struct.pack_into("<I", datos, inicio + 24, 1000)
        inicio += 4
    with pytest.raises((ImportacionError, zipfile.BadZipFile)):
        filas_xlsx(bytes(datos))


def test_malformed_cells_are_an_import_error_not_a_crash() -> None:
    for celda in ('<c t="s"><v>99</v></c>', '<c s="x"><v>1</v></c>', '<c t="s"><v>no</v></c>'):
        with pytest.raises(ImportacionError):
            filas_xlsx(_xlsx(_sheet(f"<row>{celda}</row>")))


def test_safe_parser_reads_ordinary_xml_and_rejects_any_dtd() -> None:
    assert parse_seguro(b"<?xml version='1.0'?><!-- hola --><a><b>1</b></a>").find("b").text == "1"
    rechazar_dtd(b"<a><b>no es xml")  # malformed: left for the real parser to report
    for documento in (b'<!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>', b"<!DOCTYPE a [<!ENTITY e 'x'>]><a>&e;</a>"):
        with pytest.raises(XmlInseguroError):
            parse_seguro(documento)
