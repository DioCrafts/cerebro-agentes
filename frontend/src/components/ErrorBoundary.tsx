// The root error boundary: a render error anywhere in the app shows a way out (reload) instead of a
// blank page. What was typed in the conversations is kept in this browser and comes back on reload.

import { Component, type ErrorInfo, type ReactNode } from "react";

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
      <div role="alert" className="flex h-dvh w-screen flex-col items-center justify-center gap-3 bg-paper px-6 text-center">
        <p className="font-serif text-[19px] font-bold text-ink">Algo ha fallado al mostrar la aplicación</p>
        <p className="max-w-md text-[13.5px] leading-snug text-ink-soft">
          Tus consultas siguen guardadas en este navegador. Recarga la página para continuar; si vuelve a pasar, avísanos.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-lg bg-primary px-4 py-2 text-[14px] font-semibold text-white transition hover:bg-primary-hover"
        >
          Recargar
        </button>
      </div>
    );
  }
}
