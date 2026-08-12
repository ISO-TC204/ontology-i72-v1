#!/usr/bin/env python3
"""VERSION / RELEASES helpers for ontology repositories.

VERSION file format (repo root):
  version: 1.2.3
  doc-only: 2026-07-27   # optional

RELEASES file format (repo root), one record per line:
  <semver> <YYYY-MM-DD> <ontology|doc-only>

Ontology TTL stamping (docs/**/*.ttl):
  Only files that already declare owl:versionInfo are updated.
  dcterms:modified / owl:versionIRI / owl:priorVersion are updated only when present
  (priorVersion may also be inserted after versionIRI when a prior release exists).
  Ontology namespace for versionIRI is derived per file from vann:preferredNamespaceUri,
  falling back to the owl:Ontology IRI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Iterable, Optional, Tuple

def _repo_root() -> Path:
    """Ontology repository root (VERSION, RELEASES, docs/).

    In GitHub Actions the script is checked out under
    ``.ontology-shared-scripts/scripts/``; ontology files live at
    ``GITHUB_WORKSPACE``. Locally the script usually lives at ``<repo>/scripts/``.
    """
    ws = os.environ.get("GITHUB_WORKSPACE")
    if ws:
        return Path(ws)
    return Path(__file__).resolve().parents[1]


ROOT = _repo_root()
VERSION_PATH = ROOT / "VERSION"
RELEASES_PATH = ROOT / "RELEASES"
DOCS_DIR = ROOT / "docs"

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))"
    r"?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _ident_key(part: str) -> Tuple:
    """SemVer pre-release identifier comparison key."""
    if part.isdigit():
        return (0, int(part))
    return (1, part)


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()
    build: str = ""

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        text = value.strip()
        if text.startswith(("v", "V")) and SEMVER_RE.match(text[1:]):
            text = text[1:]
        m = SEMVER_RE.match(text)
        if not m:
            raise ValueError(f"Invalid SemVer: {value!r}")
        pre = tuple(m.group(4).split(".")) if m.group(4) else ()
        build = m.group(5) or ""
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)

    @property
    def text(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += "-" + ".".join(self.prerelease)
        if self.build:
            base += "+" + self.build
        return base

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (other.major, other.minor, other.patch, other.prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        # Pre-release has lower precedence than release
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and other.prerelease:
            return False
        for a, b in zip(self.prerelease, other.prerelease):
            if a == b:
                continue
            return _ident_key(a) < _ident_key(b)
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class VersionFile:
    version: str
    doc_only: Optional[dt.date] = None

    @property
    def is_doc_only(self) -> bool:
        return self.doc_only is not None

    @property
    def semver(self) -> SemVer:
        return SemVer.parse(self.version)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.semver.prerelease)

    @property
    def release_type(self) -> str:
        return "doc-only" if self.is_doc_only else "ontology"


@dataclass(frozen=True)
class ReleaseRecord:
    version: str
    date: dt.date
    kind: str  # ontology | doc-only


def normalize_semver(value: str) -> str:
    return SemVer.parse(value).text


def discover_ttl_files(docs_dir: Path = DOCS_DIR) -> list[Path]:
    """All Turtle files under docs/ (recursive)."""
    if not docs_dir.is_dir():
        return []
    return sorted(p for p in docs_dir.rglob("*.ttl") if p.is_file())


def _has_ttl_field(text: str, field: str) -> bool:
    return bool(re.search(rf"^\s*{re.escape(field)}\s+", text, flags=re.MULTILINE))


def _normalize_ontology_ns(ns: str) -> str:
    """Ensure a path-style namespace base suitable for versionIRI concatenation."""
    ns = ns.strip()
    if ns.endswith("#"):
        ns = ns[:-1]
    if not ns.endswith("/"):
        ns += "/"
    return ns


def ontology_ns_from_ttl(text: str) -> str:
    """Derive ontology namespace from vann:preferredNamespaceUri, owl:Ontology IRI, BASE, or default PREFIX."""
    m = re.search(
        r'^\s*vann:preferredNamespaceUri\s+(?:"([^"]+)"|<([^>]+)>)',
        text,
        flags=re.MULTILINE,
    )
    if m:
        return _normalize_ontology_ns(m.group(1) or m.group(2))

    m = re.search(
        r"<(https?://[^>\s]+)>\s+(?:a|rdf:type)\s+owl:Ontology\b",
        text,
    )
    if m:
        return _normalize_ontology_ns(m.group(1))

    # Common RITSO style: BASE / default PREFIX declare the topic-area namespace.
    m = re.search(
        r"^(?:BASE|@base)\s+<(https?://[^>\s]+)>\s*\.?\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if m:
        return _normalize_ontology_ns(m.group(1))

    m = re.search(
        r"^(?:PREFIX|@prefix)\s+:\s+<(https?://[^>\s]+)>\s*\.?\s*$",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    if m:
        return _normalize_ontology_ns(m.group(1))

    raise ValueError(
        "Could not derive ontology namespace from vann:preferredNamespaceUri, "
        "owl:Ontology IRI, BASE, or PREFIX :"
    )


def version_iri(version: str, ontology_ns: str) -> str:
    return f"{_normalize_ontology_ns(ontology_ns)}{normalize_semver(version)}"


def parse_version_file(path: Path = VERSION_PATH) -> VersionFile:
    if not path.is_file():
        raise ValueError(f"VERSION file not found: {path}")
    version: Optional[str] = None
    doc_only: Optional[dt.date] = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid VERSION line (expected key: value): {raw!r}")
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key == "version":
            version = normalize_semver(val)
        elif key == "doc-only":
            if not DATE_RE.match(val):
                raise ValueError(f"Invalid doc-only date (YYYY-MM-DD): {val!r}")
            doc_only = dt.date.fromisoformat(val)
        else:
            raise ValueError(f"Unknown VERSION key: {key!r}")
    if not version:
        raise ValueError("VERSION must contain a 'version:' line")
    return VersionFile(version=version, doc_only=doc_only)


def parse_releases(path: Path = RELEASES_PATH) -> list[ReleaseRecord]:
    if not path.is_file():
        return []
    records: list[ReleaseRecord] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(
                "Invalid RELEASES line "
                "(expected '<semver> <YYYY-MM-DD> <ontology|doc-only>'): "
                f"{raw!r}"
            )
        version, date_s, kind = parts
        version = normalize_semver(version)
        if not DATE_RE.match(date_s):
            raise ValueError(f"Invalid RELEASES date: {date_s!r}")
        if kind not in ("ontology", "doc-only"):
            raise ValueError(f"Invalid RELEASES type: {kind!r}")
        records.append(
            ReleaseRecord(
                version=version,
                date=dt.date.fromisoformat(date_s),
                kind=kind,
            )
        )
    return records


def latest_semver(records: Iterable[ReleaseRecord]) -> Optional[str]:
    best: Optional[SemVer] = None
    best_text: Optional[str] = None
    for rec in records:
        cur = SemVer.parse(rec.version)
        if best is None or cur > best:
            best = cur
            best_text = normalize_semver(rec.version)
    return best_text


def latest_full_ontology_version(records: Iterable[ReleaseRecord]) -> Optional[str]:
    """Latest ontology release with no pre-release label."""
    best: Optional[SemVer] = None
    best_text: Optional[str] = None
    for rec in records:
        if rec.kind != "ontology":
            continue
        ver = SemVer.parse(rec.version)
        if ver.prerelease:
            continue
        if best is None or ver > best:
            best = ver
            best_text = normalize_semver(rec.version)
    return best_text


def latest_ontology_version(records: Iterable[ReleaseRecord]) -> Optional[str]:
    """Latest ontology SemVer in RELEASES (includes pre-releases)."""
    best: Optional[SemVer] = None
    best_text: Optional[str] = None
    for rec in records:
        if rec.kind != "ontology":
            continue
        ver = SemVer.parse(rec.version)
        if best is None or ver > best:
            best = ver
            best_text = normalize_semver(rec.version)
    return best_text


def prior_version_for(records: list[ReleaseRecord]) -> Optional[str]:
    """Prefer last full ontology release; else last ontology release (pre-release OK)."""
    full = latest_full_ontology_version(records)
    if full:
        return full
    return latest_ontology_version(records)


def max_release_date(records: Iterable[ReleaseRecord]) -> Optional[dt.date]:
    dates = [r.date for r in records]
    return max(dates) if dates else None


def suggest_next_semver(latest: Optional[str], *, prerelease: bool = True) -> str:
    """Suggest the next SemVer after latest (default: bump pre-release or patch-pre)."""
    if not latest:
        return "0.0.1-alpha.1" if prerelease else "0.0.1"
    cur = SemVer.parse(latest)
    if prerelease:
        if cur.prerelease:
            # bump last numeric id if present, else append .1
            pre = list(cur.prerelease)
            if pre and pre[-1].isdigit():
                pre[-1] = str(int(pre[-1]) + 1)
            else:
                pre.append("1")
            return SemVer(cur.major, cur.minor, cur.patch, tuple(pre)).text
        # turn 1.2.3 into 1.2.4-alpha.1
        return SemVer(cur.major, cur.minor, cur.patch + 1, ("alpha", "1")).text
    if cur.prerelease:
        # next full release of same X.Y.Z
        return SemVer(cur.major, cur.minor, cur.patch).text
    return SemVer(cur.major, cur.minor, cur.patch + 1).text


def validate(
    version_path: Path = VERSION_PATH, releases_path: Path = RELEASES_PATH
) -> VersionFile:
    vf = parse_version_file(version_path)
    records = parse_releases(releases_path)
    latest = latest_semver(records)
    max_date = max_release_date(records)

    if vf.is_doc_only:
        assert vf.doc_only is not None
        if latest is None:
            raise ValueError(
                "doc-only releases require a prior ontology SemVer in RELEASES"
            )
        if normalize_semver(vf.version) != normalize_semver(latest):
            raise ValueError(
                f"doc-only PR must keep version: {latest} (VERSION has {vf.version}). "
                f"Do not bump SemVer for documentation-only changes."
            )
        if max_date is not None and vf.doc_only < max_date:
            raise ValueError(
                f"doc-only date {vf.doc_only.isoformat()} must be >= latest "
                f"RELEASES date {max_date.isoformat()}"
            )
        print(
            f"OK: doc-only release for ontology {vf.version} "
            f"on {vf.doc_only.isoformat()}"
        )
        return vf

    if latest is not None and not (vf.semver > SemVer.parse(latest)):
        suggested = suggest_next_semver(latest, prerelease=True)
        raise ValueError(
            f"VERSION {vf.version} must be > latest release {latest} "
            f"(from the PR base RELEASES). Suggested next line:\n"
            f"  version: {suggested}"
        )
    kind = "pre-release" if vf.is_prerelease else "full release"
    print(f"OK: ontology {kind} {vf.version} (latest on base was {latest or 'none'})")
    return vf


def _field_terminator_after(text: str, field: str) -> str:
    """Return '.' or ';' currently used after the given owl/dcterms field."""
    m = re.search(
        rf"^\s*{re.escape(field)}\s+\S+.*?([;.])\s*$",
        text,
        flags=re.MULTILINE,
    )
    return m.group(1) if m else ";"


def _replace_ttl_metadata(
    text: str,
    *,
    modified: str,
    version: str,
    prior: Optional[str],
    ontology_ns: str,
) -> str:
    """Update version metadata fields that are already present in the TTL.

    owl:versionInfo is required (caller must ensure it exists). dcterms:modified and
    owl:versionIRI are updated only when already declared. owl:priorVersion is updated
    when present, or inserted after owl:versionIRI when a prior release exists.
    """
    if not _has_ttl_field(text, "owl:versionInfo"):
        raise ValueError("Expected owl:versionInfo in TTL")

    ver = normalize_semver(version)
    iri = version_iri(ver, ontology_ns)
    has_modified = _has_ttl_field(text, "dcterms:modified")
    has_version_iri = _has_ttl_field(text, "owl:versionIRI")
    has_prior = _has_ttl_field(text, "owl:priorVersion")

    def sub_field(field: str, new_value: str, body: str, term: Optional[str] = None) -> str:
        terminator = term if term is not None else _field_terminator_after(body, field)
        pattern = rf"^(\s*{re.escape(field)}\s+)\S+.*$"
        replacement = rf"\1{new_value} {terminator}"
        new_body, n = re.subn(pattern, replacement, body, count=1, flags=re.MULTILINE)
        if n != 1:
            raise ValueError(f"Expected one match for field {field}")
        return new_body

    if has_modified:
        text = sub_field("dcterms:modified", f'"{modified}"^^xsd:date', text)
    text = sub_field("owl:versionInfo", f'"{ver}"', text)
    if has_version_iri:
        text = sub_field("owl:versionIRI", f"<{iri}>", text)

    if has_prior:
        if prior:
            text = sub_field(
                "owl:priorVersion", f"<{version_iri(prior, ontology_ns)}>", text
            )
        else:
            text = re.sub(
                r"^\s*owl:priorVersion\s+\S+.*\n",
                "",
                text,
                count=1,
                flags=re.MULTILINE,
            )
    elif prior and has_version_iri:
        # Insert priorVersion after versionIRI, using the terminator versionIRI had
        # (versionIRI becomes ';' and priorVersion takes the old terminator).
        term = _field_terminator_after(text, "owl:versionIRI")
        text = re.sub(
            r"^(\s*owl:versionIRI\s+<[^>]+>)\s*[;.]\s*$",
            rf"\1 ;\n    owl:priorVersion               "
            rf"<{version_iri(prior, ontology_ns)}> {term}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    return text


def stamp_ttl(
    vf: VersionFile,
    records: list[ReleaseRecord],
    today: Optional[dt.date] = None,
) -> list[Path]:
    """Update ontology TTL metadata for an ontology release. No-op for doc-only.

    Scans docs/**/*.ttl and stamps only files that already declare owl:versionInfo.
    """
    if vf.is_doc_only:
        return []
    today = today or dt.date.today()
    # Exclude the version we are stamping if it somehow already appears
    prior_records = [
        r
        for r in records
        if not (
            r.kind == "ontology"
            and normalize_semver(r.version) == normalize_semver(vf.version)
        )
    ]
    prior = prior_version_for(prior_records)
    modified = today.isoformat()
    changed: list[Path] = []
    for path in discover_ttl_files():
        original = path.read_text(encoding="utf-8")
        if not _has_ttl_field(original, "owl:versionInfo"):
            try:
                label = path.relative_to(ROOT)
            except ValueError:
                label = path
            print(f"Skipping {label} (no owl:versionInfo)")
            continue
        # Namespace is only needed when building/updating version IRIs.
        needs_ns = _has_ttl_field(original, "owl:versionIRI") or _has_ttl_field(
            original, "owl:priorVersion"
        )
        ontology_ns = ""
        if needs_ns:
            try:
                ontology_ns = ontology_ns_from_ttl(original)
            except ValueError as e:
                try:
                    label = path.relative_to(ROOT)
                except ValueError:
                    label = path
                raise ValueError(f"{label}: {e}") from e
        updated = _replace_ttl_metadata(
            original,
            modified=modified,
            version=vf.version,
            prior=prior,
            ontology_ns=ontology_ns,
        )
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(path)
            try:
                label = path.relative_to(ROOT)
            except ValueError:
                label = path
            print(f"Stamped {label}")
    return changed


def append_release(
    vf: VersionFile,
    releases_path: Path = RELEASES_PATH,
    release_date: Optional[dt.date] = None,
) -> ReleaseRecord:
    release_date = release_date or (vf.doc_only if vf.is_doc_only else dt.date.today())
    assert release_date is not None
    rec = ReleaseRecord(
        version=normalize_semver(vf.version),
        date=release_date,
        kind=vf.release_type,
    )
    line = f"{rec.version} {rec.date.isoformat()} {rec.kind}\n"
    if releases_path.is_file():
        existing = releases_path.read_text(encoding="utf-8")
    else:
        existing = (
            "# version date type\n"
            "# type is 'ontology' or 'doc-only'\n"
        )
    if not existing.endswith("\n"):
        existing += "\n"
    releases_path.write_text(existing + line, encoding="utf-8")
    print(f"Appended to RELEASES: {line.strip()}")
    return rec


def already_recorded(vf: VersionFile, records: list[ReleaseRecord]) -> bool:
    date = vf.doc_only if vf.is_doc_only else None
    for rec in reversed(records):
        if rec.version != normalize_semver(vf.version):
            continue
        if rec.kind != vf.release_type:
            continue
        if vf.is_doc_only:
            if date and rec.date == date:
                return True
        else:
            return True
    return False


def choose_doc_tag(
    version: str, doc_date: dt.date, existing_tags: Iterable[str]
) -> str:
    """vX.Y.Z-docs-YYYY-MM-DD (Mike-friendly), with UTC time if that tag exists."""
    tags = set(existing_tags)
    ver = ontology_tag(version)
    base = f"{ver}-docs-{doc_date.isoformat()}"
    if base not in tags:
        return base
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{ver}-docs-{stamp}"


def ontology_tag(version: str) -> str:
    return f"v{normalize_semver(version)}"


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        releases = Path(args.releases) if getattr(args, "releases", "") else RELEASES_PATH
        validate(version_path=VERSION_PATH, releases_path=releases)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    try:
        releases = Path(args.releases) if args.releases else RELEASES_PATH
        records = parse_releases(releases)
        latest = latest_semver(records)
        print(suggest_next_semver(latest, prerelease=not args.full))
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _tag_for(vf: VersionFile, existing_tags: Iterable[str]) -> str:
    if vf.is_doc_only:
        assert vf.doc_only is not None
        return choose_doc_tag(vf.version, vf.doc_only, existing_tags)
    return ontology_tag(vf.version)


def cmd_apply(args: argparse.Namespace) -> int:
    """Stamp TTL (ontology only), append RELEASES if not already recorded.

    Always emits the git tag for the current VERSION so the workflow can create
    a GitHub Release even when metadata was already written (retry / race).
    """
    try:
        vf = parse_version_file()
        records = parse_releases()
        existing = [
            t.strip() for t in (args.existing_tags or "").split(",") if t.strip()
        ]
        tag = _tag_for(vf, existing)
        prerelease = (not vf.is_doc_only) and vf.is_prerelease

        if already_recorded(vf, records):
            print(
                "RELEASES already has this version; ensuring GitHub Release can be published."
            )
            _emit_outputs(
                vf, tag=tag, prerelease=prerelease, changed=False, publish=True
            )
            return 0

        # Only enforce bump/date rules when this VERSION is not yet recorded
        validate()

        if not vf.is_doc_only:
            stamp_ttl(vf, records)

        release_date = vf.doc_only if vf.is_doc_only else dt.date.today()
        append_release(vf, release_date=release_date)

        _emit_outputs(vf, tag=tag, prerelease=prerelease, changed=True, publish=True)
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _emit_outputs(
    vf: VersionFile,
    *,
    tag: str,
    prerelease: bool,
    changed: bool,
    publish: bool = True,
) -> None:
    lines = [
        f"kind={vf.release_type}",
        f"version={vf.version}",
        f"tag={tag}",
        f"prerelease={'true' if prerelease else 'false'}",
        f"changed={'true' if changed else 'false'}",
        f"publish={'true' if publish else 'false'}",
    ]
    for line in lines:
        print(line)
    gh_out = Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None
    if gh_out:
        with gh_out.open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")


def cmd_print_tag(args: argparse.Namespace) -> int:
    vf = parse_version_file()
    if vf.is_doc_only:
        assert vf.doc_only is not None
        existing = [
            t.strip() for t in (args.existing_tags or "").split(",") if t.strip()
        ]
        print(choose_doc_tag(vf.version, vf.doc_only, existing))
    else:
        print(ontology_tag(vf.version))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser(
        "validate",
        help="Validate VERSION against RELEASES (use --releases for the PR base copy)",
    )
    p_val.add_argument(
        "--releases",
        default="",
        help="Path to RELEASES to compare against (default: ./RELEASES)",
    )
    p_val.set_defaults(func=cmd_validate)

    p_sug = sub.add_parser(
        "suggest",
        help="Print a suggested next SemVer based on RELEASES",
    )
    p_sug.add_argument("--releases", default="")
    p_sug.add_argument(
        "--full",
        action="store_true",
        help="Suggest a full release instead of a pre-release",
    )
    p_sug.set_defaults(func=cmd_suggest)

    p_apply = sub.add_parser(
        "apply",
        help="Stamp TTL (ontology) and append RELEASES; print release outputs",
    )
    p_apply.add_argument(
        "--existing-tags",
        default="",
        help="Comma-separated existing git tags (for v*-docs-* tag collision)",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_tag = sub.add_parser("print-tag", help="Print the git tag for current VERSION")
    p_tag.add_argument("--existing-tags", default="")
    p_tag.set_defaults(func=cmd_print_tag)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
