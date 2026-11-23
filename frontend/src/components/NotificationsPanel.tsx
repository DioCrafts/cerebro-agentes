// What the advisor has to tell the user without being asked: deadlines coming
// up, the year-end plan, law changes that affect their profile, the notices of
// the household modules the user switched on, and the weekly digest. Grouped by
// module, the digest first.
//
// Migrated to the design system (docs/ui/sistema-de-diseno.md): the reference example. It renders only its
// content; the shell shows it in a side sheet (registro.ts: presentacion "dialogo") with the title and icon.

import type { AdvisorNotification } from "../api";
import { moduloDeKind } from "../apiModulos";
import { formatDate, formatMoney, relativeTime } from "../lib/presentation";
import { Blankslate, Button, Caja, CargandoLista, CounterLabel, Flash, Label } from "../ui";
import type { Icon } from "../ui/iconos";
import { BellIcon, CalendarIcon, CheckIcon, CommentDiscussionIcon, GraphIcon, HomeIcon, LawIcon, SlidersIcon, SyncIcon, TasklistIcon } from "../ui/iconos";
import { AvisoConsejo } from "./DictamenConsejo";
import { MemoriaBotones } from "./MemoriaBotones";
import { RepasoCard } from "./RepasoCard";
import { RichText } from "./RichText";

const KIND: Record<string, { icon: Icon; label: string }> = {
  plazo: { icon: CalendarIcon, label: "Plazo" },
  fin_de_ejercicio: { icon: GraphIcon, label: "Antes de fin de año" },
  cambio_normativo: { icon: LawIcon, label: "Cambio normativo" },
  resumen_semanal: { icon: TasklistIcon, label: "Resumen semanal" },
};

function kindInfo(kind: string, moduleNames: Record<string, string>): { icon: Icon; label: string } {
  const known = KIND[kind];
  if (known) return known;
  const clave = moduloDeKind(kind);
  return { icon: HomeIcon, label: clave ? (moduleNames[clave] ?? clave) : "Aviso" };
}

interface Group {
  key: string;
  title: string;
  items: AdvisorNotification[];
}

/** The digest first, then the tax notices, then each module (by name); newest first inside each. */
function groupByModule(notifications: AdvisorNotification[], moduleNames: Record<string, string>): Group[] {
  const groups = new Map<string, Group>();
  for (const n of notifications) {
    const clave = moduloDeKind(n.kind) ?? "resumen";
    const title =
      clave === "resumen" ? "Resumen semanal" : clave === "fiscal" ? "Impuestos" : clave === "consejo" ? "Consejo del hogar" : (moduleNames[clave] ?? clave);
    const group = groups.get(clave) ?? { key: clave, title, items: [] };
    group.items.push(n);
    groups.set(clave, group);
  }
  const rank = (g: Group) => (g.key === "resumen" ? 0 : g.key === "fiscal" ? 1 : 2);
  return [...groups.values()].sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title, "es"));
}

interface NotificationsPanelProps {
  notifications: AdvisorNotification[];
  hasCase: boolean;
  loading: boolean;
  error: string | null;
  onMarkRead: (id: string) => void;
  onRefresh: () => void;
  // Ask the advisor about a notification (prefills and sends the question).
  onAsk: (question: string) => void;
  // «Lo que sé de tus preferencias»: another panel.
  onOpenPreferences: () => void;
  // Display names of the household modules, by key.
  moduleNames?: Record<string, string>;
  // The case the notices belong to: enables «No me interesa / Ya lo hice / Recuérdamelo» and the preferences.
  caseId?: string | null;
}

export function NotificationsPanel({ notifications, hasCase, loading, error, onMarkRead, onRefresh, onAsk, onOpenPreferences, moduleNames = {}, caseId = null }: NotificationsPanelProps) {
  const grupos = groupByModule(notifications, moduleNames);
  return (
    <div className="flex flex-col gap-4">
      {(caseId || hasCase) && (
        <div className="flex flex-wrap justify-end gap-2">
          {caseId && (
            <Button size="small" leadingVisual={SlidersIcon} onClick={onOpenPreferences}>
              Tus preferencias
            </Button>
          )}
          {hasCase && (
            <Button size="small" leadingVisual={SyncIcon} onClick={onRefresh} disabled={loading} loading={loading}>
              Actualizar
            </Button>
          )}
        </div>
      )}

      {error && <Flash variant="danger">{error}</Flash>}

      {!hasCase && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <BellIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Aún no hay avisos</Blankslate.Heading>
          <Blankslate.Description>
            Cuando me cuentes tu situación, aquí te avisaré de los plazos que te tocan y de lo que puedes hacer antes de fin de año para
            pagar menos.
          </Blankslate.Description>
        </Blankslate>
      )}
      {hasCase && !error && notifications.length === 0 && !loading && (
        <Blankslate narrow>
          <Blankslate.Visual>
            <CheckIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading as="h3">Nada que avisarte ahora</Blankslate.Heading>
          <Blankslate.Description>
            Cuantos más datos tenga de tu situación (ingresos, familia, si tienes actividad económica), más útiles serán los avisos.
          </Blankslate.Description>
        </Blankslate>
      )}
      {hasCase && loading && notifications.length === 0 && <CargandoLista filas={2} etiqueta="Cargando los avisos" />}

      {grupos.map((group) => (
        <Caja key={group.key} as="section" aria-label={`Avisos: ${group.title}`}>
          <Caja.Cabecera titulo={group.title} acciones={<CounterLabel>{group.items.length}</CounterLabel>} />
          <Caja.Lista>
            {group.items.map((n) => (
              <Caja.Fila key={n.id}>
                <Aviso n={n} caseId={caseId} moduleNames={moduleNames} onMarkRead={onMarkRead} onRefresh={onRefresh} onAsk={onAsk} />
              </Caja.Fila>
            ))}
          </Caja.Lista>
        </Caja>
      ))}

      <p className="m-0 text-xs text-fg-muted">
        Los importes son estimaciones calculadas con los datos de tu expediente, cada uno por su módulo (no se suman entre sí); los plazos
        no tienen en cuenta festivos.
      </p>
    </div>
  );
}

interface AvisoProps {
  n: AdvisorNotification;
  caseId: string | null;
  moduleNames: Record<string, string>;
  onMarkRead: (id: string) => void;
  onRefresh: () => void;
  onAsk: (question: string) => void;
}

function Aviso({ n, caseId, moduleNames, onMarkRead, onRefresh, onAsk }: AvisoProps) {
  // The weekly/daily digest: the brain's structured repaso (falls back to the text when there is none).
  if (n.kind === "resumen_semanal" && caseId) {
    return <RepasoCard notification={n} caseId={caseId} onMarkRead={onMarkRead} onRefresh={onRefresh} onAsk={onAsk} />;
  }
  // The household council's ruling: its text, and the ruling itself on demand.
  if (n.kind === "modulo:consejo" && caseId) {
    return <AvisoConsejo notification={n} caseId={caseId} onMarkRead={onMarkRead} />;
  }
  const kind = kindInfo(n.kind, moduleNames);
  const Icono = kind.icon;
  const unread = !n.read_at;
  const urgente = n.severity === "urgente";
  const fiscal = moduloDeKind(n.kind) === "fiscal";
  const importe = n.amount && Number(n.amount) > 0 ? n.amount : null;
  return (
    <article aria-label={n.title} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <Icono className={urgente ? "text-fg-danger" : "text-fg-muted"} />
        <span>{kind.label}</span>
        {urgente && <Label variant="danger">Urgente</Label>}
        {unread && <Label variant="accent">Nuevo</Label>}
        <span className="ml-auto">{relativeTime(Date.parse(n.created_at))}</span>
      </div>
      <h4 className="m-0 text-base font-semibold">{n.title}</h4>
      {(importe || n.due_date) && (
        <div className="flex flex-wrap gap-2">
          {importe && fiscal && <Label variant="success">hasta −{formatMoney(importe)} (estimado)</Label>}
          {importe && !fiscal && <Label variant="secondary">importe: {formatMoney(importe)}</Label>}
          {n.due_date && <Label variant="attention">hasta el {formatDate(n.due_date)}</Label>}
        </div>
      )}
      <RichText text={n.body} />
      {n.actions.length > 0 && (
        <ul className="m-0 list-disc pl-6">
          {n.actions.map((action, i) => (
            <li key={i}>{action}</li>
          ))}
        </ul>
      )}
      {n.sources.length > 0 && <p className="m-0 text-xs text-fg-muted">{n.sources.join(" · ")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="small" leadingVisual={CommentDiscussionIcon} onClick={() => onAsk(`Sobre el aviso «${n.title}»: explícame cómo me afecta y qué debería hacer.`)}>
          Preguntar al asesor
        </Button>
        {unread && (
          <Button size="small" variant="invisible" leadingVisual={CheckIcon} onClick={() => onMarkRead(n.id)}>
            Marcar como leído
          </Button>
        )}
      </div>
      {caseId && n.kind !== "resumen_semanal" && <MemoriaBotones caseId={caseId} notificationId={n.id} onDone={onRefresh} />}
    </article>
  );
}
