"""
Icon sets a team/bot can display -- filenames under app/static/img/.

Headphone Company Simulator asset pack: 30 icons cropped from a single
10x3 sprite sheet (`Business Sim Headphone Sprites Final.png`, supplied by
the user) -- row 1 (factories) -> AVATAR_CHOICES, row 2 (headphones) -> a
hand-picked 3 for TIER_ICONS, row 3 (logos) -> BADGE_CHOICES. This replaces
the old Kenney "City Kit Industrial" building-icon avatar set entirely.

Kept as real lists here, not inferred from whatever happens to be in each
folder, so adding/removing an image file can't silently change what's
selectable/assignable without a deliberate code change (same rationale as
the original AVATAR_CHOICES).
"""

import random


AVATAR_CHOICES = [
    "factory-01.png", "factory-02.png", "factory-03.png", "factory-04.png", "factory-05.png",
    "factory-06.png", "factory-07.png", "factory-08.png", "factory-09.png", "factory-10.png",
]

# One per firm/bot "badge" (app/static/img/badges/) -- new field, there was
# no equivalent image-badge system before this (the existing ".badge" CSS
# class elsewhere in the app is a text/status pill -- Active/Bankrupt/BOT --
# unrelated to this). Auto-assigned at registration/bot-assignment time,
# same "random pick from a fixed pool" mechanism as AVATAR_CHOICES; no
# separate picker UI was requested for it.
BADGE_CHOICES = [
    "logo-01.png", "logo-02.png", "logo-03.png", "logo-04.png", "logo-05.png",
    "logo-06.png", "logo-07.png", "logo-08.png", "logo-09.png", "logo-10.png",
]

# The team's own product shot (app/static/img/products/) -- chosen at
# signup like the factory and the logo, and shown beside them in the Firm
# Dashboard header. Distinct from TIER_ICONS below: this is the team's
# branding and never changes, whereas the tier icon is DERIVED from which
# tier they're currently selling and changes as they move up/down.
PRODUCT_CHOICES = [
    "headphone-01.png", "headphone-02.png", "headphone-03.png", "headphone-04.png", "headphone-05.png",
    "headphone-06.png", "headphone-07.png", "headphone-08.png", "headphone-09.png", "headphone-10.png",
]

# One headphone icon per product tier (app/static/img/tiers/) -- the
# request explicitly said to pick 3 of the 10 headphone/earbud icons to
# represent Entry/Mid/Premium; chosen for a visual "step up" read (in-ear
# buds -> grey over-ear -> premium over-ear), not tied to game balance.
TIER_ICONS = {
    "Entry": "entry.png",
    "Mid": "mid.png",
    "Premium": "premium.png",
}


ICON_FIELDS = (
    ("avatar", AVATAR_CHOICES),
    ("badge", BADGE_CHOICES),
    ("product_icon", PRODUCT_CHOICES),
)


def pick_unused_icons(other_firms, rng=None):
    """{avatar, badge, product_icon} for a BOT, avoiding every icon already
    shown by another firm in the same game -- so a bot never wears the
    player's factory, logo or product, or another bot's.

    `other_firms` is anything with those three attributes (Firm rows, or
    ones not flushed yet). Each field is picked at random from what's still
    free; if a pool is ever exhausted (more than 10 firms in one game) it
    falls back to the least-used icons, so it degrades to "rarely repeats"
    rather than failing. Real teams still choose freely at registration --
    this only governs what a bot is given."""
    rng = rng or random
    icons = {}
    for field, pool in ICON_FIELDS:
        counts = {choice: 0 for choice in pool}
        for firm in other_firms:
            value = getattr(firm, field, None)
            if value in counts:
                counts[value] += 1
        fewest = min(counts.values())
        icons[field] = rng.choice([c for c in pool if counts[c] == fewest])
    return icons
