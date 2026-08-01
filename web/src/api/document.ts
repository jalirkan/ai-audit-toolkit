/**
 * The engine's document model, as the JSON renderer emits it.
 *
 * Mirrors `report/document.py`. Kept structurally identical rather than
 * flattened into something more convenient for React: the whole point of the
 * shared model is that a block present in one renderer is present in all of
 * them, and a client that reshaped it could silently drop one.
 */

export const SUPPORTED_DOCUMENT_SCHEMA = 1;

export interface ParagraphBlock {
  type: "paragraph";
  text: string;
}
export interface BulletsBlock {
  type: "bullets";
  items: string[];
}
export interface FieldsBlock {
  type: "fields";
  pairs: [string, string][];
}
export interface TableBlock {
  type: "table";
  headers: string[];
  rows: string[][];
}
export interface CalloutBlock {
  type: "callout";
  text: string;
  kind: "note" | "warning";
}
export interface PreformattedBlock {
  type: "preformatted";
  text: string;
}

export type DocumentBlock =
  | ParagraphBlock
  | BulletsBlock
  | FieldsBlock
  | TableBlock
  | CalloutBlock
  | PreformattedBlock;

export interface DocumentSection {
  title: string;
  level: number;
  blocks: DocumentBlock[];
  subsections: DocumentSection[];
}

export interface DocumentModel {
  schema_version: number;
  title: string;
  subtitle: string;
  meta: [string, string][];
  footer: string;
  sections: DocumentSection[];
}

export function parseDocument(raw: unknown): DocumentModel {
  const doc = raw as DocumentModel;
  if (!doc || typeof doc !== "object") {
    throw new Error("workpaper payload is not an object");
  }
  if ((doc.schema_version ?? 1) > SUPPORTED_DOCUMENT_SCHEMA) {
    throw new Error(
      `workpaper schema version ${doc.schema_version} is newer than this build ` +
        `understands (${SUPPORTED_DOCUMENT_SCHEMA})`,
    );
  }
  return doc;
}
