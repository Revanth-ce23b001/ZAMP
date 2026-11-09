"""Display name: InvoPilot (DEC-022).

The product's visible name changed; nothing the running system depends on did. These tests hold
both halves: no page a person reads carries the old name, and the policy version hash is exactly
what it was before the rename, because policy.yaml (whose first line still names the old product)
was not touched.
"""
import json
import re
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.drafts.templates import SIGN_OFF, TEMPLATES, TO_LINE
from app.main import PRODUCT_NAME
from app.policy import load_policy

REPO = BACKEND_DIR.parent
FRONTEND = REPO / "frontend"
NEW_NAME = "InvoPilot"
OLD_NAME = "Clear" + "Match"          # assembled so this file's own prose does not trip the scan

# The policy version at the time of the rename. policy.yaml is hashed whole, including its first
# line, which still names the old product: changing that line would change every decision's
# recorded policy version, so it stays.
PINNED_POLICY_VERSION = "8e6324a000e6ace81f8e601af67db02461623ee4fc0c63b8a1dba79e4d685cca"


def test_the_policy_version_hash_is_unchanged_by_the_rename():
    assert load_policy().version == PINNED_POLICY_VERSION


def test_policy_yaml_keeps_the_old_name_because_it_is_inside_the_hash():
    text = (BACKEND_DIR / "app" / "policy.yaml").read_text(encoding="utf-8")
    assert OLD_NAME in text, "policy.yaml was edited; the policy version would have changed"


def test_the_backend_display_name_is_the_new_name(client):
    body = client.get("/api/meta").json()
    assert body["product"] == NEW_NAME == PRODUCT_NAME
    assert OLD_NAME not in json.dumps(body)


def test_the_openapi_title_is_the_new_name(client):
    assert client.get("/openapi.json").json()["info"]["title"] == NEW_NAME


def test_the_draft_sign_off_carries_the_new_name():
    assert SIGN_OFF.startswith(f"- Draft prepared by {NEW_NAME}")
    text = SIGN_OFF + "\n".join(t.subject + t.body + t.ask for t in TEMPLATES.values())
    text += "\n".join(TO_LINE.values())
    assert OLD_NAME not in text


# ------------------------------------------------------------------ the pages a person reads

# Every file that renders visible text, plus the docs. Deployment URLs, the Vercel project name,
# package names and log keys deliberately keep the old spelling and are not scanned.
PAGE_SOURCES = [FRONTEND / "app", FRONTEND / "components", FRONTEND / "lib"]
PAGE_SUFFIXES = {".tsx", ".ts"}
DOCS = [REPO / "README.md", REPO / "docs" / "DEMO_SCRIPT.md", REPO / "docs" / "ACCEPTANCE.md",
        REPO / "docs" / "MODEL_STRATEGY.md", REPO / "DECISIONS.md",
        BACKEND_DIR / "seed" / "README_FIXTURES.md"]

# Lines allowed to keep the old name, because they are an identifier, a URL or a deliberate record.
ALLOWED = re.compile(
    r"https?://|vercel|render\.com|onrender|clearmatch-ap|clearmatch\.db|data/clearmatch|"      # URLs, services, paths
    r"clearmatch\.(reset|worker)|clearmatch-worker|"                                            # log and thread names
    r"_PRD_v1\.2_Final\.md|"                                                                    # the frozen PRD filename
    r"Formerly |formerly |keep the original name|the original \"clearmatch\" spelling|"          # the deliberate records
    r"policy\.yaml", re.I)


def visible_sources() -> list[Path]:
    out = []
    for root in PAGE_SOURCES:
        out += [f for f in sorted(root.rglob("*")) if f.is_file() and f.suffix in PAGE_SUFFIXES]
    return out + [d for d in DOCS if d.is_file()]


def offending_lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if OLD_NAME.lower() in line.lower() and not ALLOWED.search(line)]


def test_there_are_sources_to_scan():
    found = visible_sources()
    assert len(found) > 20
    assert any(f.name == "layout.tsx" for f in found)


@pytest.mark.parametrize("path", visible_sources(), ids=lambda p: p.name)
def test_no_visible_text_carries_the_old_product_name(path):
    assert offending_lines(path) == []


def test_the_header_and_tab_title_use_the_shared_app_name():
    layout = (FRONTEND / "app" / "layout.tsx").read_text(encoding="utf-8")
    brand = (FRONTEND / "lib" / "brand.ts").read_text(encoding="utf-8")
    assert f'APP_NAME = "{NEW_NAME}"' in brand
    assert "title: APP_NAME" in layout                      # the browser tab title
    assert "{APP_NAME}" in layout and "{APP_MARK}" in layout  # the header wordmark and its logo square
    assert OLD_NAME not in layout
