// Test data for the council components (DictamenConsejo, ConvocarConsejo, the «Consejo» panel).

import type { ResumenSesion, SesionConsejo } from "../../apiConsejo";

export function resumen(extra: Partial<ResumenSesion> = {}): ResumenSesion {
  return {
    id: "s1",
    estado: "terminada",
    fase: null,
    pregunta_usuario: "¿Amortizo la hipoteca o invierto los ahorros?",
    creada: new Date().toISOString(),
    actualizada: new Date().toISOString(),
    estimacion_usd: "0.40",
    tope_usd: "1.00",
    coste_usd: "0.35",
    minutos_estimados: 4,
    origen: "usuario",
    recomendacion: "Amortizar",
    consenso: "mayoría",
    confianza: 0.7,
    parcial: null,
    notificacion_id: "n9",
    error: null,
    ...extra,
  };
}

export function sesion(extra: Partial<SesionConsejo> = {}): SesionConsejo {
  return {
    ...resumen(),
    miembros: ["Prudente", "Inversor"],
    motivo: "Importe grande y difícil de deshacer",
    opciones_sugeridas: [],
    precios_conocidos: true,
    llamadas_estimadas: 12,
    desglose_usd: {},
    progreso: [],
    coste: { llamadas: 12, usd: "0.35", tokens_entrada: 1, tokens_salida: 1 },
    expediente: {
      id: "e1",
      pregunta: "¿Amortizar o invertir?",
      opciones: [
        { id: "a", descripcion: "Amortizar la hipoteca" },
        { id: "b", descripcion: "Invertir los ahorros" },
      ],
      hechos: [],
      cifras: [{ id: "c1", especialista: "hipoteca", concepto: "Ahorro en intereses", valor: "3200", literal: "3.200 €" }],
      fragment_ids: [],
      perfil_minimo: { edad: "40-49" },
      restricciones: [],
      incertidumbres: [],
      preferencia_usuario: null,
      anonimizado: ["Nombre y dirección"],
    },
    propuestas: [
      { miembro_id: "m1", ronda: 1, recomendacion: "a", confianza: 0.8, razonamiento: "Reduce el riesgo.", supuestos: [], riesgos: [], que_me_haria_cambiar: [], cifras_usadas: [] },
      { miembro_id: "m2", ronda: 1, recomendacion: "b", confianza: 0.6, razonamiento: "Más rentabilidad.", supuestos: [], riesgos: [], que_me_haria_cambiar: [], cifras_usadas: [] },
    ],
    criticas: [],
    rankings: [],
    dictamen: {
      sesion_id: "s1",
      recomendacion: "a",
      confianza: 0.72,
      consenso: "mayoria",
      acuerdo: 0.5,
      resumen: "Conviene **amortizar**.",
      posturas: [
        { opcion_id: "a", a_favor: ["Menos riesgo"], en_contra: [], miembros: ["m1"] },
        { opcion_id: "b", a_favor: ["Más rentabilidad"], en_contra: ["Volatilidad"], miembros: ["m2"] },
      ],
      disenso: "Invertir rinde más a largo plazo.",
      supuestos_decisivos: ["El tipo de interés no baja"],
      sensibilidad: [],
      datos_que_resolverian: [],
      puerta: { violaciones: [], atribuciones: [{ literal: "3.200 €", valor: "3200", cifra_id: "c1", especialista: "hipoteca", del_usuario: false }] },
      miembros: [
        { rol: { id: "m1", nombre: "Prudente", mandato: "", proposito: "" }, proveedor: "proveedor-a", modelo: "modelo-a" },
        { rol: { id: "m2", nombre: "Inversor", mandato: "", proposito: "" }, proveedor: "proveedor-b", modelo: "modelo-b" },
      ],
      autoria: { A: "m1", B: "m2" },
      avisos: [],
    },
    autoria_revelada: false,
    ...extra,
  };
}
