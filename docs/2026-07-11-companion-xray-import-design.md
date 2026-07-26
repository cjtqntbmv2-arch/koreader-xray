# Spec B — Companion-file X-Ray import (koreader-xray-plugin)

Status: draft for review · Date: 2026-07-11 · Repo: `koreader-xray-plugin-main`
Sibling spec: `docs/superpowers/superpowers/specs/2026-07-11-claude-xray-extraction-and-companion-output-design.md` (desktop side; kam mit dem Monorepo-Merge herüber, das alte Repo liegt archiviert als `../calibre-xray-ARCHIV-2026-07-25`).

## 1. Problem & goal

The importer currently adopts X-Ray data **only** from the EPUB's embedded zip member `xray/xray.json` (`xray_import.lua:_readEmbeddedXray`, gated by `_zipHasEntry` + `_gateImport`, cached into the `.sdr` sidecar by `_buildImportedCache`). Embedding requires rewriting the EPUB, which changes the file bytes. KOReader's **reading-time statistics** (`statistics.sqlite3`) key a book by a **partial file digest**, so a byte change can orphan the accumulated reading time. (The `.sdr` sidecar — progress, bookmarks, highlights — keys by file path/name and survives a same-name replacement.)

Goal: let the importer adopt X-Ray from a **companion file next to the book** so the EPUB is never modified — preserving **all** statistics, including for already-read books.

Non-goals: changing the `xray.json` schema or the gate/mapping/caching logic; removing embedded import (it stays as a fallback).

## 2. Change

Add a **companion reader** and make both import callers try companion → embedded, gating each source and **falling through** on failure. Corrected against the actual control flow (grill P0/P1/P2).

### 2a. Companion path derivation — the cross-repo contract
- **`companion = book_path .. ".xray.json"`** — append-based (e.g. `/mnt/onboard/Book.epub` → `/mnt/onboard/Book.epub.xray.json`). Same dir, book basename + `.xray.json`.
- **Not** `gsub("%.epub$", ".xray.json")`: the entry guard admits books case-insensitively (`book_path:lower():match("%.epub$")`, `xray_import.lua:544`,`:576`), so `Book.EPUB` would leave a case-sensitive `%.epub$` gsub unmatched → derived path == the EPUB itself → the reader `json.decode`s the EPUB binary. Append-form is case-proof and format-agnostic. Desktop (Spec A) MUST derive the identical string.
- Sibling location only. (The `.sdr`-dir alternative is dropped: the desktop can't know the device's sidecar-storage policy, so `getSidecarDir` won't resolve identically across desktop and device — grill P3.)

### 2b. Where to hook — two readers, caller orchestrates (do NOT factor a shared parse+gate tail)
The schema gate is **already source-agnostic**: `_readEmbeddedXray` does zip-probe + `json.decode` + `type(doc)=="table"` and returns a raw table (`xray_import.lua:496-538`) with **no** `schema_version` check; the gate lives in `_gateImport` (`:45-51`, `missing schema_version` / `schema > SUPPORTED_SCHEMA`), invoked by the **callers** (`:550`, `:587`). So there is nothing to "factor" — adding a shared parse+gate tail would actively fight the fall-through requirement below.

- Add a small `_readCompanionXray(book_path)`: derive the path (§2a), `io.open`+`json.decode`, empty-string→nil guard (mirroring `:531`), return a table or nil. No unzip/BusyBox concerns.
- In **each** caller (`maybeImportEmbeddedXray` AND `manualImportEmbeddedXray`), replace the single `_readEmbeddedXray` call with an **ordered try-with-gate-fallback**:
  1. `doc = _readCompanionXray(...)`; if `doc` and `_gateImport(doc, props)` passes → use it.
  2. else `doc = _readEmbeddedXray(...)`; if `doc` and `_gateImport` passes → use it.
  3. else → the existing no-import / reason-message path.
- This satisfies grill **P1**: a companion that parses but fails the gate (wrong-book fingerprint, `#checkpoints==0`, future `schema_version`) **falls through to the embedded doc** instead of aborting the import (the current callers `return` on gate failure — that must become a fall-through, not a hard return, when a second source exists).
- **`manualImportEmbeddedXray` currently reads embedded only** (`:576-577`) — it MUST gain the companion path too, or the manual menu can never adopt a companion.
- Everything after a source is accepted is unchanged: `_resolveCheckpointPages`, `_buildImportedCache` into the `.sdr` sidecar.

### 2c. Precedence is first-open only (grill P0 — the important correction)
Auto-import fires only `if not self.book_data` (`main.lua:405`), and `book_data` is populated from the `.sdr` cache on every open. So:
- "Companion wins" holds **only on the first cacheless open** of a book. Once either source has imported and written the `.sdr` `xray_cache`, that cache shadows **both** sources on subsequent opens; the auto path never re-reads either.
- Therefore a companion **dropped next to an already-imported book does NOT take effect via auto-import.** The re-adoption route is the **manual menu** (which §2b now teaches to read the companion).
- Overriding an already-imported cache automatically (mtime/version compare of companion vs. cache) is **out of scope** for this feature — call it out explicitly; do not imply first-open precedence extends to already-imported books.

## 3. Schema / two-repo contract (per CLAUDE.md)
- The companion file carries the **byte-identical** `xray.json` the desktop already produces — no schema change, so **no `schema_version` bump required** by this feature alone (grill-confirmed).
- Future-version rejection works for the companion **for free**: `_gateImport` (`:49-51`) runs in the caller on any `doc_json`, so a companion with an unknown/newer `schema_version` is rejected exactly like an embedded one.
- No fixture is *required*; add one under `spec/mocks/` only if a companion test wants it.
- Desktop side (Spec A) writes `book_path .. ".xray.json"` (append-form, §2a) with the byte-identical document it would have embedded.

## 4. Testing
- `spec/` (busted) cases:
  - companion present + valid → adopted; embedded ignored on first open (first-open precedence, §2c).
  - **companion present but fails gate (wrong-book fingerprint / future `schema_version` / 0 checkpoints) + valid embedded present → falls through to embedded** (grill P1 regression guard — the caller must NOT hard-return on companion gate failure).
  - companion malformed / empty / unreadable → treated as absent (nil), embedded still tried.
  - companion absent + embedded present → existing embedded behavior byte-for-byte unchanged (regression guard).
  - **manual import (`manualImportEmbeddedXray`) adopts a companion** (guards that the manual path gained companion support, §2b).
  - path derivation: append-form is exercised for `Book.epub`, `Book.EPUB` (case), and names with spaces/shell-hostile chars (no unzip/shell on the companion path, but assert the exact derived string matches Spec A's).
- **On-device (Kobo, per memory `koreader-e2e-and-device-setup`):** copy a companion `<book>.epub.xray.json` next to an **already-read** book, trigger the **manual** import (auto won't fire once cached, §2c), confirm X-Ray imports AND that reading progress + **reading-time statistics** are preserved. This is the acceptance test for the stats-safety claim.

## 5. Risks / open questions
- **KOReader stats identity — verify, don't assume (must-verify gate).** The plugin has **zero** coupling to `statistics.sqlite3` / partial-md5 (it only touches the path-keyed `.sdr` via `getSidecarDir`), so "byte change orphans reading-time stats; companion avoids it" is a KOReader-**core** assumption unprovable in this repo. The on-device test (§4) is the acceptance gate. If KOReader re-links stats by title/author metadata, urgency drops (companion is still strictly safer).
- **Already-imported books need the manual path (grill P0).** Auto-import is first-open-only; a companion added later is adopted only via the manual menu. If seamless auto-override of a stale cache is ever wanted, that is a separate feature (mtime/version compare) — explicitly out of scope here.
- **Discovery cost:** one extra `io.open` on a cacheless open (same trigger as the current embedded probe). Negligible.
- **Cross-repo filename drift:** the append-form derivation (§2a) is the single most likely place desktop and importer diverge. Pinned as a shared contract clause in both specs; the path-derivation test asserts the exact string.
