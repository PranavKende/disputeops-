# 05 — UiPath Integration · InvoiceLens (Document Understanding)

This folder is evidence for the **InvoiceLens** Generative Extractor — the Document
Understanding component that turns carrier demurrage/detention invoice PDFs into the
structured `Invoice` model the PaperTrail agent consumes.

## Status

| Artifact | State | Where |
|---|---|---|
| Taxonomy (extraction schema) | ✅ Authored | `../../../uipath/document_understanding/invoice_lens_taxonomy.json` |
| Field validation against fixtures | ✅ Done | [`validation_report.md`](./validation_report.md) |
| Live Generative Extractor run | ⬜ **Pending — needs you, in the DU portal** | — |
| `taxonomy.png` screenshot | ⬜ **Pending — placeholder below** | `taxonomy.png` |
| `extraction_result.png` screenshot | ⬜ **Pending — placeholder below** | `extraction_result.png` |
| Live taxonomy export | ⬜ Pending (optional) | — |

## Why the live steps aren't done here

The build environment has the UiPath CLI (`uip` 1.1.0) and is logged in, **but no
Document Understanding / IXP tool is installed or available in the tool registry.** The
only DU-adjacent tool (`@uipath/docsai-tool`) is a documentation-search assistant, not an
extractor. So the Generative Extractor, taxonomy upload, and result screenshots are
**web-portal operations** that a coding agent can't perform from the terminal.

A prior session hit this same wall and accidentally saved the CLI error
(`"unknown command 'ixp'"`) *as* the taxonomy file. That file has now been replaced with a
real, hand-authored extraction schema derived from the `Invoice` Pydantic model and the
fixture PDFs.

## What you (human) need to do in the UiPath DU portal

1. Create a Document Understanding project (Generative Extraction) named **InvoiceLens**.
2. Import the taxonomy from
   `uipath/document_understanding/invoice_lens_taxonomy.json` (or recreate the 17 fields
   it defines — field names, types, and per-field instructions are all in that file).
3. Run extraction against the three PDFs in `tests/fixtures/documents/`:
   - `empire_freight_invoice.pdf` (primary demo document)
   - `pacific_liner_invoice.pdf`
   - `coastal_carrier_invoice.pdf`
4. Compare the extractor output against the **Expected value** column in
   [`validation_report.md`](./validation_report.md). Pay attention to the four caveats
   called out there (charge_type case, missing free-time-of-day, tariff prefix, and the
   coastal `/day` vs `/hour` rate-unit ambiguity).
5. Capture the two screenshots described in the placeholder files in this folder, save
   them as `taxonomy.png` and `extraction_result.png` here, then delete the matching
   `*.PLACEHOLDER.md` files.
6. (Optional) Export the live taxonomy from the portal and commit it alongside the
   authored one if you want the as-built version on record.

## Note on the folder number

The top-level `coding-agent-evidence/README.md` table currently lists the UiPath
integration component as `06-uipath-integration` (with `05-simulator` ahead of it). This
folder uses `05-uipath-integration` to match the build instructions. If you keep this
numbering, update that table for consistency.
