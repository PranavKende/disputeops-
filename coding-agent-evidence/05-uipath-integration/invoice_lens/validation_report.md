# InvoiceLens — Extraction Validation Report

**Date:** 2026-05-31
**Validator:** Claude Code (Opus 4.8), local file-level validation
**Scope:** Step 4 — validate that the three fixture PDFs contain the field values the
`invoice_lens` Generative Extractor is expected to pull, checked against the
DisputeOps fixtures.

> **Important — how this validation was done.** No `uip ixp` / Document Understanding
> extraction CLI is available in this environment (the IXP tool is not installed and is
> not in the tool registry; only a docs-search tool `@uipath/docsai-tool` exists). The
> UiPath **Generative Extractor itself was therefore not run here** — that is a cloud /
> web-portal operation. What this report verifies is the **ground truth**: that each PDF's
> rendered text actually contains the values a correct extraction should return, compared
> field-by-field against the source fixtures. Run the live extractor in the DU portal and
> compare its output against the "Expected value" column below.

## Document → fixture mapping

| PDF | Fixture | Scenario |
|---|---|---|
| `empire_freight_invoice.pdf` | `clean_violation_case` | Strong violation (6 rules fire) |
| `pacific_liner_invoice.pdf` | `valid_charge_case` | No-merit (0 violations) |
| `coastal_carrier_invoice.pdf` | `borderline_case` | Moderate (1 violation) |

The PDFs are rendered directly from these fixtures by `tools/generate_test_documents.py`,
so the rendered text is the authoritative expected-extraction target.

## Empire Freight invoice — field-by-field (primary target)

`empire_freight_invoice.pdf` vs `clean_violation_case`:

| Field | Expected value (fixture) | Present in PDF text | Match |
|---|---|---|:--:|
| `invoice_number` | `EMFR-INV-2026-0491` | `EMFR-INV-2026-0491` | ✅ |
| `carrier_name` | `Empire Freight Lines` | `Empire Freight Lines` | ✅ |
| `bol_number` | `EMFR20260401-001` | `EMFR20260401-001` | ✅ |
| `container_number` | `MEDU9871234` | `MEDU9871234` | ✅ |
| `charge_type` | `demurrage` | `DEMURRAGE` (uppercase) | ✅ ¹ |
| `charge_incurred_date` | `2026-05-09` | `2026-05-09` | ✅ |
| `invoice_date` | `2026-06-25` | `2026-06-25` | ✅ |
| `free_time_start` | `2026-05-07 08:00 UTC` | `2026-05-07` (date only) | ⚠️ ² |
| `free_time_end` | `2026-05-09 08:00 UTC` | `2026-05-09` (date only) | ⚠️ ² |
| `published_free_time_days` | `3.0` (72h) | `3 days` | ✅ |
| `tariff_number` | `EMFR-2024-DEM` | `tariff EMFR-2024-DEM` | ✅ ³ |
| `hours_billed` | `56.0` | `56.0 hours` | ✅ |
| `hourly_rate_usd` | `70.0` | `$70.00/hr` | ✅ |
| `total_amount_usd` | `3920.00` | `$3,920.00` | ✅ |
| `billed_to_party` | `Pacifica Apparel Inc.` | `Pacifica Apparel Inc.` | ✅ |
| `basis_for_charge` | `Demurrage charge per tariff EMFR-2024-DEM` | identical | ✅ |

**All target fields the task calls out are present and correct** — `invoice_number`,
`container_number`, `total_amount_usd = 3920.00`, `charge_incurred_date`, `invoice_date`.

## Notes & expected extractor caveats

These are the fields a Generative Extractor is most likely to get *wrong or miss*, and why.
Watch these specifically when reviewing the live extraction:

1. **`charge_type` case** — the PDF prints `DEMURRAGE` (uppercase) but the downstream
   `Invoice` model expects lowercase `demurrage`. Normalization is required (the taxonomy
   instruction covers this). Not a defect, but a post-processing step.
2. **`free_time_start` / `free_time_end` time-of-day is NOT in the document.** The PDF shows
   date only (`2026-05-07`); the fixture carries `08:00 UTC`. **The extractor cannot recover
   the hour** — it is not printed anywhere. Any rule that depends on the time component
   (e.g. the 48h-vs-72h free-time math behind R004) relies on a downstream default of 08:00,
   not on extraction. Expect the extractor to return the date only; this is correct behavior.
3. **`tariff_number` prefix** — rendered as `tariff EMFR-2024-DEM`. The extractor should drop
   the leading word `tariff` and return just `EMFR-2024-DEM`.
4. **`published_free_time` is the published value, not the claimed window.** The PDF prints
   `Published Free Time: 3 days` (= 72h). The "48h claimed" figure that drives the R004
   violation is *derived* (`free_time_end − free_time_start`), not a labeled field — do not
   expect the extractor to surface "48h" as a field.

## Other two PDFs (spot-validated)

| PDF | Key fields checked | Result |
|---|---|---|
| `pacific_liner_invoice.pdf` | invoice `PACL-INV-2026-0774`, container `TCLU4470129`, total `$640.00`, charge_type `DETENTION`, 32.0h × $20.00/hr | ✅ all match `valid_charge_case` |
| `coastal_carrier_invoice.pdf` | invoice `CCGV-INV-2026-0334`, container `CMAU7723445`, total `$1,800.00`, 72.0h × $25.00/day | ✅ match `borderline_case`, with one caveat ⁵ |

5. **`coastal_carrier` rate-unit ambiguity (extractor risk).** The billing table shows
   `$25.00/**day**`, but the *Basis for Charge* prose states "rate of `$25.00/**hour**`".
   A Generative Extractor reading the narrative may pull the wrong unit. The taxonomy
   instruction tells it to **prefer the billing-table suffix** (`/day`). Verify the live
   extractor resolves this correctly — this is the single most likely field error across the
   three documents.

## Verdict

The fixture PDFs are sound extraction targets: every field defined in the `invoice_lens`
taxonomy is present in the rendered text with the expected value (subject to the
normalization/derivation notes above). The Empire Freight invoice — the primary demo
document — matches `clean_violation_case` exactly.

**Remaining work (requires the UiPath DU web portal, cannot be done from this CLI):**
run the Generative Extractor against the three PDFs, capture `taxonomy.png` and
`extraction_result.png`, and export the live taxonomy. See `README.md` in this folder.
