"""Workpaper and management-letter rendering, to Markdown and standalone HTML.

Both formats are built from one document model, so they cannot drift apart in
content. See ``report.document`` for why that beats converting one to the other.
"""

from report.document import (  # noqa: F401
    Bullets,
    Callout,
    Document,
    Fields,
    Paragraph,
    Preformatted,
    Section,
    Table,
    render_html,
    render_markdown,
)
from report.letter import (  # noqa: F401
    SEVERITY_BASIS,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    Finding,
    build_findings,
    build_letter,
    severity_for,
)
from report.workpaper import SCOPE_CAVEAT, build_workpapers, excerpt  # noqa: F401

__all__ = [
    "Bullets",
    "Callout",
    "Document",
    "Fields",
    "Finding",
    "Paragraph",
    "Preformatted",
    "SCOPE_CAVEAT",
    "SEVERITY_BASIS",
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "Section",
    "Table",
    "build_findings",
    "build_letter",
    "build_workpapers",
    "excerpt",
    "render_html",
    "render_markdown",
    "severity_for",
]
