"""Web Push: notices on the users' phones and computers, opt-in, with no personal data in the message.

* :mod:`asistente.plataforma.push.cifrado` — RFC 8291 / RFC 8188 message encryption.
* :mod:`asistente.plataforma.push.vapid` — RFC 8292 server identification (``PUSH_VAPID_*``).
* :mod:`asistente.plataforma.push.almacen` — subscriptions and opt-ins per user (erased with the account).
* :mod:`asistente.plataforma.push.envio` — the generic payload, sending, the ``push`` delivery channel.

HTTP endpoints: :mod:`asistente.plataforma.api.routers.push`. Docs: README «App instalable (PWA) y notificaciones»,
docs/privacidad.md.
"""
