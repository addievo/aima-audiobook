# aima-audiobook

Turn **your own PDF** of Russell and Norvig, *Artificial Intelligence: A Modern Approach* (4th edition) into a chapterised audiobook, with the notation read the way a lecturer says it: `∀x P(x) ⇒ Q(x)` comes out as "for all x, P of x implies Q of x", `A*` as "A star", `O(b^d)` as "big O of b to the d", `P(x | e)` as "the probability of x given e".

Bring your own book. This repository contains no text from it. The narration is free (Microsoft's neural voices through `edge-tts`, no account, no key) and runs on your machine; a full chapter takes about ten minutes of wall time.

## Install (one line)

macOS / Linux:

```
curl -fsSL https://raw.githubusercontent.com/addievo/aima-audiobook/main/install.sh | bash
```

Windows (PowerShell):

```
irm https://raw.githubusercontent.com/addievo/aima-audiobook/main/install.ps1 | iex
```

Either line installs [uv](https://docs.astral.sh/uv/) if you do not have it, the `aima-audiobook` command, and ffmpeg if it is missing. If you already have uv or pipx: `uv tool install git+https://github.com/addievo/aima-audiobook` or `pipx install git+https://github.com/addievo/aima-audiobook`, plus ffmpeg from your package manager.

## Use

```
aima-audiobook toc   book.pdf                        # what the PDF's outline gives us
aima-audiobook build book.pdf --select rmit-ai26     # the RMIT COSC1127 (2026) prescribed readings, one M4B
aima-audiobook build book.pdf                        # the whole book (about 50 hours of audio; leave it overnight)
aima-audiobook build book.pdf --select 3,6.1-6.5,12.S --per-chapter --voice en-GB-RyanNeural
aima-audiobook text  book.pdf --select 7             # spoken-form text only, for ElevenReader or any other TTS app
aima-audiobook voices                                # the English voices
```

The PDF must be the 4th-edition PDF with the publisher's outline (bookmarks): the tool reads chapters and sections from it and never guesses page ranges. `toc` shows what it found.

### Selecting what to narrate

`--select` takes `all`, a preset, or a comma list of tokens:

| Token | Means |
|---|---|
| `3` | chapter 3, every section and its summary |
| `6.1-6.5` | sections 6.1 to 6.5 (no summary, since it would cover sections you skipped) |
| `3.4` | one section |
| `12.S` | the summary of chapter 12 |
| `rmit-ai26` | preset: the sections RMIT's COSC1127/3117 (2026) prescribes for Weeks 1 to 12 |

Any chapter you touch is announced with its opening line, so the listener always knows where they are.

### What you get

- `<title>.m4b`: one audiobook with a marker for **every chapter, every section, every numbered topic inside a section, and every summary**. A player's chapter list reads: Chapter 3: Solving Problems by Searching, 3.1 Problem-Solving Agents, 3.1.1 Search problems and solutions, 3.1.2 Formulating problems, 3.2 Example Problems, and so on.
- `<title>.chapters.txt`: the same markers as timestamps, for players that import them.
- `--per-chapter` adds one M4B per chapter with the same markers, tagged as tracks.
- `<title> (spoken).txt`: the narration script, if you would rather feed a premium voice such as ElevenReader.
- `<title> (pages).pdf`: exactly the pages that were narrated.
- `audio/`: one MP3 per unit. Narration resumes from here if it is interrupted.

Tags: title, authors, album, genre Textbook, cover from the PDF's first page (or `--cover`), narrator recorded as the voice name.

### How the notation is spoken

Text-to-speech reads maths as typed, which is the difference between a listenable chapter and noise. Before synthesis every unit goes through a spoken-form rewriter:

| On the page | Spoken |
|---|---|
| `∧ ∨ ¬ ⇒ ⇔ ∀ ∃ ⊨ ⊢` | and, or, not, implies, if and only if, for all, there exists, entails, derives |
| `α β γ … λ π σ` | alpha, beta, gamma, lambda, pi, sigma |
| `A*`, `IDA*` | A star, I D A star |
| `h(n)`, `f(n)`, `Q(s,a)` | h of n, f of n, Q of s, a |
| `P(x | e)`, `P(x)` | the probability of x given e, the probability of x |
| `O(b^d)`, `O(n²)` | big O of b to the d, big O of n squared |
| `x₁`, `s₂` | x one, s two |
| `BEST-FIRST-SEARCH`, `TT-ENTAILS` | best first search, T T entails |
| `≤ ≥ ≈ ∞ ∑ √ ←` | less than or equal to, greater than or equal to, approximately, infinity, the sum over, the square root of, gets |

Running heads, page numbers, pseudo-code figure lines and figure debris are dropped, hyphenation across lines is repaired, and the bibliographical notes are left out. Chapter summaries are kept: they are the best five minutes of every chapter for a walk.

### Voices

The default is `en-AU-WilliamMultilingualNeural`. Multilingual voices handle the odd Greek letter and formula name most naturally. `aima-audiobook voices` lists the English ones; `--voice` picks, `--rate +10%` speeds up.

## What listening is good for

Audio matches print for prose and lags it for technical material (Rogowsky, Calhoun and Tallal 2016; Daniel and Woody 2010), so this carries the concepts and the vocabulary, not the derivations. Re-listening has diminishing returns; retrieval practice and spacing are what move marks (Dunlosky et al. 2013). Listen on walks, then do the problems.

## Share this tool, not your audio

The audio you make from your copy is a private format-shift of a copyrighted textbook. Point people at this repository so they can make their own from their own copy. Do not post the files.

## Credits

[edge-tts](https://github.com/rany2/edge-tts) for the voices, [PyMuPDF](https://pymupdf.readthedocs.io/) for the pages, [ffmpeg](https://ffmpeg.org/) for the M4B. MIT licence.
