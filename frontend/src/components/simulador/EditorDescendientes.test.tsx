import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { emptyChild } from "../../lib/simulador/descendientes";
import { EditorDescendientes } from "./EditorDescendientes";

describe("EditorDescendientes", () => {
  it("adds a child, names each box and expense after the child, and removes it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<EditorDescendientes rows={[]} problems={[]} year={2026} brokenSaved={false} onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Añadir el detalle de un hijo" }));
    expect(onChange).toHaveBeenCalledWith([expect.objectContaining({ edad: "" })]);

    const hijo = { ...emptyChild("h1"), fecha: "2020-05-01" };
    rerender(<EditorDescendientes rows={[hijo]} problems={["la fecha no es válida"]} year={2026} brokenSaved={false} onChange={onChange} />);
    expect(screen.getByRole("listitem", { name: "Hijo 1" })).toHaveTextContent("6 años a 31-12-2026");
    expect(screen.getByLabelText(/^o edad a 31-12-2026/)).toHaveAttribute("readonly");
    expect(screen.getByRole("alert")).toHaveTextContent("Hijo 1: la fecha no es válida");
    expect(screen.getAllByLabelText(/del hijo 1$/).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Quitar el hijo 1" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
