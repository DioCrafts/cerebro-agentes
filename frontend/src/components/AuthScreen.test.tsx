import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "../test/jsdom";
import * as api from "../api";
import type { AuthConfig, SessionResponse } from "../api";
import { ProveedorUI } from "../ui";
import { AuthScreen } from "./AuthScreen";

const config: AuthConfig = { auth_mode: "required", password_min_length: 10, consent_text_version: "v1", privacy_notice_url: "" };
const sesion = { token: "t" } as unknown as SessionResponse;

function pintar(onSignedIn = vi.fn(), notice: string | null = null) {
  render(
    <ProveedorUI>
      <AuthScreen config={config} notice={notice} onSignedIn={onSignedIn} />
    </ProveedorUI>,
  );
  return onSignedIn;
}

describe("AuthScreen", () => {
  it("signs in with the app's name and no other brand", async () => {
    const user = userEvent.setup();
    const login = vi.spyOn(api, "login").mockResolvedValue(sesion);
    const onSignedIn = pintar(vi.fn(), "Tu sesión ha caducado.");
    expect(screen.getByRole("heading", { level: 1, name: "Inicia sesión en HaciendaGPT" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Tu sesión ha caducado.");
    expect(screen.queryByText(/github/i)).toBeNull();

    const entrar = screen.getByRole("button", { name: "Entrar" });
    expect(entrar).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: /Correo electrónico/ }), "ana@example.com");
    await user.type(screen.getByLabelText(/Contraseña/), "secreta-larga");
    await user.click(entrar);
    expect(login).toHaveBeenCalledWith({ email: "ana@example.com", password: "secreta-larga" });
    expect(onSignedIn).toHaveBeenCalledWith(sesion);
  });

  it("shows the server's refusal", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "login").mockRejectedValue(new api.ApiError("Correo o contraseña incorrectos.", 401));
    pintar();
    await user.type(screen.getByRole("textbox", { name: /Correo electrónico/ }), "ana@example.com");
    await user.type(screen.getByLabelText(/Contraseña/), "mala");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Correo o contraseña incorrectos.");
  });

  it("creates an account only with a long enough password and the required consent", async () => {
    const user = userEvent.setup();
    const register = vi.spyOn(api, "register").mockResolvedValue(sesion);
    pintar();
    await user.click(screen.getByRole("button", { name: "Crea una cuenta" }));
    expect(screen.getByRole("heading", { level: 1, name: "Crea tu cuenta de HaciendaGPT" })).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /Correo electrónico/ }), "ana@example.com");
    await user.type(screen.getByLabelText(/Contraseña/), "corta");
    await user.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(screen.getByRole("alert")).toHaveTextContent("al menos 10 caracteres");

    await user.type(screen.getByLabelText(/Contraseña/), "-y-ya-larga");
    await user.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(screen.getByRole("alert")).toHaveTextContent("aceptar el tratamiento de tus datos");
    expect(register).not.toHaveBeenCalled();

    await user.click(screen.getByRole("checkbox", { name: /Acepto que se guarden y traten mis datos/ }));
    await user.click(screen.getByRole("checkbox", { name: /Avisos por correo/ }));
    await user.click(screen.getByRole("button", { name: "Crear cuenta" }));
    expect(register).toHaveBeenCalledWith({
      email: "ana@example.com",
      password: "corta-y-ya-larga",
      consents: { tratamiento_datos: true, envio_documentos_a_proveedor_llm: false, avisos_por_correo: true },
    });
  });

  it("explains what happens to the data in a dialog", async () => {
    const user = userEvent.setup();
    pintar();
    await user.click(screen.getByRole("button", { name: "Privacidad" }));
    const dialogo = await screen.findByRole("dialog", { name: "Qué hacemos con tus datos" });
    expect(within(dialogo).getByText(/todavía no ha publicado el suyo/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
