# -*- coding: utf-8 -*-
"""aima-audiobook: your own PDF of Russell and Norvig, Artificial Intelligence: A Modern Approach (4th edition),
read aloud as a chapterised audiobook with the notation spoken the way a lecturer says it.

Bring your own book. This tool ships no text from it; everything comes from the PDF you point it at, and the
audio it makes is a private copy for you.

    aima-audiobook toc   book.pdf                        # what the PDF's outline gives us, and what a selection covers
    aima-audiobook text  book.pdf --select rmit-ai26     # spoken-form text only (for ElevenReader or any TTS app)
    aima-audiobook build book.pdf --select rmit-ai26     # text + narration + one M4B with chapter, section and topic markers
    aima-audiobook build book.pdf --select 3,6.1-6.5 --per-chapter --voice en-GB-RyanNeural
    aima-audiobook voices                                # the English voices available

Markers: every chapter (announced, with its opening line), every section, every numbered topic inside a section,
and each chapter summary. Resume-safe: narration that already exists is kept.
"""
import argparse, asyncio, json, pathlib, re, shutil, subprocess, sys

try:
    import pymupdf as fitz
except ImportError:                              # PyMuPDF before 1.24 only had the old name
    import fitz

AUTHORS = "Stuart Russell, Peter Norvig"
BOOK = "Artificial Intelligence: A Modern Approach, 4th edition"
SHORT = "Artificial Intelligence - A Modern Approach 4e"
VOICE = "en-AU-WilliamMultilingualNeural"
CHUNK = 3500                                  # characters per synthesis request; each unit is a few of these

# named selections: (label shown in the chapter marker, selection token)
PRESETS = {
    "rmit-ai26": [("Week 1", "1"), ("Week 1", "2"), ("Weeks 1-2", "3"), ("Week 3", "6.1-6.5"), ("Week 4", "7"),
                  ("Week 5", "8"), ("Week 5", "9"), ("Week 6", "11.1-11.3"), ("Week 7", "12"), ("Week 9", "15"),
                  ("Week 9", "16.1-16.3"), ("Week 10", "23.1-23.5"), ("Week 11", "13.1-13.4"), ("Week 12", "28.1-28.3")],
}


# ----------------------------------------------------------------------------------------------------- selection

def parse_selection(spec):
    """'all' | a preset name | comma list of  3  6.1-6.5  3.4  12.S   ->  {chapter: {label, sections|None, summary}}"""
    if spec in (None, "", "all"):
        return None
    items = PRESETS[spec] if spec in PRESETS else [(None, tok.strip()) for tok in spec.split(",") if tok.strip()]
    sel = {}
    for label, tok in items:
        m = re.fullmatch(r"(\d+)", tok)
        if m:
            s = sel.setdefault(int(m.group(1)), {"label": label, "sections": set(), "summary": False})
            s["sections"] = None; s["summary"] = True; continue
        m = re.fullmatch(r"(\d+)\.[sS]", tok)
        if m:
            sel.setdefault(int(m.group(1)), {"label": label, "sections": set(), "summary": False})["summary"] = True; continue
        m = re.fullmatch(r"(\d+)\.(\d+)(?:-(?:(\d+)\.)?(\d+))?", tok)
        if not m:
            raise SystemExit(f"bad selection token {tok!r}: use 3, 6.1-6.5, 3.4 or 12.S, or a preset ({', '.join(PRESETS)})")
        ch, a = int(m.group(1)), int(m.group(2))
        b = int(m.group(4)) if m.group(4) else a
        if m.group(3) and int(m.group(3)) != ch:
            raise SystemExit(f"a range must stay inside one chapter: {tok}")
        s = sel.setdefault(ch, {"label": label, "sections": set(), "summary": False})
        if s["sections"] is not None:
            s["sections"].update(f"{ch}.{k}" for k in range(a, b + 1))
    return sel


# ----------------------------------------------------------------------------------------------------- the PDF

LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "\x00": ""}


def norm(s):
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return s.strip()


def outline(doc):
    toc = [(l, norm(t), p) for l, t, p in doc.get_toc()]
    if not toc:
        raise SystemExit("this PDF has no outline (bookmarks). aima-audiobook needs the 4th-edition PDF, whose outline lists "
                         "'Chapter N: Title' and 'N.M Title' entries.")
    chapters, cur = {}, None
    for i, (l, t, p) in enumerate(toc):
        m = re.match(r"^Chapter (\d+):\s*(.+)$", t)
        if m:
            cur = int(m.group(1))
            end = next((p2 for l2, _, p2 in toc[i + 1:] if l2 <= l), doc.page_count + 1)
            chapters[cur] = {"n": cur, "title": m.group(2).strip(), "page": p, "end": end, "sections": [], "summary": None, "notes": None}
            continue
        if cur is None:
            continue
        m = re.match(r"^(\d+)\.(\d+)\s+(.+)$", t)
        if m and int(m.group(1)) == cur:
            chapters[cur]["sections"].append({"id": f"{cur}.{int(m.group(2))}", "name": m.group(3).strip(), "page": p})
        elif t == "Summary":
            chapters[cur]["summary"] = p
        elif t.startswith("Bibliographical"):
            chapters[cur]["notes"] = p
    if not chapters:
        raise SystemExit("no 'Chapter N: Title' entries in the outline. Is this the 4th-edition PDF?")
    return chapters


HEAD_RE = re.compile(r"^(Section \d+\.\d+\s.*\d+|\d+\s+Chapter \d+\s.*|Chapter \d+\s.*\d+|\d{1,4})$")
CODE_RE = re.compile(r"(←|\bfunction\b|\breturn\b|\bfor each\b|\bwhile\b|\bif .* then\b|PRECOND|EFFECT|\bInit\(|\bGoal\(|\b[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+\b)")


def line_text(spans):
    """Join a line's spans. A span set two points smaller than the running text is a footnote marker (digits after
    punctuation: dropped), a superscript (raised baseline: marked ^{...}) or a subscript (lowered: marked _{...});
    the spoken-form pass turns the marks into words."""
    out, base = [], None
    for s in spans:
        t = s["text"]
        if not t.strip():
            out.append(t); continue
        if base is not None and s["size"] < base["size"] - 2:
            dy = s["origin"][1] - base["origin"][1]
            math_base = "Ital" in base["font"] or base["font"].startswith(("CMMI", "CMSY", "CMR", "CMEX")) or re.search(r"[\d)\]]\s*$", base["text"])
            if re.fullmatch(r"\d{1,2}", t.strip()) and dy < -1 and not math_base:
                continue                                 # a footnote marker: raised digits after ordinary prose
            if dy < -1:
                out.append("^{" + t.strip() + "}"); continue
            if dy > 1:
                out.append("_{" + t.strip() + "}"); continue
        out.append(t); base = s
    return "".join(out).replace("}^{", "").replace("}_{", "")


def page_text(page):
    """The page's text in reading order, minus what the typesetting marks as not-the-body: margin keywords (the small
    sans font), footnotes and figure labels (under 9.5 pt), and figure blocks set at 10 pt that read as pseudo-code or
    formal notation. Block quotations (also 10 pt) are kept; figure captions are kept but moved to the end of the page
    so they do not land mid-sentence."""
    out, captions = [], []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0 or not b["lines"]:
            continue
        lines = []
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            size = max(s["size"] for s in spans)
            if size < 9.5 or all(s["font"].startswith("CMSS8") for s in spans):
                continue
            lines.append((size, line_text(ln["spans"])))
        if not lines:
            continue
        if lines[0][1].lstrip().startswith("Figure ") and lines[0][0] < 10.5:
            captions.extend(t for _, t in lines); continue         # a caption (set smaller than the body)
        if all(sz < 10.5 for sz, _ in lines) and any(CODE_RE.search(t) for _, t in lines):
            continue                                     # a pseudo-code or formal-notation figure
        out.extend(t for _, t in lines)
    return "\n".join(out + captions)


def strip_heads(text):
    """Drop running heads and page numbers before anything else looks at the page."""
    raw = [ln.strip() for ln in text.split("\n")]
    out = []
    for i, s in enumerate(raw):
        if HEAD_RE.match(s) or re.match(r"^Section \d+\.\d+$", s) or (i > 0 and re.match(r"^Section \d+\.\d+$", raw[i - 1])):
            continue
        out.append(s)
    return "\n".join(out)


def heading_pattern(anchor):
    kind = anchor["kind"]
    if kind == "chapter":
        return re.compile(rf"(?m)^CHAPTER\s+{anchor['n']}\b")
    if kind == "summary":
        return re.compile(r"(?m)^Summary\s*$")
    if kind == "notes":
        return re.compile(r"(?m)^Bibliographical and Historical Notes\s*$")
    words = [re.escape(w) for w in re.split(r"[\s–—-]+", anchor["name"]) if w]
    name = r"[\s–—-]+".join(words)
    return re.compile(rf"(?m)^{re.escape(anchor['id'])}\s+{name}")


def find_heading(text, anchor):
    m = heading_pattern(anchor).search(text)
    if m:
        return m.start()
    if anchor["kind"] == "section":                                  # the outline and the page can disagree on punctuation
        m = re.search(rf"(?m)^{re.escape(anchor['id'])}\s+\S", text)
        if m:
            return m.start()
    return None


def span_text(doc, a, b, warn):
    """Pages a.page .. b.page: cut before a's heading on the first page (and drop the heading line itself, since the
    unit announces itself), cut at b's heading on the last page. Chapters always begin on a fresh page, so the last
    page is exclusive when b is a chapter or the end."""
    first, last = a["page"], b["page"]
    if b["kind"] in ("chapter", "end"):
        last = b["page"] - 1
    out = []
    for pno in range(first, min(last, doc.page_count) + 1):
        t = strip_heads(norm(page_text(doc[pno - 1])))
        if pno == first and a["kind"] != "end":
            k = find_heading(t, a)
            if k is None:
                warn(f"heading of {a['marker']} not found on page {pno}; kept the whole page")
            else:
                t = t[k:]
                if a["kind"] in ("section", "summary"):
                    m = heading_pattern(a).match(t) or re.match(r"[^\n]*\n", t)
                    t = t[m.end():] if m else t
        if pno == b["page"] and b["kind"] not in ("chapter", "end"):
            k = find_heading(t, b)
            if k is None:
                warn(f"heading of {b['marker']} not found on page {pno}; {a['marker']} keeps the whole page")
            else:
                t = t[:k]
        out.append(t)
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------------- text

def clean(text):
    lines = []
    raw = [ln.strip() for ln in text.split("\n")]
    for s in raw:
        if not s or HEAD_RE.match(s):
            continue
        if re.match(r"^\d+\.\d+(\.\d+)?\s+\S", s):
            lines.append(s); continue                   # a numbered heading, whatever its letter ratio ("1.3.4 Expert systems (1969-1986)")
        if len(s.split()) == 1 and not any(ch.isdigit() for ch in s) and len(s) < 16 and s[0].isupper() and s not in ("Summary",):
            continue                                    # single-word figure debris (map labels, axis names, margin keywords)
        if re.match(r"^(function|procedure|return|if |else|for each|while|loop|inputs:|persistent:|local variables:)", s) and len(s) < 90:
            continue                                    # pseudo-code figure lines
        letters = sum(ch.isalpha() for ch in s)
        if letters < 0.55 * len(s.replace(" ", "")) and len(s) < 60:
            continue                                    # equation fragments, figure axes
        lines.append(s)
    out = []
    for s in lines:                                     # repair hyphenation and wrapped lines, never across a heading
        prev = out[-1] if out else None
        if prev is not None and not re.match(r"^\d+\.\d+(\.\d+)?\s+\S", prev):
            if re.search(r"\w-$", prev) and re.match(r"\w", s):
                out[-1] = prev[:-1] + s; continue
            if not re.search(r"[.!?:]$", prev) and re.match(r"[a-z(]", s):
                out[-1] = prev + " " + s; continue
        out.append(s)
    return "\n".join(out).strip()


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ϵ": "epsilon", "ε": "epsilon", "θ": "theta", "λ": "lambda", "µ": "mu", "μ": "mu",
         "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "ω": "omega", "Φ": "Phi", "Ω": "Omega", "∆": "delta", "Δ": "delta", "ℓ": "ell"}
SYMBOLS = {"∧": " and ", "∨": " or ", "¬": " not ", "⇒": " implies ", "⇔": " if and only if ", "↔": " if and only if ", "∀": " for all ", "∃": " there exists ",
           "⊨": " entails ", "⊢": " derives ", "⊥": " false ", "⊤": " true ", "∈": " in ", "∪": " union ", "∩": " intersection ", "⊆": " is a subset of ",
           "⊃": " implies ", "≡": " is equivalent to ", "≤": " less than or equal to ", "≥": " greater than or equal to ", "≈": " approximately ", "∞": " infinity ",
           "±": " plus or minus ", "×": " times ", "−": " minus ", "·": " times ", "∑": " the sum over ", "∏": " the product over ", "∂": " partial ",
           "∇": " gradient of ", "√": " the square root of ", "←": " gets ", "→": " to ", "≻": " is preferred to ", "∼": " is indifferent to ", "⟨": " the tuple ",
           "⟩": " ", "′": " prime", "∗": " star", "∥": " parallel to ", "⌊": " floor of ", "⌋": " ", "⊕": " exclusive or ", "◦": " degrees ", "•": "\n",
           "—": ", ", "–": " to ", "“": '"', "”": '"', "‘": "'", "’": "'", "◀": "", "▶": "", "△": " triangle ", "▽": " nabla ", "̸": " not ", "¨": "", "ˆ": " hat ",
           "´": "", "ˇ": "", "˙": "", "˝": "", "˜": " tilde ", "ı": "i"}
BIGO = {"1": "constant", "n": "n", "m": "m", "bd": "b to the d", "bm": "b to the m", "2n": "2 to the n", "n2": "n squared", "n3": "n cubed", "bm/2": "b to the m over 2",
        "bℓ": "b to the ell", "mn": "m times n", "2k": "2 to the k", "n2n": "n times 2 to the n", "bd/2": "b to the d over 2", "b1+C/ε": "b to the 1 plus C over epsilon", "d": "d", "k": "k"}
ACRONYMS = {"TT", "PL", "FOL", "FC", "BC", "KB", "CSP", "MDP", "POMDP", "DBN", "HMM", "BFS", "DFS", "IDS", "UCS", "AC", "SAT", "DPLL", "CNF", "MEU", "VI", "PI", "RL", "TD", "MCTS", "AI"}
ONES = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}


def superscript(e):
    e = e.strip().replace("/", " over ")
    return {"2": " squared ", "3": " cubed ", "T": " transpose ", "-1": " inverse ", "*": " star ", "∗": " star ", "′": " prime "}.get(e, " to the " + e.replace("-", " minus ") + " ")


def subscript(e):
    e = e.strip().replace("+", " plus ").replace("-", " minus ")
    return " " + (ONES[e] if e in ONES else e) + " "


def plain(title):
    """A marker title for the player: superscripts and subscripts back to plain characters."""
    t = re.sub(r"\^\{([^}]*)\}", lambda m: m.group(1) if m.group(1) in ("∗", "*", "′") else "^" + m.group(1), title)
    t = re.sub(r"_\{([^}]*)\}", r"\1", t).replace("∗", "*")
    return re.sub(r"\*(?=[A-Za-z])", "* ", t)


def spoken(t):
    """Rewrite notation into what a narrator would say. Applied after clean()."""
    def ident(m):                                        # BEST-FIRST-SEARCH -> best first search; TT-ENTAILS -> T T entails
        parts = m.group(0).split("-")
        return " ".join(" ".join(p) if (p in ACRONYMS or len(p) <= 2) else p.lower() for p in parts)
    t = re.sub(r"\^\{([^}]*)\}", lambda m: superscript(m.group(1)), t)      # marked by line_text from the typesetting
    t = re.sub(r"_\{([^}]*)\}", lambda m: subscript(m.group(1)), t)
    t = re.sub(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b", ident, t)
    t = re.sub(r"\bO\(([^)]{1,12})\)", lambda m: "big O of " + BIGO.get(m.group(1).replace(" ", ""), m.group(1)), t)
    t = re.sub(r"\bP\(([^()|]{1,40})\|([^()]{1,40})\)", lambda m: f"the probability of {m.group(1).strip()} given {m.group(2).strip()}", t)
    t = re.sub(r"\bP\(([^()]{1,40})\)", lambda m: f"the probability of {m.group(1).strip()}", t)
    t = re.sub(r"\b([a-zA-Z])\(([a-zA-Z0-9]{1,3}(?:, ?[a-zA-Z0-9]{1,3}){0,3})\)", lambda m: f"{m.group(1)} of {m.group(2).replace(',', ', ')}", t)
    t = t.replace("A∗", "A star").replace("A*", "A star").replace("IDA∗", "I D A star").replace("SMA∗", "S M A star")
    for k, v in GREEK.items():
        t = t.replace(k, f" {v} ")
    for k, v in SYMBOLS.items():
        t = t.replace(k, v)
    t = re.sub(r"\b([a-zA-Z])(\d)\b", lambda m: f"{m.group(1)} {ONES[m.group(2)]}", t)
    t = re.sub(r" = ", " equals ", t); t = re.sub(r" ≠ ", " is not equal to ", t)
    t = re.sub(r"(?<=\w) < (?=\w)", " less than ", t); t = re.sub(r"(?<=\w) > (?=\w)", " greater than ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t


def split_topics(text, sec_id):
    """A section's cleaned text -> (head text, [(k, title, text), ...]) split at the numbered topic headings."""
    pat = re.compile(rf"^{re.escape(sec_id)}\.(\d+)\s+(\S.{{2,90}})$")
    head, topics, cur = [], [], None
    for ln in text.split("\n"):
        m = pat.match(ln.strip())
        if m and not ln.rstrip().endswith("."):
            cur = [int(m.group(1)), m.group(2).strip(), []]; topics.append(cur); continue
        (cur[2] if cur else head).append(ln)
    return "\n".join(head).strip(), [(k, ttl, "\n".join(body).strip()) for k, ttl, body in topics]


# ----------------------------------------------------------------------------------------------------- units

def plan_units(doc, chapters, sel, warn=print):
    """Everything to narrate, in book order. Each unit becomes one marker and one audio file."""
    units = []
    for n in sorted(chapters):
        ch = chapters[n]
        want = sel.get(n) if sel is not None else {"label": None, "sections": None, "summary": True}
        if not want:
            continue
        secs = [s for s in ch["sections"] if want["sections"] is None or s["id"] in want["sections"]]
        if not secs and not want["summary"]:
            continue
        label = want["label"]
        anchors = [{"kind": "chapter", "n": n, "page": ch["page"], "marker": f"Chapter {n}: {ch['title']}" + (f" ({label})" if label else "")}]
        for s in ch["sections"]:
            anchors.append({"kind": "section", "id": s["id"], "name": s["name"], "page": s["page"], "marker": f"{s['id']} {s['name']}",
                            "keep": want["sections"] is None or s["id"] in want["sections"]})
        if ch["summary"]:
            anchors.append({"kind": "summary", "n": n, "page": ch["summary"], "marker": f"Summary of Chapter {n}", "keep": want["summary"]})
        if ch["notes"]:
            anchors.append({"kind": "notes", "page": ch["notes"], "marker": "notes", "keep": False})
        anchors.append({"kind": "end", "page": ch["end"], "marker": "end"})
        for a, b in zip(anchors, anchors[1:]):
            if a["kind"] in ("notes",) or not a.get("keep", True):
                continue
            raw = span_text(doc, a, b, warn)
            pages = list(range(a["page"], (b["page"] if b["kind"] not in ("chapter", "end") else b["page"] - 1) + 1))
            if a["kind"] == "chapter":
                teaser = "\n".join(ln for ln in clean(raw).split("\n") if not re.match(r"^CHAPTER\s+\d+", ln) and ln != ln.upper())
                units.append({"kind": "chapter", "chapter": n, "marker": a["marker"], "pages": pages,
                              "text": f"Chapter {n}: {ch['title']}.\n\n{spoken(teaser)}".strip()})
            elif a["kind"] == "summary":
                units.append({"kind": "summary", "chapter": n, "marker": a["marker"], "pages": pages,
                              "text": f"Summary of chapter {n}.\n\n{spoken(clean(raw))}"})
            else:
                head, topics = split_topics(clean(raw), a["id"])
                units.append({"kind": "section", "chapter": n, "marker": a["marker"], "pages": pages,
                              "text": f"Section {a['id']}: {a['name']}.\n\n{spoken(head)}".strip()})
                for k, title, body in topics:
                    units.append({"kind": "topic", "chapter": n, "marker": f"{a['id']}.{k} {plain(title)}", "pages": pages,
                                  "text": f"{spoken(title)}.\n\n{spoken(body)}".strip()})
    for i, u in enumerate(units, 1):
        u["index"] = i
        u["stem"] = f"{i:03d} {re.sub(r'[^A-Za-z0-9 .,()-]+', '', u['marker'])[:70].strip()}"
    return units


def write_text(doc, units, out, title):
    text_dir = out / "text"; text_dir.mkdir(parents=True, exist_ok=True)
    compiled = []
    for u in units:
        (text_dir / f"{u['stem']}.txt").write_text(u["text"] + "\n", encoding="utf-8")
        compiled.append(f"\n\n{'=' * 60}\n{u['marker']}\n{'=' * 60}\n\n{u['text']}")
    (out / f"{title} (spoken).txt").write_text("".join(compiled).strip() + "\n", encoding="utf-8")
    pages = sorted({p for u in units for p in u["pages"] if p <= doc.page_count})
    sel = fitz.open()
    for p in pages:
        sel.insert_pdf(doc, from_page=p - 1, to_page=p - 1)
    sel.save(out / f"{title} (pages).pdf")
    (out / "units.json").write_text(json.dumps([{k: v for k, v in u.items() if k != "text"} | {"chars": len(u["text"])} for u in units], indent=1), encoding="utf-8")
    total = sum(len(u["text"]) for u in units)
    kinds = {k: sum(1 for u in units if u["kind"] == k) for k in ("chapter", "section", "topic", "summary")}
    print(f"{len(units)} units ({kinds['chapter']} chapters, {kinds['section']} sections, {kinds['topic']} topics, {kinds['summary']} summaries), "
          f"{len(pages)} pages, {total:,} characters, about {total / 5.6 / 150 / 60:.1f} h of narration at 150 wpm")
    return total


# ----------------------------------------------------------------------------------------------------- audio

def need(tool):
    if not shutil.which(tool):
        raise SystemExit(f"{tool} not found. Install ffmpeg: Windows 'winget install Gyan.FFmpeg', macOS 'brew install ffmpeg', "
                         "Debian/Ubuntu 'sudo apt install ffmpeg', then open a new terminal.")


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def chunk_text(text, n):
    paras = text.split("\n\n"); chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 > n and cur:
            chunks.append(cur); cur = ""
        cur += (("\n\n" if cur else "") + p)
    if cur:
        chunks.append(cur)
    return chunks


def concat_list(paths):
    return "".join("file '" + p.as_posix().replace("'", r"'\''") + "'\n" for p in paths)


async def speak(text, path, voice, rate):
    import edge_tts
    last = None
    for attempt in range(6):
        try:
            await edge_tts.Communicate(text, voice, rate=rate).save(str(path))
            if path.exists() and path.stat().st_size > 0:
                return
        except Exception as e:                        # the free endpoint drops connections now and then; back off and retry
            last = e
        await asyncio.sleep(4 * (attempt + 1))
    raise RuntimeError(f"narration failed for {path.name}: {last}")


async def narrate_all(units, audio, voice, rate, jobs):
    sem = asyncio.Semaphore(jobs)
    todo = [u for u in units if not (audio / f"{u['stem']}.mp3").exists()]
    done = 0

    async def one(u):
        nonlocal done
        mp3 = audio / f"{u['stem']}.mp3"
        parts = []
        async with sem:
            for i, c in enumerate(chunk_text(u["text"], CHUNK)):
                part = audio / f"{u['stem']}.part{i:03d}.mp3"
                if not part.exists():
                    await speak(c, part, voice, rate)
                parts.append(part)
        lst = audio / f"{u['stem']}.list"
        lst.write_text(concat_list(parts), encoding="utf-8")
        await asyncio.to_thread(run, ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(mp3)])
        for p in parts:
            p.unlink()
        lst.unlink()
        done += 1
        print(f"  [{done}/{len(todo)}] {u['marker'][:60]:60} {duration(mp3) / 60:5.1f} min", flush=True)

    print(f"narrating {len(todo)} of {len(units)} units with {voice} ({jobs} at a time)")
    await asyncio.gather(*(one(u) for u in todo))


def esc(s):
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def assemble(units, audio, out_path, tags, cover, bitrate):
    """Concatenate the units' audio into one M4B; each unit is one marker."""
    lst = audio / (out_path.stem + ".list")
    lst.write_text(concat_list([audio / f"{u['stem']}.mp3" for u in units]), encoding="utf-8")
    meta = [";FFMETADATA1"] + [f"{k}={esc(v)}" for k, v in tags.items()]
    t, marks = 0.0, []
    for u in units:
        d = duration(audio / f"{u['stem']}.mp3")
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t * 1000)}", f"END={int((t + d) * 1000)}", f"title={esc(u['marker'])}"]
        marks.append((t, u["marker"])); t += d
    mf = audio / (out_path.stem + ".ffmeta")
    mf.write_text("\n".join(meta) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(mf), "-i", str(cover),
         "-map", "0:a", "-map", "2:v", "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", bitrate, "-ac", "1",
         "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)",
         "-movflags", "+faststart", str(out_path)])
    lines = []
    for s, m in marks:
        h, rem = divmod(int(s * 1000), 3600000); mnt, rem = divmod(rem, 60000); sec, ms = divmod(rem, 1000)
        lines.append(f"{h:02d}:{mnt:02d}:{sec:02d}.{ms:03d} {m}")
    out_path.with_suffix(".chapters.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lst.unlink(); mf.unlink()
    print(f"wrote {out_path.name}: {t / 3600:.2f} h, {len(units)} markers")
    return t


def make_cover(doc, path, custom):
    if custom:
        shutil.copy2(custom, path)
    elif not path.exists():
        doc[0].get_pixmap(dpi=110).save(str(path))


# ----------------------------------------------------------------------------------------------------- commands

def load(args):
    pdf = pathlib.Path(args.pdf)
    if not pdf.exists():
        raise SystemExit(f"no such file: {pdf}")
    doc = fitz.open(pdf)
    return doc, outline(doc), parse_selection(args.select)


def cmd_toc(args):
    doc, chapters, sel = load(args)
    for n in sorted(chapters):
        ch = chapters[n]
        want = sel.get(n) if sel is not None else {"sections": None, "summary": True}
        flag = "*" if want else " "
        print(f"{flag} Chapter {n}: {ch['title']}  (pages {ch['page']}-{ch['end'] - 1})")
        for s in ch["sections"]:
            inc = want and (want["sections"] is None or s["id"] in want["sections"])
            print(f"    {'*' if inc else ' '} {s['id']:6} {s['name']}  p{s['page']}")
        if ch["summary"]:
            print(f"    {'*' if want and want['summary'] else ' '} {'S':6} Summary  p{ch['summary']}")
    print("\n* = in the current selection" + (f" ({args.select})" if args.select else " (everything)"))


def book_title(args, sel):
    if args.title:
        return args.title
    if args.select in PRESETS:
        return f"{SHORT} ({args.select} readings)"
    return f"{SHORT} (selection)" if sel else SHORT


def cmd_text(args):
    doc, chapters, sel = load(args)
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    units = plan_units(doc, chapters, sel, warn=lambda m: print("  note:", m))
    write_text(doc, units, out, book_title(args, sel))
    print(f"text in {out}")


def cmd_build(args):
    need("ffmpeg"); need("ffprobe")
    doc, chapters, sel = load(args)
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)
    title = book_title(args, sel)
    units = plan_units(doc, chapters, sel, warn=lambda m: print("  note:", m))
    write_text(doc, units, out, title)
    audio = out / "audio"; audio.mkdir(exist_ok=True)
    asyncio.run(narrate_all(units, audio, args.voice, args.rate, args.jobs))
    cover = out / "cover.jpg"; make_cover(doc, cover, args.cover)
    base = {"artist": AUTHORS, "album_artist": AUTHORS, "album": BOOK, "genre": "Textbook", "date": "2021",
            "composer": f"{args.voice} (synthetic narration)",
            "comment": f"Made with aima-audiobook from the reader's own copy. Markers: chapters, sections, numbered topics, summaries. "
                       f"Selection: {args.select or 'all'}. Notation rewritten into spoken form."}
    chapter_ns = sorted({u["chapter"] for u in units})
    if args.per_chapter:
        for i, n in enumerate(chapter_ns, 1):
            cu = [u for u in units if u["chapter"] == n]
            fn = out / f"{SHORT} - Chapter {n:02d} - {re.sub(r'[^A-Za-z0-9 ,-]+', '', chapters[n]['title'])}.m4b"
            assemble(cu, audio, fn, {**base, "title": f"Chapter {n}: {chapters[n]['title']}", "track": f"{i}/{len(chapter_ns)}"}, cover, args.bitrate)
    if not args.no_single:
        total = assemble(units, audio, out / f"{title}.m4b", {**base, "title": title, "track": "1/1"}, cover, args.bitrate)
        print(f"done: {out / (title + '.m4b')} ({total / 3600:.2f} h)")


def cmd_voices(args):
    import edge_tts
    voices = asyncio.run(edge_tts.list_voices())
    for v in sorted(voices, key=lambda v: v["ShortName"]):
        if v["Locale"].startswith(args.locale):
            print(f"{v['ShortName']:42} {v['Gender']:7} {', '.join(v.get('VoiceTag', {}).get('VoicePersonalities', []))}")
    print(f"\ndefault: {VOICE}. Multilingual voices read the odd Greek letter and formula name most naturally.")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="aima-audiobook", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("pdf", help="your own PDF of AIMA 4th edition (must carry the publisher's outline)")
        p.add_argument("--select", default="all", help=f"'all', a preset ({', '.join(PRESETS)}), or tokens like 3,6.1-6.5,3.4,12.S")
        p.add_argument("--out", default="aima-audiobook", help="output folder (default ./aima-audiobook)")
        p.add_argument("--title", help="book title for the tags and file name")

    common(sub.add_parser("toc", help="show the outline and what the selection covers"))
    common(sub.add_parser("text", help="spoken-form text only, for ElevenReader or any TTS app"))
    b = sub.add_parser("build", help="text, narration and M4B with chapter, section and topic markers")
    common(b)
    b.add_argument("--voice", default=VOICE, help=f"edge-tts voice (default {VOICE}); see 'aima-audiobook voices'")
    b.add_argument("--rate", default="+0%", help="speaking rate, e.g. +10%% (default +0%%)")
    b.add_argument("--jobs", type=int, default=2, help="parallel narration requests (default 2)")
    b.add_argument("--per-chapter", action="store_true", help="also write one M4B per chapter")
    b.add_argument("--no-single", action="store_true", help="skip the single whole-selection M4B")
    b.add_argument("--cover", help="cover image (default: the PDF's first page)")
    b.add_argument("--bitrate", default="64k", help="AAC bitrate (default 64k, mono)")
    v = sub.add_parser("voices", help="list the narration voices")
    v.add_argument("--locale", default="en", help="locale prefix (default en)")

    args = ap.parse_args(argv)
    {"toc": cmd_toc, "text": cmd_text, "build": cmd_build, "voices": cmd_voices}[args.cmd](args)


if __name__ == "__main__":
    main()
