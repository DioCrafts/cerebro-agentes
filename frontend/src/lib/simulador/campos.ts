// The boxes of the simulator: the state's figures, Madrid's own deductions and the regional ones by
// subject. Pure data (the simulator screen reads it).

import type { FieldDef as BaseFieldDef } from "../formulario";

export type FieldKind = "money" | "count" | "year" | "flag";
export type FieldDef = BaseFieldDef<FieldKind>;
export interface Group {
  title: string;
  fields: FieldDef[];
}

export const GROUPS: Group[] = [
  {
    title: "Ingresos del año",
    fields: [
      { name: "rendimiento_trabajo_bruto", label: "Salario bruto", kind: "money" },
      { name: "cotizaciones_seguridad_social", label: "Cotizaciones a la Seguridad Social", kind: "money", hint: "Lo que te descuentan en nómina (≈ 6,5 % del bruto)" },
      { name: "retenciones", label: "IRPF ya retenido", kind: "money" },
      { name: "rendimiento_actividad_neto", label: "Beneficio neto de tu actividad", kind: "money", hint: "Si eres autónomo: calcúlalo en el panel «Autónomos»" },
      { name: "rendimiento_capital_mobiliario", label: "Intereses y dividendos", kind: "money" },
      { name: "ganancias_patrimoniales", label: "Ganancias por ventas", kind: "money", hint: "Acciones, fondos, inmuebles… (negativo si son pérdidas)" },
    ],
  },
  {
    title: "Familia",
    fields: [
      { name: "num_descendientes", label: "Hijos a cargo", kind: "count" },
      { name: "num_descendientes_menores_3", label: "De ellos, menores de 3 años", kind: "count" },
      { name: "num_descendientes_discapacidad", label: "Hijos con discapacidad del 33 % o más", kind: "count", hint: "Cuentan a cualquier edad si viven contigo" },
      { name: "num_descendientes_discapacidad_65", label: "De ellos, con discapacidad del 65 % o más", kind: "count" },
      { name: "num_descendientes_asistencia", label: "Hijos del 33 % al 64 % que necesitan ayuda de terceros", kind: "count", hint: "O que tienen movilidad reducida (desde el 65 % ya se suma)" },
      { name: "num_ascendientes_mayores_65", label: "Padres o abuelos mayores de 65 que viven contigo", kind: "count", hint: "Al menos medio año y sin rentas de más de 8.000 €" },
      { name: "num_ascendientes_mayores_75", label: "De ellos, mayores de 75", kind: "count" },
      { name: "num_ascendientes_discapacidad", label: "Padres o abuelos con discapacidad del 33 % o más", kind: "count", hint: "Que viven contigo, tengan la edad que tengan" },
      { name: "num_ascendientes_discapacidad_hasta_65", label: "De ellos, de 65 años o menos", kind: "count", hint: "También dan derecho al mínimo por ascendientes" },
      { name: "num_ascendientes_discapacidad_65", label: "De ellos, con discapacidad del 65 % o más", kind: "count" },
      { name: "num_ascendientes_asistencia", label: "Padres o abuelos del 33 % al 64 % que necesitan ayuda de terceros", kind: "count", hint: "O que tienen movilidad reducida" },
      { name: "num_declaraciones_mismos_ascendientes", label: "Declaraciones que aplican el mínimo por ellos", kind: "count", hint: "Contando la tuya: 2 si un hermano también vive con ellos; se reparte" },
    ],
  },
  {
    title: "Lo que puedes mover",
    fields: [
      { name: "aportacion_plan_pensiones", label: "Aportación a plan de pensiones", kind: "money", hint: "Reduce la base hasta 1.500 € al año" },
      { name: "donativos", label: "Donativos", kind: "money" },
    ],
  },
];
// The deductions the Comunidad de Madrid computes with its own rules; the groups below add
// the inputs of its other deductions.
export const MADRID_GROUP: Group = {
  title: "Deducciones de la Comunidad de Madrid",
  fields: [
    { name: "edad", label: "Tu edad", kind: "count", hint: "El alquiler solo deduce si tienes menos de 40 años" },
    { name: "alquiler_vivienda_habitual", label: "Alquiler pagado en el año", kind: "money" },
    { name: "hijos_nacidos_ultimos_3_ejercicios", label: "Hijos nacidos en los últimos 3 años", kind: "count" },
    { name: "gastos_escolaridad", label: "Gastos de escolaridad", kind: "money", hint: "Colegio privado no concertado o primer ciclo de infantil" },
    { name: "gastos_ensenanza_idiomas", label: "Clases de idiomas de los hijos", kind: "money" },
    { name: "gastos_uniformes_escolares", label: "Uniformes escolares", kind: "money" },
    { name: "cotizaciones_empleada_hogar", label: "Cotizaciones de empleada de hogar", kind: "money", hint: "Con un hijo menor de 3 años" },
    { name: "base_imponible_resto_unidad_familiar", label: "Rentas netas de tu pareja", kind: "money", hint: "Cuentan para los límites de renta familiar" },
  ],
};
// Inputs of the regional deductions computed from data, by subject. Where a law covers only
// part of an expense (only dental care, only interest…), the part is asked under its total.
export const REGIONAL_GROUPS: Group[] = [
  {
    title: "Tu comunidad · tú y tu hogar",
    fields: [
      { name: "edad", label: "Tu edad", kind: "count", hint: "Varias deducciones (alquiler, mayores) dependen de ella" },
      { name: "grado_discapacidad", label: "Tu grado de discapacidad (%)", kind: "count" },
      { name: "necesita_asistencia", label: "Necesito ayuda de terceras personas o tengo movilidad reducida", kind: "flag", hint: "Con una discapacidad del 33 % al 64 %, suma los gastos de asistencia al mínimo" },
      { name: "edad_conyuge", label: "Edad de tu cónyuge", kind: "count", hint: "En conjunta, a veces basta con que uno cumpla la edad" },
      { name: "grado_discapacidad_conyuge", label: "Discapacidad de tu cónyuge (%)", kind: "count" },
      { name: "necesita_asistencia_conyuge", label: "Mi cónyuge necesita ayuda de terceras personas o tiene movilidad reducida", kind: "flag", hint: "Cuenta en la declaración conjunta" },
      { name: "base_imponible_resto_unidad_familiar", label: "Rentas netas de tu pareja", kind: "money", hint: "Cuentan para los límites de renta familiar" },
      { name: "dias_desempleo", label: "Días del año en desempleo", kind: "count", hint: "Inscrito como demandante de empleo" },
      { name: "familia_numerosa", label: "Familia numerosa", kind: "flag" },
      { name: "familia_numerosa_especial", label: "De categoría especial", kind: "flag" },
      { name: "familia_monoparental", label: "Familia monoparental", kind: "flag" },
      { name: "viudedad", label: "Soy viudo o viuda", kind: "flag" },
      { name: "ejercicio_viudedad", label: "Año en que enviudaste", kind: "year" },
      { name: "victima_violencia_genero", label: "Tengo reconocida la condición de víctima de violencia de género", kind: "flag" },
      { name: "victima_terrorismo", label: "Tengo reconocida la condición de víctima del terrorismo", kind: "flag" },
    ],
  },
  {
    title: "Tu comunidad · hijos y personas a tu cargo",
    fields: [
      { name: "hijos_nacidos_en_el_ejercicio", label: "Hijos nacidos o adoptados este año", kind: "count" },
      { name: "hijos_nacidos_ultimos_3_ejercicios", label: "Nacidos o adoptados este año o los dos anteriores", kind: "count" },
      { name: "num_hijos_primer_ciclo_infantil", label: "Hijos en primer ciclo de infantil (0-3)", kind: "count" },
      { name: "num_hijos_gastos_educativos", label: "Hijos escolarizados", kind: "count", hint: "Por los que pagas libros o clases" },
      { name: "gastos_guarderia", label: "Guardería o escuela infantil (0-3)", kind: "money" },
      { name: "gastos_libros_texto", label: "Libros de texto y material escolar", kind: "money" },
      { name: "gastos_ensenanza_idiomas", label: "Clases de idiomas de los hijos", kind: "money" },
      { name: "cotizaciones_empleada_hogar", label: "Cotizaciones de empleada de hogar", kind: "money" },
      { name: "gastos_cuidado_familiares", label: "Cuidado de hijos, mayores o personas con discapacidad", kind: "money", hint: "Pagado a cuidadores o centros, sin las cotizaciones" },
      { name: "progenitores_trabajan", label: "Trabajamos los dos progenitores (o yo, si no hay otro)", kind: "flag", hint: "Por cuenta ajena o propia, de alta en la Seguridad Social o en una mutualidad" },
      {
        name: "num_familiares_discapacidad_a_cargo",
        label: "Hijos y ascendientes con discapacidad a tu cargo (total)",
        kind: "count",
        sumOf: ["num_descendientes_discapacidad", "num_ascendientes_discapacidad"],
      },
      {
        name: "num_familiares_discapacidad_65_a_cargo",
        label: "De ellos, con discapacidad del 65 % o más (total)",
        kind: "count",
        sumOf: ["num_descendientes_discapacidad_65", "num_ascendientes_discapacidad_65"],
      },
      { name: "num_personas_acogidas_mayores_o_discapacidad", label: "Mayores de 65 o con discapacidad que acoges", kind: "count", hint: "Sin parentesco cercano y sin cobrar por ello" },
      { name: "menores_acogidos", label: "Menores en acogimiento familiar", kind: "count" },
      { name: "dias_acogimiento", label: "Días del año de convivencia con ellos", kind: "count" },
    ],
  },
  {
    title: "Tu comunidad · vivienda y municipio",
    fields: [
      { name: "alquiler_vivienda_habitual", label: "Alquiler pagado en el año", kind: "money" },
      { name: "dias_alquiler_vivienda_habitual", label: "Días del año con el alquiler en vigor", kind: "count", hint: "365 si fue todo el año; algunos límites se prorratean" },
      { name: "num_declaraciones_mismo_alquiler", label: "Declaraciones que deducen por ese alquiler", kind: "count", hint: "Contando la tuya: 2 si lo compartes con otro inquilino" },
      {
        name: "inversion_vivienda_habitual",
        label: "Compra o rehabilitación de tu vivienda habitual (total)",
        kind: "money",
        hint: "Todo lo pagado en el año, hipoteca incluida",
        parts: [
          { name: "pagos_adquisicion_vivienda", label: "Compra: capital y gastos", kind: "money", hint: "Sin los intereses" },
          { name: "pagos_rehabilitacion_vivienda", label: "Rehabilitación", kind: "money", hint: "Sin los intereses" },
          { name: "intereses_prestamo_vivienda", label: "Intereses del préstamo", kind: "money" },
        ],
      },
      { name: "ejercicio_adquisicion_vivienda", label: "Año en que la compraste", kind: "year" },
      { name: "num_declaraciones_misma_vivienda", label: "Declaraciones que deducen por esa vivienda", kind: "count", hint: "Contando la tuya: 2 si la compráis entre dos" },
      { name: "primera_vivienda", label: "Es mi primera vivienda en propiedad", kind: "flag" },
      { name: "vivienda_protegida", label: "Es una vivienda protegida", kind: "flag" },
      { name: "vivienda_nueva_construccion", label: "Es de nueva construcción", kind: "flag" },
      { name: "vivienda_en_municipio_rural", label: "La vivienda que compro está en un municipio rural", kind: "flag" },
      {
        name: "gastos_eficiencia_energetica",
        label: "Obras de mejora energética en tu vivienda (total)",
        kind: "money",
        hint: "Aislamiento, ventanas, calderas, renovables…",
        parts: [{ name: "gastos_instalaciones_renovables", label: "Energía renovable o autoconsumo", kind: "money", hint: "Placas solares, aerotermia…" }],
      },
      { name: "habitantes_municipio", label: "Habitantes de tu municipio", kind: "count", hint: "Varias deducciones exigen un municipio pequeño" },
      { name: "ejercicio_traslado_residencia", label: "Año en que te mudaste a él", kind: "year" },
      { name: "municipio_rural_o_despoblacion", label: "Vivo en un municipio pequeño, rural o en riesgo de despoblación", kind: "flag", hint: "Cada comunidad lo define a su manera; el resultado te dirá el criterio" },
    ],
  },
  {
    title: "Tu comunidad · salud, deporte y otros gastos",
    fields: [
      {
        name: "gastos_salud",
        label: "Gastos de salud (total)",
        kind: "money",
        hint: "Lo pagado y no cubierto por la sanidad pública ni por un seguro",
        parts: [
          { name: "gastos_honorarios_sanitarios", label: "Honorarios de médicos y otros sanitarios", kind: "money", hint: "Sin dentista ni salud mental" },
          { name: "gastos_salud_bucodental", label: "Dentista y ortodoncia", kind: "money" },
          { name: "gastos_salud_mental", label: "Psicología y psiquiatría", kind: "money" },
          { name: "gastos_optica", label: "Gafas, lentillas y prótesis", kind: "money" },
          { name: "gastos_enfermedades_raras", label: "Por una enfermedad rara", kind: "money" },
          { name: "gastos_ela", label: "Por ELA", kind: "money" },
        ],
      },
      {
        name: "gastos_deporte_cultura",
        label: "Deporte y cultura (total)",
        kind: "money",
        parts: [
          { name: "gastos_deporte", label: "Deporte", kind: "money", hint: "Cuotas de gimnasio, clubes o federaciones" },
          { name: "gastos_cultura", label: "Cultura", kind: "money", hint: "Entradas, abonos, cuotas de actividades culturales" },
        ],
      },
      { name: "gastos_veterinarios", label: "Gastos veterinarios", kind: "money" },
      { name: "adquisicion_vehiculo_electrico", label: "Compra de un vehículo eléctrico", kind: "money" },
    ],
  },
  {
    title: "Tu comunidad · donativos e inversiones",
    fields: [
      {
        name: "donativos_autonomicos",
        label: "Donativos a entidades de tu comunidad (total)",
        kind: "money",
        hint: "Cada comunidad incentiva destinos distintos: indica a qué fueron",
        parts: [
          { name: "donativos_medio_ambiente", label: "Medio ambiente", kind: "money" },
          { name: "donativos_cultura_patrimonio", label: "Cultura y patrimonio", kind: "money" },
          { name: "donativos_investigacion", label: "Investigación y universidades", kind: "money" },
          { name: "donativos_cooperacion_fines_sociales", label: "Cooperación y fines sociales", kind: "money", hint: "Desarrollo, pobreza, discapacidad" },
          { name: "donativos_lengua_propia", label: "Fomento de la lengua propia", kind: "money" },
          { name: "donativos_fundaciones_autonomicas", label: "Fundaciones de tu comunidad", kind: "money", hint: "Inscritas en su registro" },
        ],
      },
      { name: "inversion_empresas_nueva_creacion", label: "Inversión en empresas nuevas", kind: "money" },
      { name: "inversion_economia_social", label: "Inversión en economía social", kind: "money", hint: "Cooperativas, sociedades laborales…" },
    ],
  },
];
export const MADRID_NAMES = new Set(MADRID_GROUP.fields.map((f) => f.name));
// Madrid: its own group first, then the rest without repeating what it already asks.
export const MADRID_EXTRA_GROUPS: Group[] = REGIONAL_GROUPS.map((group) => ({
  title: group.title,
  fields: group.fields.filter((f) => !MADRID_NAMES.has(f.name)),
}));

export const FIELDS = [...GROUPS, MADRID_GROUP, ...REGIONAL_GROUPS].flatMap((g) => g.fields).flatMap((f) => [f, ...(f.parts ?? [])]);
export const FLAGS = new Set(FIELDS.filter((f) => f.kind === "flag").map((f) => f.name));
export const TRIBUTACION = [
  { value: "individual", label: "Individual" },
  { value: "conjunta_biparental", label: "Conjunta (matrimonio)" },
  { value: "conjunta_monoparental", label: "Conjunta (monoparental)" },
];
// What the "Probar" button on a lever sets.
export const LEVER_PRESET: Record<string, { field: string; value: string }> = {
  plan_pensiones: { field: "aportacion_plan_pensiones", value: "1500" },
  donativos: { field: "donativos", value: "250" },
};

/** The groups shown for a region: Madrid has its own; other regions the common ones; none, only the state's. */
export function gruposDeComunidad(comunidad: string): Group[] {
  if (/madrid/i.test(comunidad)) return [...GROUPS, MADRID_GROUP, ...MADRID_EXTRA_GROUPS];
  return comunidad && comunidad !== "Otra" ? [...GROUPS, ...REGIONAL_GROUPS] : GROUPS;
}
