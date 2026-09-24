#!/usr/bin/env python3
"""Draw the Read Smoother app icon (no text: an open book with sound waves on a warm gradient)
and write app/icon.icns plus app/icon_preview.png. Needs Pillow and macOS iconutil."""
import subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter

here = Path(__file__).resolve().parent
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# --- background: macOS-style rounded square, vertical gradient, soft light at the top
inset, radius = 96, 200
top, bottom = (240, 152, 102), (176, 72, 40)
grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(grad)
for y in range(inset, S - inset):
    f = (y - inset) / (S - 2 * inset)
    c = tuple(round(top[i] * (1 - f) + bottom[i] * f) for i in range(3))
    gd.line([(inset, y), (S - inset, y)], fill=c + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((inset, inset, S - inset, S - inset), radius=radius, fill=255)
grad.putalpha(ImageChops.multiply(grad.getchannel("A"), mask))
img.alpha_composite(grad)

light = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ld = ImageDraw.Draw(light)
for y in range(inset, inset + 440):
    a = round(64 * (1 - (y - inset) / 440) ** 2)
    ld.line([(inset, y), (S - inset, y)], fill=(255, 255, 255, a))
light.putalpha(ImageChops.multiply(light.getchannel("A"), mask))
img.alpha_composite(light)

# --- glyph: an open book (two cream pages tilted toward the spine) with a soft shadow
dx = -28
cream = (255, 247, 236, 255)
page_l = [(266 + dx, 386), (496 + dx, 418), (496 + dx, 722), (266 + dx, 690)]
page_r = [(526 + dx, 418), (756 + dx, 386), (756 + dx, 690), (526 + dx, 722)]
shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
for poly in (page_l, page_r):
    sd.polygon([(x, y + 20) for x, y in poly], fill=(70, 20, 5, 120))
shadow = shadow.filter(ImageFilter.GaussianBlur(24))
shadow.putalpha(ImageChops.multiply(shadow.getchannel("A"), mask))
img.alpha_composite(shadow)
d = ImageDraw.Draw(img)
for poly in (page_l, page_r):
    d.polygon(poly, fill=cream)
d.line([(511 + dx, 404), (511 + dx, 730)], fill=(214, 120, 84, 255), width=10)   # spine crease
tan = (232, 205, 176, 255)
for k in range(4):
    y0 = 458 + k * 56
    d.line([(298 + dx, y0 + 10), (464 + dx, y0 + 10 + 23)], fill=tan, width=15)
    d.line([(558 + dx, y0 + 33), (724 + dx, y0 + 10)], fill=tan, width=15)

# --- sound waves leaving the right page
cx, cy = 752 + dx, 554
for rad in (82, 142, 202):
    d.arc((cx - rad, cy - rad, cx + rad, cy + rad), start=-34, end=34, fill=cream, width=28)

img.save(here / "icon_preview.png")
iconset = Path(tempfile.mkdtemp()) / "icon.iconset"
iconset.mkdir()
for s in (16, 32, 64, 128, 256, 512, 1024):
    img.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
    if s <= 512:
        img.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(here / "icon.icns")], check=True)
print("wrote", here / "icon.icns")
