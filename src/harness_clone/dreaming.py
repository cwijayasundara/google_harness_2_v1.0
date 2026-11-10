"""Offline consolidation of the experience library ("dreaming").

In-session reflection captures salience while it happens; this module is the
offline pass that later reviews the run store's own records with hindsight.
It mirrors the division of labor Anthropic describes for agent memory
("The components of agent memory", Nov 2026) and the Managed Agents
dreaming cookbook:

- The store's records are the ground truth. Execution traces are the
  transcript analog: every lesson's evidence references are re-resolved
  against them, and only lessons that still check out are restated as
  verified (with the verification date recorded — "how it is known").
- Duplicate lessons are merged, conflicting lessons are marked CONFLICT
  with both sides and dates rather than guessed away, and superseded
  entries stay in the store as audit stubs while snapshots stop serving
  them.
- The consolidated library is republished as a small linked memory wiki
  (one file per domain plus an AGENTS.md index) and offered to the next
  harness bundle as a memory-only patch. The index file is the small
  injected top layer of the memory hierarchy; the patch flows through the
  normal bundle validation path, so the dream never touches anything
  outside the editable surface.

The deterministic dream makes no model calls and spends nothing.
"""

import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .bundles import apply_patch, bundle_digest
from .contracts import (
    DreamAudit,
    DreamFinding,
    DreamReport,
    ExperienceEntry,
    ExperimentConfig,
    HarnessConfig,
    HarnessPatch,
)
from .experience import ExperienceLibrary, entry_key
from .storage import RunStore

# Cheap credential-shape scan. A lesson that carries a secret-shaped string
# is dropped from the wiki; the store copy is untouched so the finding stays
# auditable.
_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?i:api[_-]?key\s*[:=]\s*[^;\s]{8,})"
)
_SLUG = re.compile(r"[^a-z0-9-]+")
_INDEX_START = "<!-- dream:index:start -->"
_INDEX_END = "<!-- dream:index:end -->"
# Frontmatter + headers overhead when budgeting a wiki page's size.
_FRONTMATTER_SLACK = 600


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def load_entries(store: RunStore) -> list[ExperienceEntry]:
    with store.transaction() as db:
        rows = db.execute("SELECT value FROM records WHERE kind='experience'").fetchall()
    return [ExperienceEntry.model_validate_json(row[0]) for row in rows]


def refs_resolve(store: RunStore, entry: ExperienceEntry, cache: dict) -> bool:
    """Re-resolve every evidence reference against stored executions.

    References have the ``<execution_id>:trace:<row id>`` or
    ``<execution_id>:artifact:<path>`` shape ``citeable_refs`` mints; the
    execution record named in the reference must exist in this store and
    actually contain the cited row or artifact.
    """
    for ref in entry.evidence_refs:
        parts = ref.rsplit(":", 2)
        if len(parts) != 3:
            return False
        head, kind, tail = parts
        record = cache.get(head)
        if record is None:
            value = store.get("execution", head)
            record = cache[head] = (
                {("trace", row.get("id")) for row in value.get("trace", [])}
                | {("artifact", rel) for rel in value.get("artifacts", {})}
                if value
                else False
            )
        if record is False or (kind, tail) not in record:
            return False
    return bool(entry.evidence_refs)


def audit_library(
    store: RunStore, entries: list[ExperienceEntry], adopted_digest: str | None
) -> DreamAudit:
    cache: dict = {}
    groups: dict[tuple, list[ExperienceEntry]] = {}
    findings = []
    superseded: set[str] = set()
    for entry in entries:
        superseded.update(entry.supersedes)
    for entry in entries:
        if entry_key(entry) in superseded:
            findings.append(
                DreamFinding(key=entry_key(entry), status="superseded", detail="replaced by a dream restatement")
            )
            continue
        status = "intact"
        detail = ""
        if _SECRET.search(entry.cue) or _SECRET.search(entry.lesson):
            status, detail = "secret", "credential-shaped text; excluded from the wiki"
        elif not refs_resolve(store, entry, cache):
            status, detail = "broken-ref", "evidence references do not resolve in this store"
        elif entry.verified_on is None:
            status = "verified"
        if (
            adopted_digest
            and entry.bundle_digest != adopted_digest
            and status in {"intact", "verified"}
        ):
            detail = (detail + "; " if detail else "") + "bundle differs from the adopted bundle"
            status = "stale" if status == "intact" else status
        findings.append(DreamFinding(key=entry_key(entry), status=status, detail=detail))
        groups.setdefault(
            (entry.namespace, entry.domain, normalize(entry.cue)), []
        ).append(entry)
    for members in groups.values():
        lessons = {normalize(m.lesson) for m in members}
        for member in members:
            if len(members) > 1 and len(lessons) == 1:
                findings.append(
                    DreamFinding(
                        key=entry_key(member),
                        status="duplicate",
                        detail=f"{len(members)} entries share this cue and lesson",
                    )
                )
            elif len(lessons) > 1 and len(members) > 1:
                findings.append(
                    DreamFinding(
                        key=entry_key(member),
                        status="conflict",
                        detail=f"{len(members)} entries share this cue with different lessons",
                    )
                )
    return DreamAudit(
        entries=len(entries), adopted_digest=adopted_digest, findings=findings
    )


def _next_revision(entries: list[ExperienceEntry], namespace: str, domain: str) -> int:
    return max(
        (
            e.revision
            for e in entries
            if e.namespace == namespace and e.domain == domain
        ),
        default=0,
    ) + 1


def _conflict_lesson(members: list[ExperienceEntry]) -> str:
    sides = "; ".join(
        f'"{m.lesson}" ({m.task_id}, bundle {m.bundle_digest[:12]}, '
        f'{m.verified_on or "unverified"})'
        for m in members
    )
    return (
        f"CONFLICT: {sides}. Unresolved; verify against task sources "
        "before relying on either side."
    )


def consolidate(
    store: RunStore, entries: list[ExperienceEntry], audit: DreamAudit
) -> list[ExperienceEntry]:
    """Append-only corrections: verified restatements, merges, conflict notes.

    Superseded originals stay in the store; snapshots stop serving them.
    Dream-origin entries are already consolidated and are never restated
    again, so repeated dreams converge instead of looping.
    """
    status = {f.key: f.status for f in audit.findings}
    superseded = set()
    for entry in entries:
        superseded.update(entry.supersedes)
    dream: list[ExperienceEntry] = []
    today = datetime.now(UTC).date().isoformat()
    lib = ExperienceLibrary(store)
    groups: dict[tuple, list[ExperienceEntry]] = {}
    for entry in entries:
        if entry_key(entry) in superseded or entry.origin == "dream":
            continue
        groups.setdefault(
            (entry.namespace, entry.domain, normalize(entry.cue)), []
        ).append(entry)
    for (namespace, domain, _cue), members in sorted(groups.items()):
        members.sort(key=lambda e: (e.ordinal, e.task_id, e.revision))
        if len(members) == 1 and status.get(entry_key(members[0])) != "verified":
            # Intact, stale, broken-ref, and secret entries are left as they
            # are: a claim without supporting evidence here may be supported
            # elsewhere, and the wiki already carries the staleness note.
            continue
        revision = _next_revision(entries, namespace, domain)
        refs = sorted({ref for m in members for ref in m.evidence_refs})
        # Group the members by distinct lesson, preserving evidence order.
        variants: dict[str, list[ExperienceEntry]] = {}
        for m in members:
            variants.setdefault(normalize(m.lesson), []).append(m)
        if len(variants) == 1:
            # Duplicate (or lone verified entry): keep the richest member,
            # supersede the rest.
            ranked = sorted(
                members,
                key=lambda m: (m.verified_on is not None, len(m.evidence_refs)),
                reverse=True,
            )
            source, lesson, verified = ranked[0], ranked[0].lesson, None
            if refs_resolve(store, source, {}):
                verified = today
        elif len(variants) == 2 and len(variants[normalize(members[-1].lesson)]) == 1:
            # Later correction: the newest member states a different lesson
            # and its evidence checks out; keep its value and note what it
            # replaced, with both sources. Never guessed: a correction that
            # does not verify here falls through to an explicit CONFLICT.
            source = members[-1]
            replaced = variants[[k for k in variants if k != normalize(source.lesson)][0]][0]
            verified = today if refs_resolve(store, source, {}) else None
            if verified is None:
                lesson = _conflict_lesson(members)
            else:
                lesson = (
                    f"{source.lesson} (replaces \"{replaced.lesson}\" from "
                    f"{replaced.task_id}, {replaced.verified_on or 'unverified'}, "
                    f"bundle {replaced.bundle_digest[:12]})"
                )
        else:
            source, lesson, verified = members[0], _conflict_lesson(members), None
        if verified:
            # A semantic "supported" verdict earns true verification;
            # a structural-only restatement says so honestly.
            verdict = store.get("semantic-verdict", entry_key(source))
            confidence = (
                "verified"
                if isinstance(verdict, dict) and verdict.get("verdict") == "supported"
                else "structural"
            )
        else:
            confidence = source.confidence
        entry = ExperienceEntry(
            namespace=namespace,
            domain=domain,
            task_id=source.task_id,
            ordinal=min(m.ordinal for m in members),
            bundle_digest=source.bundle_digest,
            cue=source.cue,
            lesson=lesson,
            evidence_refs=refs,
            confidence=confidence,
            origin="dream",
            revision=revision,
            verified_on=verified,
            supersedes=sorted(entry_key(m) for m in members),
        )
        try:
            lib.append(entry)
        except ValueError:
            continue  # already published by an earlier dream with the same content
        entries.append(entry)
        dream.append(entry)
    return dream


def _provenance(entry: ExperienceEntry, adopted_digest: str | None) -> str:
    parts = [f"cue: {entry.cue}"]
    parts.append(f"verified {entry.verified_on}" if entry.verified_on else "unverified")
    parts.append(f"task {entry.task_id}")
    parts.append(f"bundle {entry.bundle_digest[:12]}")
    if adopted_digest and entry.bundle_digest != adopted_digest:
        parts.append(f"stale: adopted bundle is {adopted_digest[:12]}")
    return f" *({'; '.join(parts)})*"


def _standing_text(parent_root: Path) -> str:
    """Hand-written memory survives the dream: only the marked index block
    between the dream markers is regenerated."""
    path = parent_root / "memory" / "AGENTS.md"
    if not path.is_file():
        return ""
    body = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"{re.escape(_INDEX_START)}.*?{re.escape(_INDEX_END)}", re.DOTALL)
    return pattern.sub("", body).strip()


def _chunk(domains: list[str], limit: int) -> list[list[str]]:
    if limit < 1:
        limit = 1
    per_file = -(-len(domains) // limit)
    return [domains[i : i + per_file] for i in range(0, len(domains), per_file)] or [[]]


def _pack_pages(
    sections: list[tuple[str, list[str]]], budget: int
) -> list[list[tuple[str, list[str]]]]:
    """Pack domain sections into page payloads of at most ~budget chars.

    A section too big for an empty page splits by entry lines (a single
    lesson is never cut). The count of pages is not capped here — the
    caller's retrieval cap bounds the original chunking, and the compiler
    budgets what actually preloads.
    """
    pages: list[list[tuple[str, list[str]]]] = [[]]
    used = 0
    for domain, lines in sections:
        header_cost = len(domain) + 8
        for group in _line_groups(lines, max(256, budget - header_cost)):
            cost = header_cost + sum(len(line) + 1 for line in group)
            if pages[-1] and used + cost > budget:
                pages.append([])
                used = 0
            pages[-1].append((domain, group))
            used += cost
    return pages


def _line_groups(lines: list[str], limit: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    used = 0
    for line in lines:
        if current and used + len(line) + 1 > limit:
            groups.append(current)
            current, used = [], 0
        current.append(line)
        used += len(line) + 1
    if current or not groups:
        groups.append(current)
    return groups


def build_memory_files(
    entries: list[ExperienceEntry],
    parent_root: Path,
    *,
    adopted_digest: str | None = None,
    retrieval_limit: int = 5,
    memory_budget_tokens: int = 4000,
) -> dict[str, str]:
    """The wiki: AGENTS.md index plus linked files per domain group.

    The index is the injected top layer of the memory hierarchy and stays
    inside the harness's retrieval cap; detail lives one link away. Files
    keep frontmatter (name/description) and ``[[slug]]`` links so a future
    agent can traverse the store instead of rediscovering it. A page that
    would exceed the injected layer's token budget is split into
    ``<domain>-<n>.md`` parts, each indexed — lessons are never truncated.
    """
    superseded: set[str] = set()
    for entry in entries:
        superseded.update(entry.supersedes)
    live = [e for e in entries if entry_key(e) not in superseded and not _SECRET.search(e.lesson)]
    by_domain: dict[tuple[str, str], list[ExperienceEntry]] = {}
    for entry in live:
        by_domain.setdefault((entry.domain, entry.namespace), []).append(entry)
    if not by_domain:
        return {}
    domains = sorted(by_domain, key=lambda d: (d[0], d[1]))
    files: dict[str, str] = {}
    file_domains: dict[str, list[str]] = {}
    budget = max(256, memory_budget_tokens) * 4 - _FRONTMATTER_SLACK
    for number, chunk in enumerate(_chunk(domains, max(1, retrieval_limit - 1)), start=1):
        slug = _SLUG.sub("-", chunk[0][0]).strip("-") or "domain"
        # Render each domain's section, then pack sections into pages of
        # at most one budget each; a single domain too big for one page
        # splits by entry lines. Lesson text is never truncated.
        sections = []
        for domain, namespace in chunk:
            members = sorted(by_domain[(domain, namespace)], key=lambda e: (e.ordinal, e.revision))
            lines = [
                # Plain statements of current understanding, not session
                # stories; provenance shows how each rule is known.
                f"- {e.lesson}{_provenance(e, adopted_digest)}"
                for e in members
            ]
            sections.append((domain, lines))
        for part, payload in enumerate(_pack_pages(sections, budget), start=1):
            name = f"{slug}.md" if len(chunk) == 1 and part == 1 else (
                f"{slug}-{part}.md" if len(chunk) == 1 else f"{slug}-and-more-{number}-{part}.md"
            )
            file_domains[f"memory/{name}"] = sorted({d for d, _ in payload})
            body_parts = []
            for domain, lines in payload:
                body_parts.extend([f"## {domain}", "", *lines, ""])
            files[f"memory/{name}"] = (
                "---\n"
                f"name: {slug}\n"
                f"description: Consolidated lessons for {', '.join(sorted({d for d, _ in payload}))}; "
                "read before tasks in these domains.\n"
                "---\n\n" + "\n".join(body_parts).rstrip() + "\n"
            )
    index = [_INDEX_START, "", "Long-term memory, distilled offline from earlier runs."]
    index.append("Trust the provenance dates; re-verify anything marked stale or conflicting.")
    for rel in sorted(file_domains):
        index.append(
            f"- [[{Path(rel).stem}]] — verified lessons for {', '.join(file_domains[rel])}"
        )
    index.append(_INDEX_END)
    standing = _standing_text(parent_root)
    files["memory/AGENTS.md"] = (standing + "\n\n" if standing else "") + "\n".join(index) + "\n"
    return files


def run_dream(
    store: RunStore,
    parent_root: Path,
    *,
    adopted_digest: str | None = None,
    retrieval_limit: int | None = None,
    adopt_base_dir: Path | None = None,
    distill_config: ExperimentConfig | None = None,
    verify_config: ExperimentConfig | None = None,
) -> DreamReport:
    """Audit, consolidate, and republish memory as a candidate bundle patch.

    The patch applies to ``parent_root`` (normally the adopted bundle) and
    is published into the run store; adopting it into a portfolio base
    directory is an explicit, separate step. ``distill_config`` and
    ``verify_config`` (spending-capped ``ExperimentConfig``) opt into the
    metered stages: full-transcript distillation and the semantic audit
    that judges existing lessons against the transcripts they cite.
    """
    parent_root = Path(parent_root)
    if adopted_digest is None:
        adopted_digest = bundle_digest(parent_root)
    else:
        # Patch the bundle the adopted digest actually names when the store
        # holds it; later dream cycles chain from their own output.
        try:
            adopted_bundle = store.bundle_path(adopted_digest)
        except ValueError:
            adopted_bundle = None
        if adopted_bundle is not None and adopted_bundle.is_dir():
            parent_root = adopted_bundle
    if retrieval_limit is None:
        config_file = parent_root / "config.json"
        tuning = (
            HarnessConfig.model_validate_json(config_file.read_text())
            if config_file.is_file()
            else HarnessConfig()
        )
        retrieval_limit = tuning.retrieval_limit
        memory_budget_tokens = tuning.memory_budget_tokens
    else:
        memory_budget_tokens = 4000
    entries = load_entries(store)
    distill_stats = None
    if distill_config is not None:
        from .distill import analyze_transcripts

        _, distill_stats = analyze_transcripts(distill_config, store, entries)
    semantic_stats = None
    if verify_config is not None:
        from .semantic import verify_semantics

        _, semantic_stats = verify_semantics(
            verify_config, store, entries, adopted_digest=adopted_digest
        )
    audit = audit_library(store, entries, adopted_digest)
    dream = consolidate(store, entries, audit)
    files = build_memory_files(
        entries,
        parent_root,
        adopted_digest=adopted_digest,
        retrieval_limit=retrieval_limit,
        memory_budget_tokens=memory_budget_tokens,
    )
    report = DreamReport(
        audit=audit,
        restated=sum(1 for e in dream if not e.lesson.startswith("CONFLICT")),
        merged=sum(1 for e in dream if len(e.supersedes) > 1),
        conflicts_marked=sum(1 for e in dream if e.lesson.startswith("CONFLICT")),
        wiki_files=sorted(files),
        parent_digest=adopted_digest,
        analyzed=distill_stats.analyzed if distill_stats else 0,
        distilled=distill_stats.distilled if distill_stats else 0,
        distill_skipped=distill_stats.skipped if distill_stats else 0,
        semantic_checked=semantic_stats.checked if semantic_stats else 0,
        semantic_supported=semantic_stats.supported if semantic_stats else 0,
        semantic_corrected=semantic_stats.corrected if semantic_stats else 0,
        semantic_inconclusive=semantic_stats.inconclusive if semantic_stats else 0,
    )
    if files:
        patch = HarnessPatch(parent_digest=adopted_digest, upserts=files)
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / "candidate"
            digest = apply_patch(parent_root, patch, candidate)
            digest = store.publish_bundle(candidate)
        report.patch_digest = digest
        if adopt_base_dir is not None:
            from .portfolio_ops import archive_bundle, write_adopted

            archive_bundle(Path(adopt_base_dir), store, digest)
            write_adopted(Path(adopt_base_dir), digest, store)
            report.adopted = True
    return report
