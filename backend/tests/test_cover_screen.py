"""The cover screen at "/" (DEC-024): source rules. Browser behaviour is in test_e2e_browser.py.

It is the one dark surface in the product. Everything behind the Work Queue keeps the light design
tokens, so this checks that the dark styling stays inside the cover and that nothing placed on the
gradient is painted in an ink token that would be unreadable there.
"""
import re
from pathlib import Path

import pytest

from app.config import BACKEND_DIR

FRONTEND = BACKEND_DIR.parent / "frontend"
BACKDROP = FRONTEND / "components" / "ui" / "tailwind-css-background-snippet.tsx"
COVER = FRONTEND / "app" / "page.tsx"
GATE = FRONTEND / "components" / "ChromeGate.tsx"
LAYOUT = FRONTEND / "app" / "layout.tsx"
GLOBALS = FRONTEND / "app" / "globals.css"

# The supplied gradient, unchanged.
GRADIENT = "[background:radial-gradient(125%_125%_at_50%_10%,#000_40%,#63e_100%)]"


def test_the_backdrop_carries_the_supplied_gradient_unchanged():
    assert BACKDROP.is_file()
    assert GRADIENT in BACKDROP.read_text(encoding="utf-8")


def test_the_backdrop_lives_where_shadcn_components_are_imported_from():
    assert BACKDROP.parent == FRONTEND / "components" / "ui"
    assert 'from "@/lib/utils"' in BACKDROP.read_text(encoding="utf-8")


def test_the_gradient_appears_only_on_the_cover():
    """A dark backdrop behind the light screens would make every card float on violet."""
    users = [p for p in sorted((FRONTEND / "app").rglob("*.tsx")) + sorted((FRONTEND / "components").rglob("*.tsx"))
             if "tailwind-css-background-snippet" in p.read_text(encoding="utf-8")]
    assert [p.name for p in users] == ["page.tsx"]
    assert users[0] == COVER
    assert GRADIENT not in GLOBALS.read_text(encoding="utf-8")


def test_cover_text_is_light_and_uses_no_ink_token():
    """White on #000 and on #63e both read; the ink tokens on either would not."""
    text = COVER.read_text(encoding="utf-8")
    assert "text-white" in text
    for unreadable in ("text-ink-2", "text-ink-3", "text-ink-4", "bg-page", "bg-surface", "border-line"):
        assert unreadable not in text, unreadable
    # The dark ink token is allowed only where it sits on white: the button and the logo mark.
    inked = [line.strip() for line in text.splitlines() if "text-ink" in line]
    assert inked, "expected the ink token on the white elements"
    for line in inked:
        assert "bg-white" in line, line


def test_the_cover_has_one_h1_and_a_way_into_the_app():
    text = COVER.read_text(encoding="utf-8")
    assert text.count("<h1") == 1
    assert 'href="/work-queue"' in text
    assert "{APP_NAME}" in text and "{APP_MARK}" in text      # the name comes from the constant


def test_the_chrome_is_hidden_on_the_cover_only():
    gate = GATE.read_text(encoding="utf-8")
    assert 'COVER = "/"' in gate
    assert "usePathname" in gate and '"use client"' in gate
    layout = LAYOUT.read_text(encoding="utf-8")
    assert "<ChromeGate>" in layout and "<MainShell>{children}</MainShell>" in layout
    # The header markup itself is untouched: still the shared name and mark.
    assert "{APP_NAME}" in layout and "{APP_MARK}" in layout


def test_the_light_design_tokens_are_untouched():
    css = GLOBALS.read_text(encoding="utf-8")
    assert "--color-page: #f7f7f8;" in css
    assert "--color-accent: #1e3a8a;" in css
    assert "--color-ink: #18181b;" in css
    assert "#63e" not in css and "radial-gradient" not in css


@pytest.mark.parametrize("path", [BACKDROP, COVER, GATE], ids=lambda p: p.name)
def test_the_cover_sources_name_no_image_asset(path):
    """No images: the backdrop is a CSS gradient, so there is nothing to host or load."""
    text = path.read_text(encoding="utf-8")
    assert re.search(r"<img|next/image|unsplash|\.png|\.jpg|\.svg", text, re.I) is None


def test_no_demo_route_was_added():
    assert not (FRONTEND / "components" / "ui" / "demo.tsx").exists()
    assert not (FRONTEND / "app" / "hero").exists()
    assert not (FRONTEND / "app" / "demo").exists()
