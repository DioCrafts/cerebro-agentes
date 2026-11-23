import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DoctrineNotice } from "./DoctrineNotice";

describe("DoctrineNotice", () => {
  it("is silent when the doctrine is settled", () => {
    const { container } = render(<DoctrineNotice conflicts={[]} unsettled={false} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the problem and lists the conflicts", () => {
    render(<DoctrineNotice conflicts={["La V0001-20 fue superada por el TS"]} unsettled={false} />);
    const nota = screen.getByRole("note", { name: "Atención a la vigencia de las fuentes" });
    expect(nota).toHaveTextContent("La V0001-20 fue superada por el TS");
  });
});
