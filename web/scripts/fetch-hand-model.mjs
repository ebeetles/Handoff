// Downloads MediaPipe's hand_landmarker.task (float16, v1) into public/models/ once, so the
// app serves it itself (no third-party requests at runtime, same as the fonts).
//   node scripts/fetch-hand-model.mjs          fail loudly if it can't download
//   node scripts/fetch-hand-model.mjs --soft   warn only (used by predev/prebuild)
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const MD5 = "15318430ea3851670fe9914116a9cfad";   // GCS x-goog-hash md5 for this object
const dest = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "models", "hand_landmarker.task");
const soft = process.argv.includes("--soft");
const md5 = (buf) => createHash("md5").update(buf).digest("hex");

if (existsSync(dest) && md5(readFileSync(dest)) === MD5) process.exit(0);
try {
  console.log(`Downloading hand model -> ${dest}`);
  const res = await fetch(URL);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (md5(buf) !== MD5) throw new Error(`md5 ${md5(buf)} != ${MD5}`);
  mkdirSync(dirname(dest), { recursive: true });
  writeFileSync(dest, buf);
  console.log(`Saved ${(buf.length / 1e6).toFixed(1)} MB`);
} catch (e) {
  const msg = `Could not fetch the hand model (${e.message}). Hand input will be unavailable until you run: npm run fetch-hand-model`;
  if (!soft) { console.error(msg); process.exit(1); }
  console.warn(`WARNING: ${msg}`);
}
