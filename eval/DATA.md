# Corpus provenance and limits

| Corpus | Evaluated studies | Reference collection |
| --- | ---: | --- |
| Oral cancer | 43 | Diagnostic adjunct systematic reviews cited in the paper |
| Antibiotic prophylaxis | 10 | Cochrane CD010266 |
| Periodontitis | 29 | Cochrane CD004714 |
| Ibuprofen | 43 | Cochrane CD015432 |

`corpora/<corpus>/studies.csv` identifies the 125 study PDFs used in the local
benchmark inventory. Hosted project and document identifiers were removed.
Filenames are reference labels, not download links. Obtain articles through
their publishers, repositories, or your institution and follow their terms.

`sheets/gt sheets/` contains reference worksheet cell values. Excel author
metadata and embedded resources were removed; nonempty cell values were checked
against the source. Antibiotic and periodontitis reference workbooks contain
more studies (11 and 35) than the evaluated subset (10 and 29). Match study labels
to the manifests when selecting the evaluated population. Directive rows such
as EXTRACT/OPTIONAL/NEGLECT/META are field metadata, not study observations.

Oral cancer uses `aligned_forms.xlsx`, the original aligned reference available
to the historical scorer. The later corrected `aligned_forms_v2.xlsx` is not
silently substituted. This choice is recorded in `data-manifest.json`; it is not
a claim that every published score used that version.

`sheets/ai sheets/` includes saved value-level predictions for Claude, GPT, and
Gemini from the local evaluation collection. Production, description-only,
scalar single-prompt, and table single-prompt outputs are included where
available. The data manifest lists the exact files; coverage varies by variant.
The collection contains historical variants whose identity must be resolved
before claiming exact reproduction of a specific published table.

This release contains study-level reference annotations and model outputs, not
patient-level records. It does not redistribute article PDFs, full-text Markdown,
the local source-paper archive, or hosted project state. Apache-2.0 applies to
the project software; it does not relicense third-party publications. Retain the
source-review citations and report the artifact versions when using this data.
