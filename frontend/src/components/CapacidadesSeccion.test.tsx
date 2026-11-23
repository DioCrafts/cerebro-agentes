import "../test/primerEnJsdom";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import type { CapacidadInfo } from "../apiModulos";

vi.mock("../apiModulos", async (original) => ({
  ...(await original<typeof import("../apiModulos")>()),
  getCapacidades: vi.fn(),
  setCapacidad: vi.fn(),
}));

import * as api from "../apiModulos";
import { CapacidadesSeccion } from "./CapacidadesSeccion";

const capacidad = (extra: Partial<CapacidadInfo>): CapacidadInfo => ({
  clave: "investigacion",
  nombre: "Investigación en la web",
  descripcion: "Busca en fuentes públicas",
  requiere_consentimiento: true,
  activa: false,
  elegida: false,
  usada_por: ["fiscal"],
  operaciones: [],
  elegida_el: null,
  modulo: null,
  ...extra,
});

describe("CapacidadesSeccion", () => {
  it("switches a capability that needs consent; one that belongs to a module only says so", async () => {
    const user = userEvent.setup();
    vi.mocked(api.getCapacidades).mockResolvedValue({
      configurable: true,
      capacidades: [capacidad({}), capacidad({ clave: "correo", nombre: "Correo", modulo: "correo", activa: true })],
    });
    vi.mocked(api.setCapacidad).mockResolvedValue({ configurable: true, capacidades: [capacidad({ activa: true })] });
    render(<CapacidadesSeccion nombres={{ correo: "Correo (Gmail)", fiscal: "Impuestos" }} disabled={false} />);
    expect(await screen.findByText("Activada con el módulo «Correo (Gmail)».")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Correo" })).not.toBeInTheDocument();
    const interruptor = screen.getByRole("button", { name: "Investigación en la web" });
    expect(interruptor).toHaveAttribute("aria-pressed", "false");
    await user.click(interruptor);
    await waitFor(() => expect(api.setCapacidad).toHaveBeenCalledWith("investigacion", true));
    expect(await screen.findByText("Activada: le has dado permiso.")).toBeInTheDocument();
  });

  it("an older backend without capabilities is not an error", async () => {
    vi.mocked(api.getCapacidades).mockRejectedValue(new ApiError("No encontrado", 404));
    render(<CapacidadesSeccion nombres={{}} disabled={false} />);
    expect(await screen.findByText("Este servicio todavía no gestiona las capacidades por separado.")).toBeInTheDocument();
  });
});
