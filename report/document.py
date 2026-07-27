"""A small document model, rendered to Markdown and to standalone HTML.

Both output formats are built from the same structure rather than one being
converted into the other. Converting Markdown to HTML would mean a Markdown
parser -- a dependency D-001 does not permit for this -- and, worse, would let
the two outputs drift apart in content. Here they cannot: a section that exists
in one exists in the other, because there is only one document.

The HTML is deliberately standalone. An audit artifact that fetches a
stylesheet from a CDN is one that renders differently, or not at all, when
opened from an evidence archive in five years.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

__all__ = [
    "Paragraph",
    "Bullets",
    "Fields",
    "Table",
    "Callout",
    "Preformatted",
    "Section",
    "Document",
    "render_markdown",
    "render_html",
]


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Bullets:
    items: Tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class Fields:
    """Label/value pairs -- the shape most of a workpaper takes."""

    pairs: Tuple[Tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pairs", tuple(tuple(p) for p in self.pairs))


@dataclass(frozen=True)
class Table:
    headers: Tuple[str, ...]
    rows: Tuple[Tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", tuple(self.headers))
        object.__setattr__(self, "rows", tuple(tuple(r) for r in self.rows))


@dataclass(frozen=True)
class Callout:
    """Something the reader must not skim past."""

    text: str
    kind: str = "note"  # note | warning


@dataclass(frozen=True)
class Preformatted:
    text: str


Block = Union[Paragraph, Bullets, Fields, Table, Callout, Preformatted]


@dataclass(frozen=True)
class Section:
    title: str
    blocks: Tuple[Block, ...] = ()
    level: int = 2
    subsections: Tuple["Section", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", tuple(self.blocks))
        object.__setattr__(self, "subsections", tuple(self.subsections))


@dataclass(frozen=True)
class Document:
    title: str
    sections: Tuple[Section, ...] = ()
    subtitle: str = ""
    #: Rendered under the title -- who generated this, when, and against what.
    meta: Tuple[Tuple[str, str], ...] = ()
    footer: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", tuple(self.sections))
        object.__setattr__(self, "meta", tuple(tuple(m) for m in self.meta))


# --- Markdown ----------------------------------------------------------------


def _md_escape_cell(text: str) -> str:
    """Pipes would break a Markdown table row."""
    return text.replace("|", "\\|").replace("\n", " ")


def _render_block_markdown(block: Block) -> List[str]:
    if isinstance(block, Paragraph):
        return [block.text, ""]
    if isinstance(block, Bullets):
        return [f"- {item}" for item in block.items] + [""]
    if isinstance(block, Fields):
        return [f"**{label}:** {value}" for label, value in block.pairs] + [""]
    if isinstance(block, Table):
        head = "| " + " | ".join(_md_escape_cell(h) for h in block.headers) + " |"
        rule = "| " + " | ".join("---" for _ in block.headers) + " |"
        body = [
            "| " + " | ".join(_md_escape_cell(c) for c in row) + " |"
            for row in block.rows
        ]
        return [head, rule, *body, ""]
    if isinstance(block, Callout):
        marker = "**Warning.**" if block.kind == "warning" else "**Note.**"
        return [f"> {marker} {block.text}", ""]
    if isinstance(block, Preformatted):
        return ["```", block.text, "```", ""]
    raise TypeError(f"unknown block type {type(block).__name__}")


def _render_section_markdown(section: Section) -> List[str]:
    lines = [f"{'#' * section.level} {section.title}", ""]
    for block in section.blocks:
        lines.extend(_render_block_markdown(block))
    for subsection in section.subsections:
        lines.extend(_render_section_markdown(subsection))
    return lines


def render_markdown(document: Document) -> str:
    lines = [f"# {document.title}", ""]
    if document.subtitle:
        lines.extend([f"*{document.subtitle}*", ""])
    if document.meta:
        lines.extend(f"**{label}:** {value}  " for label, value in document.meta)
        lines.append("")
    for section in document.sections:
        lines.extend(_render_section_markdown(section))
    if document.footer:
        lines.extend(["---", "", document.footer, ""])
    return "\n".join(lines).rstrip() + "\n"


# --- HTML --------------------------------------------------------------------

_CSS = """
:root { color-scheme: light; }
body {
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
  color: #1a1a1a; background: #fff; margin: 0 auto; padding: 2.5rem 1.5rem 4rem;
  max-width: 52rem;
}
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.2rem; margin: 2rem 0 .5rem; padding-bottom: .25rem;
     border-bottom: 1px solid #e2e2e2; }
h3 { font-size: 1.02rem; margin: 1.4rem 0 .4rem; }
h4 { font-size: .95rem; margin: 1rem 0 .3rem; color: #333; }
p { margin: .5rem 0; }
ul { margin: .5rem 0; padding-left: 1.3rem; }
li { margin: .2rem 0; }
.subtitle { color: #555; font-style: italic; margin: 0 0 1rem; }
.meta { color: #555; font-size: .87rem; margin: 0 0 1.5rem; }
.meta div { margin: .1rem 0; }
.field { margin: .35rem 0; }
.field .label { font-weight: 600; }
table { border-collapse: collapse; width: 100%; margin: .75rem 0;
        font-size: .9rem; }
th, td { border: 1px solid #dcdcdc; padding: .4rem .55rem; text-align: left;
         vertical-align: top; }
th { background: #f5f5f5; font-weight: 600; }
.callout { border-left: 3px solid #b8b8b8; background: #f7f7f7;
           padding: .6rem .85rem; margin: .8rem 0; }
.callout.warning { border-left-color: #b3541e; background: #fdf5f0; }
pre { background: #f5f5f5; padding: .7rem .85rem; overflow-x: auto;
      font-size: .85rem; border: 1px solid #e6e6e6; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas,
            monospace; }
footer { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #e2e2e2;
         color: #555; font-size: .87rem; }
""".strip()


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


def _render_block_html(block: Block) -> List[str]:
    if isinstance(block, Paragraph):
        return [f"<p>{_e(block.text)}</p>"]
    if isinstance(block, Bullets):
        items = "".join(f"<li>{_e(i)}</li>" for i in block.items)
        return [f"<ul>{items}</ul>"]
    if isinstance(block, Fields):
        return [
            f'<div class="field"><span class="label">{_e(label)}:</span> '
            f"{_e(value)}</div>"
            for label, value in block.pairs
        ]
    if isinstance(block, Table):
        head = "".join(f"<th>{_e(h)}</th>" for h in block.headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>"
            for row in block.rows
        )
        return [f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"]
    if isinstance(block, Callout):
        label = "Warning." if block.kind == "warning" else "Note."
        return [
            f'<div class="callout {_e(block.kind)}">'
            f"<strong>{label}</strong> {_e(block.text)}</div>"
        ]
    if isinstance(block, Preformatted):
        return [f"<pre>{_e(block.text)}</pre>"]
    raise TypeError(f"unknown block type {type(block).__name__}")


def _render_section_html(section: Section) -> List[str]:
    level = min(max(section.level, 2), 6)
    out = [f"<h{level}>{_e(section.title)}</h{level}>"]
    for block in section.blocks:
        out.extend(_render_block_html(block))
    for subsection in section.subsections:
        out.extend(_render_section_html(subsection))
    return out


def render_html(document: Document) -> str:
    """Render a complete, self-contained HTML page. No external requests."""
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(document.title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        f"<h1>{_e(document.title)}</h1>",
    ]
    if document.subtitle:
        parts.append(f'<p class="subtitle">{_e(document.subtitle)}</p>')
    if document.meta:
        parts.append('<div class="meta">')
        parts.extend(
            f"<div><strong>{_e(label)}:</strong> {_e(value)}</div>"
            for label, value in document.meta
        )
        parts.append("</div>")
    for section in document.sections:
        parts.extend(_render_section_html(section))
    if document.footer:
        parts.append(f"<footer>{_e(document.footer)}</footer>")
    parts.extend(["</body>", "</html>", ""])
    return "\n".join(parts)
