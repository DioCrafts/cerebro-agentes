import "../test/primerEnJsdom";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Fact } from "../api";

vi.mock("../api", async (original) => ({
  ...(await original<typeof import("../api")>()),
  getCalendar: vi.fn(),
  analyzeDocument: vi.fn(),
}));

import * as api from "../api";
import { CasePanel } from "./CasePanel";

function tipoDe(value: Fact["value"]): Fact["value_type"] {
  if (Array.isArray(value)) return "array";
  if (typeof value === "string") return "string";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "object";
}

const hecho = (name: string, value: Fact["value"]): Fact => ({
  fact_id: name,
  name,
  value,
  value_type: tipoDe(value),
  source: "user",
  confidence: 1,
  updated_at: "2026-11-01",
});

function montar(extra: Partial<Parameters<typeof CasePanel>[0]> = {}) {
  const props = {
    facts: [hecho("ciudad", "Valencia")],
    taxPeriod: "2025",
    obligations: [],
    caseId: "c1",
    onChangeFact: vi.fn().mockResolvedValue(true),
    saving: null,
    error: null,
    disabled: false,
    ...extra,
  };
  render(<CasePanel {...props} />);
  return props;
}

describe("CasePanel", () => {
  beforeEach(() => {
    vi.mocked(api.getCalendar).mockResolvedValue({ vencimientos: [], aviso: "" } as never);
  });

  it("explains what will appear while there are no facts", () => {
    montar({ facts: [], taxPeriod: null });
    expect(screen.getByRole("heading", { name: "Todavía no he anotado nada" })).toBeInTheDocument();
  });

  it("shows the facts as rows; the fiscal year cannot be deleted", () => {
    montar();
    const datos = screen.getByRole("region", { name: "Tus datos" });
    expect(within(datos).getAllByRole("listitem")).toHaveLength(2);
    expect(within(datos).getByText("Valencia")).toBeInTheDocument();
    expect(within(datos).queryByRole("button", { name: /Eliminar «Periodo fiscal»|Eliminar «Ejercicio/ })).not.toBeInTheDocument();
  });

  it("edits a fact in place", async () => {
    const user = userEvent.setup();
    const props = montar();
    const editar = screen.getAllByRole("button", { name: /^Editar «/ }).at(-1);
    if (!editar) throw new Error("no edit button");
    await user.click(editar);
    const campo = screen.getByRole("textbox");
    await user.clear(campo);
    await user.type(campo, "Alicante");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(props.onChangeFact).toHaveBeenCalledWith("ciudad", "Alicante"));
  });

  it("Escape cancels the edit without saving", async () => {
    const user = userEvent.setup();
    const props = montar();
    const editar = screen.getAllByRole("button", { name: /^Editar «/ }).at(-1);
    if (!editar) throw new Error("no edit button");
    await user.click(editar);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(props.onChangeFact).not.toHaveBeenCalled();
  });

  it("deletes a fact only after confirming", async () => {
    const user = userEvent.setup();
    const props = montar();
    const borrar = screen.getAllByRole("button", { name: /^Eliminar «/ })[0];
    if (!borrar) throw new Error("no delete button");

    await user.click(borrar);
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancelar" }));
    expect(props.onChangeFact).not.toHaveBeenCalled();

    await user.click(borrar);
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Eliminar" }));
    await waitFor(() => expect(props.onChangeFact).toHaveBeenCalledWith("ciudad", null));
  });
});
