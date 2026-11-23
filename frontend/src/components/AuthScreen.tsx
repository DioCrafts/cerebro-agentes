// Login / register screen, shown only when the backend requires an account.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): it copies the layout of GitHub's sign-in page
// (a narrow centred column: the app's monogram, the heading, the form in a muted box and, below, a second box
// to switch between signing in and creating an account), with the app's name and colours.

import { type FormEvent, useState } from "react";

import type { AuthConfig, SessionResponse } from "../api";
import { ApiError, login, register } from "../api";
import { Button, Caja, Checkbox, CheckboxGroup, Flash, FormControl, Heading, Link, TextInput } from "../ui";
import { AuthPrivacyNotice } from "./AuthPrivacyNotice";
import { Monogram } from "./Brand";

interface AuthScreenProps {
  config: AuthConfig;
  // Why the user is here again (session expired, account deleted…).
  notice: string | null;
  onSignedIn: (session: SessionResponse) => void;
}

function failure(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "No hay conexión con el servicio. Comprueba que está en marcha y tu conexión a internet.";
  return "Ha ocurrido un error inesperado.";
}

export function AuthScreen({ config, notice, onSignedIn }: AuthScreenProps) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [acceptsData, setAcceptsData] = useState(false);
  const [acceptsDocuments, setAcceptsDocuments] = useState(false);
  const [acceptsEmail, setAcceptsEmail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noticeOpen, setNoticeOpen] = useState(false);

  const registering = mode === "register";
  const minLength = config.password_min_length;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (registering && password.length < minLength) {
      setError(`La contraseña debe tener al menos ${minLength} caracteres.`);
      return;
    }
    if (registering && !acceptsData) {
      setError("Para crear la cuenta tienes que aceptar el tratamiento de tus datos.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const session = registering
        ? await register({
            email,
            password,
            consents: {
              tratamiento_datos: acceptsData,
              envio_documentos_a_proveedor_llm: acceptsDocuments,
              avisos_por_correo: acceptsEmail,
            },
          })
        : await login({ email, password });
      onSignedIn(session);
    } catch (err) {
      setError(failure(err));
      setBusy(false);
    }
  }

  function switchMode(next: "login" | "register") {
    setMode(next);
    setError(null);
  }

  return (
    <div className="flex min-h-dvh w-full justify-center bg-canvas-default px-4 py-10">
      <main className="flex w-full max-w-[340px] flex-col gap-4">
        <div className="flex flex-col items-center gap-4">
          <Monogram size={48} />
          <Heading as="h1" variant="medium" className="text-center">
            {registering ? "Crea tu cuenta de Cerebro" : "Inicia sesión en Cerebro"}
          </Heading>
        </div>

        {notice && !error && (
          <Flash role="status" className="text-center">
            {notice}
          </Flash>
        )}
        {error && (
          <Flash variant="danger" role="alert">
            {error}
          </Flash>
        )}

        <div className="rounded-medium border border-border-default bg-canvas-muted p-4">
          <form onSubmit={(event) => void handleSubmit(event)} className="flex flex-col gap-4" noValidate>
            <FormControl required>
              <FormControl.Label>Correo electrónico</FormControl.Label>
              <TextInput block type="email" name="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </FormControl>
            <FormControl required>
              <FormControl.Label>Contraseña</FormControl.Label>
              <TextInput
                block
                type="password"
                name="password"
                autoComplete={registering ? "new-password" : "current-password"}
                minLength={registering ? minLength : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {registering && <FormControl.Caption>Al menos {minLength} caracteres.</FormControl.Caption>}
            </FormControl>

            {registering && (
              <CheckboxGroup>
                <CheckboxGroup.Label visuallyHidden>Consentimientos</CheckboxGroup.Label>
                <FormControl>
                  <Checkbox
                    name="tratamiento_datos"
                    checked={acceptsData}
                    onChange={(e) => {
                      setAcceptsData(e.target.checked);
                      setError(null);
                    }}
                  />
                  <FormControl.Label>Acepto que se guarden y traten mis datos (obligatorio)</FormControl.Label>
                  <FormControl.Caption>
                    Los datos fiscales, familiares y de salud (discapacidad) que aporte, para recibir asesoramiento.
                  </FormControl.Caption>
                </FormControl>
                <FormControl>
                  <Checkbox name="envio_documentos_a_proveedor_llm" checked={acceptsDocuments} onChange={(e) => setAcceptsDocuments(e.target.checked)} />
                  <FormControl.Label>Enviar mis documentos al proveedor del modelo (opcional)</FormControl.Label>
                  <FormControl.Caption>
                    Para que lea los documentos que suba (nóminas, certificados…). Sin él no podrás subir documentos.
                  </FormControl.Caption>
                </FormControl>
                <FormControl>
                  <Checkbox name="avisos_por_correo" checked={acceptsEmail} onChange={(e) => setAcceptsEmail(e.target.checked)} />
                  <FormControl.Label>Avisos por correo (opcional)</FormControl.Label>
                  <FormControl.Caption>Los plazos y cambios que me afecten, cuando el servicio los ofrezca.</FormControl.Caption>
                </FormControl>
                <Link as="button" type="button" className="self-start text-xs" onClick={() => setNoticeOpen(true)}>
                  Qué hacemos con tus datos
                </Link>
              </CheckboxGroup>
            )}

            <Button type="submit" variant="primary" block loading={busy} disabled={!email || !password}>
              {registering ? "Crear cuenta" : "Entrar"}
            </Button>
          </form>
        </div>

        <Caja>
          <Caja.Cuerpo className="text-center">
            {registering ? "¿Ya tienes cuenta? " : "¿Es tu primera vez? "}
            <Link as="button" type="button" onClick={() => switchMode(registering ? "login" : "register")}>
              {registering ? "Inicia sesión" : "Crea una cuenta"}
            </Link>
          </Caja.Cuerpo>
        </Caja>

        <p className="m-0 text-center text-xs text-fg-muted">
          Tu expediente solo es visible con tu cuenta.{" "}
          <Link as="button" type="button" onClick={() => setNoticeOpen(true)}>
            Privacidad
          </Link>
        </p>
      </main>
      <AuthPrivacyNotice open={noticeOpen} onClose={() => setNoticeOpen(false)} noticeUrl={config.privacy_notice_url} />
    </div>
  );
}
