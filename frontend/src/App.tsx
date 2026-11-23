// Application entry: the account gate around the advisor's shell (src/shell/Asesor.tsx).
//
// When the backend requires an account (AUTH_MODE=required) the shell is
// wrapped by a gate that shows the login screen first; with accounts off the
// gate renders the shell directly and nothing else changes.
//
// «Privacidad y datos» belongs to the gate (it can erase the account): it opens as the panel
// `privacidad` of the URL (`/?panel=privacidad`), like the shell's panels.

import { useEffect, useRef, useState } from "react";

import type { AuthConfig, SessionResponse, SessionUser } from "./api";
import { ApiError, getAuthConfig, getMe, logout, onSessionExpired, revocarTokenAntiguo } from "./api";
import { AuthScreen } from "./components/AuthScreen";
import { AvisoActualizacion } from "./components/AvisoActualizacion";
import { Monogram } from "./components/Brand";
import { PrivacyPanel } from "./components/PrivacyPanel";
import { abrirPanel, cerrarPanel, usePanelUrl } from "./lib/panelUrl";
import { olvidarDispositivo } from "./lib/push";
import { cacheAuthMode, cachedAuthMode, historyOwner, setHistoryOwner, tomarTokenAntiguo } from "./lib/session";
import { borrarConversaciones, loadConversations, saveConversations } from "./lib/storage";
import { useTheme } from "./lib/theme";
import { Asesor } from "./shell/Asesor";
import { Spinner } from "./ui";

function Splash({ message }: { message?: string }) {
  return (
    <div role="status" className="flex h-dvh w-full flex-col items-center justify-center gap-4 px-6 text-center text-fg-muted">
      {/* The mark, not an empty screen, while the session is checked. */}
      <Monogram size={48} />
      <Spinner size="medium" srText={message ? null : "Cargando"} />
      {message}
    </div>
  );
}

/**
 * Make the conversation history in this browser safe to show to `userId`.
 * Another account's history is discarded. History from before accounts existed
 * is kept, detached from its cases: those have no owner and cannot be reached.
 */
function adoptLocalHistory(userId: string): void {
  const owner = historyOwner();
  if (owner === userId) return;
  if (owner === null) {
    saveConversations(loadConversations().map((c) => ({ ...c, caseId: null, facts: [], obligations: [], taxPeriod: null })));
  } else {
    saveConversations([]);
  }
  setHistoryOwner(userId);
}

/** The conversation history in this browser goes with the session that owned it (see lib/storage.ts). */
function borrarHistorialLocal(): void {
  borrarConversaciones();
  setHistoryOwner(null);
}

// A session token an older version kept in localStorage: taken out once per page load (idempotent, so
// React's double-invoked initialisers in development see the same answer).
let migracion: { token: string | null; revocado: boolean } | null = null;
function tokenAntiguo(): string | null {
  migracion ??= { token: tomarTokenAntiguo(), revocado: false };
  return migracion.token;
}

const AVISO_MIGRACION = "Hemos reforzado la seguridad de las sesiones. Vuelve a iniciar sesión.";

const CONFIG_RETRY_MS = 5_000;

export default function App() {
  return (
    <>
      <Puerta />
      <AvisoActualizacion />
    </>
  );
}

function Puerta() {
  // Applies the stored theme to the document, also on the screens shown before the shell.
  useTheme();
  const [lastKnownMode] = useState(cachedAuthMode);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [configFailed, setConfigFailed] = useState(false);
  const [configAttempt, setConfigAttempt] = useState(0);
  const [user, setUser] = useState<SessionUser | null>(null);
  // The session cookie cannot be read from here: the server says whether there is a live session.
  const [sessionChecked, setSessionChecked] = useState(false);
  const [notice, setNotice] = useState<string | null>(() => (tokenAntiguo() !== null ? AVISO_MIGRACION : null));
  // Read by the session-expired handler, which must know whether somebody was signed in.
  const userRef = useRef<SessionUser | null>(null);
  useEffect(() => {
    userRef.current = user;
  }, [user]);
  const [panel] = usePanelUrl();
  const privacyOpen = panel === "privacidad";
  // Set by an explicit sign-out or an account deletion: the history in this browser must go.
  const wipeHistoryRef = useRef(false);

  // An old-style session: revoke its token on the server (it sat in localStorage), drop the history it
  // owned, and ask for a new sign-in, which sets the cookie.
  useEffect(() => {
    const token = tokenAntiguo();
    if (token === null || migracion === null || migracion.revocado) return;
    migracion.revocado = true;
    borrarHistorialLocal();
    void revocarTokenAntiguo(token);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    getAuthConfig(controller.signal).then(
      (loaded) => {
        cacheAuthMode(loaded.auth_mode);
        setConfig(loaded);
        setConfigFailed(false);
      },
      () => {
        if (controller.signal.aborted) return;
        // Backend unreachable: keep asking, so the right screen appears as soon as it is back.
        setConfigFailed(true);
        retry = setTimeout(() => setConfigAttempt((n) => n + 1), CONFIG_RETRY_MS);
      },
    );
    return () => {
      controller.abort();
      clearTimeout(retry);
    };
  }, [configAttempt]);

  const required = config?.auth_mode === "required";

  useEffect(() => {
    onSessionExpired(() => {
      // Somebody was signed in: their session ended under them, and their history goes with it.
      if (userRef.current !== null) {
        wipeHistoryRef.current = true;
        setNotice("Tu sesión ha caducado. Vuelve a iniciar sesión.");
      }
      setUser(null);
      if (privacyOpen) cerrarPanel();
      setSessionChecked(true);
      // If we believed accounts were off, the backend has changed: ask again.
      if (!required) setConfigAttempt((n) => n + 1);
    });
    return () => onSessionExpired(null);
  }, [required, privacyOpen]);

  // When accounts were on last time, ask for the session while /auth/config is still on its way: both
  // requests go out together instead of one after the other (one round trip less before the shell).
  const [mePrevio] = useState<Promise<SessionUser> | null>(() => {
    if (lastKnownMode !== "required") return null;
    const pendiente = getMe();
    pendiente.catch(() => undefined); // read by the effect below, or never if accounts turned out to be off
    return pendiente;
  });
  const mePrevioUsado = useRef(false);

  useEffect(() => {
    if (!required || sessionChecked) return;
    const controller = new AbortController();
    // The early request is used once: a later check (after a sign-out or an expiry) asks again.
    const adelantada = mePrevio !== null && !mePrevioUsado.current;
    const pendiente = adelantada ? mePrevio : getMe(controller.signal);
    const usada = () => {
      if (adelantada) mePrevioUsado.current = true;
    };
    pendiente.then(
      (me) => {
        if (controller.signal.aborted) return;
        usada();
        adoptLocalHistory(me.id);
        setUser(me);
        setSessionChecked(true);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        usada();
        if (err instanceof ApiError && err.status === 401) {
          // No live session (never signed in, or it expired while the app was closed): a history left by
          // a past session must not outlive it.
          if (historyOwner() !== null) borrarHistorialLocal();
        } else {
          setNotice("No se pudo comprobar tu sesión. Inicia sesión de nuevo.");
        }
        setSessionChecked(true);
      },
    );
    return () => controller.abort();
  }, [required, sessionChecked, mePrevio]);

  // Runs once the shell is unmounted, so nothing it still held can be written back.
  useEffect(() => {
    if (user !== null || !wipeHistoryRef.current) return;
    wipeHistoryRef.current = false;
    borrarHistorialLocal();
  }, [user]);

  function handleSignedIn(session: SessionResponse) {
    // The server set the session cookie; `session.token` is null for the web client.
    adoptLocalHistory(session.user.id);
    setNotice(null);
    setUser(session.user);
  }

  /** Forget the session on this browser, and the conversations with it. */
  function signOutLocally(message: string | null) {
    wipeHistoryRef.current = true;
    if (privacyOpen) cerrarPanel();
    setUser(null);
    setNotice(message);
  }

  function handleLogout() {
    const confirmed = window.confirm(
      "Al cerrar sesión se borra de este navegador el historial de conversaciones. Tu expediente sigue guardado en tu cuenta. ¿Cerrar sesión?",
    );
    if (!confirmed) return;
    // Best effort: the session is dropped here even if the server cannot be told. This device first stops
    // receiving the account's notices on the phone (while the session can still tell the server).
    olvidarDispositivo()
      .then(() => logout())
      .catch(() => undefined)
      .finally(() => signOutLocally(null));
  }

  if (config === null) {
    // Until the backend answers, trust what it said last time; a first visit
    // (or an unreachable backend that never had accounts) gets the open app.
    if (lastKnownMode === "required") {
      return <Splash message={configFailed ? "No hay conexión con el servicio. Reintentando…" : undefined} />;
    }
    if (lastKnownMode === "off" || configFailed) return <Asesor key="open" />;
    return <Splash />;
  }
  if (!required) return <Asesor key="open" />;
  if (!sessionChecked) return <Splash />;
  if (!user) return <AuthScreen config={config} notice={notice} onSignedIn={handleSignedIn} />;
  return (
    <>
      <Asesor key={user.id} account={{ email: user.email, onOpenPrivacy: () => abrirPanel("privacidad"), onLogout: handleLogout }} />
      {/* Mounted only while open, so the typed password never outlives the panel. */}
      {privacyOpen && (
        <PrivacyPanel
          open
          onClose={cerrarPanel}
          email={user.email}
          noticeUrl={config.privacy_notice_url}
          onAccountDeleted={() => signOutLocally("Tu cuenta y tus datos se han eliminado.")}
        />
      )}
    </>
  );
}
