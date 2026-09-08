"""
The set of avatar images a team can pick at registration -- filenames under
app/static/img/avatars/ (Kenney "City Kit Industrial" preview renders, CC0).
Kept as a real list here, not inferred from whatever happens to be in the
folder, so adding/removing an image file can't silently change what's
selectable without a deliberate code change.
"""

AVATAR_CHOICES = [
    "building-a.png", "building-b.png", "building-c.png", "building-d.png",
    "building-e.png", "building-f.png", "building-g.png", "building-h.png",
    "building-i.png", "building-j.png", "building-k.png", "building-l.png",
    "building-m.png", "building-n.png", "building-o.png", "building-p.png",
    "building-q.png", "building-r.png", "building-s.png", "building-t.png",
    "chimney-basic.png", "chimney-large.png", "chimney-medium.png", "chimney-small.png",
    "detail-tank.png", "detail-tank-large.png",
    "shipping-container-a.png", "shipping-container-b.png", "shipping-container-c.png",
    "solar-panel-flat.png", "solar-panel-landscape.png", "solar-panel-portrait.png",
    "water-tower.png", "windmill.png", "windmill-low.png",
]
