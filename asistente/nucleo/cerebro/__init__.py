"""The household brain: one orchestrator that understands the request, calls the right specialists (in parallel,
and letting them ask each other for data through it) and writes one answer whose every figure says where it came
from. The tax advisor is one of its specialists, with its sources and verifier intact.

``contrato`` holds the types every part shares (the brain, the collaboration runtime, the conversation memory,
the evaluation); see ``docs/cerebro/arquitectura.md``.
"""
