# A Plain-English Guide to the SOLIDWORKS File Format

> **Who this is for:** anyone who has never cracked open a binary file and
> wants to understand what's *actually inside* a `.sldprt`, `.sldasm`, or
> `.slddrw` — and how this project reads and edits them **without
> SOLIDWORKS installed**. No prior file-format knowledge assumed. Jargon is
> defined the first time it appears. If you only read one doc, read this
> one; the others (`ARCHITECTURE.md`, `REVERSE_ENGINEERING.md`) go deeper.

---

## 1. The one-sentence version

A modern SOLIDWORKS file is **a container** — think of a single `.zip` that
holds many smaller files inside it — except SOLIDWORKS uses its **own
custom container format** instead of ZIP, and most of the smaller files
inside are **compressed** and have **scrambled names**.

Our job is to (1) open the container, (2) list and read the little files
inside, (3) change one of them, and (4) re-seal the container so that
SOLIDWORKS still happily opens it. The tricky part is step 4 — re-sealing
it correctly — and most of this guide builds up to *why*.

---

## 2. A useful mental model: a filing cabinet

Picture a **filing cabinet** (the `.sldprt` file on disk).

- Inside are many **folders** (we call them **streams**). Each folder holds
  one kind of information: one folder for the part's custom properties, one
  for the 3D geometry, one for the little thumbnail preview image, one for
  the "save history", and so on. A small part has ~40 folders; a big
  assembly can have hundreds.
- Each folder has a **label** (the stream's *name*, e.g.
  `docProps/custom.xml` or `Contents/DisplayLists`).
- The paper inside each folder is **shrunk down to save space**
  (compressed). To read a folder you first "un-shrink" it (decompress).
- At the **back of the cabinet** there's an **index card box** (the
  **TOC**, short for *Table Of Contents*, also called the *directory*).
  The index has one card per folder, and each card says *"folder X starts
  at this position and is this many pages long."*

That last part — the index that records **where each folder is** — is the
single most important idea in this whole document. Remember it.

---

## 3. Two eras of SOLIDWORKS files

SOLIDWORKS changed its container format over the years. There are two big
families:

| Era | Roughly | Container style | This project |
|---|---|---|---|
| **Old (OLE2)** | before ~2015 | Microsoft "compound file" (the same tech old `.doc`/`.xls` used) | not decoded here |
| **Modern** | ~2015 and newer | SOLIDWORKS' own custom container | **this is what we decode** |

How do we tell them apart? We peek at the **first few bytes** of the file
(its *signature* / *magic number*):

- Old OLE2 files start with the bytes `D0 CF 11 E0 A1 B1 1A E1` (a famous
  signature nicknamed "docfile").
- Modern files contain a special 6-byte **marker** `14 00 06 00 08 00` very
  near the start.

(`detect_format()` in the code does exactly this peek.) Everything below is
about the **modern** format.

---

## 4. What "bytes" and "hex" mean (skip if you know)

A file on disk is just a long row of **bytes**. One byte is a number from 0
to 255. We usually write bytes in **hexadecimal** ("hex") because it's
compact: hex `FF` = 255, hex `14` = 20, hex `00` = 0. When you see
`14 00 06 00 08 00`, that's six bytes in a row.

A few bytes grouped together can encode a bigger number. Four bytes encode
a number up to ~4.2 billion; we call that a **uint32** ("unsigned 32-bit
integer"). SOLIDWORKS stores most sizes and positions as uint32s, written
**little-endian** — a quirk meaning the *least* significant byte comes
first. So the four bytes `BB 02 00 00` are read as `0x02BB` = **699**, not
`0xBB020000`. Little-endian trips up everyone once; now you're warned.

---

## 5. Inside the modern container: chunks

The modern file is a flat sequence of **chunks**. A *chunk* is one folder
from our filing-cabinet analogy, stored as a small **header** followed by
the **compressed data**.

Every chunk header begins with that 6-byte **marker** `14 00 06 00 08 00`,
which is how we find chunks: we scan the file looking for the marker, like
finding staples that hold each folder together. Right after the marker, the
header records a handful of numbers. The ones that matter:

```
        ┌─ 4 "val_a" bytes (a fixed per-file signature; not a size)
        │           ┌─ the 6-byte marker  14 00 06 00 08 00
        │           │            ┌─ section_type (what kind of chunk)
        ▼           ▼            ▼
   [val_a]  [14 00 06 00 08 00] [type] [..] [f1] [csz] [usz] [nsz] [name...] [compressed data...]
                                              │     │     │     │     │
   f1   = a flag/size field                   │     │     │     │     └─ the scrambled stream name
   csz  = Compressed SiZe (bytes on disk) ────┘     │     │     └─ Name SiZe (how long the name is)
   usz  = UncompreSsed siZe (bytes after un-shrink)─┘─────┘
```

So from one header we learn: what type of chunk this is, how many bytes its
compressed data takes on disk (**csz**), how big it becomes once
decompressed (**usz**), and its name.

### The name is scrambled (ROL encoding)

The stream names aren't stored as plain text. Each letter is **rotated**
bit-by-bit by a secret amount — a reversible scramble called **ROL**
(rotate-left). The "secret amount" (the *key*) is simply **the 8th byte of
the file** (`data[7]`). Unscramble with that key and `docProps/custom.xml`
pops out. (`rol_decode()` does this. It's obfuscation, not encryption —
trivial to reverse once you know the trick.)

### The data is compressed (raw DEFLATE)

The compressed data uses **DEFLATE**, the same algorithm inside ZIP and
gzip — but SOLIDWORKS stores it "raw", without the usual wrapper bytes. In
Python that's `zlib` with the magic setting `wbits=-15`. Decompress and you
get the real content (XML text, geometry, an image, etc.).

---

## 6. What's actually in those streams?

You'll meet the same folder names again and again. The friendly tour:

| Stream name | Plain-English contents |
|---|---|
| `docProps/custom.xml` | **Custom properties** — the part number, material, revision, weight, description… as human-readable XML. (This is the one M2 edits.) |
| `docProps/core.xml` | Author, title, **last-saved time**. |
| `Contents/DisplayLists` | The **graphics cache** — a pre-chewed copy of the 3D view so SOLIDWORKS can draw the model instantly. Big and rebuilt on every save. |
| `Contents/Definition` | Part of the real **feature/geometry definition** (the actual model recipe). Hard to decode (that's the far-future milestone M5). |
| `Config-0-FeatureBodies/LocalBodies` | The solid **body geometry** (Parasolid-flavored binary). |
| `Preview` / `PreviewPNG` | The little **thumbnail** you see in Explorer. |
| `_MO_VERSION_NNNNN/Biography` | The file's **edit/save history**. The `NNNNN` in the name is the format **version** number. |
| `[Content_Types].xml`, `_rels/.rels` | Bookkeeping files borrowed from the **OPC** packaging convention (the same idea Office `.docx` uses). Their presence is a strong hint the whole container is "OPC-like". |
| `ThirdPtyStore/*` | Data parked by **add-ins** (toolbox, etc.). We never poke inside these. |

Most of these we treat as **opaque** — we can copy them around perfectly
but we don't pretend to understand their innards. That's a deliberate,
honest stance, not a gap to apologize for: you don't need to understand the
graphics cache to change a part number.

---

## 7. The TOC: the index card box (and why it rules everything)

Near the **end** of the file is a region full of **directory records** —
one per stream — that together form the **TOC** (Table Of Contents). Each
record stores, for its stream:

- the **csz** (compressed size) and **usz** (uncompressed size), and
- the stream's **position in the file**, i.e. its byte **offset**.

Here's the crucial twist we discovered by experiment. SOLIDWORKS does **not
trust the markers** to find chunks when it opens a file — it trusts the
**TOC**. It reads the index card for `docProps/custom.xml`, sees "starts at
byte 85,112, is 699 bytes long", and jumps straight there.

That means: **if you change a chunk's size, every chunk after it slides to
a new position — and now all those TOC index cards point to the wrong
places.** SOLIDWORKS jumps to a stale position, reads garbage, and refuses
the file with the error `swFileRequiresRepairError`. (Confusingly, the
error text mentions "custom property data corruption" even when the change
was somewhere unrelated — it's just the first sanity check that trips.)

### The offset is stored with a `-8` twist

One more empirical detail worth knowing because it cost real effort to
find: the position in each TOC record is stored as **`offset − 8`** (a
uint32). A chunk that truly starts at byte 572 has `564` written in its
index card. We don't fully know *why* it's minus eight (some preamble
reference point), but the rule is exact and consistent everywhere. There's
also a **self-pointer** (the TOC recording where the TOC itself begins) and
a few other loose pointers — all using the same `offset − 8` encoding, and
all living in the "gaps" between chunks (never inside compressed data).

---

## 8. Why two saves of the same file look totally different

A surprise for newcomers: open a part, save it, wait ten seconds, save it
again with **no changes** — and the two files are **not identical**, not
even the same length. We measured this carefully (the "twin-save baseline",
milestone M0.5): across 9 varied files, **none** were byte-stable.

Why? Several streams are regenerated every save:

- **timestamps** (the save clock ticks),
- **regenerated IDs / hashes** (fresh random-looking values each save),
- the **graphics cache** and **save history** (re-encoded, often a
  different length),

and DEFLATE itself can compress the same content to slightly different
bytes. None of this is corruption — it's just non-determinism.

This has a big consequence for *testing*: you cannot prove our writer is
correct by demanding "our output is byte-identical to what SOLIDWORKS would
have written" — SOLIDWORKS isn't even byte-identical to *itself*. So the
real test is **semantic equivalence**: write the file, reopen it in real
SOLIDWORKS, and check the *meaning* matches (same configurations, same
property values, same mass, same sheet names). That's the project's primary
quality gate ("Layer 3").

---

## 9. How we read, modify, and write — the safe way

Putting it together, here's the whole pipeline in plain terms.

**Read.** Scan for markers → for each chunk, unscramble the name and (if
asked) decompress the data. Everything that *isn't* a recognized chunk —
the file header at the front, padding, and the TOC at the back — we keep
verbatim as **gaps**. The golden rule: **account for every single byte**,
either as a chunk or as a gap. Concatenate them all back and you must get
the original file *exactly* (we call this "no orphan bytes").

**Modify — two cases:**

1. *Change nothing* (or copy the file): re-emit every chunk's original
   bytes untouched. Result is byte-for-byte identical to the input. We call
   this the **lazy round-trip**, and it's always safe. (It also dodges the
   DEFLATE non-determinism above, because we never re-compress what we
   didn't change.)

2. *Change a stream's content* (e.g. edit a property): re-compress just that
   stream. Its size changes, so chunks after it slide — which means we
   **must also fix the TOC**: update that stream's csz/usz card, and shift
   every `offset − 8` pointer whose target moved. Do that and SOLIDWORKS
   reopens the file happily. (This is what `write_with_toc` does, and it's
   the breakthrough that unlocks editing.)

**Write.** Glue the (possibly re-compressed) chunks and the verbatim gaps
back together in original order, apply the TOC fixes, save.

**Verify.** Reopen the result in real SOLIDWORKS (via the `combridge`
bridge) and confirm the meaning is intact.

---

## 10. A tiny worked example: changing a part number

Suppose `docProps/custom.xml` contains `…<property name="REVISION">…<vt:lpstr>0</vt:lpstr>…`
and we want the revision to read `B`.

1. **Read** the file; decompress `docProps/custom.xml` to get its XML text.
2. **Edit the text**: replace the `0` with `B` (it's just XML — find the
   REVISION element and change its value).
3. **Re-compress** the edited XML. Say it's now 4 bytes longer.
4. Everything after `custom.xml` in the file shifts 4 bytes later.
5. **Fix the TOC**: bump `custom.xml`'s recorded csz/usz, and add 4 to the
   `offset − 8` pointer of every chunk that moved (and the TOC
   self-pointer).
6. **Write** the new file.
7. Open it in SOLIDWORKS → it shows REVISION = `B`. Done — and SOLIDWORKS
   never ran during steps 1–6.

We've actually done this end-to-end (with a longer test value); SOLIDWORKS
reopened the file cleanly and reported the new value. A neat bonus: the
properties are *also* cached in a separate binary stream
(`Contents/CusProps`), but it turns out `docProps/custom.xml` **wins** — we
can leave the binary cache stale and SOLIDWORKS still believes the XML.

---

## 11. What's solid, what's still mysterious

**Confidently understood (and tested):**
- The container framing: markers, chunk headers, ROL names, raw DEFLATE.
- The TOC and its `offset − 8` pointer encoding; how to rewrite it.
- Reading any stream; faithfully copying any file; editing text streams
  like custom properties so SOLIDWORKS accepts the result.

**Deliberately opaque (we copy, we don't interpret):**
- The graphics cache (`DisplayLists`), thumbnails, add-in stores.

**Still genuinely hard (future research):**
- The real **geometry/feature recipe** (`Contents/Definition`, the bodies).
  This is the long-tail "decode the CAD model itself" work (milestone M5)
  that even long-running open-source efforts have only partially achieved.

---

## 12. Mini-glossary

- **Byte** — a number 0–255; files are sequences of bytes.
- **Hex** — base-16 notation for bytes (`FF` = 255).
- **uint32 / little-endian** — a 4-byte number, least-significant byte
  first (`BB 02 00 00` = 699).
- **Chunk** — one stored stream: a header + compressed data.
- **Stream** — one logical "folder" of content, identified by a name.
- **Marker** — the 6 bytes `14 00 06 00 08 00` that begin each chunk header.
- **ROL** — the rotate-left scramble applied to stream names; key = `data[7]`.
- **DEFLATE / `wbits=-15`** — the (raw) compression used for chunk data.
- **csz / usz** — compressed size / uncompressed size of a chunk.
- **TOC (directory)** — the index near the file's end mapping each stream to
  its size and position.
- **Offset** — a byte position within the file. Stored in the TOC as
  `offset − 8`.
- **Gap** — any bytes that aren't part of a recognized chunk (file header,
  padding, the TOC); copied verbatim.
- **Lazy round-trip** — re-writing unchanged chunks byte-for-byte.
- **OPC** — Open Packaging Convention, the Office-style "container of parts"
  idea the modern SW format borrows from.
- **Semantic equivalence (Layer 3)** — the real correctness test: reopen in
  SOLIDWORKS and check the *meaning* matches, since exact bytes are
  non-deterministic.

---

*Want the next level of detail? `docs/ARCHITECTURE.md` gives the five-layer
engineering model; `docs/REVERSE_ENGINEERING.md` shows the experimental
method we used to figure all this out; and the dated lab notebooks under
`research/empirical_findings/` show the actual experiments, including the
ones that disproved our first guesses.*
