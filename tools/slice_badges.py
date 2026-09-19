"""Cut the ten brand badges out of the colour icon sheet.

Source: "Business Sim Brand Icons Color.png" -- ten icons, each inside a
thin cyan frame, on a flat dark-teal ground. Same ten marks in the same
order as the flat teal set they replace, so the output keeps the existing
logo-NN.png filenames and no stored firm.badge value has to change.

Two things this does NOT do, deliberately:

  * No flood fill. The factory sprites needed one to protect enclosed dark
    areas (a lit garage interior), but a logo's enclosed areas are holes --
    the gap inside the "O", the cuts in the hexagon -- and they have to key
    out. A distance-to-background ramp does that; a flood fill would leave
    a dark disc inside the O.
  * No stretch to fill. The old set was resized to exactly 128x128 from
    whatever its content bounding box was, which distorts anything that
    isn't square. Each icon here is fitted into the square canvas with its
    aspect ratio intact.

    python tools/slice_badges.py            # writes app/static/img/badges/
    python tools/slice_badges.py --contact  # plus a contact sheet to eyeball
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(r"C:\Users\ralle\Coding Projects\Graphic Tiles and Images"
           r"\Business Sim Graphics\Business Sim Brand Icons Color.png")
OUT = ROOT / "app" / "static" / "img" / "badges"

# Measured off the sheet by finding the cyan frame lines: ten cells on a
# ~211px pitch, interiors inset from the frame so no cyan bleeds in.
# Inset 12px inside each frame. The cyan frames throw a glow several pixels
# inward -- at a tighter inset it survives the key as a bright bar down the
# edge of the icon, and every icon came out with an identical 190x235
# bounding box because that bar, not the artwork, was defining it.
CELLS = [(42, 214), (254, 426), (465, 638), (677, 849), (889, 1062),
         (1101, 1274), (1313, 1486), (1525, 1698), (1738, 1911), (1953, 2127)]
TOP, BOTTOM = 236, 454

BG = np.array([5.0, 38.0, 47.0])
FADE_LO, FADE_HI = 14.0, 40.0     # "brighter than the background" alpha ramp
SIZE = 256
MARGIN = 0.04


def key_out(rgb):
    """Alpha from how much BRIGHTER than the background a pixel is, summed
    per channel -- not its distance from the background colour.

    Distance keeps drop shadows: a shadow is nowhere near the background
    colour, it is far below it, so every icon came out wearing a black
    smear. Measuring only the positive difference puts shadows at zero
    while the teal glow around an icon, which is genuinely brighter, still
    fades out softly."""
    pos = np.clip(rgb - BG, 0, None).sum(axis=-1)
    a = np.clip((pos - FADE_LO) / (FADE_HI - FADE_LO), 0.0, 1.0)
    return (a * 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheet = np.asarray(Image.open(SRC).convert("RGB")).astype(float)
    made = []
    for i, (x0, x1) in enumerate(CELLS, start=1):
        cell = sheet[TOP:BOTTOM, x0:x1]
        alpha = key_out(cell)
        rgba = np.dstack([cell.astype(np.uint8), alpha])

        ys, xs = np.where(alpha > 90)
        img = Image.fromarray(rgba, "RGBA").crop(
            (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

        inner = int(SIZE * (1 - 2 * MARGIN))
        scale = min(inner / img.width, inner / img.height)
        img = img.resize((max(1, round(img.width * scale)),
                          max(1, round(img.height * scale))), Image.LANCZOS)

        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        canvas.paste(img, ((SIZE - img.width) // 2, (SIZE - img.height) // 2), img)
        path = out_dir / f"logo-{i:02d}.png"
        canvas.save(path)
        made.append(canvas)
        print(f"{path.name}  content {img.width}x{img.height}")

    if args.contact:
        sheet_img = Image.new("RGBA", (10 * 140, 150), (20, 23, 26, 255))
        for i, c in enumerate(made):
            t = c.copy(); t.thumbnail((128, 128))
            sheet_img.paste(t, (i * 140 + (140 - t.width) // 2,
                                (150 - t.height) // 2), t)
        p = out_dir.parent / "_badge-contact.png"
        sheet_img.save(p)
        print("contact sheet ->", p)


if __name__ == "__main__":
    main()
