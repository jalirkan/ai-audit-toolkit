/**
 * Payload types for comparison, coverage and drift.
 *
 * These mirror `ComparisonMatrix.to_dict`, `CoverageReport.to_dict` and
 * `DriftReport.to_dict`. `all_overlap` on a metric row is the engine's
 * judgment about what the run established (D-036/D-042); the client reads it
 * and never recomputes it.
 */

import type { Measurement, Fingerprint, OutcomeCounts } from "./schema";

export interface TokenAccounting {
  calls: number;
  calls_with_usage: number;
  calls_without_usage: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export interface ComparisonEndpoint {
  label: string;
  description: string;
  outcome: string;
  outcome_counts: OutcomeCounts;
  run_id: string;
  fingerprint: Fingerprint;
  total_calls: number;
  tokens: TokenAccounting;
}

export interface MetricRowPayload {
  probe_id: string;
  unit: string;
  metric: string;
  direction: string;
  all_overlap: boolean;
  overlapping_labels: string[];
  by_label: Record<string, Measurement>;
}

export interface ComparisonPayload {
  battery: string;
  endpoints: ComparisonEndpoint[];
  units: { probe_id: string; unit: string; outcomes: Record<string, string> }[];
  metric_rows: MetricRowPayload[];
  undistinguished_metrics: { probe_id: string; unit: string; metric: string }[];
}

// --- coverage ---------------------------------------------------------------

export interface ControlCoveragePayload {
  framework: string;
  control_id: string;
  summary: string;
  topic: string;
  status: string;
  probe_ids: string[];
  capabilities: string[];
  outcome_counts: Record<string, number>;
  references: { framework: string; control_id: string; rationale: string }[];
}

export interface FrameworkCoveragePayload {
  framework: {
    id: string;
    name: string;
    publication: string;
    partial: boolean;
    ids_verified: string;
    note: string;
    controls: unknown[];
  };
  counts: Record<string, number>;
  controls: ControlCoveragePayload[];
}

export interface CoveragePayload {
  active_probe_ids: string[];
  active_capabilities: string[];
  inactive_sources: string[];
  frameworks: FrameworkCoveragePayload[];
  disclaimer: string;
}

// --- drift ------------------------------------------------------------------

export interface DifferenceInterval {
  point: number;
  low: number;
  high: number;
  confidence: number;
  resamples: number;
  seed: number;
  /** D-035: widened to the analytic bound where Newcombe is wider. */
  widened?: boolean;
}

export interface MetricComparisonPayload {
  probe_id: string;
  unit: string;
  metric: string;
  direction: string;
  verdict: string;
  detail: string;
  baseline: Measurement;
  current: Measurement;
  delta: number;
  interval: DifferenceInterval | null;
}

export interface UnitComparisonPayload {
  probe_id: string;
  unit: string;
  baseline_outcome: string;
  current_outcome: string;
  outcome_changed: boolean;
  outcome_worsened: boolean;
  config_changed: boolean;
  metrics: MetricComparisonPayload[];
}

export interface DriftPayload {
  baseline_label: string;
  baseline_run_id: string;
  current_run_id: string;
  baseline_fingerprint: Fingerprint;
  current_fingerprint: Fingerprint;
  fingerprint_changed: boolean;
  comparable: boolean;
  has_drift: boolean;
  added_units: [string, string][];
  removed_units: [string, string][];
  units: UnitComparisonPayload[];
}
