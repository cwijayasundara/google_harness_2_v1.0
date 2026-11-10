"""The reflect-steering source of truth, kept inside the editable bundle.

``memory/instructions.md`` tells the reflect step what kind of memory is
worth writing — the research's "instructions are part of memory". The
hardcoded text below is only the fallback for bundles that predate the
file; a rewrite landed by ``harness_clone.review`` or a hand edit takes
priority. Task agents never see this file preloaded; it is consumed by
the reflect step (the memory-writing part of the loop).
"""

from pathlib import Path

# The pre-file steering text, preserved as the fallback.
DEFAULT_INSTRUCTIONS = (
    "Return a transferable cue and lesson. Memory is a prediction about "
    "what a future task will need: state a rule the harness could not "
    "re-derive on its own, how the rule was verified, and any trap that "
    "cost retries. Do not record information readable from the task or "
    "snapshot every run. evidence_refs must be copied exactly from "
    "valid_evidence_refs. Stage labels, rationales, and prose are not "
    "evidence IDs. Adoption is not proof of correctness. Avoid "
    "task-specific facts."
)

_INSTRUCTIONS_PATH = Path("memory") / "instructions.md"


def load_instructions(bundle_root: Path | None) -> str:
    """The steering instruction: the bundle's file when it exists, else default."""
    if bundle_root is not None:
        candidate = Path(bundle_root) / _INSTRUCTIONS_PATH
        if candidate.is_file():
            text = candidate.read_text().strip()
            if text:
                return text
    return DEFAULT_INSTRUCTIONS
