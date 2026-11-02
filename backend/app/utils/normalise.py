"""Normalisation rules (PRD §9.5, §9.5.1, FR-050, FR-063, FR-064, DEC-003)."""
import re

# FR-050 / A-14: these four characters only. Nothing else is removed.
_INVOICE_NO_REMOVE = str.maketrans("", "", " -/.")

# FR-064 / A-32 step 2: a '.' is kept only when it sits between two digits.
_DOT_NOT_BETWEEN_DIGITS = re.compile(r"(?<!\d)\.|\.(?!\d)")
_OTHER_PUNCTUATION = re.compile(r"[^\w\s.]")
# Step 3: join a number to the unit (letters) that follows it: "12 mm" -> "12mm".
_NUMBER_SPACE_UNIT = re.compile(r"(\d)\s+([a-z])")
_WHITESPACE = re.compile(r"\s+")


def normalise_invoice_number(raw: str) -> str:
    """Uppercase and remove space, '-', '/', '.' (FR-050). 'INV-2041-R' -> 'INV2041R'."""
    return raw.upper().translate(_INVOICE_NO_REMOVE)


# PO references are normalised like invoice numbers (FR-060).
normalise_po_reference = normalise_invoice_number


def normalise_description(text: str) -> str:
    """Line-description normalisation for pairing (FR-064, §9.5.1), applied in order:

    1. lowercase;
    2. keep '.' only between two digits; every other '.' and all other punctuation -> space;
    3. remove spaces between a number and the unit that follows it;
    4. collapse whitespace and trim.

    'Copper Cable 2.5 sqmm Coil' -> 'copper cable 2.5sqmm coil'. Idempotent.
    """
    s = text.lower()
    s = _DOT_NOT_BETWEEN_DIGITS.sub(" ", s)
    s = _OTHER_PUNCTUATION.sub(" ", s)
    s = _NUMBER_SPACE_UNIT.sub(r"\1\2", s)
    return _WHITESPACE.sub(" ", s).strip()


def normalise_item_code(code: str) -> str:
    """Item codes compare case-insensitively with whitespace removed (FR-063)."""
    return _WHITESPACE.sub("", code).upper()


def normalise_gstin(gstin: str) -> str:
    """GSTIN comparison form: uppercase, whitespace removed (FR-030)."""
    return _WHITESPACE.sub("", gstin).upper()
