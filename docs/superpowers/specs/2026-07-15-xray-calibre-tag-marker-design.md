# Embed a calibre-visible "X-Ray" marker tag

**Date:** 2026-07-15
**Status:** approved (brainstorming 2026-07-15)

## Goal

Let the user see in calibre which EPUBs already carry embedded X-Ray data, and
filter by it. The marker must travel *inside* the EPUB (the CLI/skill path
produces files outside calibre and `xray_core` must never import calibre).

## Decision

On **full** embed mode, `embed_xray` adds `<dc:subject>X-Ray</dc:subject>` to the
OPF `<metadata>`. calibre maps `dc:subject` → **Tags**, so on read the book gets
a filterable "X-Ray" tag (Tag browser, one-click filter). Both callers (CLI
assembler and the Gemini plugin) go through full-mode `embed_xray`, so both get
it with no change of their own.

## Design

- New helper `_add_dc_subject(opf_bytes, value="X-Ray") -> bytes` in
  `xray_core/embed.py`, byte-splice (no ElementTree reserialize — same reasoning
  as `_add_manifest_item`: reserializing corrupts calibre's `opf:` attributes).
  Insert `<dc:subject>X-Ray</dc:subject>` immediately before the `</metadata>`
  close tag (prefixed or not).
- Called in `embed_xray` full-mode branch, right after `_add_manifest_item`, on
  the same OPF bytes.
- **Idempotent:** if a `<dc:subject>` whose normalized text equals "x-ray"
  already exists, return the bytes unchanged (re-embed must not duplicate).
- **Preserves existing `dc:subject`** entries (genre/other tags untouched).
- **Append mode unchanged:** it guarantees byte-identical head bytes (KOReader
  partialMD5 / reading stats), so it must not touch the OPF. No marker there by
  design; append is the "already read on-device" path.

## Known limitation (accepted)

The tag surfaces only when calibre **reads** the OPF: reliably on *add-as-new*.
On the *replace-format* workflow calibre keeps its library metadata and does not
re-read the OPF, so the tag will not auto-appear. Some calibre versions offer
"set metadata from format" in the single-book Edit-metadata dialog, which would
surface it on an existing book. A reliable calibre-side plugin action was
considered and deferred (user chose file-only for now).

## Test

`tests/test_embed.py` (or existing embed test module): after full-mode embed,
the OPF contains exactly one `<dc:subject>X-Ray`; a pre-existing `dc:subject`
survives; a second embed does not add a duplicate. Append mode adds no subject.

## Version

New backward-compatible feature → MINOR bump **0.1.1 → 0.2.0** (VERSION, README
badge, `XRayGeneratorPlugin.version`; regenerate golden — only `generator_version`
changes).
