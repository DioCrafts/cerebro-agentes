// The user's privacy controls: consents, a copy of everything stored about
// them, and erasing the account. Only exists when the backend has accounts.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): a side sheet (HojaLateral) with the sections of
// GitHub's account settings: one box per section and, last, the «danger zone» to delete the account. App.tsx
// opens it outside the panel registry and mounts it only while open, so the typed password never outlives it;
// when it goes, focus goes back to whatever opened it.

import { type FormEvent, useEffect, useState } from "react";

import type { ConsentState, ConsentType } from "../api";
import { ApiError, deleteMyAccount, exportMyData, getConsents, updateConsents } from "../api";
import { formatDate } from "../lib/presentation";
import { loadConversations } from "../lib/storage";
import { Button, Caja, CargandoLista, Checkbox, Flash, FormControl, HojaLateral, Link, TextInput, useConfirm } from "../ui";
import { DownloadIcon, ShieldCheckIcon, TrashIcon } from "../ui/iconos";
import { AuthPrivacyNotice } from "./AuthPrivacyNotice";

interface PrivacyPanelProps {
  open: boolean;
  onClose: () => void;
  email: string;
  noticeUrl: string;
  // The account no longer exists on the server: drop the session and local data.
  onAccountDeleted: () => void;
}

const CONSENT_TEXT: Record<ConsentType, { title: string; detail: string }> = {
  tratamiento_datos: {
    title: "Tratamiento de mis datos",
    detail:
      "Guardar y tratar los datos fiscales, familiares y de salud (discapacidad) que aportas, para asesorarte. Es imprescindible para tener cuenta: para retirarlo, elimina tu cuenta.",
  },
  envio_documentos_a_proveedor_llm: {
    title: "Enviar mis documentos al proveedor del modelo de lenguaje",
    detail: "Necesario para que el asesor lea los documentos que subes. Sin él, la subida de documentos queda desactivada. Los documentos no se almacenan.",
  },
  avisos_por_correo: {
    title: "Avisos por correo",
    detail: "Recibir por correo los avisos de plazos y cambios que te afecten, cuando el servicio los ofrezca. Los avisos dentro de la aplicación no dependen de esto.",
  },
};

const ORDER: ConsentType[] = ["tratamiento_datos", "envio_documentos_a_proveedor_llm", "avisos_por_correo"];

function failure(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof TypeError) return "No hay conexión con el servicio.";
  return fallback;
}

function download(filename: string, data: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PrivacyPanel({ open, onClose, email, noticeUrl, onAccountDeleted }: PrivacyPanelProps) {
  const [consents, setConsents] = useState<ConsentState[] | null>(null);
  const [consentsError, setConsentsError] = useState<string | null>(null);
  const [savingConsent, setSavingConsent] = useState<ConsentType | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [password, setPassword] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const confirmar = useConfirm();

  // Focus goes back to what opened the sheet once it goes away (the shell does this for registry panels). Read
  // while rendering: by the time this component's effects run, the sheet has already taken the focus.
  const [antes] = useState(() => document.activeElement);
  useEffect(
    () => () => {
      if (antes instanceof HTMLElement && antes.isConnected) antes.focus();
    },
    [antes],
  );

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    getConsents(controller.signal).then(
      (resp) => {
        setConsents(resp.consents);
        setConsentsError(null);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setConsentsError(failure(err, "No se pudieron cargar tus consentimientos."));
      },
    );
    return () => controller.abort();
  }, [open]);

  function handleToggle(type: ConsentType, granted: boolean) {
    if (savingConsent) return;
    setSavingConsent(type);
    setConsentsError(null);
    updateConsents({ [type]: granted })
      .then(
        (resp) => setConsents(resp.consents),
        (err: unknown) => setConsentsError(failure(err, "No se pudo guardar el cambio.")),
      )
      .finally(() => setSavingConsent(null));
  }

  function handleExport() {
    if (exporting) return;
    setExporting(true);
    setExportMessage(null);
    exportMyData()
      .then(
        (data) => {
          // The conversations live only in this browser; add them so the copy is complete.
          download(`haciendagpt-mis-datos-${new Date().toISOString().slice(0, 10)}.json`, {
            ...data,
            conversaciones_en_este_navegador: loadConversations(),
          });
          setExportMessage({ ok: true, text: "Descarga preparada. El archivo contiene datos personales: guárdalo en un lugar seguro." });
        },
        (err: unknown) => setExportMessage({ ok: false, text: failure(err, "No se pudieron exportar tus datos.") }),
      )
      .finally(() => setExporting(false));
  }

  async function handleDelete(event: FormEvent) {
    event.preventDefault();
    if (deleting || !password || !understood) return;
    const si = await confirmar({
      title: "¿Eliminar tu cuenta definitivamente?",
      content: "Se borrarán tu cuenta, tu expediente y tus avisos. No se puede deshacer.",
      confirmButtonContent: "Eliminar definitivamente",
      cancelButtonContent: "Cancelar",
      confirmButtonType: "danger",
    });
    if (!si) return;
    setDeleting(true);
    setDeleteError(null);
    deleteMyAccount(password).then(
      () => onAccountDeleted(),
      (err: unknown) => {
        setDeleteError(failure(err, "No se pudo eliminar la cuenta."));
        setDeleting(false);
      },
    );
  }

  const byType = new Map((consents ?? []).map((c) => [c.type, c]));

  return (
    <HojaLateral
      open={open}
      onClose={onClose}
      titulo="Privacidad y datos"
      subtitulo={email}
      icono={ShieldCheckIcon}
      pie={
        <p className="m-0 text-xs text-fg-muted">
          <Link as="button" type="button" onClick={() => setNoticeOpen(true)}>
            Qué hacemos con tus datos
          </Link>
          {noticeUrl && (
            <>
              {" · "}
              <Link href={noticeUrl} target="_blank" rel="noreferrer">
                Aviso de privacidad
              </Link>
            </>
          )}
        </p>
      }
    >
      <div className="flex flex-col gap-4">
        <Caja as="section" aria-label="Consentimientos">
          <Caja.Cabecera titulo="Consentimientos" />
          {consentsError && (
            <Caja.Cuerpo>
              <Flash variant="danger" role="alert">
                {consentsError}
              </Flash>
            </Caja.Cuerpo>
          )}
          {!consents && !consentsError && (
            <Caja.Cuerpo>
              <CargandoLista filas={3} etiqueta="Cargando tus consentimientos" />
            </Caja.Cuerpo>
          )}
          {consents && (
            <Caja.Lista>
              {ORDER.map((type) => {
                const state = byType.get(type);
                const granted = state?.granted ?? false;
                const locked = type === "tratamiento_datos";
                return (
                  <Caja.Fila key={type}>
                    <FormControl disabled={locked || savingConsent !== null}>
                      <Checkbox name={type} checked={granted} onChange={(e) => handleToggle(type, e.target.checked)} />
                      <FormControl.Label>{CONSENT_TEXT[type].title}</FormControl.Label>
                      <FormControl.Caption>
                        {CONSENT_TEXT[type].detail}{" "}
                        {state?.timestamp
                          ? `${granted ? "Concedido" : "No concedido"} el ${formatDate(state.timestamp.slice(0, 10))}.`
                          : "Todavía no has decidido."}
                      </FormControl.Caption>
                    </FormControl>
                  </Caja.Fila>
                );
              })}
            </Caja.Lista>
          )}
        </Caja>

        <Caja as="section" aria-label="Una copia de mis datos">
          <Caja.Cabecera titulo="Una copia de mis datos" />
          <Caja.Cuerpo className="flex flex-col gap-3">
            <p className="m-0">
              Descarga en un archivo JSON todo lo que hay guardado sobre ti: tu perfil, tus expedientes con su historial, la memoria de
              conversaciones, tus avisos y tus consentimientos, más las conversaciones guardadas en este navegador.
            </p>
            <div>
              <Button leadingVisual={DownloadIcon} onClick={handleExport} loading={exporting}>
                Exportar mis datos
              </Button>
            </div>
            {exportMessage &&
              (exportMessage.ok ? (
                <p role="status" className="m-0 text-xs text-fg-muted">
                  {exportMessage.text}
                </p>
              ) : (
                <Flash variant="danger" role="alert">
                  {exportMessage.text}
                </Flash>
              ))}
          </Caja.Cuerpo>
        </Caja>

        <Caja as="section" aria-label="Eliminar mi cuenta" variante="peligro">
          <Caja.Cabecera titulo="Eliminar mi cuenta" />
          <Caja.Cuerpo>
            <form onSubmit={(e) => void handleDelete(e)} className="flex flex-col gap-3">
              <p className="m-0">
                Se borran del servidor tu cuenta, tus expedientes con su historial, la memoria de conversaciones, tus avisos, tus
                consentimientos y tus sesiones, y de este navegador tus conversaciones. <strong>No se puede deshacer.</strong>
              </p>
              <FormControl>
                <FormControl.Label>Confirma tu contraseña</FormControl.Label>
                <TextInput
                  block
                  type="password"
                  name="delete-password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </FormControl>
              <FormControl>
                <Checkbox name="delete-understood" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
                <FormControl.Label>Entiendo que mis datos se borrarán y no podré recuperarlos.</FormControl.Label>
              </FormControl>
              {deleteError && (
                <Flash variant="danger" role="alert">
                  {deleteError}
                </Flash>
              )}
              <div>
                <Button type="submit" variant="danger" leadingVisual={TrashIcon} loading={deleting} disabled={!password || !understood}>
                  Eliminar mi cuenta y mis datos
                </Button>
              </div>
            </form>
          </Caja.Cuerpo>
        </Caja>
      </div>
      <AuthPrivacyNotice open={noticeOpen} onClose={() => setNoticeOpen(false)} noticeUrl={noticeUrl} />
    </HojaLateral>
  );
}
