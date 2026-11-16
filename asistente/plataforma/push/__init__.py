"""Web Push: notices on the users' phones and computers, opt-in, with no personal data in the message.

* :mod:`hacienda_gpt.push.cifrado` — RFC 8291 / RFC 8188 message encryption.
* :mod:`hacienda_gpt.push.vapid` — RFC 8292 server identification (``PUSH_VAPID_*``).
* :mod:`hacienda_gpt.push.almacen` — subscriptions and opt-ins per user (erased with the account).
* :mod:`hacienda_gpt.push.envio` — the generic payload, sending, the ``push`` delivery channel.

HTTP endpoints: :mod:`hacienda_gpt.api.routers.push`. Docs: README «App instalable (PWA) y notificaciones»,
docs/privacidad.md.
"""
