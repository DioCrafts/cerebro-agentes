import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TrustStrip } from "./TrustStrip";

describe("TrustStrip", () => {
  it("states the answer's verification as a status with its explanation", () => {
    render(<TrustStrip mode="partially_verified" detail="4 de 5 afirmaciones verificadas" />);
    const estado = screen.getByRole("status");
    expect(estado).toHaveTextContent("Verificada solo en parte");
    expect(estado).toHaveTextContent("4 de 5 afirmaciones verificadas");
    expect(estado).toHaveTextContent("Mira el detalle antes de fiarte");
  });
});
