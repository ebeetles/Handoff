// POST /api/composer/compose v1; mirrored by backend.app.ComposeRequest and its JSON schema.
// Response: Transitions v2. Keys and model selection stay on the server.
export interface ComposeRequest {
  schema_version: 1;
  out_track: string;
  in_track: string;
  out_start_bar: number | null;
  min_start_bar: number;
  max_bars: number;
  candidates: number;
  brief: string;
}
