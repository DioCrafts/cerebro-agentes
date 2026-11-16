// Generates the app icons (PWA manifest, iOS home screen) from one geometry, with no dependencies:
//   node scripts/iconos.mjs
// Writes public/icons/*.png and public/icons/icono.svg. The «H» is drawn with rectangles (no font), so the
// result is identical on every machine. Re-run after changing the colours or the shapes below.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const FONDO = [0x0f, 0x4f, 0x4a]; // --primary-hover / favicon background
const LETRA = [0xe4, 0xef, 0xed];

// The «H» in a 64×64 box: two stems with serifs and a crossbar.
const H = [
  [18, 16, 24, 48],
  [40, 16, 46, 48],
  [24, 29.5, 40, 34],
  [15, 16, 27, 18.5],
  [15, 45.5, 27, 48],
  [37, 16, 49, 18.5],
  [37, 45.5, 49, 48],
];

const SALIDA = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "icons");

/** Coverage of a pixel (0..1) by the shape, by 4×4 supersampling. */
function pintar(size, { redondeo, escala }) {
  const px = new Uint8Array(size * size * 4);
  const u = size / 64;
  const radio = redondeo * u;
  const c = size / 2;
  const n = 4;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let fondo = 0;
      let letra = 0;
      for (let sy = 0; sy < n; sy++) {
        for (let sx = 0; sx < n; sx++) {
          const fx = x + (sx + 0.5) / n;
          const fy = y + (sy + 0.5) / n;
          // Rounded-rectangle background.
          const dx = Math.max(radio - fx, fx - (size - radio), 0);
          const dy = Math.max(radio - fy, fy - (size - radio), 0);
          if (radio === 0 || dx * dx + dy * dy <= radio * radio) fondo++;
          // The glyph, scaled about the centre.
          const gx = (c + (fx - c) / escala) / u;
          const gy = (c + (fy - c) / escala) / u;
          if (H.some(([x0, y0, x1, y1]) => gx >= x0 && gx < x1 && gy >= y0 && gy < y1)) letra++;
        }
      }
      const total = n * n;
      const a = fondo / total;
      const l = Math.min(letra, fondo) / total;
      const i = (y * size + x) * 4;
      for (let k = 0; k < 3; k++) {
        // Premultiplied mix of glyph over background, then un-premultiply for PNG.
        const v = a > 0 ? (FONDO[k] * (a - l) + LETRA[k] * l) / a : 0;
        px[i + k] = Math.round(v);
      }
      px[i + 3] = Math.round(a * 255);
    }
  }
  return px;
}

const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function trozo(tipo, datos) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(datos.length);
  const cuerpo = Buffer.concat([Buffer.from(tipo, "ascii"), datos]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(cuerpo));
  return Buffer.concat([len, cuerpo, crc]);
}

function png(size, px) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const filas = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    filas[y * (size * 4 + 1)] = 0; // filter: none
    Buffer.from(px.buffer, y * size * 4, size * 4).copy(filas, y * (size * 4 + 1) + 1);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    trozo("IHDR", ihdr),
    trozo("IDAT", deflateSync(filas, { level: 9 })),
    trozo("IEND", Buffer.alloc(0)),
  ]);
}

function svg() {
  const hex = (rgb) => `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
  const rects = H.map(([x0, y0, x1, y1]) => `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="${hex(FONDO)}"/><g fill="${hex(LETRA)}">${rects}</g></svg>\n`;
}

mkdirSync(SALIDA, { recursive: true });
const salidas = [
  // "any" icons: rounded like the favicon.
  ["icono-192.png", 192, { redondeo: 14, escala: 1 }],
  ["icono-512.png", 512, { redondeo: 14, escala: 1 }],
  // "maskable": full bleed, the glyph inside the 80 % safe zone (the platform applies its own mask).
  ["icono-maskable-512.png", 512, { redondeo: 0, escala: 0.85 }],
  // iOS rounds the corners itself and shows transparency as black: full bleed.
  ["apple-touch-icon.png", 180, { redondeo: 0, escala: 0.9 }],
];
for (const [nombre, size, opciones] of salidas) {
  writeFileSync(join(SALIDA, nombre), png(size, pintar(size, opciones)));
}
writeFileSync(join(SALIDA, "icono.svg"), svg());
console.log(`Iconos escritos en ${SALIDA}`);
