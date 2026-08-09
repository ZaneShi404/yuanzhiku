"""Technology-neutral parsed-document value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSegment:
    """A text range with a parser-proven native location."""

    start: int
    end: int
    locator: dict[str, int | str]


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    parser_name: str
    config_hash: str
    format: str
    blocked_reason: str | None = None
    segments: tuple[ParsedSegment, ...] = ()
