import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ContextoPanel, DefinicionPanel, PropsPanel } from "../paneles/tipos";
import { BellIcon, FileIcon, GearIcon } from "../ui/iconos";
import { PanelHost } from "./PanelHost";

function Escribible() {
  const [texto, setTexto] = useState("");
  return <input aria-label="Nota" value={texto} onChange={(e) => setTexto(e.target.value)} />;
}

function Legado({ open, onClose }: PropsPanel) {
  if (!open) return null;
  return (
    <div role="dialog" aria-label="Panel antiguo">
      <button type="button" onClick={onClose}>
        Cerrar antiguo
      </button>
    </div>
  );
}

const panel = (id: string, extra: Partial<DefinicionPanel>, componente: (p: PropsPanel) => React.ReactNode): DefinicionPanel => ({
  id,
  etiqueta: id,
  titulo: `Título de ${id}`,
  icono: BellIcon,
  orden: 1,
  cargar: () => Promise.resolve({ default: componente }),
  ...extra,
});

const PANELES = [
  panel("hoja", { presentacion: "dialogo", descripcion: "Una hoja lateral" }, () => <Escribible />),
  panel("pagina", { presentacion: "pagina", icono: FileIcon }, () => <p>Contenido de la página</p>),
  panel("antiguo", { icono: GearIcon }, Legado),
];

const ctx = {} as ContextoPanel;

describe("PanelHost", () => {
  it("shows a sheet panel as a named modal dialog that closes with Escape and keeps what was typed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { rerender } = render(<PanelHost zona="superpuesto" paneles={PANELES} abierto="hoja" ctx={ctx} onClose={onClose} />);
    const dialogo = await screen.findByRole("dialog", { name: "Título de hoja" });
    expect(dialogo).toHaveAttribute("aria-modal", "true");
    expect(dialogo).toHaveAccessibleDescription("Una hoja lateral");
    await user.type(await screen.findByLabelText("Nota"), "hola");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();

    rerender(<PanelHost zona="superpuesto" paneles={PANELES} abierto={null} ctx={ctx} onClose={onClose} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    rerender(<PanelHost zona="superpuesto" paneles={PANELES} abierto="hoja" ctx={ctx} onClose={onClose} />);
    expect(await screen.findByLabelText("Nota")).toHaveValue("hola");
  });

  it("shows a page panel only in the page zone, with its header, and hides it when closed", async () => {
    const { rerender } = render(<PanelHost zona="pagina" paneles={PANELES} abierto="pagina" ctx={ctx} onClose={() => undefined} />);
    expect(await screen.findByText("Contenido de la página")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Título de pagina" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Título de pagina" })).toHaveFocus();
    rerender(<PanelHost zona="pagina" paneles={PANELES} abierto={null} ctx={ctx} onClose={() => undefined} />);
    expect(screen.getByText("Contenido de la página")).not.toBeVisible();

    render(<PanelHost zona="superpuesto" paneles={PANELES} abierto="pagina" ctx={ctx} onClose={() => undefined} />);
    expect(screen.getAllByText("Contenido de la página")).toHaveLength(1);
  });

  it("lets a legacy panel draw its own dialog and moves focus into it", async () => {
    render(<PanelHost zona="superpuesto" paneles={PANELES} abierto="antiguo" ctx={ctx} onClose={() => undefined} />);
    await act(() => Promise.resolve());
    expect(await screen.findByRole("dialog", { name: "Panel antiguo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cerrar antiguo" })).toHaveFocus();
  });
});
