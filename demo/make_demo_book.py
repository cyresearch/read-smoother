#!/usr/bin/env python3
"""Typeset a Project Gutenberg plain-text book into a PDF with an outline (for the demo/screenshots).

Usage: python demo/make_demo_book.py pg37134.txt demo/The-Elements-of-Style.pdf
Needs Google Chrome (headless) on the machine.
"""
import html, re, subprocess, sys, tempfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
raw = src.read_text(encoding="utf-8")
body = raw[raw.index("\n", raw.index("*** START OF")) + 1: raw.index("*** END OF")]
body = re.sub(r"_(.+?)_", r"\1", body)                     # Gutenberg italics markers
# skip the book's own contents listing (up to the first chapter heading)
m = re.search(r"^(?:CONTENTS)\s*$", body, re.M)
if m:
    first = re.search(r"^I\.\s+[A-Z][A-Z ,'\-]+$", body[m.end():], re.M)
    if first:
        body = body[:m.start()] + body[m.end() + first.start():]

out_html, para = [], []
def flush():
    if para:
        txt = " ".join(x.strip() for x in para).strip()
        if txt:
            out_html.append(f"<p>{html.escape(txt)}</p>")
        para.clear()
for ln in body.split("\n"):
    s = ln.strip()
    if not s:
        flush(); continue
    if re.fullmatch(r"[IVX]+\.\s+[A-Z][A-Z ,'\-]+", s) and len(s) < 70:
        num, rest = s.split(".", 1)
        flush(); out_html.append(f"<h1>{html.escape(num + '.' + rest.title())}</h1>"); continue
    if not para and re.fullmatch(r"\d{1,2}\.\s+[A-Z].{4,110}", s):
        flush(); out_html.append(f"<h2>{html.escape(s)}</h2>"); continue
    para.append(s)
flush()

doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>The Elements of Style</title>
<style>
@page {{ size: 6in 9in; margin: 0.8in 0.75in; }}
body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 11.5pt; line-height: 1.45; color: #111; }}
h1 {{ font-size: 20pt; margin: 0 0 14pt; page-break-before: always; }}
h2 {{ font-size: 13.5pt; margin: 16pt 0 6pt; }}
p {{ margin: 0 0 8pt; text-align: justify; }}
.tp {{ text-align: center; margin-top: 2.2in; page-break-after: always; }} .tp h1 {{ page-break-before: auto; font-size: 28pt; }}
.tp p {{ text-align: center; color: #444; }}
</style></head><body>
<div class="tp"><h1>The Elements of Style</h1><p>William Strunk Jr.</p>
<p>1918 edition, public domain.<br>Text from Project Gutenberg eBook #37134.<br>Typeset for the Read Smoother demo.</p></div>
{chr(10).join(out_html)}
</body></html>"""
tmp = Path(tempfile.mkdtemp()) / "book.html"
tmp.write_text(doc, encoding="utf-8")
chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--generate-pdf-document-outline",
                f"--print-to-pdf={out.resolve()}", tmp.as_uri()], check=True, capture_output=True)
# Chrome joins wrapped heading lines without a space in the outline; restore the real titles.
import pymupdf
headings = [re.sub(r"<[^>]+>", "", x) for x in out_html if x.startswith("<h")]
headings = [html.unescape(h) for h in headings]
by_key = {re.sub(r"\s+", "", h): h for h in headings}
doc = pymupdf.open(str(out))
toc = doc.get_toc()
for entry in toc:
    key = re.sub(r"\s+", "", entry[1])
    for k, h in by_key.items():
        if k.startswith(key) or key.startswith(k):
            entry[1] = h
            break
doc.set_toc(toc)
doc.save(str(out) + ".tmp", garbage=3, deflate=True)
doc.close()
Path(str(out) + ".tmp").replace(out)
print("wrote", out, "| h1:", sum(x.startswith("<h1>") for x in out_html), "h2:", sum(x.startswith("<h2>") for x in out_html))
