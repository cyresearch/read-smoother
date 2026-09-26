#!/usr/bin/env python3
"""Read Smoother (畅读): read a PDF while a neural voice reads it to you.

Usage:
    python reader.py                      # open the shelf (books/ + Zotero search)
    python reader.py path/to/book.pdf     # open one PDF directly
    python reader.py --config my.json --data-dir ./data-demo --port 8799

Sentences are read aloud with Microsoft's neural voices (edge-tts). Highlights and
notes are written to Markdown files (an Obsidian vault if configured):
  - books  -> <notes root>/<notes_folder>/<title>.md
  - papers -> <notes root>/<paper_notes_folder>/@citekey.md (an existing note gets
              a managed section appended; everything else in the file is left alone)
Geometry (page, rectangles) lives in data/<slug>.json. The Markdown file is the
single source of truth for note text: edits made in Obsidian show up on reload.
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pymupdf

try:
    import edge_tts
except ImportError:  # pragma: no cover
    sys.exit("请先 pip install edge-tts pymupdf")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
CACHE = ROOT / "cache"
DATA = ROOT / "data"        # overridable with --data-dir
BOOKS = ROOT / "books"
CONFIG_FILE = ROOT / "config.json"

# Every key here can be overridden in config.json (see config.example.json).
DEFAULT_CONFIG = {
    "language": "en",                 # "en" or "zh": default UI language and the language of generated note headings
    "vault_path": "",                 # Obsidian vault (or any folder) that receives the notes; "" = ./notes
    "vault_name": "",                 # Obsidian vault name, used for obsidian:// links ("" = no links)
    "notes_folder": "Reading",        # subfolder for book notes
    "paper_notes_folder": "Papers",   # subfolder for paper notes (@citekey.md)
    "book_note_template": "templates/book_note.md",
    "paper_note_template": "templates/paper_note.md",
    "bib_path": "",                   # Better BibTeX auto-export of your Zotero library ("" = no Zotero search)
    "default_voice": "en-US-AriaNeural",
    "tts_locales": ["en"],            # which voices to list (locale prefixes)
    "ai_read_dirs": [],               # folders the AI may read (read-only); paths typed into a question are added on the fly
    "profile_path": "profile.md",     # a short note about you, injected into the AI's system prompt
}


def load_config(path=None):
    """Read config.json (created from config.example.json on first run). Never rewrites the user's file."""
    cfg_file = Path(path).expanduser().resolve() if path else CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    if not cfg_file.exists() and cfg_file == CONFIG_FILE and (ROOT / "config.example.json").exists():
        cfg_file.write_text((ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    if cfg_file.exists():
        cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
    return cfg


def cfg_path(cfg, key, default=None):
    """Resolve a path-valued config key relative to the project root; '' -> default."""
    v = cfg.get(key) or default
    if not v:
        return None
    pth = Path(os.path.expanduser(str(v)))
    return pth if pth.is_absolute() else (ROOT / pth)


def render_template(text: str, vars: dict) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(vars.get(m.group(1), "")), text)


# Strings that end up in generated Markdown, by language.
NOTE_TEXT = {
    "en": {
        "section": "## Highlights & notes",
        "callout": "> [!info]- This section is maintained by Read Smoother\n"
                   "> Edit the note text under each quote freely; add or remove quotes from the reading page. Keep the `%% ... %%` marker lines.",
        "no_toc": "(no table of contents)",
    },
    "zh": {
        "section": "## 摘录与感想",
        "callout": "> [!info]- 这一节由畅读自动维护\n"
                   "> 每条摘录下面的感想可以直接在这里改；摘录本身请在阅读页面里删改。标记行 `%% ... %%` 请保留。",
        "no_toc": "（没有目录）",
    },
}


def note_text(cfg, key):
    return NOTE_TEXT.get(cfg.get("language", "en"), NOTE_TEXT["en"])[key]


# ----------------------------------------------------------------------------
# 文本抽取：把每页切成句子，并保留每句在页面上的行矩形
# ----------------------------------------------------------------------------

ABBREV = {"e.g", "i.e", "etc", "vs", "cf", "dr", "mr", "mrs", "ms", "prof", "fig",
          "no", "vol", "pp", "p", "al", "ch", "ed", "eds", "st", "jr", "sr"}
END_PUNCT = ".!?"
CLOSERS = "\"'”’)]"


FUNC_WORDS = {"the", "a", "an", "of", "to", "in", "on", "at", "and", "or", "but", "for", "with", "by", "from",
              "as", "that", "which", "is", "are", "was", "were", "be", "not", "than", "this", "these", "their",
              "its", "his", "her", "our", "your", "into", "about", "between", "when", "if", "because", "while"}


class Sentencizer:
    def __init__(self, doc: pymupdf.Document):
        self._toks = {}
        self.doc = doc
        self._raw = {}
        self._cache = {}

    # -- 跨页拼句 --------------------------------------------------------------
    @staticmethod
    def _first_readable(p):
        return next((s for s in p["sentences"] if not s["skip"]), None)

    @staticmethod
    def _last_readable(p):
        return next((s for s in reversed(p["sentences"]) if not s["skip"]), None)

    @staticmethod
    def _spans_pages(prev_text, next_text):
        """上一页末句是否没结束、要接到下一页开头。"""
        a = prev_text.rstrip()
        b = next_text.lstrip()
        if not a or not b:
            return False
        if a.rstrip(CLOSERS)[-1:] in END_PUNCT or a.endswith(":"):
            return False
        if b[:1].islower():
            return True
        if a.endswith(("-", "\u00ad", ",", ";")):
            return True
        last = re.sub(r"[^A-Za-z]", "", a.split()[-1]).lower()
        return last in FUNC_WORDS

    @staticmethod
    def _join(a, b):
        a = a.rstrip()
        if a.endswith(("-", "\u00ad")) and b[:1].islower():
            return a.rstrip("-\u00ad") + b.lstrip()
        return a + " " + b.lstrip()

    def page(self, n: int):
        """带跨页拼句的结果：末句若跨页，text 为完整句并标 continues；
        首句若是上一页的尾巴，text 也为完整句并标 continued（顺序朗读时跳过）。"""
        if n in self._cache:
            return self._cache[n]
        import copy
        out = copy.deepcopy(self._raw_page(n))
        last = self._last_readable(out)
        if last and n + 1 < self.doc.page_count:
            nxt = self._first_readable(self._raw_page(n + 1))
            if nxt and self._spans_pages(last["text"], nxt["text"]):
                last["text"] = self._join(last["text"], nxt["text"])
                last["continues"] = True
        first = self._first_readable(out)
        if first and n > 0:
            prv = self._last_readable(self._raw_page(n - 1))
            if prv and self._spans_pages(prv["text"], first["text"]):
                first["text"] = self._join(prv["text"], first["text"])
                first["continued"] = True
        self._cache[n] = out
        return out

    def _raw_page(self, n: int):
        if n in self._raw:
            return self._raw[n]
        pg = self.doc[n]
        words = pg.get_text("words")  # x0,y0,x1,y1,word,block,line,wordno
        sizes = self._line_sizes(pg)
        tokens = self._merge_hyphens(words)
        for t in tokens:
            t["size0"] = sizes.get((t["rects"][0][4], t["rects"][0][5]), 0)
            t["size1"] = sizes.get((t["rects"][-1][4], t["rects"][-1][5]), 0)
        sentences = self._split(tokens)
        sentences = self._mark_skips(sentences, pg.rect)
        words = []
        self._toks[n] = {}
        for sent in sentences:
            self._toks[n][sent["id"]] = [(t["off"], t["text"], t["rects"]) for t in sent["_toks"]]
            for t in sent.pop("_toks"):
                if re.search(r"[A-Za-z]", t["text"]):
                    for r in t["rects"]:
                        words.append([round(r[0], 1), round(r[1], 1), round(r[2], 1), round(r[3], 1), t["text"], sent["id"], t["off"]])
        out = {"page": n + 1, "width": pg.rect.width, "height": pg.rect.height, "sentences": sentences, "words": words}
        self._raw[n] = out
        return out

    @staticmethod
    def _line_sizes(pg):
        """(block, line) -> 该行最大字号，用来识别标题。"""
        sizes = {}
        for b in pg.get_text("dict")["blocks"]:
            for li, line in enumerate(b.get("lines", [])):
                mx = max((sp["size"] for sp in line.get("spans", []) if sp["text"].strip()), default=0)
                sizes[(b["number"], li)] = round(mx, 1)
        return sizes

    @staticmethod
    def _merge_hyphens(words):
        """把行尾连字符（含软连字符 U+00AD）断开的词合并成一个 token。"""
        toks = []
        i = 0
        while i < len(words):
            x0, y0, x1, y1, w, b, l, _ = words[i]
            rects = [(x0, y0, x1, y1, b, l)]
            text = w
            while (text.endswith("­") or (text.endswith("-") and len(text) > 1)) and i + 1 < len(words):
                nxt = words[i + 1]
                same_block_next_line = nxt[5] == b and nxt[6] != l
                if not (same_block_next_line and nxt[4][:1].islower()):
                    break
                text = text.rstrip("­-") + nxt[4]
                rects.append((nxt[0], nxt[1], nxt[2], nxt[3], nxt[5], nxt[6]))
                l = nxt[6]
                i += 1
            text = text.replace("­", "").replace(" ", " ").strip()
            if text:
                toks.append({"text": text, "rects": rects, "block": b})
            i += 1
        return toks

    def _split(self, toks):
        sentences = []
        cur = []

        def flush():
            if cur:
                sentences.append(self._make_sentence(cur))
                cur.clear()

        for idx, t in enumerate(toks):
            cur.append(t)
            nxt = toks[idx + 1] if idx + 1 < len(toks) else None
            if nxt is None:
                flush()
                continue
            if self._is_boundary(t["text"], nxt["text"]):
                flush()
                continue
            if abs(t["size1"] - nxt["size0"]) > 0.6:
                flush()
                continue
            if nxt["block"] != t["block"]:
                # 块边界：段落以标点结尾，或当前块很短（标题），就断句
                blk_len = sum(1 for x in cur if x["block"] == t["block"])
                if t["text"].rstrip(CLOSERS)[-1:] in END_PUNCT + ":;" or blk_len < 12:
                    flush()
        return sentences

    @staticmethod
    def _is_boundary(word, nxt):
        core = word.rstrip(CLOSERS)
        if not core or core[-1] not in END_PUNCT:
            return False
        if core[-1] == ".":
            stem = core[:-1].lower().lstrip("(\"'“‘")
            if stem in ABBREV or (len(stem) == 1 and stem.isalpha()):
                return False
            if re.fullmatch(r"\d+", stem) and nxt[:1].isdigit():
                return False
        first = nxt.lstrip("(\"'“‘[")[:1]
        return first.isupper() or first.isdigit() or nxt[:1] in "\"'“‘(["

    @staticmethod
    def _make_sentence(toks):
        parts, offs, pos = [], [], 0
        for t in toks:
            tt = re.sub(r"\s+", " ", t["text"]).strip()
            offs.append(pos)
            parts.append(tt)
            pos += len(tt) + 1
        text = " ".join(parts)
        lines = {}
        for t in toks:
            for x0, y0, x1, y1, b, l in t["rects"]:
                key = (b, l)
                if key in lines:
                    r = lines[key]
                    lines[key] = [min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1)]
                else:
                    lines[key] = [x0, y0, x1, y1]
        rects = [[round(v, 2) for v in r] for r in lines.values()]
        toks_out = [{"text": parts[i], "off": offs[i], "rects": [r[:4] for r in t["rects"]]} for i, t in enumerate(toks)]
        return {"text": text, "rects": rects, "_toks": toks_out}

    @staticmethod
    def _mark_skips(sentences, rect):
        """页眉页脚、纯数字等不朗读。"""
        out = []
        for i, s in enumerate(sentences):
            skip = False
            txt = s["text"]
            if not re.search(r"[A-Za-z]", txt):
                skip = True
            elif len(s["rects"]) == 1:
                y = s["rects"][0][1]
                near_edge = y < rect.height * 0.09 or y > rect.height * 0.92
                looks_header = re.match(r"^\d{1,4}\s+\S", txt) or re.search(r"\s\d{1,4}$", txt)
                if near_edge and looks_header and len(txt.split()) <= 12:
                    skip = True
            out.append({"id": i, "text": txt, "rects": s["rects"], "skip": skip, "_toks": s["_toks"]})
        return out


COVER_MARKERS = ("additional services and information for", "the online version of this article", "downloaded from",
                 "contents lists available at", "author's personal copy", "this content downloaded", "terms and conditions of use",
                 "please scroll down for article", "to link to this article", "how to cite this article")
JUNK_LINE = re.compile(r"(doi:|https?://|www\.|@|©|\bemail\b|e-mail|published by|version of record|corresponding author|"
                       r"all rights reserved|reprints and permission|^[–\-•·]\s|^\d{1,2}\s+[A-Z][a-z]+\s+\d{4}$)", re.I)


def is_cover_page(text):
    """出版社加的封面/下载页（SAGE、Elsevier、JSTOR……）：标题之外全是链接和说明，猜目录时整页跳过。"""
    t = text.lower()
    return sum(m in t for m in COVER_MARKERS) >= 2 or ("downloaded from" in t and len(t.split()) < 120)


def heuristic_toc(doc, max_pages=400):
    """PDF 没有书签时，按字号 / 字体（粗体、异体）从版面里猜标题（论文常用）。"""
    from collections import Counter
    lines = []
    weight = Counter()
    for pno in range(min(doc.page_count, max_pages)):
        if pno < 3 and is_cover_page(doc[pno].get_text()):
            continue
        for b in doc[pno].get_text("dict")["blocks"]:
            ls = b.get("lines", [])
            for li, ln in enumerate(ls):
                spans = [sp for sp in ln.get("spans", []) if sp["text"].strip()]
                if not spans:
                    continue
                text = re.sub(r"\s+", " ", " ".join(sp["text"].strip() for sp in spans)).strip()
                text = re.sub(r"&[a-z0-9]+;", " ", text).strip()
                size = round(max(sp["size"] for sp in spans), 1)
                fonts = {sp["font"] for sp in spans}
                bold = all(("bold" in f.lower() or "-bd" in f.lower() or "black" in f.lower()) for f in fonts)
                italic = all(("italic" in f.lower() or "oblique" in f.lower()) for f in fonts)
                weight[(size, min(fonts))] += len(text)
                lines.append(dict(page=pno + 1, y=ln["bbox"][1], size=size, fonts=fonts, bold=bold, italic=italic,
                                  text=text, block=b["number"], li=li, nlines=len(ls)))
    if not lines:
        return []
    (body_size, body_font), _ = weight.most_common(1)[0]
    body_family = body_font.split("-")[0].split(",")[0].lower()

    def looks_heading(L):
        t = L["text"]
        if not (3 <= len(t) <= 110) or not re.search(r"[A-Za-z]", t) or len(t.split()) > 14:
            return False
        if t[:1].islower() or t.endswith((",", ";")):
            return False
        if t.endswith(".") and not re.match(r"^\d+(\.\d+)*\.?\s", t):
            return False
        if re.match(r"^(figure|fig\.|table|note[s]?\b)\s*\d*", t, re.I):
            return False
        if t.endswith(":") or JUNK_LINE.search(t):
            return False
        if L["size"] < body_size - 1.5:
            return False
        if L["size"] >= body_size + 1.0:
            return True
        other_family = all(f.split("-")[0].split(",")[0].lower() != body_family for f in L["fonts"])
        return L["bold"] or (other_family and L["li"] == 0) or (L["italic"] and L["nlines"] <= 2)

    cands = [L for L in lines if looks_heading(L)]
    merged = []
    for c in cands:
        m = merged[-1] if merged else None
        if m and m["page"] == c["page"] and m["block"] == c["block"] and m["size"] == c["size"] \
                and m["bold"] == c["bold"] and c["li"] == m["li"] + 1 and c["y"] - m["y"] < c["size"] * 2.2:
            m["text"] += " " + c["text"]
            m["li"], m["y"] = c["li"], c["y"]
        else:
            merged.append(dict(c))
    freq = Counter(m["text"] for m in merged)
    merged = [m for m in merged if freq[m["text"]] < 3 and len(m["text"].split()) <= 22
              and not re.search(r"\(continued\)\s*$", m["text"], re.I)]
    # 级别：先按字号，再按 粗体 > 其他字体 > 斜体
    def rank(m):
        return (-m["size"], 0 if m["bold"] else (2 if m["italic"] else 1))
    # 同一页上同一样式冒出 5 条以上，多半是表格的列名 / 单元格，不是标题
    per_page = Counter((m["page"], rank(m)) for m in merged)
    merged = [m for m in merged if per_page[(m["page"], rank(m))] < 5]
    if not merged:
        return []
    ranks = sorted({rank(m) for m in merged})
    while len(merged) > 160 and len(ranks) > 1:
        drop = ranks.pop()
        merged = [m for m in merged if rank(m) != drop]
    level_of = {r: min(i + 1, 3) for i, r in enumerate(ranks)}
    return [{"level": level_of[rank(m)], "title": m["text"], "page": m["page"], "auto": True} for m in merged]


REF_HEADING = re.compile(r"^\s*(references?|reference list|bibliography|works cited|literature cited|参考文献|引用文献|文献)\s*$", re.I)
YEAR_RE = re.compile(r"\b((?:1[6-9]|20)\d{2})([a-z])?\b")
NAME_PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "ten", "ter", "te", "el", "al", "bin", "ibn", "mac", "mc", "st", "o"}


def _fold(w):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c)).lower().replace("’", "'")


def _ref_lines(doc, start_page):
    """Layout lines of the reference pages: page, x0, y0, size, text (headers and footers dropped)."""
    out = []
    for pno in range(start_page, doc.page_count):
        pg = doc[pno]
        H = pg.rect.height
        for b in pg.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                spans = [sp for sp in ln.get("spans", []) if sp["text"].strip()]
                if not spans:
                    continue
                y0 = ln["bbox"][1]
                if y0 < H * 0.08 or y0 > H * 0.93:
                    continue
                text = re.sub(r"\s+", " ", " ".join(sp["text"] for sp in spans)).strip()
                bold = all(re.search(r"bold|-bd|black|heavy", sp["font"], re.I) for sp in spans)
                out.append(dict(page=pno + 1, x0=ln["bbox"][0], y0=y0, size=round(max(sp["size"] for sp in spans), 1), text=text, bold=bold))
    return out


def find_references(doc, toc):
    """Page index (0-based) where the reference list starts, or None."""
    for t in toc:
        if REF_HEADING.match(t["title"]):
            return t["page"] - 1
    pages = [pno for pno in range(doc.page_count) if any(REF_HEADING.match(l) for l in doc[pno].get_text().splitlines())]
    if not pages:
        return None
    start = pages[-1]          # the last run of consecutive pages (a running header repeats on every page of the list)
    while start - 1 in pages:
        start -= 1
    return start


def parse_ref_entry(text):
    """Split one reference into authors / year / title / rest (APA-style, best effort)."""
    m = (re.search(r"\((\d{4}[a-z]?|n\.d\.|in press)(?:,\s*[^)]{1,24})?\)[.,:]?", text)   # (2000) / (2000, November) / (n.d.)
         or re.search(r"(?<=[.,])\s(\d{4}[a-z]?)[.,:]\s", text))
    if not m:
        return None
    year = m.group(1)
    authors = text[:m.start()].strip().rstrip(",").strip()
    rest = text[m.end():].strip()
    tm = re.match(r"(.+?[^A-Z]\.|.+?[?!])\s+(.*)$", rest)
    title, source = (tm.group(1).strip(), tm.group(2).strip()) if tm else (rest, "")
    surnames = [_fold(w) for w in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+", authors)
                if len(w) > 1 and w.lower() not in ("and", "eds", "ed", "et", "al")]
    first = [_fold(w) for w in re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+", authors.split(",")[0])]
    first = [w for w in first if len(w) > 1] or surnames[:1]
    return {"authors": authors, "year": year, "title": title, "source": source, "text": text,
            "_first": first[-1] if first else "", "_names": set(surnames)}


def parse_references(doc, toc):
    """Reference list entries, found by the hanging indent of each entry (falls back to splitting on author patterns)."""
    start = find_references(doc, toc)
    if start is None:
        return start, None, []
    lines = _ref_lines(doc, start)
    idx = next((i for i, L in enumerate(lines) if L["page"] == start + 1 and REF_HEADING.match(L["text"])), None)
    heading_y = lines[idx]["y0"] if idx is not None else 0
    if idx is not None:   # drop what sits above the heading (blocks are not always in top-down order)
        lines = [L for L in lines if L["page"] > start + 1 or L["y0"] > heading_y]
    if not lines:
        return start, heading_y, []
    from collections import Counter
    body = Counter(round(L["size"]) for L in lines).most_common(1)[0][0]
    kept = []
    for L in lines:
        if re.fullmatch(r"(\d+\s+)?(references?|bibliography|works cited)(\s+\d+)?", L["text"], re.I):
            continue   # running header of the reference pages
        heading = (L["size"] >= body + 0.9 or L["bold"]) and len(L["text"]) < 80 and not YEAR_RE.search(L["text"])
        if kept and (heading or re.match(r"^(appendix|appendices|author biograph|supplement|notes?)\b", L["text"], re.I)):
            break   # next section (Appendix, Author biographies…)
        if abs(L["size"] - body) <= 1.2:
            kept.append(L)
    # columns: cluster x0; the smallest x0 of a column is where entries start, deeper x0 is the hanging indent
    starts = []
    for pno in sorted({L["page"] for L in kept}):
        pl = [L for L in kept if L["page"] == pno]
        xs = sorted(set(round(L["x0"]) for L in pl))
        cols = [[xs[0]]] if xs else []
        for x in xs[1:]:
            if x - cols[-1][-1] < 60:
                cols[-1].append(x)
            else:
                cols.append([x])
        col_min = {}
        for c in cols:
            cnt = Counter(round(L["x0"]) for L in pl if round(L["x0"]) in c)
            base = min((x for x in c if cnt[x] >= 2), default=c[0])
            for x in c:
                col_min[x] = base
        pl.sort(key=lambda L: (col_min[round(L["x0"])], L["y0"]))
        for L in pl:
            L["start"] = round(L["x0"]) <= col_min[round(L["x0"])] + 2.5
            starts.append(L)
    entries, cur = [], ""
    for L in starts:
        if L["start"] and cur:
            entries.append(cur)
            cur = ""
        if cur.endswith("-") and L["text"][:1].islower():
            cur = cur[:-1] + L["text"]
        else:
            cur = (cur + " " + L["text"]).strip()
    if cur:
        entries.append(cur)
    parsed = [e for e in (parse_ref_entry(t) for t in entries) if e]
    n_start = sum(1 for L in starts if L["start"])
    if n_start < 3 or n_start >= 0.9 * len(starts):   # no usable hanging indent: split the flat text on "Surname, I." starts
        flat = " ".join(L["text"] for L in kept)
        parts = re.split(r"(?<=[.)])\s+(?=[A-Z][A-Za-zÀ-ÿ'’\-]+,\s(?:[A-Z]\.|[A-Z][a-z]+\s[A-Z]\.))", flat)
        parsed = [e for e in (parse_ref_entry(t) for t in parts) if e]
    for i, e in enumerate(parsed):
        e["id"] = i
    return start, heading_y, parsed


def find_citations(text, refs_by_year):
    """In-text citations in one sentence -> [(start, end, [ref ids])], matched on first-author surname + year."""
    found = {}
    for m in YEAR_RE.finditer(text):
        year, letter = m.group(1), m.group(2) or ""
        cands = refs_by_year.get(year + letter) or refs_by_year.get(year) or []
        if not cands:
            continue
        pre = text[:m.start()]
        narrative = pre.rstrip().endswith("(")
        if narrative:
            pre = pre.rstrip()[:-1]
        cut = max(pre.rfind(";"), pre.rfind("("), pre.rfind(")"))
        seg_start = cut + 1
        seg = pre[seg_start:]
        if len(seg) > 90:
            seg_start += len(seg) - 90
            seg = seg[-90:]
        toks = [(mm.start() + seg_start, _fold(mm.group(0))) for mm in re.finditer(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]+", seg)]
        hits = []
        for e in cands:
            pos = [p for p, w in toks if w == e["_first"]]
            if not pos and e["_names"]:
                pos = [p for p, w in toks if w in e["_names"] and len(w) > 2]
            if pos:
                hits.append((pos[-1], e["id"]))
        if not hits:
            continue
        first_pos = min(p for p, _ in hits)
        hits = [i for p, i in hits if p == first_pos] or [i for _, i in hits]   # the first author is the leftmost name
        # pull leading name particles ("van", "de") into the span
        while True:
            pm = re.search(r"(\S+)\s+$", text[:first_pos])
            if pm and _fold(pm.group(1)) in NAME_PARTICLES:
                first_pos = pm.start(1)
            else:
                break
        key = next((k for k in found if k[0] == first_pos or (k[0] <= first_pos < k[1])), None)
        if key:
            st, en, ids = key[0], max(key[1], m.end()), found.pop(key)
            found[(st, en)] = ids + [i for i in hits if i not in ids]
        else:
            found[(first_pos, m.end())] = hits
    return [(k[0], k[1], v) for k, v in sorted(found.items())]


def build_toc(doc):
    """清理 PDF 书签：去掉 Word 残留锚点，把只有数字的章号和下一条章名合并。没有书签就猜一个。"""
    raw = [t for t in doc.get_toc() if not t[1].startswith("_Hlk")]
    if not raw:
        return heuristic_toc(doc)
    out = []
    i = 0
    while i < len(raw):
        lvl, title, page = raw[i]
        title = re.sub(r"\s+", " ", title.replace(" ", " ")).strip()
        if re.fullmatch(r"\d+", title) and i + 1 < len(raw) and raw[i + 1][0] == lvl + 1:
            nxt = raw[i + 1]
            title = f"{title} {re.sub(r'\\s+', ' ', nxt[1])}".strip()
            out.append({"level": lvl, "title": title, "page": nxt[2]})
            i += 2
            continue
        out.append({"level": lvl, "title": title, "page": page})
        i += 1
    levels = sorted({t["level"] for t in out})
    remap = {l: i + 1 for i, l in enumerate(levels)}
    for t in out:
        t["level"] = remap[t["level"]]
    return out


# ----------------------------------------------------------------------------
# Zotero：解析 Better BibTeX 自动导出的 .bib
# ----------------------------------------------------------------------------

class BibIndex:
    def __init__(self, path: str):
        self.path = Path(path) if path else None
        self._mtime = None
        self.entries = []

    def _ensure(self):
        if not self.path or not self.path.exists():
            self.entries = []
            return
        m = self.path.stat().st_mtime
        if m != self._mtime:
            self.entries = self._parse(self.path.read_text(encoding="utf-8", errors="replace"))
            self._mtime = m

    @staticmethod
    def _clean(v: str) -> str:
        v = v.strip()
        v = re.sub(r"[{}]", "", v)
        v = re.sub(r"\\(enspace|thinspace|quad|qquad|,|;)", " ", v)
        v = re.sub(r"\\[a-zA-Z]+\s*", "", v)
        v = v.replace("\\&", "&").replace("--", "–").replace("$", "")
        return re.sub(r"\s+", " ", v).strip()

    @staticmethod
    def _balanced(s, start):
        """s[start] == '{'；返回配对右括号的下标（不含）。"""
        d, k = 1, start + 1
        while k < len(s) and d:
            if s[k] == "{":
                d += 1
            elif s[k] == "}":
                d -= 1
            k += 1
        return k

    def _parse(self, text):
        entries = []
        pos = 0
        while True:
            m = re.search(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text[pos:])
            if not m:
                break
            typ, key = m.group(1).lower(), m.group(2)
            open_brace = pos + m.start() + m.group(0).index("{")
            end = self._balanced(text, open_brace)
            body = text[pos + m.end():end - 1]
            pos = end
            if typ in ("comment", "preamble", "string"):
                continue
            fields = {}
            j = 0
            while j < len(body):
                fm = re.match(r"\s*(\w+)\s*=\s*", body[j:])
                if not fm:
                    break
                name = fm.group(1).lower()
                j += fm.end()
                if j < len(body) and body[j] == "{":
                    k = self._balanced(body, j)
                    val = body[j + 1:k - 1]
                    j = k
                elif j < len(body) and body[j] == '"':
                    k = body.find('"', j + 1)
                    val = body[j + 1:k]
                    j = k + 1
                else:
                    k = body.find(",", j)
                    if k < 0:
                        k = len(body)
                    val = body[j:k]
                    j = k
                fields[name] = val
                cm = re.match(r"\s*,", body[j:])
                if cm:
                    j += cm.end()
            files = [f.strip() for f in fields.get("file", "").split(";") if f.strip().lower().endswith(".pdf")]
            files = [f for f in files if os.path.exists(f)]
            authors = [a.strip() for a in re.split(r"\s+and\s+", self._clean(fields.get("author", "") or fields.get("editor", "")))
                       if a.strip()]
            lasts = [a.split(",")[0].strip() if "," in a else a.split()[-1] for a in authors]
            if len(lasts) == 0:
                short = "?"
            elif len(lasts) == 1:
                short = lasts[0]
            elif len(lasts) == 2:
                short = f"{lasts[0]} & {lasts[1]}"
            else:
                short = f"{lasts[0]} et al."
            year = self._clean(fields.get("year", "") or fields.get("date", "")[:4])
            entries.append({
                "citekey": key, "type": typ,
                "title": self._clean(fields.get("title", "")),
                "shorttitle": self._clean(fields.get("shorttitle", "")),
                "authors": authors, "short": short, "year": year,
                "journal": self._clean(fields.get("journal", "") or fields.get("booktitle", "") or fields.get("publisher", "")),
                "doi": self._clean(fields.get("doi", "")),
                "url": self._clean(fields.get("url", "")),
                "abstract": self._clean(fields.get("abstract", ""))[:2000],
                "files": files,
            })
        return entries

    def search(self, q: str, limit=40):
        self._ensure()
        toks = [t for t in q.lower().split() if t]
        out = []
        for e in self.entries:
            hay = " ".join([e["citekey"], e["title"], " ".join(e["authors"]), e["year"], e["journal"]]).lower()
            if all(t in hay for t in toks):
                item = {k: e[k] for k in ("citekey", "title", "short", "year", "journal", "authors")}
                item["has_pdf"] = bool(e["files"])
                item["files"] = [os.path.basename(f) for f in e["files"]]
                out.append(item)
        out.sort(key=lambda e: (not e["has_pdf"], -int(e["year"]) if (e["year"] or "").isdigit() else 0, e["citekey"]))
        return out[:limit]

    def get(self, citekey: str):
        self._ensure()
        return next((e for e in self.entries if e["citekey"] == citekey), None)


# ----------------------------------------------------------------------------
# 笔记存储：Markdown（在 vault 里）+ JSON（几何信息，在项目 data/ 里）
# ----------------------------------------------------------------------------

MARK_RE = re.compile(r"^%% hl:([A-Za-z0-9]+) %%\s*$", re.M)
BEGIN = "%% read-smoother:begin %%"
END = "%% read-smoother:end %%"
BEGIN_RE = re.compile(r"%% (?:read-smoother|reading-companion|bandu|pdf-tts-reader):begin %%")
END_RE = re.compile(r"%% (?:read-smoother|reading-companion|bandu|pdf-tts-reader):end %%")


class NoteStore:
    """Writes highlights into one Markdown file (inside the user's vault if configured).

    Only the region between the BEGIN/END markers is managed; everything else in the
    file is preserved verbatim. One entry = a `%% hl:id %%` line + a `[!quote]` callout + note text.
    """

    def __init__(self, cfg, book):
        self.cfg = cfg
        self.book = book
        self.lock = threading.Lock()
        root = cfg_path(cfg, "vault_path", None) or (ROOT / "notes")
        if book.kind == "paper":
            folder = root / cfg["paper_notes_folder"]
            self.vault_rel = f"{cfg['paper_notes_folder']}/@{book.citekey}"
            self.md_path = folder / f"@{book.citekey}.md"
        else:
            folder = root / cfg["notes_folder"]
            safe = re.sub(r'[\\/:*?"<>|#^\[\]]', " ", book.title).strip()
            self.vault_rel = f"{cfg['notes_folder']}/{safe}"
            self.md_path = folder / f"{safe}.md"
        folder.mkdir(parents=True, exist_ok=True)
        DATA.mkdir(exist_ok=True)
        self.json_path = DATA / f"{book.slug}.json"
        self.geo = json.loads(self.json_path.read_text()) if self.json_path.exists() else {}
        self.created_note = not self.md_path.exists()
        if self.created_note:
            self.md_path.write_text(self._new_file(), encoding="utf-8")

    # -- new file from template ------------------------------------------------
    def _new_file(self):
        b = self.book
        z = b.zotero or {}
        title = b.title.replace('"', "'")
        if b.kind == "paper":
            tpl_path = cfg_path(self.cfg, "paper_note_template", "templates/paper_note.md")
            fallback = TEMPLATES / "paper_note.md"
            url = f"https://doi.org/{z['doi']}" if z.get("doi") else z.get("url", "")
            vars = {
                "title": title, "citekey": b.citekey, "year": z.get("year", ""), "journal": z.get("journal", ""),
                "url": url, "zotero_uri": f"zotero://select/items/@{b.citekey}",
                "authors_yaml": "".join(f'  - "[[{a}]]"\n' for a in z.get("authors_full", [])).rstrip("\n"),
                "short_title": (z.get("shorttitle") or title.split(":")[0]).replace('"', "'"),
                "abstract_block": f"> [!abstract]- Abstract\n> {z['abstract']}\n" if z.get("abstract") else "",
                "pdf_path": str(b.pdf_path), "date": dt.date.today().isoformat(),
            }
        else:
            tpl_path = cfg_path(self.cfg, "book_note_template", "templates/book_note.md")
            fallback = TEMPLATES / "book_note.md"
            vars = {"title": title, "author": b.author, "pdf_path": str(b.pdf_path), "date": dt.date.today().isoformat()}
        tpl = (tpl_path if tpl_path and tpl_path.exists() else fallback).read_text(encoding="utf-8")
        head = render_template(tpl, vars).rstrip("\n") + "\n\n"
        return head + self._section_header() + BEGIN + "\n\n" + END + "\n"

    def _section_header(self):
        return note_text(self.cfg, "section") + "\n\n" + note_text(self.cfg, "callout") + "\n\n"

    # -- 解析 / 渲染 ----------------------------------------------------------
    def _parse(self):
        """返回 (head, entries, tail)。"""
        text = self.md_path.read_text(encoding="utf-8")
        mb, me = BEGIN_RE.search(text), END_RE.search(text)
        if mb and me and me.start() > mb.end():
            head, region, tail = text[:mb.start()], text[mb.end():me.start()], text[me.end():]
        else:
            head, region, tail = text.rstrip("\n") + "\n\n" + self._section_header(), "", ""
        parts = MARK_RE.split(region)
        entries = []
        for i in range(1, len(parts), 2):
            hid, body = parts[i], parts[i + 1]
            quote_lines, note_lines = [], []
            page = None
            in_quote = False
            for line in body.split("\n"):
                m = re.match(r"^> \[!quote\]\s*p\.(\d+)", line)
                if m:
                    page = int(m.group(1))
                    in_quote = True
                    continue
                if in_quote and line.startswith(">"):
                    quote_lines.append(line[1:].lstrip())
                    continue
                in_quote = False
                note_lines.append(line)
            entries.append({"id": hid, "page": page, "quote": " ".join(quote_lines).strip(),
                            "note": "\n".join(note_lines).strip()})
        return head, entries, tail

    def _render(self, head, entries, tail):
        chunks = [head.rstrip("\n") + "\n\n" if head.strip() else "", BEGIN + "\n\n"]
        for e in entries:
            link = self.book.zotero_page_link(e["page"])
            title = f"p.{e['page']}" + (f" [🆉]({link})" if link else "")
            block = f"%% hl:{e['id']} %%\n> [!quote] {title}\n> {e['quote'].replace(chr(10), ' ')}\n"
            if e["note"]:
                block += "\n" + e["note"].rstrip() + "\n"
            chunks.append(block + "\n")
        chunks.append(END + "\n" + (tail if tail.strip() else ""))
        return "".join(chunks)

    def _write(self, head, entries, tail):
        self.md_path.write_text(self._render(head, entries, tail), encoding="utf-8")

    def _save_geo(self):
        self.json_path.write_text(json.dumps(self.geo, ensure_ascii=False, indent=1))

    # -- 对外接口 -------------------------------------------------------------
    def list(self):
        with self.lock:
            _, entries, _ = self._parse()
            out = []
            for e in entries:
                g = self.geo.get(e["id"], {})
                out.append({**e, "rects": g.get("rects", []), "sids": g.get("sids", []), "created": g.get("created")})
            out.sort(key=lambda x: ((x["page"] or 0), (x["sids"] or [0])[0]))
            return out

    def add(self, page, quote, rects, sids, note=""):
        with self.lock:
            head, entries, tail = self._parse()
            hid = hashlib.sha1(f"{page}{quote}{dt.datetime.now().isoformat()}".encode()).hexdigest()[:8]
            entry = {"id": hid, "page": page, "quote": quote, "note": note}
            pos = len(entries)
            for i, e in enumerate(entries):
                g = self.geo.get(e["id"], {})
                if ((e["page"] or 0), (g.get("sids") or [0])[0]) > (page, (sids or [0])[0]):
                    pos = i
                    break
            entries.insert(pos, entry)
            self.geo[hid] = {"page": page, "rects": rects, "sids": sids,
                             "created": dt.datetime.now().isoformat(timespec="seconds")}
            self._write(head, entries, tail)
            self._save_geo()
            return {**entry, "rects": rects, "sids": sids}

    def update(self, hid, note=None, quote=None):
        with self.lock:
            head, entries, tail = self._parse()
            for e in entries:
                if e["id"] == hid:
                    if note is not None:
                        e["note"] = note.strip()
                    if quote is not None:
                        e["quote"] = quote.strip()
                    self._write(head, entries, tail)
                    return e
            return None

    def delete(self, hid):
        with self.lock:
            head, entries, tail = self._parse()
            entries = [e for e in entries if e["id"] != hid]
            self.geo.pop(hid, None)
            self._write(head, entries, tail)
            self._save_geo()
            return True

    def obsidian_uri(self):
        if not self.cfg.get("vault_name"):
            return None
        return ("obsidian://open?vault=" + urllib.parse.quote(self.cfg["vault_name"])
                + "&file=" + urllib.parse.quote(self.vault_rel))


# ----------------------------------------------------------------------------
# 书 / 论文 对象，以及书架
# ----------------------------------------------------------------------------

class Book:
    def __init__(self, cfg, slug, pdf_path, title, author, kind="book", citekey=None, att_key=None, zotero=None):
        self.cfg = cfg
        self.slug = slug
        self.pdf_path = Path(pdf_path)
        self.title = title
        self.author = author
        self.kind = kind
        self.citekey = citekey
        self.att_key = att_key
        self.zotero = zotero
        self.doc = pymupdf.open(str(self.pdf_path))
        self.sent = Sentencizer(self.doc)
        self.toc_path = DATA / f"{slug}.toc.json"     # AI-built table of contents, if the user asked for one
        self.toc = build_toc(self.doc)
        if self.toc_path.exists():
            try:
                self.toc = json.loads(self.toc_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        self.notes = NoteStore(cfg, self)
        self.notes_chat = ChatStore(self)
        self.progress_path = DATA / f"{slug}.progress.json"

    @property
    def refs(self):
        if not hasattr(self, "_refs"):
            try:
                self._ref_page, self._ref_y, self._refs = parse_references(self.doc, self.toc)
            except Exception:  # noqa
                import traceback
                traceback.print_exc()
                self._ref_page, self._ref_y, self._refs = None, None, []
            self._refs_by_year = {}
            for e in self._refs:
                self._refs_by_year.setdefault(e["year"], []).append(e)
                if e["year"][-1:].isalpha():
                    self._refs_by_year.setdefault(e["year"][:-1], []).append(e)
        return self._refs

    def ref_public(self, e):
        return {k: e[k] for k in ("id", "authors", "year", "title", "source", "text")}

    def cites(self, n):
        """Citations on page n (0-based): rects + the matching reference entries."""
        if not self.refs or (self._ref_page is not None and n > self._ref_page):
            return []
        raw = self.sent._raw_page(n)
        out = []
        for sent in raw["sentences"]:
            if n == self._ref_page and sent["rects"] and sent["rects"][0][1] >= (self._ref_y or 0):
                continue   # the reference list itself
            toks = self.sent._toks.get(n, {}).get(sent["id"], [])
            for st, en, ids in find_citations(sent["text"], self._refs_by_year):
                lines = {}
                for off, txt, rects in toks:
                    if off < en and off + len(txt) > st:   # token overlaps the citation (handles a leading "(")
                        for r in rects:
                            key = round(r[1])
                            if key in lines:
                                q = lines[key]
                                lines[key] = [min(q[0], r[0]), min(q[1], r[1]), max(q[2], r[2]), max(q[3], r[3])]
                            else:
                                lines[key] = list(r[:4])
                if lines:
                    out.append({"sid": sent["id"], "label": sent["text"][st:en], "refs": ids,
                                "rects": [[round(v, 2) for v in r] for r in lines.values()]})
        return out

    def set_toc(self, toc):
        """Replace the table of contents (AI-built) or, with None, go back to bookmarks / the font guess."""
        if hasattr(self, "_refs"):
            del self._refs
        if toc is None:
            self.toc_path.unlink(missing_ok=True)
            self.toc = build_toc(self.doc)
        else:
            self.toc = toc
            self.toc_path.write_text(json.dumps(toc, ensure_ascii=False, indent=1), encoding="utf-8")
        tocf = DATA / "books" / self.slug / "toc.txt"
        if tocf.parent.exists():
            tocf.write_text("\n".join("  " * (t["level"] - 1) + f"{t['title']}  (p.{t['page']})" for t in self.toc)
                            or note_text(self.cfg, "no_toc"), encoding="utf-8")

    def search(self, q, limit=300):
        """Case-insensitive full-text search over every page's sentences (headers/footers included)."""
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            return {"hits": [], "total": 0, "pages": 0}
        pat = re.compile(re.escape(q).replace(r"\ ", r"\s+"), re.I)
        hits, total, pages = [], 0, set()
        for n in range(self.doc.page_count):
            for sent in self.sent._raw_page(n)["sentences"]:
                ms = list(pat.finditer(sent["text"]))
                if not ms:
                    continue
                total += len(ms); pages.add(n + 1)
                if len(hits) < limit:
                    a = max(0, ms[0].start() - 70)
                    snippet = sent["text"][a:ms[0].end() + 90]
                    hits.append({"page": n + 1, "sid": sent["id"], "n": len(ms),
                                 "before": ("…" if a else "") + snippet[:ms[0].start() - a],
                                 "match": ms[0].group(0),
                                 "after": snippet[ms[0].end() - a:] + ("…" if ms[0].end() + 90 < len(sent["text"]) else "")})
        return {"hits": hits, "total": total, "pages": len(pages), "truncated": total > len(hits)}

    def zotero_page_link(self, page):
        if self.kind == "paper" and self.att_key:
            return f"zotero://open-pdf/library/items/{self.att_key}?page={page}"
        return None

    def info(self):
        prog = json.loads(self.progress_path.read_text()) if self.progress_path.exists() else {}
        return {
            "slug": self.slug, "title": self.title, "author": self.author, "kind": self.kind,
            "citekey": self.citekey, "pages": self.doc.page_count,
            "note_path": str(self.notes.md_path), "obsidian_uri": self.notes.obsidian_uri(),
            "zotero_uri": f"zotero://select/items/@{self.citekey}" if self.citekey else None,
            "default_voice": self.cfg["default_voice"], "progress": prog,
            "language": self.cfg.get("language", "en"), "dict_ok": _DS is not None,
        }


class Library:
    """slug -> Book。data/library.json 记录打开过的书（尤其是 Zotero 论文）以便下次直接打开。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.bib = BibIndex(cfg.get("bib_path"))
        self.open_books = {}
        self.lock = threading.Lock()
        self.reg_path = DATA / "library.json"
        DATA.mkdir(exist_ok=True)
        self.registry = json.loads(self.reg_path.read_text()) if self.reg_path.exists() else {}

    def _save_registry(self):
        self.reg_path.write_text(json.dumps(self.registry, ensure_ascii=False, indent=1))

    @staticmethod
    def slug_for_pdf(pdf: Path):
        return re.sub(r"[^A-Za-z0-9]+", "-", pdf.stem).strip("-").lower()[:80]

    def register_pdf(self, pdf: Path, title=None, author=None):
        slug = self.slug_for_pdf(pdf)
        meta = pymupdf.open(str(pdf)).metadata or {}
        old = self.registry.get(slug, {})
        self.registry[slug] = {
            "kind": "book", "pdf": str(pdf),
            "title": title or old.get("title") or meta.get("title") or pdf.stem,
            "author": author or old.get("author") or meta.get("author") or "",
            "added": old.get("added") or dt.datetime.now().isoformat(timespec="seconds"),
            "last_opened": old.get("last_opened"),
        }
        self._save_registry()
        return slug

    def register_zotero(self, citekey: str, file_idx=0):
        e = self.bib.get(citekey)
        if not e:
            raise KeyError(f"{citekey} is not in the .bib export")
        if not e["files"]:
            raise KeyError(f"{citekey} has no PDF attachment on disk")
        pdf = e["files"][min(file_idx, len(e["files"]) - 1)]
        m = re.search(r"/storage/([A-Z0-9]{8})/", pdf)
        slug = "z-" + re.sub(r"[^A-Za-z0-9]+", "-", citekey).strip("-")
        old = self.registry.get(slug, {})
        self.registry[slug] = {
            "kind": "paper", "pdf": pdf, "citekey": citekey, "att_key": m.group(1) if m else None,
            "title": e["title"], "author": f"{e['short']} ({e['year']})",
            "zotero": {"authors_full": [self._flip(a) for a in e["authors"]], "year": e["year"],
                       "journal": e["journal"], "doi": e["doi"], "url": e["url"], "abstract": e["abstract"],
                       "shorttitle": e["shorttitle"]},
            "added": old.get("added") or dt.datetime.now().isoformat(timespec="seconds"),
            "last_opened": old.get("last_opened"),
        }
        self.open_books.pop(slug, None)
        self._save_registry()
        return slug

    @staticmethod
    def _flip(a):
        """'Last, First' -> 'First Last' (matches the Obsidian Citations plugin's wikilink style)."""
        if "," in a:
            last, first = a.split(",", 1)
            return f"{first.strip()} {last.strip()}".strip()
        return a

    def get(self, slug: str) -> Book:
        with self.lock:
            if slug in self.open_books:
                return self.open_books[slug]
            r = self.registry.get(slug)
            if not r:
                raise KeyError(slug)
            if not Path(r["pdf"]).exists():
                raise KeyError(f"PDF is missing: {r['pdf']}")
            b = Book(self.cfg, slug, r["pdf"], r["title"], r["author"], r["kind"],
                     r.get("citekey"), r.get("att_key"), r.get("zotero"))
            self.open_books[slug] = b
            r["last_opened"] = dt.datetime.now().isoformat(timespec="seconds")
            self._save_registry()
            return b

    def shelf(self):
        for pdf in sorted(BOOKS.glob("*.pdf")):
            if self.slug_for_pdf(pdf) not in self.registry:
                self.register_pdf(pdf)
        items = []
        for slug, r in self.registry.items():
            prog_path = DATA / f"{slug}.progress.json"
            prog = json.loads(prog_path.read_text()) if prog_path.exists() else {}
            geo = DATA / f"{slug}.json"
            n_hl = len(json.loads(geo.read_text())) if geo.exists() else 0
            items.append({"slug": slug, "kind": r["kind"], "title": r["title"], "author": r["author"],
                          "citekey": r.get("citekey"), "exists": Path(r["pdf"]).exists(),
                          "page": prog.get("page"), "last_opened": r.get("last_opened"), "n_hl": n_hl})
        items.sort(key=lambda x: x["last_opened"] or "", reverse=True)
        return items

    def forget(self, slug):
        with self.lock:
            self.registry.pop(slug, None)
            self.open_books.pop(slug, None)
            self._save_registry()


# ----------------------------------------------------------------------------
# 词典：macOS 自带 Dictionary（牛津英汉 / NOAD），离线
# ----------------------------------------------------------------------------

try:
    import DictionaryServices as _DS
except ImportError:  # pragma: no cover
    _DS = None

_dict_cache = {}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"


def _format_definition(word, raw):
    """把 DCSCopyTextDefinition 的一长串整理成 头部 + 义项 行。"""
    txt = raw.strip()
    head, body = txt, ""
    m = re.search(r"\b(noun|verb|adjective|adverb|transitive verb|intransitive verb|pronoun|preposition|conjunction|"
                  r"interjection|determiner|plural noun|abbreviation|prefix|suffix|exclamation|reflexive verb|modal verb)\b", txt)
    if m:
        head, body = txt[:m.end()], txt[m.end():]
    body = re.sub(r"\s*([" + CIRCLED + r"])\s*", r"\n\1 ", body)
    body = re.sub(r"\s*▸\s*", "\n    ▸ ", body)
    body = re.sub(r"\s*(ORIGIN|PHRASES|DERIVATIVES|USAGE)\b", r"\n\1 ", body)
    return head.strip(), body.strip()


PARTICLES = {"up", "down", "in", "out", "off", "on", "over", "away", "back", "through", "along", "around",
             "about", "for", "into", "with", "to", "at", "by", "after", "across", "forward", "ahead", "apart", "aside"}
TRIM_WORDS = {"a", "an", "the", "his", "her", "their", "its", "my", "your", "our", "this", "that", "these", "those"}
SECTION_RE = re.compile(r"\b(PHRASAL VERBS?|PHRASES|IDIOMS)\b")
POS_RE = r"(?:A\.|transitive verb|intransitive verb|reflexive verb|verb|noun|adjective|adverb|\[|\||①)"
PLACEHOLDER = r"(?:\s+(?:sth|sb|something|someone|somebody|one's|oneself|itself))?"


def _phrase_regex(phrase):
    ws = [re.escape(w) for w in phrase.lower().split()]
    return re.compile(r"\b" + (PLACEHOLDER + r"\s+").join(ws) + PLACEHOLDER + r"\b", re.I)


def _extract_phrase(entry: str, phrase: str, base: str):
    """从基础词的整条词条里切出某个短语的小段。找不到返回 None。"""
    m_sec = SECTION_RE.search(entry)
    sec_start = m_sec.start() if m_sec else 0
    rx = _phrase_regex(phrase)
    best = None
    for m in rx.finditer(entry, sec_start):
        after = entry[m.end():m.end() + 60]
        if re.match(r"\s*" + POS_RE, after) or re.match(r"\s*[A-Za-z(]", after) and "▸" not in entry[max(0, m.start() - 25):m.start()]:
            best = m
            break
    if best is None:
        m = rx.search(entry, sec_start) or rx.search(entry)
        if m is None:
            return None
        best = m
    start = best.start()
    # 结束：下一条子词条 / 下一段标记 / 700 字
    ends = [len(entry), start + 700]
    nxt_sub = re.compile(r"(?<=[\s.。！!])" + re.escape(base) + r"(?:\s+\w+){1,3}\s+" + POS_RE, re.I)
    m2 = nxt_sub.search(entry, best.end())
    if m2:
        ends.append(m2.start())
    m3 = re.compile(r"(?<=\. )[a-z][\w' -]{1,40}\s\|").search(entry, best.end())
    if m3:
        ends.append(m3.start())
    m4 = re.compile(r"\b(PHRASAL VERBS?|PHRASES|IDIOMS|DERIVATIVES|ORIGIN|USAGE)\b").search(entry, best.end())
    if m4:
        ends.append(m4.start())
    snippet = entry[start:min(ends)].strip().rstrip(".。")
    snippet = re.sub(r"\s*([" + CIRCLED + r"])\s*", r"\n\1 ", snippet)
    snippet = re.sub(r"\s*▸\s*", "\n    ▸ ", snippet)
    snippet = re.sub(r"\s*\|\s*", " | ", snippet, count=0)
    return snippet.strip()


def _headword(entry: str):
    m = re.match(r"\s*([A-Za-z][A-Za-z'’-]*)", entry or "")
    return m.group(1).lower() if m else None


def define_in_context(sentence: str, off: int):
    """按上下文查词：先让系统词典圈出短语；再找分开的短语动词。"""
    res = {"found": False}
    if not sentence or off < 0 or off >= len(sentence):
        return res
    # 目标词
    m = re.compile(r"[A-Za-z][A-Za-z'’-]*").search(sentence, off)
    if not m or m.start() != off:
        wm = re.search(r"[A-Za-z][A-Za-z'’-]*", sentence[off:])
        if not wm:
            return res
        m = re.compile(re.escape(wm.group())).search(sentence, off)
    word = m.group()
    res.update(define(word))
    res["word"] = word
    res["range"] = [off, off + len(word)]
    if _DS is None:
        return res
    base = None
    if res.get("found"):
        raw = _DS.DCSCopyTextDefinition(None, res["word"], (0, len(res["word"]))) or ""
        base = _headword(raw)
    # a) 连着的短语
    try:
        rng = _DS.DCSGetTermRangeInString(None, sentence, off)
        term = sentence[rng.location:rng.location + rng.length] if rng.length else ""
    except Exception:
        term, rng = "", None
    tws = term.split()
    while tws and tws[-1].lower().strip("’'") in TRIM_WORDS:
        tws.pop()
    while tws and tws[0].lower() in TRIM_WORDS and len(tws) > 1 and tws[0].lower() != "in":
        tws.pop(0)
    term = " ".join(tws)
    if len(tws) >= 2:
        entry = _DS.DCSCopyTextDefinition(None, term, (0, len(term))) or ""
        head = _headword(entry) or term.split()[0].lower()
        snip = _extract_phrase(entry, term, head) if entry else None
        if snip:
            res["phrase"] = {"text": term, "body": snip, "kind": "phrase"}
            i = sentence.lower().find(term.lower(), max(0, off - 60))
            if i >= 0:
                res["range"] = [i, i + len(term)]
    # b) 分开的短语动词：基础词 + 后面 4 个词内的小品词
    if "phrase" not in res and base:
        rest = re.findall(r"[A-Za-z'’]+", sentence[off + len(word):])[:5]
        for k, w in enumerate(rest):
            if w.lower() in PARTICLES and (k > 0 or True):
                cand = f"{base} {w.lower()}"
                entry = _DS.DCSCopyTextDefinition(None, base, (0, len(base))) or ""
                if not SECTION_RE.search(entry):
                    break
                snip = _extract_phrase(entry[SECTION_RE.search(entry).start():], cand, base)
                if snip and _phrase_regex(cand).search(snip[:len(cand) + 40]):
                    res["phrase"] = {"text": cand, "body": snip, "kind": "separated",
                                     "note": f"{word} … {w} (separated in this sentence; possibly this phrasal verb)"}
                    break
    return res


def define(word: str):
    w = word.strip().strip("“”‘’\"'()[]{}.,;:!?—–-…")
    key = w.lower()
    if not w or key in _dict_cache:
        return _dict_cache.get(key, {"word": w, "found": False})
    res = {"word": w, "found": False}
    if _DS is not None:
        cands = [w, w.lower()]
        if w.lower().endswith("’s") or w.lower().endswith("'s"):
            cands.append(w[:-2])
        for c in cands:
            raw = _DS.DCSCopyTextDefinition(None, c, (0, len(c)))
            if raw:
                head, body = _format_definition(c, raw)
                res = {"word": w, "found": True, "head": head, "body": body}
                break
    _dict_cache[key] = res
    return res


# ----------------------------------------------------------------------------
# 问 AI：调本机 Claude Code 命令行（走订阅），每本书一个会话
# ----------------------------------------------------------------------------

import shutil
import subprocess

AI_MODELS = [   # Claude Code model aliases; the UI adds its own labels
    {"id": "haiku", "label": "Haiku 4.5"},
    {"id": "sonnet", "label": "Sonnet 5"},
    {"id": "opus", "label": "Opus 5.5"},
    {"id": "fable", "label": "Fable 5.1"},
]
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


class ChatStore:
    def __init__(self, book):
        self.book = book
        self.path = DATA / f"{book.slug}.chat.json"
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"session_id": None, "messages": []}
        self.lock = threading.Lock()

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))

    def reset(self):
        with self.lock:
            self.data = {"session_id": None, "messages": []}
            self.save()


def export_book_text(book):
    """把整本书导出成 data/books/<slug>/full.txt（按页分隔）+ toc.txt，给 AI 用只读工具翻。"""
    d = DATA / "books" / book.slug
    full, tocf = d / "full.txt", d / "toc.txt"
    if full.exists() and full.stat().st_mtime >= book.pdf_path.stat().st_mtime:
        return d
    d.mkdir(parents=True, exist_ok=True)
    parts = []
    for n in range(book.doc.page_count):
        raw = book.sent._raw_page(n)
        txt = "\n".join(x["text"] for x in raw["sentences"] if not x["skip"])
        parts.append(f"=== p.{n + 1} ===\n{txt}\n")
    full.write_text("\n".join(parts), encoding="utf-8")
    tocf.write_text("\n".join("  " * (t["level"] - 1) + f"{t['title']}  (p.{t['page']})" for t in book.toc) or note_text(book.cfg, "no_toc"), encoding="utf-8")
    return d


PATH_RE = re.compile(r"(?:\"([^\"]+)\"|'([^']+)'|「([^」]+)」|((?:~|/)[^\s\"'<>，。；：！？、）]+))")


def paths_in_text(text):
    """从问题里找出存在的本地路径。"""
    out = []
    for m in PATH_RE.finditer(text):
        cand = next(g for g in m.groups() if g)
        cand = cand.strip().rstrip(".,;:)")
        if not (cand.startswith("~") or cand.startswith("/")):
            continue
        pth = Path(os.path.expanduser(cand))
        if pth.exists():
            out.append(pth.resolve())
    return out


def ai_dirs(cfg, extra_paths):
    dirs = []
    for d in cfg.get("ai_read_dirs", []):
        pth = Path(os.path.expanduser(d))
        if pth.is_dir():
            dirs.append(pth.resolve())
    for pth in extra_paths:
        d = pth if pth.is_dir() else pth.parent
        if not any(str(d).startswith(str(x)) for x in dirs):
            dirs.append(d)
    return dirs


def _clean_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
    return env


def ai_toc(book, model="haiku"):
    """Ask Claude to build the table of contents from the book's own text (papers without bookmarks)."""
    book_dir = export_book_text(book)
    full = (book_dir / "full.txt").read_text(encoding="utf-8")
    if len(full) > 400_000:   # a long book: give the font guess instead of the whole text
        cands = "\n".join(f"p.{t['page']}: {t['title']}" for t in heuristic_toc(book.doc))
        material = "[Candidate heading lines guessed from fonts; keep the real headings, drop the rest]\n" + cands
    else:
        material = "[Full text; each page starts with '=== p.N ===']\n" + full
    prompt = (
        f"Build the table of contents of this document. Title: \"{book.title}\"; author(s): {book.author}.\n"
        "Output ONLY lines of the form  LEVEL<TAB>PAGE<TAB>TITLE  (LEVEL 1-3, PAGE = the '=== p.N ===' page where the heading "
        "appears). Include section and subsection headings in reading order (e.g. Abstract, Introduction, Method, Participants, "
        "Results, Discussion, References, Appendix; for a book: parts, chapters, sections). Skip the document title, author names, "
        "affiliations, journal/publisher boilerplate, running headers, figure and table captions. Copy heading text exactly. "
        "If a heading has a number keep it. No other text.\n\n" + material)
    cmd = [CLAUDE_BIN, "-p", "--model", model, "--tools", "", "--permission-mode", "dontAsk",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--output-format", "json"]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=_clean_env(), cwd=str(DATA), timeout=600)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip()[-400:] or f"claude exited with code {proc.returncode}")
    try:
        out = json.loads(proc.stdout).get("result", "")
    except ValueError:
        out = proc.stdout
    toc = []
    for line in out.splitlines():
        m = re.match(r"^\s*([123])\s*[\t|]\s*(?:p\.?\s*)?(\d+)\s*[\t|]\s*(.+?)\s*$", line)
        if m and 0 < int(m.group(2)) <= book.doc.page_count:
            toc.append({"level": int(m.group(1)), "title": m.group(3), "page": int(m.group(2)), "ai": True})
    if not toc:
        raise RuntimeError("no headings in the answer: " + out.strip()[:200])
    return toc


def ask_stream(book, question, model, page, sid, include_page, include_sentence, new_session):
    """生成器：逐段产出回答文字；最后一段是 JSON 元信息。"""
    chat = book.notes_chat
    if new_session:
        chat.reset()
    book_dir = export_book_text(book)
    mentioned = paths_in_text(question)
    dirs = ai_dirs(book.cfg, mentioned)
    notes_root = cfg_path(book.cfg, "vault_path", None) or (ROOT / "notes")
    profile = ""
    pp = cfg_path(book.cfg, "profile_path", "profile.md")
    if pp and pp.exists():
        profile = pp.read_text(encoding="utf-8").strip()[:6000]
    system = (
        "You are a reading companion. The user is reading a book or paper while it is read aloud to them, "
        f"sentence by sentence. Title: \"{book.title}\"; author(s): {book.author}.\n"
        "Answer in the language the user writes in. Be concise and direct, like quick Q&A. "
        "Keep quotations from the text in the original language. No Markdown headings; short paragraphs and small lists are fine.\n"
        "When asked what a passage means, explain the meaning first, then what it does in the argument.\n"
        "The user's message may contain [Current sentence]: the sentence they are listening to right now. "
        "When they say 'this sentence', 'here', 'this word' or 'it', they mean that sentence. "
        "Prefer answering around the current sentence and its neighbours over summarising the whole page.\n"
        f"The full text of the book is in {book_dir}/full.txt (each page starts with '=== p.N ==='); "
        f"the table of contents is in {book_dir}/toc.txt. For anything outside the current page (earlier chapters, "
        "where a term first appears, what the book says elsewhere) use Grep or Read on full.txt and cite page numbers. "
        "Do not guess from memory; if it is not there, say so.\n"
        "\n[Local files] You may also read (read-only, with Read / Grep / Glob) these folders: "
        + ("; ".join(str(d) for d in dirs) or "(none configured)")
        + ". When the user gives a path in the question, read it directly without asking; PDFs can be Read.\n"
        f"The user's notes live in {notes_root}. The note file for this book is {book.notes.md_path}; "
        "it holds their highlights and comments, so read it when they ask what they noted before.\n"
        "Never read back or repeat anything that looks like a password, API key or token."
        + (f"\n\n[About the user, written by them]\n{profile}" if profile else "")
    )
    ctx_parts = []
    try:
        raw = book.sent._raw_page(page - 1) if page else None
    except Exception:
        raw = None
    if raw and include_page:
        ctx_parts.append(f"[Page {page}, full text]\n" + "\n".join(x["text"] for x in raw["sentences"] if not x["skip"]))
    if raw and include_sentence and sid is not None and 0 <= int(sid) < len(raw["sentences"]):
        sid = int(sid)
        ss = [x for x in raw["sentences"] if not x["skip"]]
        idx = next((i for i, x in enumerate(ss) if x["id"] == sid), None)
        if idx is None:
            ctx_parts.append(f"[Current sentence] (page {page})\n" + raw["sentences"][sid]["text"])
        else:
            before = " ".join(x["text"] for x in ss[max(0, idx - 2):idx])
            after = " ".join(x["text"] for x in ss[idx + 1:idx + 3])
            block = f"[Current sentence] (page {page}; the user is listening to this one)\n{ss[idx]['text']}"
            if before:
                block += f"\n[Before it]\n{before}"
            if after:
                block += f"\n[After it]\n{after}"
            ctx_parts.append(block)
    prompt = question.strip()
    if ctx_parts:
        prompt = "\n\n".join(ctx_parts) + "\n\n[Question]\n" + prompt
    cmd = [CLAUDE_BIN, "-p", prompt, "--model", model, "--tools", "Read,Grep,Glob",
           "--allowedTools", "Read,Grep,Glob", "--permission-mode", "dontAsk",
           "--add-dir", str(book_dir), *[str(d) for d in dirs],
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--system-prompt", system,
           "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if chat.data.get("session_id"):
        cmd += ["--resume", chat.data["session_id"]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_clean_env(), cwd=str(DATA), text=True)
    answer, session_id, err = [], chat.data.get("session_id"), None
    for line in proc.stdout:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t == "stream_event":
            ev = d.get("event", {})
            if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                txt = ev["delta"]["text"]
                answer.append(txt)
                yield txt
            elif ev.get("type") == "content_block_start" and ev.get("content_block", {}).get("type") == "tool_use":
                yield "\x1f" + json.dumps({"tool": ev["content_block"].get("name")}) + "\x1f"
        elif t == "result":
            session_id = d.get("session_id") or session_id
            if d.get("is_error"):
                err = d.get("result") or "error"
            elif not answer and d.get("result"):
                answer.append(d["result"])
                yield d["result"]
    proc.wait()
    if proc.returncode != 0 and not answer:
        err = (proc.stderr.read() or "").strip()[-400:] or f"claude exited with code {proc.returncode}"
    with chat.lock:
        chat.data["session_id"] = session_id
        chat.data["messages"].append({"role": "user", "text": question, "page": page, "ts": dt.datetime.now().isoformat(timespec="seconds")})
        chat.data["messages"].append({"role": "assistant", "text": "".join(answer), "model": model, "error": err,
                                      "ts": dt.datetime.now().isoformat(timespec="seconds")})
        chat.save()
    yield "\n\x1e" + json.dumps({"done": True, "error": err, "model": model}, ensure_ascii=False)


# ----------------------------------------------------------------------------
# TTS
# ----------------------------------------------------------------------------

def clean_for_tts(text: str) -> str:
    text = text.replace("­", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def synthesize(text: str, voice: str) -> bytes:
    text = clean_for_tts(text)
    key = hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()
    d = CACHE / voice
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{key}.mp3"
    if f.exists():
        return f.read_bytes()

    async def run():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    audio = asyncio.run(run())
    f.write_bytes(audio)
    return audio


_voices_cache = None


def list_voices(locales=("en",)):
    global _voices_cache
    if _voices_cache is None:
        vs = asyncio.run(edge_tts.list_voices())
        locs = tuple(f"{l}-" if not l.endswith("-") else l for l in locales) or ("en-",)
        _voices_cache = sorted(
            [{"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]} for v in vs if v["Locale"].startswith(locs)],
            key=lambda v: (not v["locale"].startswith("en-US"), not v["locale"].startswith("en-GB"), v["name"]))
    return _voices_cache


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    lib: Library = None  # injected in main()

    def log_message(self, fmt, *args):
        if args and ("/tts" in args[0] or "/page/" in args[0]):
            return
        sys.stderr.write("%s\n" % (fmt % args))

    def _send(self, status, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path: Path, ctype):
        cache = "no-store" if ctype.startswith("text/html") else "public, max-age=3600"
        self._send(200, path.read_bytes(), ctype, {"Cache-Control": cache})

    def do_GET(self):
        lib = self.lib
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = urllib.parse.unquote(u.path)
        try:
            if p == "/":
                return self._file(STATIC / "library.html", "text/html; charset=utf-8")
            if p.startswith("/static/"):
                f = (STATIC / p[len("/static/"):]).resolve()
                if not str(f).startswith(str(STATIC)) or not f.exists():
                    return self._send(404, b"not found", "text/plain")
                ctype = {"js": "application/javascript", "css": "text/css", "html": "text/html; charset=utf-8"}.get(f.suffix[1:], "application/octet-stream")
                return self._file(f, ctype)
            m = re.fullmatch(r"/read/([A-Za-z0-9_-]+)", p)
            if m:
                lib.get(m.group(1))
                return self._file(STATIC / "index.html", "text/html; charset=utf-8")
            m = re.fullmatch(r"/pdf/([A-Za-z0-9_-]+)", p)
            if m:
                return self._file(lib.get(m.group(1)).pdf_path, "application/pdf")
            if p == "/api/library":
                return self._json({"items": lib.shelf(), "bib": str(lib.bib.path or ""),
                                   "bib_ok": bool(lib.bib.path and lib.bib.path.exists()),
                                   "language": lib.cfg.get("language", "en")})
            if p == "/api/zotero/search":
                return self._json(lib.bib.search(q.get("q", [""])[0]))
            if p == "/api/voices":
                return self._json(list_voices(tuple(lib.cfg.get("tts_locales") or ["en"])))
            if p == "/api/define":
                return self._json(define(q.get("word", [""])[0]))
            m = re.fullmatch(r"/api/b/([A-Za-z0-9_-]+)/define", p)
            if m:
                book = lib.get(m.group(1))
                page = int(q.get("page", ["1"])[0]) - 1
                sid = int(q.get("sid", ["0"])[0])
                off = int(q.get("off", ["0"])[0])
                raw = book.sent._raw_page(page)
                sent = raw["sentences"][sid]["text"] if 0 <= sid < len(raw["sentences"]) else ""
                return self._json(define_in_context(sent, off))
            if p == "/api/tts":
                text = q.get("text", [""])[0]
                voice = q.get("voice", [lib.cfg["default_voice"]])[0]
                if not text.strip():
                    return self._send(400, b"empty", "text/plain")
                return self._send(200, synthesize(text, voice), "audio/mpeg", {"Cache-Control": "public, max-age=86400"})
            m = re.fullmatch(r"/api/b/([A-Za-z0-9_-]+)/(.*)", p)
            if m:
                book = lib.get(m.group(1))
                sub = m.group(2)
                if sub == "info":
                    return self._json(book.info())
                if sub == "toc":
                    return self._json(book.toc)
                if sub == "search":
                    return self._json(book.search(q.get("q", [""])[0]))
                if sub == "notes":
                    return self._json(book.notes.list())
                if sub == "chat":
                    return self._json({"messages": book.notes_chat.data["messages"], "models": AI_MODELS,
                                       "has_session": bool(book.notes_chat.data.get("session_id")),
                                       "claude_ok": bool(CLAUDE_BIN and os.path.exists(CLAUDE_BIN))})
                pm = re.fullmatch(r"page/(\d+)", sub)
                if pm:
                    n = int(pm.group(1)) - 1
                    if not 0 <= n < book.doc.page_count:
                        return self._json({"error": "page out of range"}, 404)
                    d = dict(book.sent.page(n))
                    try:
                        d["cites"] = book.cites(n)
                    except Exception:  # noqa
                        import traceback
                        traceback.print_exc()
                        d["cites"] = []
                    return self._json(d)
                if sub == "refs":
                    return self._json([book.ref_public(e) for e in book.refs])
            return self._send(404, b"not found", "text/plain")
        except KeyError as e:
            return self._send(404, f"Not found: {e}".encode(), "text/plain; charset=utf-8")
        except Exception as e:  # noqa
            import traceback
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        lib = self.lib
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            if p == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
                name = re.sub(r"[\\/:*?\"<>|]", "_", name)[:120] or "file"
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n)
                up = DATA / "uploads"
                up.mkdir(exist_ok=True)
                target = up / name
                i = 1
                while target.exists():
                    target = up / f"{Path(name).stem}-{i}{Path(name).suffix}"
                    i += 1
                target.write_bytes(raw)
                return self._json({"path": str(target)})
            body = self._body()
            if p == "/api/quit":
                self._json({"ok": True})
                threading.Timer(0.3, lambda: os._exit(0)).start()
                return
            if p == "/api/zotero/open":
                slug = lib.register_zotero(body["citekey"], int(body.get("file_idx", 0)))
                book = lib.get(slug)
                return self._json({"slug": slug, "created_note": book.notes.created_note, "note_path": str(book.notes.md_path)})
            m = re.fullmatch(r"/api/b/([A-Za-z0-9_-]+)/(.*)", p)
            if m:
                book = lib.get(m.group(1))
                sub = m.group(2)
                if sub == "notes":
                    return self._json(book.notes.add(int(body["page"]), body["quote"], body.get("rects", []),
                                                     body.get("sids", []), body.get("note", "")))
                if sub == "progress":
                    book.progress_path.write_text(json.dumps(body))
                    return self._json({"ok": True})
                if sub == "forget":
                    lib.forget(book.slug)
                    return self._json({"ok": True})
                if sub == "chat/reset":
                    book.notes_chat.reset()
                    return self._json({"ok": True})
                if sub == "toc/ai":
                    if not (CLAUDE_BIN and os.path.exists(CLAUDE_BIN)):
                        return self._json({"error": "claude CLI not found"}, 500)
                    book.set_toc(ai_toc(book, body.get("model") or "haiku"))
                    return self._json(book.toc)
                if sub == "toc/reset":
                    book.set_toc(None)
                    return self._json(book.toc)
                if sub == "ask":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    for chunk in ask_stream(book, body.get("question", ""), body.get("model", "sonnet"),
                                            int(body.get("page") or 0), body.get("sid"),
                                            bool(body.get("include_page", True)), bool(body.get("include_sentence", True)),
                                            bool(body.get("new_session", False))):
                        try:
                            self.wfile.write(chunk.encode("utf-8"))
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    return
            return self._send(404, b"not found", "text/plain")
        except KeyError as e:
            return self._json({"error": str(e)}, 404)
        except Exception as e:  # noqa
            import traceback
            traceback.print_exc()
            return self._json({"error": str(e)}, 500)

    def do_PUT(self):
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        m = re.fullmatch(r"/api/b/([A-Za-z0-9_-]+)/notes/([A-Za-z0-9]+)", p)
        if not m:
            return self._send(404, b"not found", "text/plain")
        body = self._body()
        e = self.lib.get(m.group(1)).notes.update(m.group(2), note=body.get("note"), quote=body.get("quote"))
        return self._json(e or {"error": "not found"}, 200 if e else 404)

    def do_DELETE(self):
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        m = re.fullmatch(r"/api/b/([A-Za-z0-9_-]+)/notes/([A-Za-z0-9]+)", p)
        if not m:
            return self._send(404, b"not found", "text/plain")
        self.lib.get(m.group(1)).notes.delete(m.group(2))
        return self._json({"ok": True})


def main():
    global DATA, CACHE
    ap = argparse.ArgumentParser(description="Read Smoother: listen while you read")
    ap.add_argument("pdf", nargs="?", help="open this PDF directly (optional)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--title")
    ap.add_argument("--author")
    ap.add_argument("--config", help="config file (default: ./config.json)")
    ap.add_argument("--data-dir", help="where progress, highlights and chats are stored (default: ./data)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    if a.data_dir:
        DATA = Path(a.data_dir).expanduser().resolve()
        CACHE = DATA / "cache"
    cfg = load_config(a.config)
    lib = Library(cfg)
    url = f"http://127.0.0.1:{a.port}/"
    if a.pdf:
        pdf = Path(a.pdf).expanduser().resolve()
        if not pdf.exists():
            sys.exit(f"PDF not found: {pdf}")
        slug = lib.register_pdf(pdf, a.title, a.author)
        url += f"read/{slug}"
    Handler.lib = lib
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Read Smoother shelf: http://127.0.0.1:{a.port}/   (Ctrl+C to quit)")
    if a.pdf:
        print(f"opening: {url}")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
