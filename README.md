# Unicode reference glyph SVGs

**Reference only. The source font licenses are highly restrictive. No reuse or
redistribution rights to the chart artwork or font designs are granted.**

This public repository contains reusable extraction and verification scripts.
The [SVGZ collection and display website](https://github.com/iebb/unicode-glyphs)
are published separately, with [block navigation](https://iebb.github.io/unicode-glyphs/navigation.html).

Extracted from the official **Unicode 18.0.0 consolidated CodeCharts.pdf**:
https://www.unicode.org/Public/18.0.0/charts/CodeCharts.pdf

This is the complete repertoire shown in that specimen PDF, including characters
introduced in earlier Unicode releases. It is not a collection of historical
glyph revisions from every earlier release.

The finished output is in `glyphs/18.0.0/`. The inventory is
`glyphs/18.0.0/manifest.jsonl`, with one record per SVG. `summary.json` gives
coverage counts and `validation.json` records verification results.

## Naming

Zero-pad the hexadecimal code point to six digits, then split it into pairs:

| Code point | File |
| --- | --- |
| U+0041 | `U00/00/41.svg` |
| U+11111 | `U01/11/11.svg` |
| U+4E00, China source | `U00/4E/00/G0-523B.svg` |
| U+4E00, Japan source | `U00/4E/00/J0-306C.svg` |
| U+210B, standardized variant | `U00/21/0B/FE00.svg` |
| U+3D028, Small Seal THX source | `U03/D0/28/TH-00038.svg` |

A single displayed form becomes `<codepoint>.svg`. Multiple displayed forms use
`<codepoint>/<source-identifier>.svg`. Variation tables use the selector as the
filename, with `reference.svg` for the main grid form and `table-reference.svg`
for the base form repeated in the variation table. Repeated base forms are kept
as separate documented specimens; regional source forms are also retained when
their outlines happen to be identical.

Unsafe filename characters in source identifiers become underscores. The exact
printed identifier remains in the manifest and SVG metadata.

## Run again

Python 3.10+ is required. Run commands from this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/download_unicode.py
.venv/bin/python scripts/extract_glyphs.py
.venv/bin/python scripts/verify_glyphs.py
.venv/bin/python scripts/audit_pdf_coverage.py
.venv/bin/python scripts/test_extractor.py
```

The downloader saves official source files, URLs, sizes, and SHA-256 hashes in
`sources/18.0.0/sources.json`. Downloads are cached. PDF parsing and vector
extraction work offline after the source files have been downloaded.

If extraction was interrupted, reuse completed page checkpoints:

```bash
.venv/bin/python scripts/extract_glyphs.py --resume
```

To regenerate specific pages with the same input and settings:

```bash
.venv/bin/python scripts/extract_glyphs.py --resume --reprocess-pages 377,558-559
```

Use `--output` for a fresh destination. For a small experimental run:

```bash
.venv/bin/python scripts/extract_glyphs.py --pages 2,34,377,558,3150 --output preview
.venv/bin/python scripts/verify_glyphs.py --output preview --samples 30
```

Other specimen PDFs may be supplied with `--pdf`, `--ucd`, `--version`, and
`--source-url`. The parser is designed and tested for Unicode 18's chart layouts;
it reports unmapped outlines and missing characters instead of substituting an
installed font. A page-limited run is explicitly marked as partial and does not
claim complete coverage.

## Extraction and validation

The extractor converts the PDF's own outlines to standalone SVG paths. It uses
printed hexadecimal labels and table geometry to identify code points, because
many PDF font mappings identify the wrong Unicode character. It handles regular
grids, mixed grid/names-list pages, CJK source rows that wrap across multiple
lines, wrapped source identifiers, standardized variation tables, and the new
Jurchen and Small Seal reference forms.

Each glyph SVG has a transparent background, a tight viewBox, and one PDF point of
padding. The 66 noncharacter chart conventions retain their native black cell
background and white hexadecimal label. No SVG needs an external font, image, or
linked resource.
Chart dotted-circle helpers are preserved by default. To extract just the mark,
use `--remove-dotted-circle` in a new output directory.

The manifest retains each specimen's PDF page, bounding box, block, exact source
identifier, font names, character name/category, variation sequence when
applicable, and PDF hash. Tight cropping removes chart-cell advance and alignment;
use the original PDF coordinates when reconstructing a cell layout.

Validation checks every SVG's XML, paths, metadata, naming, inventory uniqueness,
and UCD coverage. CJK, Tangut, Jurchen, and Small Seal identities are checked
against independent official source data. Displayed standardized sequences are
checked against `StandardizedVariants.txt`. Raster samples include every chart
block plus deterministic random specimens. The SVG is placed back at its original
PDF coordinates before comparison, with an explicit 0.25-point edge tolerance
for PDF font hinting. Intersecting chart borders are excluded; thin specimens
that need more precision are checked again at twice the raster resolution.
Validation fails for missing characters, unknown identities,
unexpected files, unmapped outlines, or excessive raster differences.

`audit_pdf_coverage.py` performs a separate comparison of every displayed chart
outline with the actual exported SVGs. It reads printed codepoint labels directly
from all PDF pages, inventories reference-font paths independently of the
extractor's selection and assignment functions, and restores exported outlines to
their original page coordinates. Exact path data, transforms, paint attributes,
and occurrence counts must match, with only a 0.00001-point tolerance for serialized
coordinate rounding. Its complete result is saved as `pdf-audit.json`.
Noncharacter cells without a reference font outline are retained as the PDF's
black rectangle and white label; the audit checks the background against its
native cell geometry and the label against the original PDF outlines. Every
printed chart codepoint must be present. A regression check intentionally changes an SVG outline in a
temporary copy and verifies that this audit rejects it.

To repeat the file and identity checks without replacing the visual report:

```bash
.venv/bin/python scripts/verify_glyphs.py --samples 0 --report-name metadata-validation.json
```

Control characters and whitespace retain the PDF's visible reference conventions.
The PDF supplies conventions for all 66 noncharacters; their black cells with
white labels are retained, along with any native placeholder font paths. These are included and counted
separately. Other unassigned cells, surrogate ranges,
and private-use ranges do not have individual reference glyphs to extract. The
PDF does not depict every possible emoji sequence, shaping combination, or IVD
variant, so those are outside this specimen inventory.

## Source notices

The original PDF's notices are preserved in every SVG's metadata and in
`SOURCE-NOTICES.txt`. This extraction does not grant redistribution rights to the
chart artwork or its licensed font designs. The source terms are available at
https://www.unicode.org/copyright.html.

## Compact output and monthly CI

The extractor produces plain SVG. Publish lossless SVGZ with:

```bash
.venv/bin/python scripts/compress_glyphs.py --source glyphs/18.0.0 --destination compressed
gzip -dc compressed/U01/11/11.svgz > U01-11-11.svg
```

Every compression is checked by a byte-for-byte round trip. Embedded metadata and
source notices remain intact. A compressed manifest retains both original and
published paths, checksums and byte sizes.

The `Extract latest release and draft` GitHub Actions workflow runs at **04:17 UTC
on the first day of every month**, and can be run manually. It discovers the final
release from the official latest-version ReadMe and the public draft from the
draft ReadMe files. An empty draft directory may return HTTP 200 and HTML; it is
treated as unavailable, not as a release. Stale beta files for a released version
are ignored. Draft-only assigned codepoints are red and explicitly labeled Draft
in the collection website. Released characters' draft forms remain separate.

GitHub can disable scheduled workflows in public repositories after 60 days
without repository activity. After each successful monthly check, a separate
keepalive job commits `.github/last-check.json` to this repository, including
when Unicode sources are unchanged. Only that job receives `contents: write`;
the extraction job retains `contents: read` and its dedicated collection key.
If the schedule is disabled after repeated failed runs, reenable it in Actions
or run `gh workflow enable monthly.yml --repo iebb/unicode-glyph-extractor`.
See [GitHub's schedule rules](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The workflow hashes all inputs and extractor scripts, skips unchanged snapshots,
and only pushes after inventory, identity, raster, whole-PDF and compression
checks pass. Future chart layouts are not assumed to work: extraction or audit
failure stops publication. No unavailable draft specimens are invented.

Cross-repository updates use the `GLYPHS_DEPLOY_KEY` Actions secret: an SSH deploy
key with write access only to `iebb/unicode-glyphs`. Plane-sized commits are pushed
to a temporary branch, then `main` advances to the complete verified snapshot.
The glyph repository's Pages workflow publishes its browser and compact indexes.
To rotate the key, add a new write deploy key to the glyph repository and replace
the extractor repository's secret with its private key. Credentials are never
committed to either repository.

To use the update pipeline locally with a checkout of the collection repository:

```bash
.venv/bin/python scripts/discover_unicode.py --output discovery.json
.venv/bin/python scripts/test_updates.py
.venv/bin/python scripts/update_glyph_repo.py --repo ../unicode-glyphs --work /tmp/unicode-update
```

The staging script writes local files; it does not commit or push. `--force`
reextracts unchanged inputs. A download override for an official draft is also
available through `download_unicode.py --base-url https://www.unicode.org/Public/draft/ --refresh`.
