"""Messaging channels to the household brain (SimpleX Chat first): a channel-agnostic gateway plus one small
adapter per channel. ``contrato`` holds the shared types; see ``docs/mensajeria/arquitectura.md``. Keep this module
import-light (the worker imports it to register its service).
"""
