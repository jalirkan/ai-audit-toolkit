/**
 * Typed client for the read-only evidence API.
 *
 * Same-origin by default: `serve.py` serves both the assets and the API in
 * production, and the Vite dev server proxies `/api` to it. There is no base
 * URL to configure and no credential to send.
 *
 * Errors arrive as `{"error": {"code", "message"}}` with a real status, and
 * the CLI's habit of naming what *does* exist when you ask for something that
 * does not is preserved -- so the message is surfaced to the reader rather
 * than replaced with a generic failure string.
 */

import {
  type BatteryResult,
  type ProbeInfo,
  type RunSummary,
  parseBatteryResult,
} from "./schema";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    signal,
    headers: { Accept: "application/json" },
  });

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new ApiError(
        response.status,
        "unreadable-response",
        `${path} did not return JSON`,
      );
    }
  }

  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "unknown-error",
      error?.message ?? `${path} failed with status ${response.status}`,
    );
  }

  return body as T;
}

export const api = {
  meta: (signal?: AbortSignal) => request<Record<string, unknown>>("/api/meta", signal),

  runs: (signal?: AbortSignal) => request<RunSummary[]>("/api/runs", signal),

  run: async (runId: string, signal?: AbortSignal): Promise<BatteryResult> =>
    parseBatteryResult(await request<unknown>(`/api/runs/${runId}`, signal)),

  coverage: (runId: string, capabilities: string[] = [], signal?: AbortSignal) => {
    const query = capabilities.length
      ? `?capabilities=${encodeURIComponent(capabilities.join(","))}`
      : "";
    return request<Record<string, unknown>>(
      `/api/runs/${runId}/coverage${query}`,
      signal,
    );
  },

  probes: (signal?: AbortSignal) => request<ProbeInfo[]>("/api/probes", signal),

  suites: (signal?: AbortSignal) =>
    request<Record<string, unknown>[]>("/api/suites", signal),
};
