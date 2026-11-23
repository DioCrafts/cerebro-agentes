import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ClaimCheck } from "../api";
import { VerificationPanel } from "./VerificationPanel";

const claims: ClaimCheck[] = [
  { text: "El mínimo personal es 5.550 €.", fragment_ids: ["F1"], verdict: "respaldada", reason: "" },
  { text: "La deducción es del 20 %.", fragment_ids: ["F2"], verdict: "no_respaldada", reason: "La fuente dice 15 %." },
];

describe("VerificationPanel", () => {
  it("opens with the problems first and lets the reader jump to a source", async () => {
    const user = userEvent.setup();
    const onCite = vi.fn();
    render(<VerificationPanel claims={claims} otherWarnings={["El verificador no respondió a tiempo."]} onCite={onCite} />);
    expect(screen.getByText("El verificador no respondió a tiempo.")).toBeInTheDocument();
    expect(screen.getByText("1 a revisar")).toBeInTheDocument();
    const filas = within(screen.getByRole("list", { name: "Afirmaciones comprobadas" })).getAllByRole("listitem") as [HTMLElement, HTMLElement];
    expect(filas[0]).toHaveTextContent("La deducción es del 20 %.");
    expect(filas[0]).toHaveTextContent("Sin respaldo en la fuente");
    expect(filas[0]).toHaveTextContent("La fuente dice 15 %.");
    expect(filas[1]).toHaveTextContent("Respaldada por la fuente");
    await user.click(screen.getByRole("button", { name: "Ver la fuente F2" }));
    expect(onCite).toHaveBeenCalledWith("F2");
  });

  it("renders nothing when there is nothing to report", () => {
    const { container } = render(<VerificationPanel claims={[]} otherWarnings={[]} onCite={() => undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
