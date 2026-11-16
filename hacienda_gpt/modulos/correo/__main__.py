"""``python -m hacienda_gpt.modulos.correo``: moved to ``python -m hacienda_gpt.capacidades.correo`` (both work)."""

import sys

from hacienda_gpt.capacidades.correo.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
