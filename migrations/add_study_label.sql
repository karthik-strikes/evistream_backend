-- Cochrane-style study identity on documents: "Raslan 2021", "Polat 2005b".
--
-- Reviewers cite a study by first author + year, not by its 200-character
-- article title, and every screen in this app previously showed
-- `documents.filename` — which for EndNote/RIS/PubMed imports IS the title.
--
-- Three columns, deliberately separate:
--   first_author — SURNAME ONLY of the first listed author ("Raslan"). Not the
--                  full author list: nothing displays it, and storing one name
--                  keeps the label derivable without parsing at render time.
--   pub_year     — publication year as text, not int: Crossref/EndNote/RIS all
--                  hand us strings, some partial ("2005 Jan"), and the value is
--                  only ever concatenated into a label, never compared numerically.
--   study_label  — MANUAL override, and the only writer of record for a curated
--                  ID. Wins over anything derived. This is what makes "Polat
--                  2005a" vs "Polat 2005b" a human decision rather than an
--                  arbitrary alphabetical accident, matching how RevMan works.
--
-- All three are nullable and best-effort: ~80% of the existing corpus has no
-- bibliographic metadata at all, so the display helper must always be able to
-- fall back to the filename. Never make these required.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS first_author text,
    ADD COLUMN IF NOT EXISTS pub_year     text,
    ADD COLUMN IF NOT EXISTS study_label  text;

-- Collision detection for the a/b/c suffix runs per project over the derived
-- labels, so the (project_id, first_author, pub_year) triple is the hot lookup.
CREATE INDEX IF NOT EXISTS idx_documents_project_study
    ON documents (project_id, first_author, pub_year)
    WHERE first_author IS NOT NULL;
