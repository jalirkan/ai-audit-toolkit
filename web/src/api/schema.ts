/**
 * Engine payload types, and runtime checks that refuse what they cannot read.
 *
 * `Evidence.from_dict` raises on a schema version newer than the build
 * understands rather than parsing what it recognises and ignoring the rest.
 * The client takes the same stance: a partially-understood evidence record is
 * worse than a refused one, because the parts it silently dropped might have
 * been the exceptions.
 *
 * The validation here is deliberately shallow and hand-written. It checks the
 * shape the views actually depend on, and it is a dependency-free ~150 lines
 * instead of a schema library. The Python side is the authority on these
 * shapes and it is already tested; this exists to fail loudly at the boundary,
 * not to restate the contract.
 */

export const SUPPORTED_EVIDENCE_SCHEMA = 1;
export const SUPPORTED_BATTERY_SCHEMA = 1;

export type Outcome = "pass" | "fail" | "inconclusive" | "error";
export type MeasurementKind = "proportion" | "mean" | "count";
export type Direction = "lower_is_better" | "higher_is_better" | "neutral";

export interface Measurement {
  name: string;
  kind: MeasurementKind;
  value: number;
  n: number;
  ci_low: number | null;
  ci_high: number | null;
  ci_method: string;
  confidence: number | null;
  successes: number | null;
  method_note: string;
  direction: Direction;
}

export interface TokenUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  [key: string]: number | undefined;
}

export interface Trial {
  index: number;
  prompt: string;
  response_text: string;
  system: string | null;
  latency_ms: number;
  passed: boolean | null;
  labels: Record<string, unknown>;
  usage?: TokenUsage;
}

export interface Fingerprint {
  adapter: string;
  model: string;
  params: Record<string, unknown>;
  system_prompt_hash: string | null;
}

export interface EvidenceConfig {
  unit?: string;
  decision_rule?: string;
  decision_threshold?: number;
  decision_direction?: Direction;
  decision_metric?: string;
  [key: string]: unknown;
}

export interface Evidence {
  schema_version: number;
  probe_id: string;
  outcome: Outcome;
  fingerprint: Fingerprint;
  started_at: string;
  finished_at: string;
  trials: Trial[];
  measurements: Measurement[];
  config: EvidenceConfig;
  notes: string;
  limitations: string;
}

export interface OutcomeCounts {
  pass: number;
  fail: number;
  inconclusive: number;
  error: number;
  [key: string]: number;
}

export interface BatteryResult {
  schema_version: number;
  battery: string;
  description: string;
  run_id: string;
  started_at: string;
  finished_at: string;
  fingerprint: Fingerprint;
  outcome: Outcome;
  outcome_counts: OutcomeCounts;
  evidence: Evidence[];
}

export interface RunSummary {
  run_id: string;
  battery: string;
  description: string;
  outcome: Outcome;
  outcome_counts: OutcomeCounts;
  started_at: string;
  finished_at: string;
  fingerprint: Fingerprint;
  units_tested: number;
  total_trials: number;
  schema_version: number;
}

export interface ProbeInfo {
  probe_id: string;
  title: string;
  procedure: string;
  population: string;
  limitations: string;
  remediation: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

/** Raised when a payload is not the shape or version this build reads. */
export class SchemaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SchemaError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new SchemaError(`${path} must be a number, got ${typeof value}`);
  }
  return value;
}

function requireString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new SchemaError(`${path} must be a string, got ${typeof value}`);
  }
  return value;
}

function nullableNumber(value: unknown, path: string): number | null {
  if (value === null || value === undefined) return null;
  return requireNumber(value, path);
}

/**
 * Check a measurement is reportable before anything tries to draw it.
 *
 * The engine refuses to construct a proportion or a mean without an interval,
 * a named method, and a confidence level. If one reaches the browser without
 * them, something upstream is broken in a way that would show the reader a
 * bare rate -- so this fails rather than rendering it.
 */
export function parseMeasurement(raw: unknown, path = "measurement"): Measurement {
  if (!isRecord(raw)) throw new SchemaError(`${path} is not an object`);
  const name = requireString(raw.name, `${path}.name`);
  const kind = requireString(raw.kind, `${path}.kind`) as MeasurementKind;
  const n = requireNumber(raw.n, `${path}.n`);
  const ci_low = nullableNumber(raw.ci_low, `${path}.ci_low`);
  const ci_high = nullableNumber(raw.ci_high, `${path}.ci_high`);

  if ((kind === "proportion" || kind === "mean") && (ci_low === null || ci_high === null)) {
    throw new SchemaError(
      `${path} "${name}" is a ${kind} with no interval; a bare rate is not reportable`,
    );
  }

  return {
    name,
    kind,
    value: requireNumber(raw.value, `${path}.value`),
    n,
    ci_low,
    ci_high,
    ci_method: typeof raw.ci_method === "string" ? raw.ci_method : "none",
    confidence: nullableNumber(raw.confidence, `${path}.confidence`),
    successes: nullableNumber(raw.successes, `${path}.successes`),
    method_note: typeof raw.method_note === "string" ? raw.method_note : "",
    direction: (typeof raw.direction === "string" ? raw.direction : "neutral") as Direction,
  };
}

export function parseEvidence(raw: unknown, path = "evidence"): Evidence {
  if (!isRecord(raw)) throw new SchemaError(`${path} is not an object`);
  const version = typeof raw.schema_version === "number" ? raw.schema_version : 1;
  if (version > SUPPORTED_EVIDENCE_SCHEMA) {
    throw new SchemaError(
      `evidence schema version ${version} is newer than this build understands ` +
        `(${SUPPORTED_EVIDENCE_SCHEMA}); refusing to parse it partially`,
    );
  }
  const measurements = Array.isArray(raw.measurements) ? raw.measurements : [];
  const trials = Array.isArray(raw.trials) ? raw.trials : [];
  return {
    schema_version: version,
    probe_id: requireString(raw.probe_id, `${path}.probe_id`),
    outcome: requireString(raw.outcome, `${path}.outcome`) as Outcome,
    fingerprint: raw.fingerprint as Fingerprint,
    started_at: requireString(raw.started_at, `${path}.started_at`),
    finished_at: requireString(raw.finished_at, `${path}.finished_at`),
    trials: trials as Trial[],
    measurements: measurements.map((m, i) =>
      parseMeasurement(m, `${path}.measurements[${i}]`),
    ),
    config: isRecord(raw.config) ? (raw.config as EvidenceConfig) : {},
    notes: typeof raw.notes === "string" ? raw.notes : "",
    limitations: typeof raw.limitations === "string" ? raw.limitations : "",
  };
}

export function parseBatteryResult(raw: unknown): BatteryResult {
  if (!isRecord(raw)) throw new SchemaError("run payload is not an object");
  const version = typeof raw.schema_version === "number" ? raw.schema_version : 1;
  if (version > SUPPORTED_BATTERY_SCHEMA) {
    throw new SchemaError(
      `battery result schema version ${version} is newer than this build ` +
        `understands (${SUPPORTED_BATTERY_SCHEMA})`,
    );
  }
  const evidence = Array.isArray(raw.evidence) ? raw.evidence : [];
  return {
    schema_version: version,
    battery: requireString(raw.battery, "battery"),
    description: typeof raw.description === "string" ? raw.description : "",
    run_id: requireString(raw.run_id, "run_id"),
    started_at: requireString(raw.started_at, "started_at"),
    finished_at: requireString(raw.finished_at, "finished_at"),
    fingerprint: raw.fingerprint as Fingerprint,
    outcome: requireString(raw.outcome, "outcome") as Outcome,
    outcome_counts: raw.outcome_counts as OutcomeCounts,
    evidence: evidence.map((e, i) => parseEvidence(e, `evidence[${i}]`)),
  };
}

/** The unit a record tested, as `config.unit` carries it (D-013). */
export function unitOf(evidence: Evidence): string {
  return typeof evidence.config.unit === "string" ? evidence.config.unit : "";
}

/**
 * The measurement a conclusion was drawn from.
 *
 * The citation probe reports two rates and names the deciding one in
 * `config.decision_metric` (D-014), because the finer-grained claim-level rate
 * is not the independent sampling unit. Honour that rather than assuming the
 * first measurement is the one the decision used.
 */
export function decisionMeasurement(evidence: Evidence): Measurement | undefined {
  const named = evidence.config.decision_metric;
  if (typeof named === "string") {
    const found = evidence.measurements.find((m) => m.name === named);
    if (found) return found;
  }
  return evidence.measurements[0];
}
