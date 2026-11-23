from __future__ import annotations

import re

MALICIOUS_PATTERNS = [
    # English
    r"ignore (all|previous|prior) instructions",
    r"system prompt",
    r"developer message",
    r"reveal .*prompt",
    r"you are now",
    r"bypass",
    r"exfiltrate",
    r"do not follow",
    # Spanish — the corpus and the realistic attackers are Spanish-speaking, so
    # the English-only list above let injections through. Patterns are kept
    # high-signal to avoid mangling legitimate fiscal text (e.g. "el pagador
    # actúa como retenedor" must NOT be redacted, so "actúa como" is omitted).
    r"ignora(?:r)?\s+(?:todas\s+)?(?:las\s+)?(?:instrucciones|reglas)\s+(?:anteriores|previas)",
    r"ignora(?:r)?\s+(?:todas\s+)?(?:las\s+)?(?:instrucciones|reglas)\b",
    r"olvida(?:r|te)?\s+(?:las\s+)?instrucciones",
    r"haz\s+caso\s+omiso\s+de\s+(?:las\s+)?(?:instrucciones|reglas)",
    r"no\s+sigas\s+(?:las\s+)?(?:instrucciones|reglas)",
    r"(?:instrucciones|mensaje|prompt)\s+del?\s+(?:sistema|desarrollador)",
    r"(?:revela|muestra|ens(?:e|é)ña)(?:r)?\s+.*prompt",
    r"ahora\s+eres\b",
    r"eres\s+ahora\b",
    r"exfiltra(?:r)?",
    # Possessives and demonstratives the patterns above missed («ignora tus/sus/estas instrucciones») and the
    # English «disregard…» / «ignore your…» / «forget…». Appended (the list is applied in order), so whatever the
    # earlier patterns redacted is redacted exactly as before.
    r"\b(?:ignora|ignorad|ignorar|olvida|olvidad|olvidar|omite|omitir|desobedece)\s+(?:todas\s+)?"
    r"(?:tus|sus|estas|esas|mis|nuestras|vuestras|las)\s+(?:\w+\s+){0,2}?(?:instrucciones|reglas|indicaciones|[oó]rdenes)\b",
    r"\b(?:ignore|disregard|forget)\s+(?:(?:all|any|the|your|my|our|these|those|previous|prior|above|earlier)\s+)+"
    r"(?:instructions|rules|prompts?)\b",
    r"\bdisregard\s+(?:\w+\s+){0,3}?(?:instructions|rules|prompts?|guidelines)\b",
]

# The broader detector shared by every reader of third-party text (research sources, …): any of these verbs a few
# words before «instrucciones/reglas/indicaciones/órdenes», «nuevas instrucciones», the English equivalents and
# «system/developer prompt|message». It FLAGS a text and neutralises it before a model reads it; it is wider than
# :data:`MALICIOUS_PATTERNS` (which rewrites retrieved legal text and must not mangle fiscal prose), so callers
# use it on texts that are never legal sources quoted verbatim.
INYECCION_AMPLIA = re.compile(
    r"\b(?:ignora|ignorad|ignorar|olvida|olvidad|olvidar|omite|omitir|desobedece)\s+(?:\w+\s+){0,3}?"
    r"(?:instrucciones|reglas|indicaciones|[oó]rdenes)\b"
    r"|\bnuevas?\s+instrucciones\b"
    r"|\b(?:ignore|disregard|forget)\s+(?:\w+\s+){0,3}?(?:instructions|rules|prompt)\b"
    r"|\b(?:system|developer)\s+(?:prompt|message)\b",
    re.IGNORECASE,
)


# Compile once at import time. Sanitisation runs once per retrieved doc on
# every ``/qa`` (~20 docs/request after the reranker first stage); previously
# the loop called ``re.sub`` with **string** patterns, relying on Python's
# internal LRU cache (512 entries, evictable). Pre-compiled patterns sidestep
# the cache lookup entirely.
#
# We intentionally keep the **sequential** application (each pattern's
# substitution feeds the next) rather than collapsing into a single
# ``|``-alternation. The alternation form would change semantics: the engine
# picks the longest match per position across the alternation, so
# ``ignore (all|previous|prior) instructions`` would now also fire on inputs
# the serial form left alone, and the order-of-application interactions
# (e.g. ``system prompt`` redacted first, then ``reveal .*prompt`` no longer
# matches) disappear. Behaviour stability over micro-perf — keep the list.
_COMPILED_PATTERNS: list[re.Pattern[str]] = [re.compile(pattern, re.IGNORECASE) for pattern in MALICIOUS_PATTERNS]
_REDACTION = "[REDACTED_INJECTION_PATTERN]"

# Zero-width / invisible format characters an attacker can splice INTO a keyword
# ("ig​nora las instrucciones") to slip it past the literal patterns above
# while it still renders normally to the model. They never carry meaning in
# fiscal prose, so stripping them before matching is safe and closes the
# in-word-obfuscation evasion. (Note: this is defence-in-depth, not exhaustive —
# e.g. an attacker replacing the SPACE between words with a zero-width char to
# *fuse* them is intentionally left to the system-prompt guard rather than risk
# mangling legitimate text. See create_system_prompt.)
_INVISIBLE_RE = re.compile(
    # soft hyphen, ZWSP, ZWNJ, ZWJ, word joiner, BOM/ZWNBSP
    "[­​‌‍⁠﻿]"
)


def sanitize_retrieved_context(text: str) -> str:
    """Redact common prompt-injection fragments from retrieved docs.

    Defence-in-depth; primary policy remains in the system prompt.
    """
    sanitized = _INVISIBLE_RE.sub("", text)
    for pattern in _COMPILED_PATTERNS:
        sanitized = pattern.sub(_REDACTION, sanitized)
    return sanitized


def quitar_invisibles(text: str) -> str:
    """``text`` without the zero-width / invisible format characters (see :data:`_INVISIBLE_RE`)."""
    return _INVISIBLE_RE.sub("", text)


def parece_inyeccion(text: str) -> bool:
    """Does ``text`` carry prompt-injection bait (what :func:`sanitize_retrieved_context` would redact, or one of
    the broader patterns of :data:`INYECCION_AMPLIA`)?"""
    limpio = quitar_invisibles(text)
    return sanitize_retrieved_context(text) != limpio or bool(INYECCION_AMPLIA.search(limpio))


def neutralizar_inyeccion(text: str, redaccion: str = _REDACTION) -> str:
    """``text`` without invisible characters and with the broader injection patterns replaced by ``redaccion``."""
    return INYECCION_AMPLIA.sub(redaccion, quitar_invisibles(text))


def sanitize_context_documents(docs: list) -> list:
    """Return copies of ``docs`` with prompt-injection fragments redacted.

    FAISS' ``InMemoryDocstore`` hands back the *same* ``Document`` instances it
    stores, so mutating ``doc.page_content`` in place would permanently corrupt
    the in-memory corpus (the searcher is cached for the process lifetime). The
    document is copied only when sanitizing actually changed the content, so
    the index keeps its original text and each request sees a clean view.
    """
    sanitized = []
    for doc in docs:
        cleaned = sanitize_retrieved_context(doc.page_content)
        if cleaned == doc.page_content:
            sanitized.append(doc)
        else:
            sanitized.append(doc.model_copy(update={"page_content": cleaned}))
    return sanitized
