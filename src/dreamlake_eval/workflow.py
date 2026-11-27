"""Explicit authored workflow snapshots; never fetch, expand expressions or execute."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .model import atomic_write, encoded, text

WORKFLOW_SCHEMA_VERSION = "dreamlake.eval.workflow/v1"
MAX_SOURCE_BYTES = 1024 * 1024


def _finite_tree(value, *, depth=0, budget=None):
    # Bound aliases/deep input before copying. Native JSON and YAML must agree on
    # string keys and finite values; timestamps/sets/Python objects are not JSON.
    from .model import json_value
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 100:
        raise ValueError("workflow exceeds JSON depth/node limit")
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("workflow object keys must be strings")
        return {k: _finite_tree(v, depth=depth + 1, budget=budget) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite_tree(v, depth=depth + 1, budget=budget) for v in value]
    return json_value(value)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _parse(content: str, format: str) -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ValueError("workflow source must be UTF-8 text of at most 1 MiB")
    try:
        if format == "json":
            value = json.loads(content, object_pairs_hook=_unique_pairs)
        elif format == "yaml":
            try:
                from ruamel.yaml import YAML
                from ruamel.yaml.tokens import AliasToken, DirectiveToken
            except ImportError:
                raise ValueError("install dreamlake-eval[workflow] to validate YAML-origin snapshots") from None
            parser = YAML(typ="safe", pure=True)
            parser.version = (1, 2)
            parser.allow_duplicate_keys = False
            # A snapshot is a bounded JSON tree, not an alias graph. Reject
            # directives so authored YAML cannot switch back to YAML 1.1 ('on').
            for token in parser.scan(content):
                if isinstance(token, (AliasToken, DirectiveToken)):
                    raise ValueError("workflow YAML aliases and directives are unsupported; expand them explicitly")
            value = parser.load(content)
        else:
            raise ValueError("workflow format must be json or yaml")
        value = _finite_tree(value)
    except (RecursionError, OverflowError):
        raise ValueError("workflow exceeds JSON limits") from None
    except ValueError:
        raise
    except Exception:
        # Parser diagnostics can include source snippets. Do not echo authored
        # credentials/commands in validation errors or CLI output.
        raise ValueError("invalid workflow document") from None
    if not isinstance(value, dict):
        raise ValueError("workflow definition must be an object")
    return value


def workflow_snapshot(content: str, *, format: str, provider: str, revision: str, path: str) -> dict:
    """Capture explicitly supplied text as JSON; revision is caller provenance.

    This validates serialization, not GitHub/native execution semantics or the
    existence of a commit. No environment, files, logs or secrets are auto-read.
    """
    definition = _parse(content, format)
    return {"schemaVersion": WORKFLOW_SCHEMA_VERSION, "kind": "workflow",
            "provider": text(provider), "source": {"format": format, "path": text(path),
            "revision": text(revision), "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()},
            "definition": definition, "definitionSha256": hashlib.sha256(encoded(definition)).hexdigest()}


def validate_workflow(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schemaVersion", "kind", "provider", "source", "definition", "definitionSha256"
    } or snapshot.get("schemaVersion") != WORKFLOW_SCHEMA_VERSION or snapshot.get("kind") != "workflow":
        raise ValueError("expected a dreamlake.eval.workflow/v1 JSON workflow snapshot")
    source = snapshot.get("source")
    if not isinstance(source, dict) or set(source) != {"format", "path", "revision", "content", "sha256"}:
        raise ValueError("workflow requires exact authored source provenance")
    expected = workflow_snapshot(source["content"], format=source["format"], provider=snapshot["provider"],
                                 revision=source["revision"], path=source["path"])
    # Compare encoded JSON, not Python equality (True == 1). Reject non-JSON
    # objects before comparing so a supplied parsed definition cannot bypass it.
    if encoded(_finite_tree(snapshot)) != encoded(expected):
        raise ValueError("workflow definition or digest differs from authored source")
    return expected


def read_workflow(path: str | Path) -> dict:
    """Read JSON only, independent of the filename suffix."""
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError):
        raise ValueError("recorded workflow must be valid JSON, not YAML") from None
    return validate_workflow(snapshot)


def write_workflow(path: str | Path, snapshot: dict) -> None:
    """Validate at the write boundary and persist atomic canonical JSON."""
    atomic_write(Path(path), validate_workflow(snapshot))
