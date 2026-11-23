import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import "../test/jsdom";
import * as api from "../api";
import type { ConsentState } from "../api";
import { ProveedorUI } from "../ui";
import { PrivacyPanel } from "./PrivacyPanel";

const consentimientos: ConsentState[] = [
  { type: "tratamiento_datos", granted: true, timestamp: "2026-11-01T10:00:00Z", text_version: "v1" },
  { type: "envio_documentos_a_proveedor_llm", granted: false, timestamp: null, text_version: null },
  { type: "avisos_por_correo", granted: false, timestamp: null, text_version: null },
];

function pintar(onAccountDeleted = vi.fn(), onClose = vi.fn()) {
  render(
    <ProveedorUI>
      <PrivacyPanel open onClose={onClose} email="ana@example.com" noticeUrl="" onAccountDeleted={onAccountDeleted} />
    </ProveedorUI>,
  );
  return { onAccountDeleted, onClose };
}

beforeEach(() => {
  vi.spyOn(api, "getConsents").mockResolvedValue({ consents: consentimientos } as Awaited<ReturnType<typeof api.getConsents>>);
});

describe("PrivacyPanel", () => {
  it("is a side sheet named by its title, with the account under it, that closes with Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = pintar();
    const hoja = screen.getByRole("dialog", { name: "Privacidad y datos" });
    expect(hoja).toHaveAccessibleDescription("ana@example.com");
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("changes a consent, but never the one an account needs", async () => {
    const user = userEvent.setup();
    const actualizar = vi
      .spyOn(api, "updateConsents")
      .mockResolvedValue({ consents: consentimientos } as Awaited<ReturnType<typeof api.updateConsents>>);
    pintar();
    expect(await screen.findByRole("checkbox", { name: "Tratamiento de mis datos" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Avisos por correo" }));
    expect(actualizar).toHaveBeenCalledWith({ avisos_por_correo: true });
  });

  it("deletes the account only after the password, the check and a confirmation", async () => {
    const user = userEvent.setup();
    const borrar = vi.spyOn(api, "deleteMyAccount").mockResolvedValue(null);
    const { onAccountDeleted } = pintar();
    const zona = screen.getByRole("region", { name: "Eliminar mi cuenta" });
    const boton = within(zona).getByRole("button", { name: "Eliminar mi cuenta y mis datos" });
    expect(boton).toBeDisabled();
    await user.type(within(zona).getByLabelText("Confirma tu contraseña"), "secreta-larga");
    await user.click(within(zona).getByRole("checkbox", { name: /Entiendo que mis datos se borrarán/ }));
    await user.click(boton);

    const confirmacion = await screen.findByRole("alertdialog");
    expect(confirmacion).toHaveTextContent("No se puede deshacer.");
    await user.click(within(confirmacion).getByRole("button", { name: "Eliminar definitivamente" }));
    await waitFor(() => expect(borrar).toHaveBeenCalledWith("secreta-larga"));
    expect(onAccountDeleted).toHaveBeenCalled();
  });
});
