"""Format-only parsing of repository text files. Decides nothing about what a file means: it only reads its syntax."""
from __future__ import annotations

import configparser
import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from ..inspector import WEIGHT_EXTS, parse_requirements

try:  # Python 3.11+
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore

_TEXT_STEMS = {"license", "licence", "notice", "readme", "citation", "authors", "copying"}


@dataclass
class Parsed:
    data: Any = None
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def kind_for(name: str) -> Optional[str]:
    """Syntax family of a file from its name alone (json, markdown, text, ...), or None when it is not a text/metadata format
    (weights, binaries, source code). The result never depends on a task or architecture."""
    base = name.lower().rsplit("/", 1)[-1]
    if base.endswith(WEIGHT_EXTS):
        return None
    if base.endswith(".json"):
        return "json"
    if base.startswith("requirements") and base.endswith(".txt"):
        return "requirements"
    if base.endswith((".md", ".markdown")):
        return "markdown"
    if base.endswith((".txt", ".rst")) or ("." not in base and base in _TEXT_STEMS):
        return "text"
    if base.endswith((".yaml", ".yml")):
        return "yaml"
    if base.endswith(".toml"):
        return "toml"
    if base.endswith((".cfg", ".ini")):
        return "ini"
    if base.endswith((".jinja", ".j2")):
        return "template"
    return None


def parse_text(kind: str, text: str) -> Parsed:
    """Parse ``text`` according to its syntax family. Failure is reported in ``error``; nothing is executed or evaluated."""
    body = text.lstrip("﻿")
    if kind == "json":
        try:
            return Parsed(json.loads(body))
        except RecursionError:
            return Parsed(error="invalid JSON: nesting too deep")
        except ValueError as exc:
            return Parsed(error=f"invalid JSON: {getattr(exc, 'msg', exc)} (line {getattr(exc, 'lineno', '?')})")
    if kind == "markdown":
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                return Parsed({"front_matter": body[3:end].strip("\r\n"), "body_chars": len(body) - end - 4},
                              notes=["YAML front matter kept as text (no YAML parser dependency)."])
        return Parsed({"front_matter": None, "body_chars": len(body)})
    if kind == "requirements":
        reqs, flags = parse_requirements(body)
        return Parsed({"requirements": reqs, "flags": flags})
    if kind == "toml":
        if tomllib is None:
            return Parsed(notes=["No TOML parser on this Python; raw text kept."])
        try:
            return Parsed(tomllib.loads(body))
        except Exception as exc:  # tomllib.TOMLDecodeError and friends
            return Parsed(error=f"invalid TOML: {str(exc)[:160]}")
    if kind == "ini":
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(body)
        except configparser.Error as exc:
            return Parsed(error=f"invalid INI: {str(exc)[:160]}")
        return Parsed({s: dict(cp.items(s)) for s in cp.sections()})
    return Parsed()  # yaml / template / text: raw text only
