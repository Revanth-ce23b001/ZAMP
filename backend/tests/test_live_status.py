"""The Live Run "reading" status: source rules (the browser behaviour is in test_e2e_browser.py).

The shimmer is a status indicator inside the existing Live Run view, not a new pipeline step. The
model reads; deterministic code decides, so the status says "reading" and never "thinking" or
"deciding".
"""
import re
from pathlib import Path

import pytest

from app.config import BACKEND_DIR

FRONTEND = BACKEND_DIR.parent / "frontend"
COMPONENT = FRONTEND / "components" / "ui" / "shining-text.tsx"
LIVE_RUN = FRONTEND / "components" / "LiveRun.tsx"
BANNED = ("thinking", "deciding", "analysing", "analyzing")


def test_the_component_exists_where_shadcn_style_components_are_imported_from():
    assert COMPONENT.is_file(), "components/ui/shining-text.tsx is missing"
    assert (FRONTEND / "components.json").is_file()
    assert (FRONTEND / "lib" / "utils.ts").is_file()


@pytest.mark.parametrize("path", [COMPONENT, LIVE_RUN], ids=lambda p: p.name)
def test_the_status_never_claims_the_model_is_thinking_or_deciding(path):
    text = path.read_text(encoding="utf-8").lower()
    assert [w for w in BANNED if w in text] == []


def test_the_status_is_announced_as_a_status_and_is_not_a_heading():
    text = COMPONENT.read_text(encoding="utf-8")
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert "<h1" not in text and "motion.h1" not in text
    assert "motion.span" in text and '"use client"' in text


def test_the_component_honours_reduced_motion_and_uses_only_existing_tokens():
    text = COMPONENT.read_text(encoding="utf-8")
    assert "useReducedMotion" in text
    assert "if (reduceMotion)" in text
    assert "font-normal" in text and "font-regular" not in text
    # Colours come from the project's ink tokens, not from literal hex values.
    assert "var(--color-ink-3)" in text and "var(--color-ink)" in text
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", text) is None, "a literal colour was introduced"
    assert "truncate" in text                                   # one line, ellipsis on narrow screens


def test_the_live_run_takes_the_product_name_from_the_shared_constant():
    text = LIVE_RUN.read_text(encoding="utf-8")
    assert 'import { APP_NAME } from "@/lib/brand";' in text
    assert "${APP_NAME} is reading the invoice" in text
    assert "InvoPilot is reading" not in text, "the name must come from the constant, not a literal"


def test_the_shimmer_shows_only_while_ingest_or_extract_is_running():
    """Visibility derives from the existing SSE stage state: no new state, no new stage list."""
    text = LIVE_RUN.read_text(encoding="utf-8")
    assert ('const reading = !error && (rows.INGEST.status === "RUNNING" '
            '|| rows.EXTRACT.status === "RUNNING");') in text
    assert "{reading && (" in text
    assert text.count("<ShiningText") == 1


def test_no_demo_page_was_shipped():
    assert not (FRONTEND / "components" / "ui" / "demo.tsx").exists()
    stray = [p for p in (FRONTEND / "app").rglob("*.tsx") if "shining" in p.name.lower()]
    assert stray == []


def test_motion_is_a_declared_dependency():
    import json
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert "motion" in pkg["dependencies"]


def test_the_design_tokens_file_was_not_touched_by_shadcn_init():
    """shadcn init rewrote globals.css; it was restored. These are the project's own tokens."""
    css = (FRONTEND / "app" / "globals.css").read_text(encoding="utf-8")
    assert "--color-accent: #1e3a8a;" in css
    assert "--color-ink-3: #71717a;" in css
    assert '"Inter", ui-sans-serif' in css
    for injected in ("tw-animate-css", "shadcn/tailwind.css", "bg-background text-foreground",
                     "--color-sidebar", "oklch("):
        assert injected not in css, injected


def test_the_layout_still_uses_the_projects_own_font():
    layout = (FRONTEND / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "Geist" not in layout and "next/font/google" not in layout
    assert "Inter" in layout                                     # the Google Fonts stylesheet link
