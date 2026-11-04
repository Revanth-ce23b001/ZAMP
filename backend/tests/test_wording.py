"""Day 8B copy rules for the value figures (DEC-019): a fixed set of words never appears in source, copy or docs,
and one word is allowed only inside a single exact negation. The words are assembled from fragments so this
file does not itself contain them."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BANNED = ["pre" + "vented", "re" + "covered", "loss " + "avoided"]
NEGATED = "sa" + "ved"
ALLOWED_PHRASE = "not money " + NEGATED
ROOTS = ["frontend/app", "frontend/components", "frontend/lib", "backend/app", "backend/tests", "docs", "README.md",
         "DECISIONS.md", "render.yaml"]
SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".css", ".py", ".md", ".json", ".yaml", ".yml", ".sql", ".html"}


def files():
    for root in ROOTS:
        p = REPO / root
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from (f for f in p.rglob("*") if f.is_file() and f.suffix in SUFFIXES and "__pycache__" not in f.parts)


def test_banned_words_never_appear():
    hits = [(str(f.relative_to(REPO)), w) for f in files() for w in BANNED
            if re.search(rf"\b{w}\b", f.read_text(encoding="utf-8", errors="ignore"), re.I)]
    assert hits == []


def test_the_permitted_word_appears_only_in_the_exact_negation():
    stray = []
    for f in files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(rf"\b{NEGATED}\b", text, re.I):
            window = text[max(0, m.start() - len("not money ")):m.end()]
            if window.lower() != ALLOWED_PHRASE:
                stray.append((str(f.relative_to(REPO)), text[max(0, m.start() - 30):m.end() + 10]))
    assert stray == []


def test_the_negation_is_on_the_value_tile():
    page = (REPO / "frontend/app/reports/page.tsx").read_text(encoding="utf-8")
    assert ALLOWED_PHRASE in page
    for label in ("Discrepancy value flagged", "Invoice value held for review", "No decided runs yet"):
        assert label in page
