import { cp, mkdir, rm } from "node:fs/promises";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { build } from "esbuild";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distDir = resolve(projectRoot, "dist");

await rm(distDir, { recursive: true, force: true });
await mkdir(distDir, { recursive: true });

await Promise.all([
  build({
    absWorkingDir: projectRoot,
    entryPoints: ["src/background/index.ts"],
    outfile: "dist/background.js",
    bundle: true,
    format: "esm",
    target: "chrome120",
    sourcemap: true
  }),
  build({
    absWorkingDir: projectRoot,
    entryPoints: ["src/content/index.ts"],
    outfile: "dist/content.js",
    bundle: true,
    format: "iife",
    target: "chrome120",
    sourcemap: true
  }),
  build({
    absWorkingDir: projectRoot,
    entryPoints: ["src/popup/index.ts"],
    outfile: "dist/popup.js",
    bundle: true,
    format: "esm",
    target: "chrome120",
    sourcemap: true
  }),
  build({
    absWorkingDir: projectRoot,
    entryPoints: ["src/options/index.ts"],
    outfile: "dist/options.js",
    bundle: true,
    format: "esm",
    target: "chrome120",
    sourcemap: true
  })
]);

await Promise.all([
  cp(resolve(projectRoot, "public/manifest.json"), resolve(distDir, "manifest.json")),
  cp(resolve(projectRoot, "public/popup.html"), resolve(distDir, "popup.html")),
  cp(resolve(projectRoot, "public/options.html"), resolve(distDir, "options.html")),
  cp(resolve(projectRoot, "public/styles.css"), resolve(distDir, "styles.css")),
  cp(
    resolve(projectRoot, "public/THIRD_PARTY_NOTICES.md"),
    resolve(distDir, "THIRD_PARTY_NOTICES.md")
  )
]);

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let index = 0; index < 8; index += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])));
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function createIcon(size) {
  const pixels = Buffer.alloc((size * 4 + 1) * size);
  const radius = size * 0.22;
  const inset = size * 0.06;
  const center = size / 2;
  const hubRadius = size * 0.13;
  const spokeWidth = Math.max(1, size * 0.055);

  for (let y = 0; y < size; y += 1) {
    const row = y * (size * 4 + 1);
    pixels[row] = 0;
    for (let x = 0; x < size; x += 1) {
      const nearestX = Math.max(inset + radius, Math.min(x, size - inset - radius));
      const nearestY = Math.max(inset + radius, Math.min(y, size - inset - radius));
      const insideMark =
        x >= inset &&
        x <= size - inset &&
        y >= inset &&
        y <= size - inset &&
        (Math.hypot(x - nearestX, y - nearestY) <= radius ||
          (x >= inset + radius && x <= size - inset - radius) ||
          (y >= inset + radius && y <= size - inset - radius));
      const verticalSpoke =
        Math.abs(x - center) <= spokeWidth &&
        y >= size * 0.23 &&
        y <= size * 0.77;
      const horizontalSpoke =
        Math.abs(y - center) <= spokeWidth &&
        x >= size * 0.23 &&
        x <= size * 0.77;
      const hub = Math.hypot(x - center, y - center) <= hubRadius;
      const light = insideMark && (verticalSpoke || horizontalSpoke || hub);
      const offset = row + 1 + x * 4;
      pixels[offset] = light ? 245 : 36;
      pixels[offset + 1] = light ? 255 : 92;
      pixels[offset + 2] = light ? 247 : 59;
      pixels[offset + 3] = insideMark ? 255 : 0;
    }
  }

  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 6;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(pixels)),
    pngChunk("IEND", Buffer.alloc(0))
  ]);
}

const iconDirectory = resolve(distDir, "icons");
await mkdir(iconDirectory, { recursive: true });
await Promise.all(
  [16, 32, 48, 128].map((size) =>
    writeFile(resolve(iconDirectory, `icon${size}.png`), createIcon(size))
  )
);
