/**
 * The workpaper, rendered from the engine's own document model.
 *
 * `report/workpaper.py` builds a structure of sections and blocks, and
 * `render_markdown` and `render_html` lay it out. This is the third renderer
 * (D-029, extended): the same blocks, laid out for a browser and for a
 * printer. Nothing here decides what a workpaper contains -- if a section is
 * missing from the screen it is missing from the Markdown too, which is the
 * property having one model was meant to buy.
 *
 * The limitations block is a callout in the model and a callout here, sitting
 * where the result is rather than as a footnote. A reviewer who reads the
 * conclusion without reading what the screen cannot detect has been misled by
 * the layout.
 */

import type {
  DocumentModel,
  DocumentSection,
  DocumentBlock,
} from "../api/document";

function Blocks({ blocks }: { blocks: DocumentBlock[] }) {
  return (
    <>
      {blocks.map((block, i) => {
        switch (block.type) {
          case "paragraph":
            return (
              <p key={i} className="my-2 max-w-prose text-sm text-ink-soft">
                {block.text}
              </p>
            );
          case "bullets":
            return (
              <ul key={i} className="my-2 list-disc pl-5 text-sm text-ink-soft">
                {block.items.map((item, j) => (
                  <li key={j} className="my-1 max-w-prose">
                    {item}
                  </li>
                ))}
              </ul>
            );
          case "fields":
            return (
              <dl key={i} className="my-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
                {block.pairs.map(([label, value], j) => (
                  <div key={j}>
                    <dt className="text-xs uppercase tracking-wider text-muted">
                      {label}
                    </dt>
                    <dd
                      className={`text-sm text-ink ${
                        /hash|fingerprint|head/i.test(label) ? "numeric break-all" : ""
                      }`}
                    >
                      {value}
                    </dd>
                  </div>
                ))}
              </dl>
            );
          case "table":
            return (
              <div key={i} className="my-3 overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-rule-strong text-left text-xs uppercase tracking-wider text-muted">
                      {block.headers.map((h, j) => (
                        <th key={j} className="py-2 pr-4 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, j) => (
                      <tr key={j} className="border-b border-rule align-top">
                        {row.map((cell, k) => (
                          <td key={k} className="py-2 pr-4 text-ink-soft">
                            {/* Figures carry intervals; keep the columns aligned. */}
                            <span className={/\d/.test(cell) ? "numeric" : ""}>
                              {cell}
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "callout":
            return (
              <p
                key={i}
                data-testid={block.kind === "warning" ? "limitation" : "note"}
                className={`my-3 max-w-prose border-l-2 py-1 pl-3 text-sm ${
                  block.kind === "warning"
                    ? "border-fail/60 text-ink"
                    : "border-rule-strong text-ink-soft"
                }`}
              >
                {block.text}
              </p>
            );
          case "preformatted":
            return (
              <pre
                key={i}
                className="my-3 overflow-x-auto border border-rule bg-raised p-3 text-xs numeric"
              >
                {block.text}
              </pre>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

function SectionView({ section, depth }: { section: DocumentSection; depth: number }) {
  const Heading = (depth === 0 ? "h2" : depth === 1 ? "h3" : "h4") as "h2";
  // One workpaper per page when printed: a procedure, its result and its
  // limitations must not be split across a page break.
  const isWorkpaper = /^WP-\d+/.test(section.title);
  return (
    <section
      className={depth === 0 ? "mb-10" : depth === 1 ? "mb-8 mt-6" : "mb-4 mt-4"}
      data-print={isWorkpaper ? "keep-together" : undefined}
      data-testid={isWorkpaper ? "workpaper-unit" : undefined}
    >
      <Heading
        className={
          depth === 0
            ? "border-b border-rule pb-2 text-sm font-semibold uppercase tracking-[0.12em] text-muted"
            : depth === 1
              ? "text-base font-medium text-ink"
              : "text-xs font-semibold uppercase tracking-wider text-muted"
        }
      >
        {section.title}
      </Heading>
      <div className={depth === 0 ? "mt-4" : "mt-2"}>
        <Blocks blocks={section.blocks} />
        {section.subsections.map((sub, i) => (
          <SectionView key={i} section={sub} depth={depth + 1} />
        ))}
      </div>
    </section>
  );
}

export function Workpaper({
  document: doc,
  onBack,
}: {
  document: DocumentModel;
  onBack: () => void;
}) {
  return (
    <article>
      <button
        className="mb-4 text-xs uppercase tracking-wider text-muted hover:text-ink"
        onClick={onBack}
        data-print="hide"
      >
        ← Back to run
      </button>

      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{doc.title}</h1>
        {doc.subtitle && (
          <p className="mt-1 max-w-prose text-sm text-ink-soft">{doc.subtitle}</p>
        )}
        {doc.meta.length > 0 && (
          <dl className="mt-5 grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {doc.meta.map(([label, value], i) => (
              <div key={i}>
                <dt className="text-xs uppercase tracking-wider text-muted">
                  {label}
                </dt>
                <dd className="text-sm text-ink numeric break-all">{value}</dd>
              </div>
            ))}
          </dl>
        )}
        <button
          className="mt-6 border border-rule px-3 py-1.5 text-xs uppercase tracking-wider text-ink-soft hover:border-rule-strong hover:text-ink"
          onClick={() => window.print()}
          data-print="hide"
        >
          Print
        </button>
      </header>

      {doc.sections.map((section, i) => (
        <SectionView key={i} section={section} depth={0} />
      ))}

      {doc.footer && (
        <footer className="mt-10 border-t border-rule pt-4 text-xs text-muted">
          {doc.footer}
        </footer>
      )}
    </article>
  );
}
