-- Mock of a calibre-generated xray.json v1 document (schema_version = 1).
-- Mirrors calibre-xray's tests/golden/xray_golden.json field-for-field.
-- 3 checkpoints: two chapter-anchored, one densified (no chapter_anchor).
return {
    schema_version = 1,
    generator = "calibre-xray",
    generator_version = "0.1.0",
    detail_level = "normal",
    language = "en",
    book_fingerprint = {
        calibre_uuid = "e2e00000-1111-2222-3333-444455556666",
        title = "Test Book",
        authors = { "Jane Author" },
        text_hash = "sha256:deadbeef",
    },
    complete = true,
    last_percent = 100,
    book_type = "fiction",
    timeline = {
        { chapter = "The Harbor at Dawn", event = "Alice arrives at Thornwick Harbor.", pct = 14 },
        { chapter = "Salt and Ledgers",   event = "Alice meets the harbourmaster.",     pct = 47 },
        { chapter = "The Long Tide",      event = "The ledger is revealed as a forgery.", pct = 92 },
    },
    checkpoints = {
        {
            percent = 14,
            snippet_anchor = "prepared her for the real salt air.",
            chapter_anchor = { toc_title = "The Harbor at Dawn", spine_index = 0 },
            snapshot = {
                characters = {
                    { name = "Alice Merrow", role = "protagonist", description = "A young cartographer.",
                      gender = "female", occupation = "cartographer", aliases = {},
                      first_pct = 14, first_seq = 1 },
                },
                locations = {
                    { name = "Thornwick Harbor", description = "A busy trade harbor.",
                      importance = "primary setting", aliases = {}, first_pct = 14, first_seq = 2 },
                },
                terms = {},
                historical_figures = {},
            },
        },
        {
            percent = 47,
            snippet_anchor = "the harbourmaster set down his pen and looked up.",
            -- densified mid-chapter checkpoint: calibre emits JSON null here
            chapter_anchor = nil,
            snapshot = {
                characters = {
                    { name = "Alice Merrow", role = "protagonist", description = "A cartographer chasing a forged chart.",
                      gender = "female", occupation = "cartographer", aliases = {},
                      first_pct = 14, first_seq = 1 },
                    { name = "Corwin Vale", role = "harbourmaster", description = "Keeper of the harbour ledgers.",
                      gender = "male", occupation = "harbourmaster", aliases = { "the harbourmaster" },
                      first_pct = 47, first_seq = 3 },
                },
                locations = {
                    { name = "Thornwick Harbor", description = "A busy trade harbor.",
                      importance = "primary setting", aliases = {}, first_pct = 14, first_seq = 2 },
                },
                terms = {
                    { name = "Ledger of Tides", definition = "The harbour's register of arrivals.",
                      expanded = "", category = "legal/magical", aliases = {} },
                },
                historical_figures = {},
            },
        },
        {
            percent = 100,
            snippet_anchor = "and the tide went out for the last time.",
            chapter_anchor = { toc_title = "The Long Tide", spine_index = 2 },
            snapshot = {
                characters = {
                    { name = "Alice Merrow", role = "protagonist", description = "A cartographer who exposed the forgery.",
                      gender = "female", occupation = "cartographer", aliases = {},
                      first_pct = 14, first_seq = 1 },
                    { name = "Corwin Vale", role = "harbourmaster", description = "The forger of the Ledger of Tides.",
                      gender = "male", occupation = "harbourmaster", aliases = { "the harbourmaster" },
                      first_pct = 47, first_seq = 3 },
                },
                locations = {
                    { name = "Thornwick Harbor", description = "A busy trade harbor.",
                      importance = "primary setting", aliases = {}, first_pct = 14, first_seq = 2 },
                    { name = "The Long Tide", description = "The tidal flats beyond the harbour.",
                      importance = "climax", aliases = {}, first_pct = 92, first_seq = 4 },
                },
                terms = {
                    { name = "Ledger of Tides", definition = "The harbour's forged register.",
                      expanded = "", category = "legal/magical", aliases = {} },
                },
                historical_figures = {
                    { name = "Saint Bede", biography = "A cartographer-saint invoked by sailors.",
                      role = "patron", importance_in_book = "Mentioned", context_in_book = "Invoked before voyages." },
                },
            },
        },
    },
}
