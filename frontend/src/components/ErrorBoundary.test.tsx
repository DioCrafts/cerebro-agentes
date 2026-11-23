import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Rompe(): never {
  throw new Error("boom");
}

describe("ErrorBoundary", () => {
  it("shows a way out instead of a blank page", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(
      <ErrorBoundary>
        <Rompe />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Algo ha fallado");
    expect(screen.getByRole("button", { name: "Recargar" })).toBeInTheDocument();
  });

  it("renders its children when nothing fails", () => {
    render(
      <ErrorBoundary>
        <p>todo bien</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("todo bien")).toBeInTheDocument();
  });
});
