"""Day 10 (DEC-021): the forbidden-label scan, extended to the resolution drafts.

The built frontend is already scanned for the FR-034 claims by
tests/test_e2e_browser.py::test_built_frontend_has_no_forbidden_labels (browser run, --run-e2e).
That scan cannot see the draft templates, which are backend source and are rendered on request, so
this scan covers them: the same claims, plus "verified" applied to a GSTIN or to account details.

"Verified contact on the vendor master" is the one permitted use of the word. It names the channel
the vendor master records; it claims nothing about a GSTIN, an account or a delivery.
"""
import re
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.drafts.templates import TEMPLATES, TO_LINE, TRUSTED_CONTACT_UI_NOTE

REPO = BACKEND_DIR.parent
FRONTEND = REPO / "frontend"

# The FR-034 claims, plus the two the Day 10 brief adds. Assembled from fragments so this file does
# not itself contain a banned phrase.
FORBIDDEN = ["gst " + "verified", "gstin " + "verified", "account " + "verified", "bank " + "verified",
             "goods " + "received"]
PERMITTED = "verified contact on the vendor master"
WORD = "veri" + "fied"

# Everything the drafts feature is made of. The rest of the decision screen keeps its own wording
# (an override records a bank-check outcome, FR-106/OD-5) and is covered by the existing scans.
SCANNED = [BACKEND_DIR / "app" / "drafts", FRONTEND / "components" / "decision" / "ResolutionDrafts.tsx"]
SUFFIXES = {".py", ".ts", ".tsx"}


def files() -> list[Path]:
    out = []
    for p in SCANNED:
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in sorted(p.rglob("*"))
                    if f.is_file() and f.suffix in SUFFIXES and "__pycache__" not in f.parts]
    return out


def stray_verified(text: str) -> list[str]:
    """Every occurrence of the word that is not the one permitted phrase."""
    stray = []
    for m in re.finditer(WORD, text, re.I):
        if text[m.start():m.start() + len(PERMITTED)].lower() != PERMITTED:
            stray.append(text[max(0, m.start() - 40):m.end() + 20])
    return stray


def test_the_scan_has_files_to_scan():
    found = files()
    assert found, "nothing scanned"
    assert any(f.parts[-2] == "drafts" for f in found)


@pytest.mark.parametrize("path", files(), ids=lambda p: str(p.name))
def test_draft_sources_make_no_forbidden_claim(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    assert [label for label in FORBIDDEN if re.search(label, text, re.I)] == []
    assert stray_verified(text) == []


def test_rendered_template_text_makes_no_forbidden_claim():
    """The templates themselves, independent of where they live on disk."""
    text = "\n".join(t.subject + "\n" + t.body + "\n" + t.ask for t in TEMPLATES.values())
    text += "\n" + "\n".join(TO_LINE.values()) + "\n" + TRUSTED_CONTACT_UI_NOTE
    assert [label for label in FORBIDDEN if re.search(label, text, re.I)] == []
    assert stray_verified(text) == []
    assert PERMITTED in text.lower()
