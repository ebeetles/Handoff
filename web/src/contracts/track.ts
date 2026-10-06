// Mirror of contracts/track_analysis.schema.json (v3). If you change one, change both,
// bump schema_version, regenerate contracts/fixtures/, and run both test suites.

export const SCHEMA_VERSION = 3;
export const STEM_NAMES = ["drums", "bass", "vocals", "other"] as const;
export type StemName = (typeof STEM_NAMES)[number];

export interface BandEnergy { low: number; mid: number; high: number }
export interface Bar { index: number; beat_index: number; start_s: number; energy: BandEnergy; rms_db: number }
export interface Phrase { index: number; start_bar: number; n_bars: number; start_s: number; section_index: number }
export interface Section {
  index: number; start_bar: number; end_bar: number; start_s: number;
  label: "intro" | "main" | "break" | "outro"; energy_level: "low" | "mid" | "high";
}

export interface TrackAnalysis {
  schema_version: 3;
  id: string;
  title: string;
  artist: string;
  source: { filename: string; license: string | null; credit: string | null };
  /** stems_gain_db: play the stems at this gain and they add back up to the mix (null: no stems). */
  audio: { mix: string; stems: Record<StemName, string> | null; stems_gain_db: number | null };
  sample_rate: number;
  duration_s: number;
  tempo: { bpm: number; ibi_cv: number; max_drift_ms: number | null; beatmatchable: boolean; grid: "regular" | "tracked" };
  beats: number[];
  beats_per_bar: 4;
  first_downbeat_index: number;
  downbeat_confidence: number;
  bars: Bar[];
  phrase_bars: number;
  phrases: Phrase[];
  sections: Section[];
  key: { name: string; camelot: string; confidence: number };
  waveform: string;
  meta: { pipeline_version: string; generated_at: string; overrides_applied: Record<string, unknown> };
}

export interface Waveform { schema_version: 1; points_per_second: number; low: number[]; mid: number[]; high: number[] }

export interface LibraryIndexEntry {
  id: string; title: string; artist: string; bpm: number; camelot: string;
  duration_s: number; has_stems: boolean; beatmatchable: boolean;
}
export interface LibraryIndex { schema_version: 1; tracks: LibraryIndexEntry[] }

/** Cheap structural check so a stale/broken library fails loudly with a readable message
 *  instead of NaNs deep inside the audio engine. Not a full JSON Schema validator. */
export function assertTrackAnalysis(x: unknown): asserts x is TrackAnalysis {
  const a = x as Partial<TrackAnalysis>;
  const fail = (why: string): never => {
    throw new Error(`analysis.json for "${a?.id ?? "?"}" is invalid: ${why}. Re-run the pipeline.`);
  };
  if (!a || typeof a !== "object") fail("not an object");
  if (a.schema_version !== SCHEMA_VERSION) fail(`schema_version ${a.schema_version} != ${SCHEMA_VERSION}`);
  if (!Array.isArray(a.beats) || a.beats.length < 8) fail("needs at least 8 beats");
  for (let i = 1; i < a.beats!.length; i++) if (!(a.beats![i]! > a.beats![i - 1]!)) fail(`beats not increasing at ${i}`);
  if (a.beats_per_bar !== 4) fail("beats_per_bar must be 4");
  if (typeof a.first_downbeat_index !== "number" || a.first_downbeat_index < 0 || a.first_downbeat_index > 3) fail("bad first_downbeat_index");
  if (!a.audio || typeof a.audio.mix !== "string") fail("missing audio.mix");
  if (!Array.isArray(a.bars) || !Array.isArray(a.phrases) || !Array.isArray(a.sections)) fail("missing bars/phrases/sections");
  if (!a.tempo || !(a.tempo.bpm > 0)) fail("bad tempo");
}
