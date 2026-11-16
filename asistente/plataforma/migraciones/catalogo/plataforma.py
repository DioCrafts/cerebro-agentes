"""``asistente.plataforma``: the processes that are running and the scheduled jobs of the worker.

* ``plataforma_procesos`` — heartbeat of each api / worker process (no user data);
* ``plataforma_trabajos`` — per scheduled job, the last slot claimed, by whom, and how it ended: the claim
  is what keeps two workers from running the same daily job twice.
"""

from __future__ import annotations

from asistente.plataforma.migraciones.motor import Migracion, Propietario

PROCESOS = """CREATE TABLE IF NOT EXISTS plataforma_procesos (
    nombre TEXT PRIMARY KEY,
    rol TEXT NOT NULL,
    equipo TEXT NOT NULL,
    pid INTEGER NOT NULL,
    iniciado TEXT NOT NULL,
    latido TEXT NOT NULL,
    detalle_json TEXT NOT NULL DEFAULT '{}'
)"""
TRABAJOS = """CREATE TABLE IF NOT EXISTS plataforma_trabajos (
    nombre TEXT PRIMARY KEY,
    turno TEXT,
    propietario TEXT,
    inicio TEXT,
    fin TEXT,
    codigo INTEGER,
    error TEXT
)"""

PROPIETARIO = Propietario(
    nombre="plataforma",
    paquete="asistente.plataforma",
    descripcion="Latido de los procesos y turnos reclamados de las tareas programadas del worker.",
    tablas=("plataforma_procesos", "plataforma_trabajos"),
    migraciones=(Migracion(1, "esquema_inicial", (PROCESOS, TRABAJOS)),),
)
