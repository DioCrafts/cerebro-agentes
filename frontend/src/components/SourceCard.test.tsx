import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Citation } from "../api";
import { SourceCardList } from "./SourceCard";

const cita: Citation = {
  title: "Ley 35/2006 del IRPF",
  locator: "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
  fragment_id: "F1",
  binding: true,
  vigencia: "discutida",
  vigencia_resumen: "El TS matizó el criterio en 2024.",
  snippet: "Se consideran rendimientos íntegros del trabajo…",
  authority: "Jefatura del Estado",
  date: "2006-11-28",
  reference: "BOE-A-2006-20764",
  section: "Artículo 17",
  source_type: "ley",
};

describe("SourceCardList", () => {
  it("shows each cited source with its labels, passage and link", () => {
    render(<SourceCardList citations={[cita]} scope="t1" highlightedId={null} />);
    const lista = screen.getByRole("region", { name: "Fuentes citadas" });
    const tarjeta = within(lista).getByRole("article", { name: "F1: Ley 35/2006 del IRPF" });
    expect(within(tarjeta).getByText("Vinculante")).toBeInTheDocument();
    expect(within(tarjeta).getByText("Criterio discutido")).toBeInTheDocument();
    expect(tarjeta).toHaveTextContent("El TS matizó el criterio en 2024.");
    expect(within(tarjeta).getByRole("link", { name: /Abrir en/ })).toHaveAttribute("href", cita.locator);
  });

  it("renders nothing without citations", () => {
    const { container } = render(<SourceCardList citations={[]} scope="t1" highlightedId={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
