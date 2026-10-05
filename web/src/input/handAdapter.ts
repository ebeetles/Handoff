// Webcam hands -> GestureController. Sibling of pointerAdapter.ts: it only emits
// PointerSamples (via HandFrameProcessor), so the engine and UI don't know hands exist.
//
// MediaPipe Tasks Vision HandLandmarker (@mediapipe/tasks-vision 1.0.1, read from its
// vision.d.ts): VIDEO running mode, detectForVideo(video, timestampMs) is synchronous and
// needs increasing timestamps. Its handedness assumes a mirrored image; we pass the raw
// camera frame and swap labels in handTracking.ts.
//
// Everything is self-hosted: the wasm comes from node_modules via Vite ?url, and the model
// from public/models (npm run fetch-hand-model). The MediaPipe JS (~1 MB) is loaded only
// when the camera is first turned on. Per-frame work never touches React state.
import wasmLoaderUrl from "@mediapipe/tasks-vision/vision_wasm_internal.js?url";
import wasmBinaryUrl from "@mediapipe/tasks-vision/vision_wasm_internal.wasm?url";
import type { HandLandmarker } from "@mediapipe/tasks-vision";
import { DEFAULT_HAND_TUNING, HandFrameProcessor, type HandFrame, type HandStats, type PointerSink } from "./handTracking";

export type CameraStatus =
  | { state: "off" }
  | { state: "starting" }
  | { state: "running"; delegate: "GPU" | "CPU" }
  | { state: "error"; message: string };

export interface InferenceStats { fps: number; meanMs: number; p95Ms: number; samples: number; warmupMs: number[] }

const MIN_FRAME_MS = 1000 / 30 - 4;   // ~30 fps cap; the slack absorbs rAF jitter at 60 Hz
const TIMING_WINDOW = 150;            // frames of inference timing kept for mean / p95
const WARMUP_FRAMES = 5;              // first frames compile GPU shaders (seconds); reported apart
const LANDMARK_COLOR = "#f4f0ff";
const PINCH_COLOR = "#ffb340";

class StartError extends Error {}

/** Raw landmark frames, for tuning on a real hand and for replay tests. */
export interface HandRecording {
  version: 1;
  createdAt: string;
  video: { width: number; height: number };
  viewport: { width: number; height: number };
  tuning: typeof DEFAULT_HAND_TUNING;
  frames: ({ t: number } & HandFrame)[];
}

const r5 = (v: number) => Math.round(v * 1e5) / 1e5;
function recordFrame(f: HandFrame, t: number): { t: number } & HandFrame {
  const pts = (hands: HandFrame["landmarks"]) => hands.map((h) => h.map((p) => ({ x: r5(p.x), y: r5(p.y), z: r5(p.z) })));
  return {
    t: Math.round(t * 10) / 10,
    landmarks: pts(f.landmarks),
    worldLandmarks: pts(f.worldLandmarks),
    handedness: f.handedness.map((c) => c.map(({ categoryName, score }) => ({ categoryName, score: r5(score) }))),
  };
}

/** Turn whatever start() threw into one sentence a user can act on. */
export function cameraErrorMessage(e: unknown): string {
  if (e instanceof StartError) return e.message;
  const name = e instanceof DOMException ? e.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Camera access is blocked. Allow the camera for this site (camera icon in the address bar), then press Camera again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No camera found. Connect one and press Camera again.";
    case "NotReadableError":
    case "AbortError":
      return "The camera is busy or blocked by the system. Close other apps using it (and check the OS camera privacy setting), then try again.";
  }
  return `Hand tracking failed to start: ${e instanceof Error ? e.message : String(e)}`;
}

export class HandAdapter {
  private st: CameraStatus = { state: "off" };
  private listeners = new Set<() => void>();
  private readonly proc: HandFrameProcessor;
  private landmarker: { lm: HandLandmarker; delegate: "GPU" | "CPU" } | null = null;
  private loading: Promise<{ lm: HandLandmarker; delegate: "GPU" | "CPU" }> | null = null;
  private stream: MediaStream | null = null;
  private video: HTMLVideoElement | null = null;   // set while running
  private els: { video: HTMLVideoElement; overlay: HTMLCanvasElement } | null = null;
  private raf = 0;
  private lastRun = 0;
  private lastVideoTime = -1;
  private timings: number[] = [];
  private warmup: number[] = [];
  private frameTimes: number[] = [];
  private session = 0;   // bumps on stop() so a slow start() can tell it was cancelled
  private recording: HandRecording | null = null;

  constructor(sink: PointerSink, private readonly modelUrl: string) {
    this.proc = new HandFrameProcessor(sink);
  }

  status(): CameraStatus { return this.st; }

  /** Pinch sensitivity (grab threshold, see handTracking PINCH_DOWN_RANGE). */
  setPinchDown(v: number): void { this.proc.setPinchDown(v); }
  pinchDown(): number { return this.proc.pinchThreshold; }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  stats(): { hands: Readonly<HandStats>; inference: InferenceStats } {
    const sorted = [...this.timings].sort((a, b) => a - b);
    const now = performance.now();
    const recent = this.frameTimes.filter((t) => now - t <= 1000);
    return {
      hands: this.proc.stats,
      inference: {
        fps: recent.length,
        meanMs: sorted.length ? sorted.reduce((a, b) => a + b, 0) / sorted.length : 0,
        p95Ms: sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor(0.95 * sorted.length))]! : 0,
        samples: sorted.length,
        warmupMs: [...this.warmup],
      },
    };
  }

  /** The camera preview: a <video> plus a landmark overlay <canvas>, owned by the adapter
   *  so any component can show it. Classes: hand-preview-video / hand-preview-overlay. */
  mountPreview(container: HTMLElement): () => void {
    const { video, overlay } = this.preview();
    container.append(video, overlay);
    return () => { video.remove(); overlay.remove(); };
  }

  private preview(): { video: HTMLVideoElement; overlay: HTMLCanvasElement } {
    if (!this.els) {
      const video = document.createElement("video");
      video.className = "hand-preview-video";
      const overlay = document.createElement("canvas");
      overlay.className = "hand-preview-overlay";
      this.els = { video, overlay };
    }
    return this.els;
  }

  async start(): Promise<void> {
    if (this.st.state === "starting" || this.st.state === "running") return;
    const session = ++this.session;
    const { video } = this.preview();
    this.set({ state: "starting" });
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new StartError("Camera input needs a secure page (https or localhost).");
      }
      // Load the model while the permission prompt is up.
      // Shared across start() calls so a stop/start while loading doesn't build a second model.
      const landmarkerP = this.loading ??= this.createLandmarker();
      landmarkerP.catch(() => { this.loading = null; });   // allow a retry; the await below reports it
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" }, audio: false,
      });
      if (session !== this.session) { stream.getTracks().forEach((t) => t.stop()); return; }
      this.stream = stream;   // from here on, teardown() stops it if anything fails
      const landmarker = await landmarkerP;
      this.landmarker = landmarker;
      if (session !== this.session) return;
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await video.play();
      if (session !== this.session) return;
      this.video = video;
      this.lastVideoTime = -1;
      this.timings = [];
      this.warmup = [];
      this.frameTimes = [];
      this.set({ state: "running", delegate: landmarker.delegate });
      this.raf = requestAnimationFrame(this.loop);
    } catch (e) {
      if (session !== this.session) return;
      this.teardown();
      this.set({ state: "error", message: cameraErrorMessage(e) });
    }
  }

  /** Record raw landmarks for `ms` (camera must be running). */
  record(ms: number): Promise<HandRecording> {
    const video = this.video;
    if (this.st.state !== "running" || !video) return Promise.reject(new Error("camera is not running"));
    const rec: HandRecording = {
      version: 1, createdAt: new Date().toISOString(),
      video: { width: video.videoWidth, height: video.videoHeight },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      tuning: DEFAULT_HAND_TUNING, frames: [],
    };
    this.recording = rec;
    // A timer only ends the recording; it doesn't trigger sound.
    return new Promise((resolve) => setTimeout(() => {
      if (this.recording === rec) this.recording = null;
      resolve(rec);
    }, ms));
  }

  stop(): void {
    this.session++;
    this.teardown();
    this.set({ state: "off" });
  }

  private teardown(): void {
    cancelAnimationFrame(this.raf);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    this.video = null;
    const overlay = this.els?.overlay;
    overlay?.getContext("2d")?.clearRect(0, 0, overlay.width, overlay.height);
    this.proc.releaseAll();
  }

  private set(s: CameraStatus): void {
    this.st = s;
    this.listeners.forEach((fn) => fn());
  }

  private async createLandmarker(): Promise<{ lm: HandLandmarker; delegate: "GPU" | "CPU" }> {
    // Vite's dev server answers a missing file with index.html, so check the type too.
    const head = await fetch(this.modelUrl, { method: "HEAD" });
    if (!head.ok || (head.headers.get("content-type") ?? "").includes("text/html")) {
      throw new StartError(`Hand model not found at ${this.modelUrl}. In web/, run: npm run fetch-hand-model`);
    }
    const { HandLandmarker } = await import("@mediapipe/tasks-vision");
    const fileset = { wasmLoaderPath: wasmLoaderUrl, wasmBinaryPath: wasmBinaryUrl };
    let lastErr: unknown = null;
    for (const delegate of ["GPU", "CPU"] as const) {
      try {
        const lm = await HandLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: this.modelUrl, delegate },
          runningMode: "VIDEO",
          numHands: 2,
          // Defaults are 0.5. Lower presence/tracking keep following a hand through awkward
          // pinch and twist poses instead of dropping it; new hands still need 0.5 to appear.
          minHandPresenceConfidence: 0.3,
          minTrackingConfidence: 0.3,
        });
        // The first inference compiles GPU shaders and blocks the main thread for seconds
        // (~5 s measured, Chromium on an M3 Max). Pay that now, while the UI says
        // "Starting", not on the first live frame.
        const blank = document.createElement("canvas");
        blank.width = 640;
        blank.height = 480;
        blank.getContext("2d")?.fillRect(0, 0, blank.width, blank.height);
        lm.detectForVideo(blank, performance.now());
        return { lm, delegate };
      } catch (e) {
        lastErr = e;   // no WebGL2 (e.g. some headless browsers): fall back to CPU
      }
    }
    throw lastErr;
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop);
    const video = this.video, lm = this.landmarker?.lm;
    const now = performance.now();
    if (!video || !lm || now - this.lastRun < MIN_FRAME_MS) return;
    if (video.readyState < 2 || video.currentTime === this.lastVideoTime) return;   // no new frame
    this.lastVideoTime = video.currentTime;
    this.lastRun = now;
    try {
      const t0 = performance.now();
      const result = lm.detectForVideo(video, now);
      const ms = performance.now() - t0;
      if (this.warmup.length < WARMUP_FRAMES) this.warmup.push(ms);
      else {
        this.timings.push(ms);
        if (this.timings.length > TIMING_WINDOW) this.timings.shift();
      }
      this.frameTimes.push(now);
      while (this.frameTimes.length && now - this.frameTimes[0]! > 1000) this.frameTimes.shift();
      this.proc.process(result, now, { width: window.innerWidth, height: window.innerHeight },
                        video.videoWidth / Math.max(1, video.videoHeight));
      this.recording?.frames.push(recordFrame(result, now));
      this.draw(result.landmarks, video);
    } catch (e) {
      this.teardown();
      this.set({ state: "error", message: `Hand tracking stopped: ${e instanceof Error ? e.message : String(e)}` });
    }
  };

  /** Landmarks over the preview, in raw camera coords (the preview is mirrored by CSS). */
  private draw(hands: { x: number; y: number }[][], video: HTMLVideoElement): void {
    const c = this.els?.overlay, ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    if (c.width !== video.videoWidth) c.width = video.videoWidth;
    if (c.height !== video.videoHeight) c.height = video.videoHeight;
    ctx.clearRect(0, 0, c.width, c.height);
    for (const h of hands) {
      ctx.fillStyle = LANDMARK_COLOR;
      for (const p of h) ctx.fillRect(p.x * c.width - 2, p.y * c.height - 2, 4, 4);
      const t = h[4], i = h[8];
      if (!t || !i) continue;
      ctx.strokeStyle = PINCH_COLOR;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(t.x * c.width, t.y * c.height);
      ctx.lineTo(i.x * c.width, i.y * c.height);
      ctx.stroke();
    }
  }
}
