"""Phase 2: wiki representations — deterministic link repair and the
metered organizer pass.

The wiki is rebuilt from entries every dream, so its shape is always
regenerable. Two improvements on top of the rebuild:

- **Link repair** is deterministic and free, part of every dream:
  ``[[slug]]`` targets that exist under a normalized alias are repointed
  at the canonical page; targets that resolve to nothing have their
  brackets dropped (the text stays, the lie goes). Hand-written standing
  text outside the index markers is never touched.
- **The organizer pass** (``--organize``, metered) is one structured call
  per wiki page that rewrites the page around its lessons — sections,
  cross-links, promotion of load-bearing rules — within the same
  memory-only patch path. Outcomes persist under ``kind='organized'``
  keyed by the page name and the digest of the raw page, so a second
  dream over unchanged entries reuses the cached rewrite and calls the
  model zero times (the same per-input accounting that makes distill and
  verify converge).
"""

import hashlib
import re

from .contracts import ExperimentConfig, Record
from .dreaming import _INDEX_END, _INDEX_START, _SECRET
from .editors import run_structured
from .storage import RunStore

_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")


class OrganizedPage(Record):
    """Structured output of one organizer call."""

    rewritten: str
    note: str = ""


class OrganizeStats(Record):
    pages: int = 0
    organized: int = 0
    cached: int = 0
    skipped: int = 0


class LinkStats(Record):
    repaired: int = 0
    dropped: int = 0


def _canon(target: str) -> str:
    """Normalized link target: case, separators, and .md don't matter."""
    stem = target.strip().removesuffix(".md")
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    return slug or target.strip().lower()


def _repair_text(text: str, canon_targets: dict[str, str], stats: LinkStats) -> str:
    def fix(match: re.Match) -> str:
        target = match.group(1)
        canonical = canon_targets.get(_canon(target))
        if canonical is None:
            stats.dropped += 1
            return target  # keep the words, drop the dead link
        if canonical != target:
            stats.repaired += 1
        return f"[[{canonical}]]"

    return _LINK.sub(fix, text)


def repair_links(files: dict[str, str]) -> LinkStats:
    """Repoint aliased ``[[slug]]`` targets; drop dangling ones. In place.

    Deterministic and free — part of every dream. In the index only the
    regenerated marker block is touched; hand-written standing text is
    left exactly as authored.
    """
    stats = LinkStats()
    canon_targets = {
        _canon(name.removesuffix(".md").split("/")[-1]): name.removesuffix(".md").split("/")[-1]
        for name in files
    }
    for name in files:
        if name == "memory/AGENTS.md":
            body = files[name]
            start = body.find(_INDEX_START)
            end = body.find(_INDEX_END)
            if start != -1 and end != -1:
                files[name] = (
                    body[:start]
                    + _repair_text(body[start : end + len(_INDEX_END)], canon_targets, stats)
                    + body[end + len(_INDEX_END) :]
                )
        else:
            files[name] = _repair_text(files[name], canon_targets, stats)
    return stats


def _context(page: str, all_pages: list[str]) -> dict:
    return {
        "task": (
            "Rewrite this memory wiki page for clarity: group related "
            "lessons under short section headings, promote load-bearing "
            "rules to the top, and add [[page-name]] cross-links to other "
            "pages where a lesson depends on them. Keep every lesson's "
            "meaning and its provenance suffix unchanged; do not invent "
            "new lessons; keep frontmatter."
        ),
        "other_pages": all_pages,
        "page": page,
    }


def _page_key(page: str, raw: str) -> str:
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{page}:{digest}"


def organize_wiki(
    config: ExperimentConfig,
    store: RunStore,
    files: dict[str, str],
    organizer=None,
) -> tuple[dict[str, str], OrganizeStats]:
    """Rewrite each wiki page with one metered structured call per page.

    The rewritten body replaces the page's content in ``files`` — no other
    path is touched, so the result still flows through the normal
    memory-only ``HarnessPatch``. Outcomes (rewrite, skip reason, or error)
    persist under ``kind='organized'`` keyed by page name plus the raw
    page's digest, which is what makes repeated dreams converge.
    """
    if config.max_usd <= 0:
        raise ValueError("the organizer requires a nonzero spending cap")

    def default_organizer(context: dict) -> OrganizedPage:
        return run_structured(
            config,
            store,
            "ORGANIZE",
            context,
            OrganizedPage,
            f"organize/{hashlib.sha256(context['page'].encode()).hexdigest()[:12]}",
        )

    if organizer is None:
        organizer = default_organizer
    pages = sorted(name for name in files if name != "memory/AGENTS.md")
    all_pages = [name.removesuffix(".md").split("/")[-1] for name in pages]
    stats = OrganizeStats(pages=len(pages))
    for page, slug in zip(pages, all_pages):
        raw = files[page]
        key = _page_key(page, raw)
        cached = store.get("organized", key)
        if cached is not None:
            if "organized" in cached:
                files[page] = cached["organized"]
                stats.cached += 1
            else:
                stats.skipped += 1
            continue
        try:
            decision = organizer(_context(raw, all_pages))
        except (RuntimeError, ValueError) as exc:
            store.put("organized", key, {"error": type(exc).__name__})
            stats.skipped += 1
            continue
        rewritten = decision.rewritten.strip()
        outcome: dict
        if not rewritten:
            outcome = {"reason": "empty rewrite"}
            stats.skipped += 1
        elif _SECRET.search(rewritten):
            outcome = {"reason": "credential-shaped text in rewrite; page kept raw"}
            stats.skipped += 1
        elif len(rewritten) > max(2 * len(raw), 4000):
            outcome = {"reason": "rewrite implausibly larger than the page"}
            stats.skipped += 1
        else:
            # Frontmatter is regenerated from the page name, not the model.
            frontmatter = raw.split("---\n")
            if raw.startswith("---\n") and len(frontmatter) >= 3:
                rewritten = "---\n" + frontmatter[1] + "---\n" + rewritten
            files[page] = rewritten + ("" if rewritten.endswith("\n") else "\n")
            stats.organized += 1
            outcome = {"organized": files[page], "note": decision.note}
        store.put("organized", key, outcome)
    return files, stats
