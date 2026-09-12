"""Plot exploratory static coverage candidates; does not modify forcing data."""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    font = ImageFont.truetype("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf", 22)
    with np.load(args.directory / "target_masks.npz") as data:
        land, domain = data["land"], data["domain"]
        shape = (land.shape[0] // 4, land.shape[1] // 4)
        palette = np.array([[255, 255, 255], [245, 216, 198], [171, 171, 171], [33, 113, 181]], dtype=np.uint8)
        canvas = Image.new("RGB", (shape[1] * 3, shape[0] + 170), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((20, 10), "Exploratory NLDAS-2-based masks: native NWM grid orientation", fill="black", font=font)
        for index, (label, title) in enumerate((
            ("thermodynamic", "Active + thermodynamic coverage"),
            ("seven_meteorological", "Active + seven meteorological fields"),
            ("all_eight", "Active + all eight fields"),
        )):
            candidate = data["candidate__" + label]
            # Maximum reduction preserves narrow retained-water features in this overview.
            def coarse(mask):
                return mask.reshape(mask.shape[0] // 4, 4, mask.shape[1] // 4, 4).any(axis=(1, 3))

            display = np.zeros(coarse(domain).shape, dtype=np.uint8)
            display[coarse(domain & ~candidate)] = 1
            display[coarse(land)] = 2
            display[coarse(candidate & ~land)] = 3
            canvas.paste(Image.fromarray(palette[display[::-1]]), (index * shape[1], 80))
            draw.text((index * shape[1] + 20, 50), title, fill="black", font=font)
        draw.text((20, shape[0] + 95),
                  "Gray: active (always retained) | Blue: inactive retained | Peach: inactive excluded inside NLDAS-2", fill="black", font=font)
        draw.text((20, shape[0] + 125), "4x4 overview preserves small retained features. Candidates are not applied to production.", fill="black", font=font)
        canvas.save(args.directory / "candidate_masks.png")


if __name__ == "__main__":
    main()
