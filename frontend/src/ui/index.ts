// The app's design system: Primer React (GitHub's open-source design system, MIT) plus the few pieces it
// does not ship, on Primer tokens painted with the app's colours (tema.css). Migrated code imports UI from
// here (and icons from "./iconos"), never from lucide-react or with Tailwind colour classes.
// Guide: docs/ui/sistema-de-diseno.md.

// --- Primer, re-exported as-is -------------------------------------------------------------------------
export {
  ActionList,
  ActionMenu,
  AnchoredOverlay,
  Avatar,
  Banner,
  Breadcrumbs,
  Button,
  ButtonGroup,
  Checkbox,
  CheckboxGroup,
  ConfirmationDialog,
  CounterLabel,
  Details,
  Dialog,
  Flash,
  FormControl,
  Heading,
  IconButton,
  Label,
  LabelGroup,
  Link,
  NavList,
  Overlay,
  PageHeader,
  PageLayout,
  Pagination,
  Popover,
  ProgressBar,
  Radio,
  RadioGroup,
  RelativeTime,
  SegmentedControl,
  Select,
  SelectPanel,
  SkeletonBox,
  Spinner,
  SplitPageLayout,
  Stack,
  StateLabel,
  SubNav,
  Text,
  TextInput,
  Textarea,
  Timeline,
  ToggleSwitch,
  Token,
  Tooltip,
  Truncate,
  UnderlineNav,
  VisuallyHidden,
  useConfirm,
} from "@primer/react";
export type { ButtonProps, IconButtonProps } from "@primer/react";
export { Blankslate, InlineMessage, SkeletonAvatar, SkeletonText } from "@primer/react/experimental";

// --- The app's own pieces ------------------------------------------------------------------------------
import { CajaCabecera, CajaCuerpo, CajaFila, CajaLista, CajaPie, CajaRaiz } from "./Caja";
/** GitHub's Box: `<Caja>` with `Caja.Cabecera`, `Caja.Cuerpo`, `Caja.Lista` › `Caja.Fila`, `Caja.Pie`. */
export const Caja = Object.assign(CajaRaiz, { Cabecera: CajaCabecera, Cuerpo: CajaCuerpo, Lista: CajaLista, Fila: CajaFila, Pie: CajaPie });
export { BotonCabecera, BotonComandos, CabeceraApp, Migas, SeparadorCabecera } from "./CabeceraApp";
export { CargandoBloque, CargandoLista, CargandoTexto } from "./Cargando";
export { Comentario, Conversacion, EventoConversacion } from "./Conversacion";
export { HojaLateral, type HojaLateralProps } from "./HojaLateral";
export { Dialogo, NivelTitulo, TituloSeccion } from "./Dialogo";
export { Iniciales } from "./Iniciales";
export { PaginaPanel, type PaginaPanelProps } from "./PaginaPanel";
export { PaletaComandos } from "./PaletaComandos";
export { PestanasNavegacion, type Pestana } from "./PestanasNavegacion";
export { filtrarComandos, type Comando } from "./comandos";
export { ProveedorUI } from "./ProveedorUI";
