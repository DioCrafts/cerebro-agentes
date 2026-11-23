// API simulada para medir la UI sin el backend real (ni modelos): responde a las rutas que usa el
// frontend con datos de ejemplo que pasan sus validadores (src/api*.ts). `vite preview` le envía /api/*
// sin el prefijo (VITE_API_PROXY), igual que haría con FastAPI.
//
// Modos, para medir los estados de la UI (POST /__control {"modo": ...}):
//   normal  respuestas de ejemplo con datos
//   vacio   listas vacías (estados vacíos)
//   lento   como normal, pero cada respuesta tarda `latenciaMs` (estados de carga)
//   error   500 con {"detail": ...} en todo salvo la sesión (estados de error)
// GET /__peticiones devuelve las rutas pedidas que no conoce (para mantener la simulación al día).

import http from "node:http";

const AHORA = "2026-11-20T09:30:00Z";
const USUARIO = { id: "usr_medicion", email: "ana.lopez@example.com", created_at: "2025-02-01T10:00:00Z" };
const COOKIE = "sesion_simulada";

const estado = {
  modo: "normal",
  latenciaMs: 2500,
  desconocidas: new Map(),
  casos: new Map(),
  leidas: new Set(),
};

export function reiniciar() {
  estado.modo = "normal";
  estado.casos.clear();
  estado.leidas.clear();
}

function hecho(name, value, value_type = typeof value === "number" ? "number" : "string") {
  return { fact_id: `f_${name}`, name, value, value_type, source: "conversacion", confidence: 0.9, updated_at: AHORA };
}

const HECHOS_RESPUESTA = [
  hecho("comunidad_autonoma", "Madrid"),
  hecho("estado_civil", "casado"),
  hecho("rendimiento_trabajo_bruto", 34000),
  hecho("rendimiento_capital_mobiliario", 420),
];

const OBLIGACIONES = [
  {
    obligation_id: "irpf_declaracion",
    title: "Presentar la declaración de la renta 2025",
    description: "Tus rendimientos del trabajo superan el límite de 22.000 € de un solo pagador.",
    risk_level: "medium",
    confidence: 0.86,
    blocking_missing_facts: [],
    trigger_facts: ["rendimiento_trabajo_bruto"],
    evidence_refs: [{ title: "Ley 35/2006, art. 96", locator: "LIRPF art. 96" }],
    due_date: "2026-06-30",
    due_date_note: null,
  },
];

function caso(id) {
  if (!estado.casos.has(id)) {
    estado.casos.set(id, {
      case_id: id,
      user_id: USUARIO.id,
      status: "open",
      jurisdiction: "ES",
      tax_period: "2025",
      facts: [],
      missing_facts: [{ fact_name: "descendientes", reason: "Para aplicar el mínimo por descendientes", priority: "medium" }],
      obligation_candidates: [],
    });
  }
  return estado.casos.get(id);
}

const AVISOS = [
  {
    id: "avi_1",
    kind: "plazo",
    title: "Quedan 12 días para el pago fraccionado del 4T",
    body: "El modelo 130 del cuarto trimestre se presenta hasta el 30 de enero. Con tus ingresos de 2025, el pago estimado es de 612 €.",
    severity: "urgente",
    actions: ["Revisar el cálculo del modelo 130"],
    sources: ["Orden HAC/1398/2003"],
    amount: null,
    due_date: "2027-01-30",
  },
  {
    id: "avi_2",
    kind: "fin_de_ejercicio",
    title: "Puedes ahorrar 380 € si aportas a tu plan de pensiones antes del 31 de diciembre",
    body: "Una aportación de 1.500 € reduce tu base imponible general. El ahorro depende de tu tipo marginal (30 %).",
    severity: "aviso",
    actions: ["Abrir el optimizador"],
    sources: ["LIRPF art. 51"],
    amount: "380.00",
    due_date: "2026-12-31",
  },
  {
    id: "avi_3",
    kind: "cambio_normativo",
    title: "Cambia la deducción autonómica por alquiler en Madrid",
    body: "Desde 2026 el límite sube a 1.200 €. Te afecta si alquilas tu vivienda habitual.",
    severity: "info",
    actions: [],
    sources: ["BOCM 2025/312"],
    amount: null,
    due_date: null,
  },
];

function avisos(caseId) {
  const lista = estado.modo === "vacio" ? [] : AVISOS;
  const notifications = lista.map((a, i) => ({
    ...a,
    case_id: caseId,
    created_at: `2026-11-${String(20 - i).padStart(2, "0")}T08:00:00Z`,
    read_at: estado.leidas.has(a.id) ? AHORA : null,
  }));
  return { case_id: caseId, unread: notifications.filter((n) => !n.read_at).length, notifications };
}

const MODULOS = {
  configurable: true,
  modulos: [
    {
      clave: "fiscal",
      nombre: "Impuestos",
      descripcion: "Renta, autónomos, deducciones y plazos con Hacienda.",
      activo: true,
      obligatorio: true,
      elegido: true,
      hechos: [],
      herramientas: [{ nombre: "simular_irpf", efecto: "lectura", descripcion: "Simula la declaración de la renta." }],
      fuentes: [{ identificador: "BOE", descripcion: "Boletín Oficial del Estado", url: "https://www.boe.es" }],
    },
    {
      clave: "energia",
      nombre: "Luz y gas",
      descripcion: "Compara tu tarifa con el mercado y avisa de subidas.",
      activo: true,
      obligatorio: false,
      elegido: true,
      elegido_el: "2025-03-01T10:00:00Z",
      hechos: [
        { nombre: "potencia_contratada_kw", tipo: "number", etiqueta: "Potencia contratada", descripcion: "En kW", sensible: false },
      ],
      herramientas: [{ nombre: "comparar_tarifa", efecto: "prepara", descripcion: "Prepara el cambio de tarifa." }],
      fuentes: [],
      lee_documentos: true,
      herramienta_de_documento: "leer_factura",
    },
    {
      clave: "vivienda",
      nombre: "Vivienda",
      descripcion: "Hipoteca, alquiler, comunidad e IBI.",
      activo: false,
      obligatorio: false,
      elegido: false,
      hechos: [],
      herramientas: [],
      fuentes: [],
    },
  ],
};

const ACCIONES = [
  {
    id: "acc_1",
    case_id: "",
    modulo: "energia",
    herramienta: "comparar_tarifa",
    argumentos: { tarifa: "PVPC" },
    resumen: "Cambiar a la tarifa regulada PVPC (ahorro estimado: 14 €/mes)",
    estado: "pendiente",
    created_at: AHORA,
    es_error: false,
  },
];

function resultadoIrpf(cuota) {
  return {
    ejercicio: 2025,
    tributacion: "individual",
    cuota_liquida: cuota,
    retenciones: "6120.00",
    resultado: (Number(cuota) - 6120).toFixed(2),
    tipo_medio_efectivo: "17.40",
    lineas: [
      { concepto: "Rendimiento neto del trabajo", importe: "31800.00", referencia: "LIRPF art. 19" },
      { concepto: "Base liquidable general", importe: "29800.00", referencia: "LIRPF art. 50" },
      { concepto: "Cuota íntegra", importe: cuota, referencia: "LIRPF art. 63" },
    ],
    avisos: [],
    fuente: "Simulación determinista (sin modelo)",
  };
}

function simulacion(cuerpo) {
  const escenarios = [{ nombre: "base", resultado: resultadoIrpf("5940.00"), ahorro_frente_a_base: "0.00", actividad: null }];
  for (const e of cuerpo?.escenarios ?? []) {
    escenarios.push({ nombre: e.nombre, resultado: resultadoIrpf("5560.00"), ahorro_frente_a_base: "380.00", actividad: null });
  }
  return {
    ejercicio: 2025,
    escenarios,
    beneficios: [
      {
        id: "pensiones",
        nombre: "Aportación a planes de pensiones",
        estado: "disponible",
        detalle: "Reduce la base imponible hasta 1.500 € al año.",
        requisitos: ["Aportar antes del 31 de diciembre"],
        referencia: "LIRPF art. 51",
        verificado: true,
        ahorro_estimado: "450.00",
        datos_que_faltan: [],
      },
    ],
    datos_no_aportados: ["descendientes"],
  };
}

function respuestaAgente(caseId) {
  return {
    answer:
      "Sí, tienes que presentar la declaración de la renta de 2025. Tus rendimientos del trabajo (34.000 €) superan el límite de 22.000 € para un solo pagador [F1]. El plazo termina el 30 de junio de 2026 [F2].\n\nComo vives en Madrid, revisa también la deducción autonómica por alquiler si es tu caso.",
    mode: "verified",
    citations: [
      {
        title: "Ley 35/2006 del IRPF",
        locator: "Artículo 96",
        fragment_id: "F1",
        authority: "BOE",
        source_type: "ley",
        binding: true,
        snippet: "Estarán obligados a declarar los contribuyentes que obtengan rendimientos íntegros del trabajo superiores a 22.000 euros anuales…",
        vigencia: "vigente",
      },
      {
        title: "Orden HAC/277/2025",
        locator: "Artículo 8",
        fragment_id: "F2",
        authority: "BOE",
        source_type: "orden",
        binding: true,
        snippet: "El plazo de presentación será el comprendido entre los días 2 de abril y 30 de junio de 2026…",
        vigencia: "vigente",
      },
    ],
    claims: [
      { text: "Superan el límite de 22.000 €", fragment_ids: ["F1"], verdict: "respaldada", reason: "El artículo fija ese límite." },
    ],
    warnings: [],
    conflicts: [],
    tool_trace: [{ step: 1, tool: "buscar_normativa", arguments: { consulta: "obligación de declarar" }, summary: "2 fragmentos", is_error: false }],
    obligations: OBLIGACIONES,
    facts: HECHOS_RESPUESTA,
    tax_period: "2025",
    case_id: caseId,
    case_updated: true,
    provider: "simulado",
    model: "simulado",
    pending_actions: [],
    cerebro: null,
  };
}

const RUTAS = [
  ["GET", /^\/auth\/config$/, () => ({ auth_mode: "required", password_min_length: 12, consent_text_version: "2025-01", privacy_notice_url: "/privacidad" })],
  ["POST", /^\/auth\/login$/, () => ({ token: null, expires_at: "2026-12-20T00:00:00Z", user: USUARIO })],
  ["POST", /^\/auth\/logout$/, () => null],
  ["GET", /^\/me\/consents$/, () => ({ text_version: "2025-01", consents: [] })],
  ["POST", /^\/cases$/, () => caso(`case_${Math.random().toString(36).slice(2, 10)}`)],
  ["GET", /^\/cases\/([^/]+)$/, (m) => (estado.modo === "vacio" ? { ...caso(m[1]), facts: [], obligation_candidates: [] } : caso(m[1]))],
  [
    "PUT",
    /^\/cases\/([^/]+)\/facts\/([^/]+)$/,
    (m, cuerpo) => {
      const c = caso(m[1]);
      const nombre = decodeURIComponent(m[2]);
      c.facts = [...c.facts.filter((f) => f.name !== nombre), { ...hecho(nombre, cuerpo?.value), source: "usuario" }];
      return c;
    },
  ],
  [
    "DELETE",
    /^\/cases\/([^/]+)\/facts\/([^/]+)$/,
    (m) => {
      const c = caso(m[1]);
      c.facts = c.facts.filter((f) => f.name !== decodeURIComponent(m[2]));
      return c;
    },
  ],
  ["GET", /^\/cases\/([^/]+)\/notifications$/, (m) => avisos(m[1])],
  [
    "POST",
    /^\/cases\/([^/]+)\/notifications\/([^/]+)\/read$/,
    (m) => {
      estado.leidas.add(decodeURIComponent(m[2]));
      return avisos(m[1]);
    },
  ],
  ["POST", /^\/cases\/([^/]+)\/proactive\/run$/, (m) => avisos(m[1])],
  ["GET", /^\/cases\/([^/]+)\/repaso/, () => [404, { detail: "No hay repaso para este aviso." }]],
  [
    "GET",
    /^\/cases\/([^/]+)\/calendar/,
    (m) => ({
      case_id: m[1],
      hoy: "2026-11-23",
      aviso: "Plazos generales de la AEAT; no tienen en cuenta festivos locales.",
      vencimientos:
        estado.modo === "vacio"
          ? []
          : [
              {
                id: "m130_4t",
                modelo: "130",
                titulo: "Pago fraccionado del IRPF (4T)",
                periodo: "4T 2026",
                fecha: "2027-01-30",
                ajustada: false,
                dias_restantes: 68,
                detalle: "Autónomos en estimación directa.",
                referencia: "Orden HAC/1398/2003",
                verificado: true,
              },
            ],
    }),
  ],
  ["GET", /^\/cases\/([^/]+)\/consejo$/, (m) => ({ case_id: m[1], disponible: true, sesiones: [] })],
  ["POST", /^\/cases\/([^/]+)\/consejo\/detectar$/, () => ({ dificil: false, motivos: [] })],
  ["GET", /^\/cases\/([^/]+)\/decisiones$/, () => ({ decisiones: [], catalogo: [] })],
  ["GET", /^\/modulos$/, () => MODULOS],
  ["GET", /^\/capacidades$/, () => ({ configurable: true, capacidades: [] })],
  [
    "GET",
    /^\/acciones/,
    (m, _c, url) => ({
      case_id: url.searchParams.get("case_id"),
      acciones: estado.modo === "vacio" ? [] : ACCIONES.map((a) => ({ ...a, case_id: url.searchParams.get("case_id") })),
    }),
  ],
  ["GET", /^\/propuestas/, (m, _c, url) => ({ case_id: url.searchParams.get("case_id"), propuestas: [] })],
  ["GET", /^\/push\/config$/, () => ({ disponible: true, consentimiento: false, dispositivos: 0, clave_publica: "BElidePrueba" })],
  [
    "GET",
    /^\/navegador\/estado$/,
    () => ({
      activo: true,
      modos: ["ventana"],
      consentimiento: false,
      texto_consentimiento: "El agente abrirá un navegador aislado y actuará en las webs oficiales que elijas. Te pedirá confirmación antes de enviar nada.",
      texto_version: "2025-06",
      tareas: [
        {
          clave: "cita_dgt",
          version: 1,
          titulo: "Pedir cita previa en la DGT",
          descripcion: "Busca hueco en tu oficina y deja la cita preparada para que la confirmes.",
          verificado_contra_sitio_real: true,
          nota_verificacion: "Probado contra sede.dgt.gob.es en octubre de 2026.",
          demostracion: false,
          sitios: ["sede.dgt.gob.es"],
          parametros: [
            { nombre: "oficina", tipo: "opcion", etiqueta: "Oficina", obligatorio: true, opciones: ["Madrid", "Alcalá de Henares"], personal: false },
          ],
          final: "detener",
          modulo_destino: null,
        },
      ],
      max_tareas_dia: 5,
      usadas_hoy: 0,
    }),
  ],
  ["GET", /^\/navegador\/tareas/, (m, _c, url) => ({ case_id: url.searchParams.get("case_id"), tareas: [] })],
  ["GET", /^\/autonomos\/ejercicios$/, () => ({ ejercicios: [2026, 2025, 2024] })],
  ["GET", /^\/tareas\/plantillas$/, () => ({ plantillas: [] })],
  ["GET", /^\/tareas/, (m, _c, url) => ({ case_id: url.searchParams.get("case_id"), tareas: [] })],
  [
    "GET",
    /^\/investigacion\/estado$/,
    () => ({
      fuentes: { corpus: true, boe: true, web: false },
      web_activada_por_el_servicio: false,
      web_consentida: false,
      web_proveedor_compatible: false,
      web_motivo: "La búsqueda web no está activada en este servicio.",
      dominios_permitidos: [],
      dominios_bloqueados: [],
      limites: { max_usd: "0.50", max_tokens: 200000, llamadas_por_profundidad: { rapida: 4, normal: 8, profunda: 16 }, precio_busqueda_web_usd: "0.01" },
      privacidad: "Tu pregunta se envía al modelo; tus datos personales no salen del servicio.",
    }),
  ],
  [
    "GET",
    /^\/recuerdos/,
    () => {
      const origen = { conversacion_id: "conv_1", fecha: "2026-10-02T10:00:00Z", cita: "prefiero hacerla cada uno por separado", titulo_conversacion: "Declaración conjunta o individual" };
      return {
        disponible: true,
        activa: true,
        sin_cuenta: false,
        retencion_dias: 365,
        explicacion: "Guardo lo que me cuentas con tus propias palabras para no volver a preguntártelo.",
        recuerdos:
          estado.modo === "vacio"
            ? []
            : [
                {
                  id: "rec_1",
                  tipo: "preferencia",
                  texto: "Prefiere tributar de forma individual.",
                  origen,
                  fuentes: [origen],
                  corregido: false,
                  sensible: false,
                  estado: "activo",
                  sustituido_por: null,
                  creado: "2026-10-02T10:00:00Z",
                  actualizado: "2026-10-02T10:00:00Z",
                  usos: 3,
                  ultimo_uso: "2026-11-18T10:00:00Z",
                },
              ],
        historial: [],
        conversaciones: 4,
      };
    },
  ],
  [
    "POST",
    /^\/autonomos\/actividad$/,
    () => ({
      ejercicio: 2025,
      lineas: [{ concepto: "Ingresos de la actividad", importe: "42000.00", referencia: "LIRPF art. 28" }],
      avisos: [],
      fuente: "Simulación determinista (sin modelo)",
      rendimiento_actividad_neto: "30500.00",
      rendimiento_neto: "29000.00",
    }),
  ],
  ["POST", /^\/simulate$/, (m, cuerpo) => simulacion(cuerpo)],
  ["GET", /^\/conversaciones/, () => ({ conversaciones: [] })],
  ["GET", /^\/evaluacion\/revision\/aceptados$/, () => ({ casos: [] })],
  ["GET", /^\/evaluacion\/revision/, () => ({ grupos: [], categorias: [] })],
  ["GET", /^\/evaluacion\/consentimiento$/, () => ({ version: "2025-01", texto: "Puedes guardar esta respuesta para mejorar el asistente.", tipos: [] })],
  ["GET", /^\/evaluacion\/aprendizaje/, () => ({ activo: false, texto: "El asistente no aprende de tus conversaciones." })],
  ["GET", /^\/correo\/estado/, () => ({ conectado: false, configurado: false })],
];

function enviar(res, status, cuerpo, cabeceras = {}) {
  if (cuerpo === null && status === 200) status = 204;
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...cabeceras });
  res.end(cuerpo === null ? undefined : JSON.stringify(cuerpo));
}

const espera = (ms) => new Promise((r) => setTimeout(r, ms));

async function flujoAgente(req, res, cuerpo) {
  if (estado.modo === "lento") await espera(estado.latenciaMs);
  if (estado.modo === "error") {
    enviar(res, 503, { detail: "El asesor no está disponible ahora mismo. Inténtalo dentro de unos minutos." });
    return;
  }
  let caseId = cuerpo?.case_id;
  if (!caseId) caseId = caso(`case_${Math.random().toString(36).slice(2, 10)}`).case_id;
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
  const evento = (tipo, datos) => res.write(`event: ${tipo}\ndata: ${JSON.stringify(datos)}\n\n`);
  const progreso = (datos) => evento("progress", datos);
  progreso({ type: "thinking", step: 1 });
  await espera(250);
  progreso({ type: "tool_call", tool: "buscar_normativa", arguments: { consulta: "obligación de declarar" } });
  await espera(300);
  progreso({ type: "tool_result", tool: "buscar_normativa", summary: "2 fragmentos de la LIRPF", is_error: false });
  const final = respuestaAgente(caseId);
  for (const trozo of final.answer.match(/.{1,24}/gs) ?? []) {
    progreso({ type: "text_delta", text: trozo });
    await espera(35);
  }
  progreso({ type: "verifying", citations: 2 });
  await espera(250);
  const c = caso(caseId);
  c.facts = [...HECHOS_RESPUESTA];
  c.obligation_candidates = OBLIGACIONES;
  evento("result", final);
  res.end();
}

function leerCuerpo(req) {
  return new Promise((resolve) => {
    let datos = "";
    req.on("data", (t) => (datos += t));
    req.on("end", () => {
      try {
        resolve(datos ? JSON.parse(datos) : undefined);
      } catch {
        resolve(undefined);
      }
    });
  });
}

function conSesion(req) {
  return (req.headers.cookie ?? "").split(/;\s*/).includes(`${COOKIE}=1`);
}

async function atender(req, res) {
  const url = new URL(req.url, "http://localhost");
  const ruta = url.pathname.replace(/^\/api(?=\/)/, "");
  const cuerpo = await leerCuerpo(req);

  if (ruta === "/__control" && req.method === "POST") {
    if (cuerpo?.reiniciar) reiniciar();
    if (cuerpo?.modo) estado.modo = cuerpo.modo;
    if (typeof cuerpo?.latenciaMs === "number") estado.latenciaMs = cuerpo.latenciaMs;
    return enviar(res, 200, { modo: estado.modo });
  }
  if (ruta === "/__peticiones") return enviar(res, 200, Object.fromEntries(estado.desconocidas));

  // La sesión: una cookie sin __Host- (el servidor de medición es HTTP).
  if (ruta === "/auth/me") {
    return conSesion(req) ? enviar(res, 200, USUARIO) : enviar(res, 401, { detail: "No has iniciado sesión." });
  }
  if (ruta === "/auth/login" && req.method === "POST") {
    return enviar(res, 200, RUTAS[1][2](), { "Set-Cookie": `${COOKIE}=1; Path=/; HttpOnly; SameSite=Lax` });
  }
  if (ruta === "/agent/stream" && req.method === "POST") return flujoAgente(req, res, cuerpo);

  const esSesion = ruta.startsWith("/auth/");
  if (estado.modo === "lento" && !esSesion) await espera(estado.latenciaMs);
  if (estado.modo === "error" && !esSesion) {
    return enviar(res, 500, { detail: "Error interno del servicio (simulado para la medición)." });
  }
  for (const [metodo, patron, manejar] of RUTAS) {
    const m = patron.exec(ruta);
    if (m && metodo === req.method) {
      const salida = manejar(m, cuerpo, url);
      if (Array.isArray(salida) && typeof salida[0] === "number") return enviar(res, salida[0], salida[1]);
      return enviar(res, 200, salida);
    }
  }
  const clave = `${req.method} ${ruta}`;
  estado.desconocidas.set(clave, (estado.desconocidas.get(clave) ?? 0) + 1);
  return enviar(res, 404, { detail: "Ruta no simulada." });
}

export function arrancarApiSimulada(puerto) {
  const servidor = http.createServer((req, res) => {
    atender(req, res).catch((err) => {
      res.writeHead(500);
      res.end(String(err));
    });
  });
  return new Promise((resolve) => servidor.listen(puerto, "127.0.0.1", () => resolve(servidor)));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const puerto = Number(process.env.PUERTO_API ?? 8799);
  await arrancarApiSimulada(puerto);
  console.log(`API simulada en http://127.0.0.1:${puerto}`);
}
