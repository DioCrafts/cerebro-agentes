// The answer to «¿estoy obligado a declarar?» as /simulate returns it inside each IRPF result
// (asistente/ambitos/fiscal/calculo/obligacion.py, ResultadoObligacion), and the facts the simulator asks for it.

export interface ReglaObligacion {
  referencia: string;
  boe_id: string;
  cita: string;
  aplicacion: string;
}

export interface ObligacionDeclarar {
  ejercicio: number;
  obligado: "si" | "no" | "no_se_sabe";
  respuesta: string;
  regla: ReglaObligacion | null;
  otras_reglas: ReglaObligacion[];
  datos_que_faltan: Array<{ campo: string | null; pregunta: string; tipo: "si_no" | "importe" | "numero" }>;
  recomendacion: string | null;
  resultado_si_declara: string | null;
  devolucion_estimada: string | null;
  avisos: string[];
}

type Kind = "si_no" | "importe" | "numero";

// The facts this card asks for (IRPFInput fields / profile facts), in the order they are asked.
export const OBLIGACION_FIELDS: Array<{ name: string; label: string; kind: Kind }> = [
  { name: "num_pagadores_trabajo", label: "Pagadores de tus rendimientos del trabajo", kind: "numero" },
  { name: "importe_segundo_y_restantes_pagadores", label: "Cobrado de todos los pagadores salvo el principal", kind: "importe" },
  { name: "pensiones_procedimiento_especial", label: "Solo cobro pensiones, con la retención fijada por el procedimiento especial", kind: "si_no" },
  { name: "pension_compensatoria_o_anualidades", label: "Cobro una pensión compensatoria o anualidades por alimentos no exentas", kind: "si_no" },
  { name: "pagador_no_obligado_a_retener", label: "Algún pagador no estaba obligado a retenerme", kind: "si_no" },
  { name: "trabajo_tipo_fijo_retencion", label: "Cobré rendimientos del trabajo con un tipo fijo de retención", kind: "si_no" },
  { name: "ganancias_sometidas_a_retencion", label: "Mis ganancias patrimoniales tenían retención", kind: "si_no" },
  { name: "alta_autonomo_en_el_ejercicio", label: "Estuve de alta como autónomo en algún momento del año", kind: "si_no" },
];

// Facts answered «sí» / «no» / unknown: sent as booleans, never as numbers.
export const OBLIGACION_SI_NO = new Set(OBLIGACION_FIELDS.filter((f) => f.kind === "si_no").map((f) => f.name));
