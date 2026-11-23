// The root error boundary: a render error anywhere in the app shows a way out (reload) instead of a
// blank page. What was typed in the conversations is kept in this browser and comes back on reload.

import { Component, type ErrorInfo, type ReactNode } from "react";

import { Blankslate, Button } from "../ui";
import { AlertIcon } from "../ui/iconos";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("render error", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="flex min-h-dvh items-center justify-center px-4">
        <Blankslate>
          <Blankslate.Visual>
            <AlertIcon size={24} />
          </Blankslate.Visual>
          <Blankslate.Heading>Algo ha fallado al mostrar la aplicación</Blankslate.Heading>
          <Blankslate.Description>
            Tus consultas siguen guardadas en este navegador. Recarga la página para continuar; si vuelve a pasar, avísanos.
          </Blankslate.Description>
          <div className="mt-4">
            <Button variant="primary" onClick={() => window.location.reload()}>
              Recargar
            </Button>
          </div>
        </Blankslate>
      </div>
    );
  }
}
