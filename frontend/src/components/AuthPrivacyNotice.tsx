// Plain-language summary of what the service does with the user's data, shown
// from the register screen and the privacy panel. It describes what the
// SOFTWARE does; the operator's legal privacy notice (who the controller is,
// legal basis, retention…) is a separate text the operator must publish.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): a centred Primer Dialog, which traps focus,
// closes with Escape (before any sheet underneath) and gives focus back to what opened it.

import type { ReactNode } from "react";

import { Dialog, Flash, Heading, Link } from "../ui";

interface AuthPrivacyNoticeProps {
  open: boolean;
  onClose: () => void;
  // The operator's own privacy notice, when it has published one.
  noticeUrl: string;
}

function Seccion({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1">
      <Heading as="h3" variant="small">
        {titulo}
      </Heading>
      <ul className="m-0 flex list-disc flex-col gap-1 pl-5">{children}</ul>
    </section>
  );
}

export function AuthPrivacyNotice({ open, onClose, noticeUrl }: AuthPrivacyNoticeProps) {
  if (!open) return null;
  return (
    <Dialog title="Qué hacemos con tus datos" onClose={onClose} width="large">
      <div className="flex flex-col gap-4">
        <Seccion titulo="Qué se guarda">
          <li>Tu correo y una huella irreversible de tu contraseña (nunca la contraseña).</li>
          <li>
            Tu expediente: los datos fiscales, familiares y, si los aportas, de discapacidad que le cuentas al asesor, junto con el registro
            de las consultas que los generaron.
          </li>
          <li>Los avisos que el asesor prepara para ti y los consentimientos que das o retiras, con su fecha.</li>
          <li>El texto de las conversaciones se guarda solo en este navegador, no en el servidor.</li>
        </Seccion>
        <Seccion titulo="Qué se envía al proveedor del modelo de lenguaje">
          <li>Tus preguntas, la conversación en curso y los datos de tu expediente que el asesor necesita para responder.</li>
          <li>
            El contenido de los documentos que subas, <strong>solo si das tu consentimiento expreso</strong>. Los documentos no se
            almacenan.
          </li>
          <li>El simulador, el calendario y los avisos se calculan sin enviar nada a ningún proveedor.</li>
        </Seccion>
        <Seccion titulo="Qué puedes hacer">
          <li>Descargar todo lo que hay guardado sobre ti, en cualquier momento.</li>
          <li>Eliminar tu cuenta: se borran tu expediente, tus avisos, tus consentimientos y tus sesiones. No se puede deshacer.</li>
          <li>Dar o retirar cada consentimiento cuando quieras, desde «Privacidad y datos».</li>
        </Seccion>
        <Flash variant={noticeUrl ? "default" : "warning"}>
          Este resumen describe lo que hace la aplicación. Quién es el responsable del tratamiento, con qué base legal trata tus datos,
          durante cuánto tiempo y cómo ejercer tus derechos lo indica el aviso de privacidad de quien te ofrece este servicio.{" "}
          {noticeUrl ? (
            <Link href={noticeUrl} target="_blank" rel="noreferrer">
              Leer el aviso de privacidad
            </Link>
          ) : (
            <strong>Este servicio todavía no ha publicado el suyo: pídeselo antes de aportar datos reales.</strong>
          )}
        </Flash>
      </div>
    </Dialog>
  );
}
