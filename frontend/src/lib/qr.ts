// A small, dependency-free QR Code encoder (ISO/IEC 18004) for the bot's contact link in «Mensajería».
//
// Byte mode (UTF-8), error correction level M (or L), versions 1–40, the eight masks scored with the standard's
// penalty rules; returns the module matrix (true = dark), which the panel draws as an SVG with a quiet zone.
// It follows the reference algorithm (the same structure as Project Nayuki's MIT-licensed «QR Code generator»),
// kept to what a contact link needs: no other segment modes, no ECI.

export type NivelCorreccion = "L" | "M";

// Error-correction codewords per block and number of blocks, by version (index 0 unused).
// prettier-ignore
const ECC_POR_BLOQUE: Record<NivelCorreccion, readonly number[]> = {
  L: [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  M: [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
};
// prettier-ignore
const BLOQUES: Record<NivelCorreccion, readonly number[]> = {
  L: [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  M: [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
};
// The two format bits of each level (L = 01, M = 00).
const BITS_NIVEL: Record<NivelCorreccion, number> = { L: 1, M: 0 };

export interface CodigoQR {
  version: number;
  mascara: number;
  tamano: number;
  /** modulos[y][x]: true = dark. */
  modulos: boolean[][];
}

export class QRDemasiadoLargoError extends Error {}

function bit(valor: number, i: number): boolean {
  return ((valor >>> i) & 1) !== 0;
}

function modulosDeDatos(version: number): number {
  let resultado = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const alineaciones = Math.floor(version / 7) + 2;
    resultado -= (25 * alineaciones - 10) * alineaciones - 55;
    if (version >= 7) resultado -= 36;
  }
  return resultado;
}

function codewordsDeDatos(version: number, nivel: NivelCorreccion): number {
  return Math.floor(modulosDeDatos(version) / 8) - ECC_POR_BLOQUE[nivel][version] * BLOQUES[nivel][version];
}

// --- Reed–Solomon over GF(2^8), polynomial 0x11D ---------------------------------------------------------------

function multiplicar(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

function divisor(grado: number): number[] {
  const resultado = new Array<number>(grado).fill(0);
  resultado[grado - 1] = 1;
  let raiz = 1;
  for (let i = 0; i < grado; i++) {
    for (let j = 0; j < resultado.length; j++) {
      resultado[j] = multiplicar(resultado[j], raiz);
      if (j + 1 < resultado.length) resultado[j] ^= resultado[j + 1];
    }
    raiz = multiplicar(raiz, 0x02);
  }
  return resultado;
}

function resto(datos: readonly number[], div: readonly number[]): number[] {
  const resultado = div.map(() => 0);
  for (const b of datos) {
    const factor = b ^ (resultado.shift() as number);
    resultado.push(0);
    div.forEach((coef, i) => {
      resultado[i] ^= multiplicar(coef, factor);
    });
  }
  return resultado;
}

function conCorreccion(datos: readonly number[], version: number, nivel: NivelCorreccion): number[] {
  const bloques = BLOQUES[nivel][version];
  const eccPorBloque = ECC_POR_BLOQUE[nivel][version];
  const totales = Math.floor(modulosDeDatos(version) / 8);
  const cortos = bloques - (totales % bloques);
  const largoCorto = Math.floor(totales / bloques);
  const div = divisor(eccPorBloque);
  const todos: number[][] = [];
  for (let i = 0, k = 0; i < bloques; i++) {
    const dato = datos.slice(k, k + largoCorto - eccPorBloque + (i < cortos ? 0 : 1));
    k += dato.length;
    const ecc = resto(dato, div);
    if (i < cortos) dato.push(0);
    todos.push(dato.concat(ecc));
  }
  const resultado: number[] = [];
  for (let i = 0; i < todos[0].length; i++) {
    todos.forEach((bloque, j) => {
      if (i !== largoCorto - eccPorBloque || j >= cortos) resultado.push(bloque[i]);
    });
  }
  return resultado;
}

// --- the data codewords (byte mode) -----------------------------------------------------------------------------

function codewords(bytes: Uint8Array, version: number, nivel: NivelCorreccion): number[] {
  const bits: number[] = [];
  const poner = (valor: number, longitud: number) => {
    for (let i = longitud - 1; i >= 0; i--) bits.push((valor >>> i) & 1);
  };
  poner(0b0100, 4);
  poner(bytes.length, version < 10 ? 8 : 16);
  for (const b of bytes) poner(b, 8);
  const capacidad = codewordsDeDatos(version, nivel) * 8;
  poner(0, Math.min(4, capacidad - bits.length));
  poner(0, (8 - (bits.length % 8)) % 8);
  for (let relleno = 0xec; bits.length < capacidad; relleno ^= 0xec ^ 0x11) poner(relleno, 8);
  const resultado: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let byte = 0;
    for (let j = 0; j < 8; j++) byte = (byte << 1) | bits[i + j];
    resultado.push(byte);
  }
  return resultado;
}

// --- the symbol -------------------------------------------------------------------------------------------------------

class Simbolo {
  readonly version: number;
  readonly tamano: number;
  readonly modulos: boolean[][];
  private readonly nivel: NivelCorreccion;
  private readonly funcion: boolean[][];

  constructor(version: number, nivel: NivelCorreccion) {
    this.version = version;
    this.nivel = nivel;
    this.tamano = version * 4 + 17;
    this.modulos = Array.from({ length: this.tamano }, () => new Array<boolean>(this.tamano).fill(false));
    this.funcion = Array.from({ length: this.tamano }, () => new Array<boolean>(this.tamano).fill(false));
    this.patronesFijos();
  }

  private fijo(x: number, y: number, oscuro: boolean): void {
    this.modulos[y][x] = oscuro;
    this.funcion[y][x] = true;
  }

  private alineaciones(): number[] {
    if (this.version === 1) return [];
    const cuantas = Math.floor(this.version / 7) + 2;
    const paso = this.version === 32 ? 26 : Math.ceil((this.version * 4 + 4) / (cuantas * 2 - 2)) * 2;
    const resultado = [6];
    for (let pos = this.tamano - 7; resultado.length < cuantas; pos -= paso) resultado.splice(1, 0, pos);
    return resultado;
  }

  private patronesFijos(): void {
    for (let i = 0; i < this.tamano; i++) {
      this.fijo(6, i, i % 2 === 0);
      this.fijo(i, 6, i % 2 === 0);
    }
    this.buscador(3, 3);
    this.buscador(this.tamano - 4, 3);
    this.buscador(3, this.tamano - 4);
    const posiciones = this.alineaciones();
    const n = posiciones.length;
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        if ((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0)) continue;
        this.alineacion(posiciones[i], posiciones[j]);
      }
    }
    this.formato(0);
    this.versionBits();
  }

  private buscador(x: number, y: number): void {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const distancia = Math.max(Math.abs(dx), Math.abs(dy));
        const xx = x + dx;
        const yy = y + dy;
        if (xx >= 0 && xx < this.tamano && yy >= 0 && yy < this.tamano) this.fijo(xx, yy, distancia !== 2 && distancia !== 4);
      }
    }
  }

  private alineacion(x: number, y: number): void {
    for (let dy = -2; dy <= 2; dy++) {
      for (let dx = -2; dx <= 2; dx++) this.fijo(x + dx, y + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }

  formato(mascara: number): void {
    const datos = (BITS_NIVEL[this.nivel] << 3) | mascara;
    let rem = datos;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = ((datos << 10) | rem) ^ 0x5412;
    for (let i = 0; i <= 5; i++) this.fijo(8, i, bit(bits, i));
    this.fijo(8, 7, bit(bits, 6));
    this.fijo(8, 8, bit(bits, 7));
    this.fijo(7, 8, bit(bits, 8));
    for (let i = 9; i < 15; i++) this.fijo(14 - i, 8, bit(bits, i));
    for (let i = 0; i < 8; i++) this.fijo(this.tamano - 1 - i, 8, bit(bits, i));
    for (let i = 8; i < 15; i++) this.fijo(8, this.tamano - 15 + i, bit(bits, i));
    this.fijo(8, this.tamano - 8, true);
  }

  private versionBits(): void {
    if (this.version < 7) return;
    let rem = this.version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (this.version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const oscuro = bit(bits, i);
      const a = this.tamano - 11 + (i % 3);
      const b = Math.floor(i / 3);
      this.fijo(a, b, oscuro);
      this.fijo(b, a, oscuro);
    }
  }

  datos(todos: readonly number[]): void {
    let i = 0;
    for (let derecha = this.tamano - 1; derecha >= 1; derecha -= 2) {
      if (derecha === 6) derecha = 5;
      for (let vertical = 0; vertical < this.tamano; vertical++) {
        for (let j = 0; j < 2; j++) {
          const x = derecha - j;
          const subiendo = ((derecha + 1) & 2) === 0;
          const y = subiendo ? this.tamano - 1 - vertical : vertical;
          if (!this.funcion[y][x] && i < todos.length * 8) {
            this.modulos[y][x] = bit(todos[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
  }

  mascara(m: number): void {
    for (let y = 0; y < this.tamano; y++) {
      for (let x = 0; x < this.tamano; x++) {
        let invertir: boolean;
        switch (m) {
          case 0:
            invertir = (x + y) % 2 === 0;
            break;
          case 1:
            invertir = y % 2 === 0;
            break;
          case 2:
            invertir = x % 3 === 0;
            break;
          case 3:
            invertir = (x + y) % 3 === 0;
            break;
          case 4:
            invertir = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
            break;
          case 5:
            invertir = ((x * y) % 2) + ((x * y) % 3) === 0;
            break;
          case 6:
            invertir = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
            break;
          default:
            invertir = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
        }
        if (!this.funcion[y][x] && invertir) this.modulos[y][x] = !this.modulos[y][x];
      }
    }
  }

  private historial(longitud: number, h: number[]): void {
    if (h[0] === 0) longitud += this.tamano;
    h.pop();
    h.unshift(longitud);
  }

  private patronesBuscador(h: readonly number[]): number {
    const n = h[1];
    const centro = n > 0 && h[2] === n && h[3] === n * 3 && h[4] === n && h[5] === n;
    return (centro && h[0] >= n * 4 && h[6] >= n ? 1 : 0) + (centro && h[6] >= n * 4 && h[0] >= n ? 1 : 0);
  }

  private cerrarTramo(color: boolean, longitud: number, h: number[]): number {
    if (color) {
      this.historial(longitud, h);
      longitud = 0;
    }
    longitud += this.tamano;
    this.historial(longitud, h);
    return this.patronesBuscador(h);
  }

  penalizacion(): number {
    const t = this.tamano;
    let resultado = 0;
    for (let eje = 0; eje < 2; eje++) {
      for (let a = 0; a < t; a++) {
        let color = false;
        let tramo = 0;
        const h = [0, 0, 0, 0, 0, 0, 0];
        for (let b = 0; b < t; b++) {
          const oscuro = eje === 0 ? this.modulos[a][b] : this.modulos[b][a];
          if (oscuro === color) {
            tramo++;
            if (tramo === 5) resultado += 3;
            else if (tramo > 5) resultado++;
          } else {
            this.historial(tramo, h);
            if (!color) resultado += this.patronesBuscador(h) * 40;
            color = oscuro;
            tramo = 1;
          }
        }
        resultado += this.cerrarTramo(color, tramo, h) * 40;
      }
    }
    for (let y = 0; y < t - 1; y++) {
      for (let x = 0; x < t - 1; x++) {
        const c = this.modulos[y][x];
        if (c === this.modulos[y][x + 1] && c === this.modulos[y + 1][x] && c === this.modulos[y + 1][x + 1]) resultado += 3;
      }
    }
    let oscuros = 0;
    for (const fila of this.modulos) for (const m of fila) if (m) oscuros++;
    const total = t * t;
    const k = Math.ceil(Math.abs(oscuros * 20 - total * 10) / total) - 1;
    return resultado + k * 10;
  }
}

/** Encode ``texto`` (UTF-8, byte mode) in the smallest version that fits. Throws when it does not fit in version 40. */
export function codificarQR(texto: string, nivel: NivelCorreccion = "M", mascaraFija?: number): CodigoQR {
  const bytes = new TextEncoder().encode(texto);
  let version = 1;
  for (; version <= 40; version++) {
    const usados = 4 + (version < 10 ? 8 : 16) + bytes.length * 8;
    if (usados <= codewordsDeDatos(version, nivel) * 8) break;
  }
  if (version > 40) throw new QRDemasiadoLargoError("El texto es demasiado largo para un código QR.");
  const simbolo = new Simbolo(version, nivel);
  simbolo.datos(conCorreccion(codewords(bytes, version, nivel), version, nivel));
  let mejor = mascaraFija ?? -1;
  if (mejor < 0) {
    let menor = Infinity;
    for (let m = 0; m < 8; m++) {
      simbolo.mascara(m);
      simbolo.formato(m);
      const p = simbolo.penalizacion();
      if (p < menor) {
        menor = p;
        mejor = m;
      }
      simbolo.mascara(m); // XOR again: undone
    }
  }
  simbolo.mascara(mejor);
  simbolo.formato(mejor);
  return { version, mascara: mejor, tamano: simbolo.tamano, modulos: simbolo.modulos };
}

/** The SVG path of the dark modules (one unit per module), offset by a quiet zone of ``margen`` modules. */
export function trazadoQR(qr: CodigoQR, margen = 4): string {
  const partes: string[] = [];
  qr.modulos.forEach((fila, y) =>
    fila.forEach((oscuro, x) => {
      if (oscuro) partes.push(`M${x + margen},${y + margen}h1v1h-1z`);
    }),
  );
  return partes.join("");
}
