"""Guards on the styling contract, not on how anything looks.

These exist because a class can be added to a template, render as a bare
unstyled element, and look merely "a bit off" rather than broken -- which is
how login-button-shifts shipped with no rule at all.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
TEMPLATES = sorted((ROOT / "app" / "templates").glob("*.html"))

# Prefixes whose classes are always meant to carry their own rule. Layout
# helpers and one-off utility classes are deliberately out of scope.
STYLED_PREFIXES = ("login-button-", "login-panel-", "event-banner", "event-chip")


def classes_used():
    found = set()
    for path in TEMPLATES:
        for attr in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            # Skip Jinja-interpolated class strings; the literal parts still count.
            for name in re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", attr).split():
                # A class built from a Jinja expression, e.g.
                # class="event-banner event-banner-{{ event.kind }}", leaves a
                # dangling stem once the expression is stripped. The stem is
                # not a real class; its resolved forms are checked below.
                if name.endswith("-"):
                    continue
                if name.startswith(STYLED_PREFIXES):
                    found.add(name)
    return found


# Classes only ever produced by a Jinja expression, so they never appear as a
# literal in a template but must still be styled.
INTERPOLATED = ("event-banner-supply", "event-banner-demand",
                "event-chip-supply", "event-chip-demand")


@pytest.mark.parametrize("name", sorted(classes_used() | set(INTERPOLATED)))
def test_every_styled_class_has_a_rule(name):
    assert re.search(r"\." + re.escape(name) + r"[\s,{:]", CSS), (
        f"{name} is used in a template but has no rule in style.css, so it "
        f"renders unstyled"
    )


def test_login_buttons_cannot_be_overflowed_by_a_long_label():
    """The global .btn is nowrap by design. The login cards are full-width and
    take long labels, so they must opt back into wrapping and grow instead."""
    block = CSS[CSS.index(".login-panel-action .btn {"):]
    block = block[:block.index("}")]
    assert "white-space: normal" in block, "a long label would overflow its button"
    assert "min-height" in block, "the button must keep its size for short labels"
    for rule in ("overflow-wrap", "line-height"):
        assert rule in block, f"{rule} is needed for a label that wraps"


def test_the_event_banner_shows_the_art_at_its_real_shape():
    """The art is a wide 1024x384 banner. Cropping it square showed a
    meaningless sliver of the middle and read as a missing image."""
    block = CSS[CSS.index(".event-banner-art {"):]
    block = block[:block.index("}")]
    assert "aspect-ratio: 1024 / 384" in block
    assert "width: 100%" in block
