"""Study identity ("Raslan 2021") — the rules that are easy to break silently.

The disambiguation rules matter more than the parsing: a wrong suffix renames a
study a reviewer has already cited, and a MISSING suffix shows two different
studies under one identical label, which is the failure this feature exists to
prevent.

The TypeScript mirror is frontend/lib/documentLabel.ts. Its outputs were
verified identical to these on the same fixtures; if you change behaviour here,
change it there in the same commit.
"""

from utils.study_label import (
    build_label_map,
    derive_label,
    first_surname,
    label_from_filename,
    surname_of,
    year_of,
)


class TestSurname:
    def test_every_import_path_shape(self):
        # Crossref, EndNote, PubMed esummary, plain prose — in that order.
        assert surname_of({"family": "Raslan"}) == "Raslan"
        assert surname_of("Raslan, Nada") == "Raslan"
        assert surname_of("Raslan N") == "Raslan"
        assert surname_of("Nada Raslan") == "Raslan"

    def test_particles_stay_attached(self):
        assert surname_of("Jan van Dijk") == "van Dijk"
        assert surname_of("van Dijk, J") == "van Dijk"

    def test_block_capitals_are_recased(self):
        # Crossref stores some author records shouting; "SEYMOUR 1996" in a list
        # of "Dionne 1994"s reads as a different kind of thing.
        assert surname_of({"family": "SEYMOUR"}) == "Seymour"
        assert surname_of("AL-SUKHUN") == "Al-Sukhun"
        assert surname_of("O'BRIEN") == "O'Brien"

    def test_deliberate_casing_is_preserved(self):
        assert surname_of("McDonald") == "McDonald"
        assert surname_of("Kyselovič") == "Kyselovič"

    def test_first_author_of_a_list(self):
        assert first_surname([{"family": "Polat"}, {"family": "Yilmaz"}]) == "Polat"
        assert first_surname([]) is None


class TestYear:
    def test_shapes(self):
        assert year_of({"date-parts": [[2021, 3]]}) == "2021"
        assert year_of("2005 Jan") == "2005"
        assert year_of(1995) == "1995"

    def test_rejects_non_years(self):
        # The bound is what stops "Receipt-2456-8583-5984.pdf" parsing as a year.
        assert year_of("n.d.") is None
        assert year_of("2456") is None


class TestFilenameParsing:
    def test_curated_filenames(self):
        assert label_from_filename("Raslan 2021.pdf") == "Raslan 2021"
        assert label_from_filename("Aggarwal_2022.pdf") == "Aggarwal 2022"
        assert label_from_filename("Kujan2020 REF 34.pdf") == "Kujan 2020"
        assert label_from_filename("Polat 2005b.pdf") == "Polat 2005b"
        assert label_from_filename("van Dijk 2005.pdf") == "van Dijk 2005"

    def test_initial_is_preserved(self):
        # "Wang Y 2017" and "Wang S 2017" are different studies in one project;
        # collapsing the initial merges two studies into one visible identity.
        assert label_from_filename("Wang Y 2017.pdf") == "Wang Y 2017"
        assert label_from_filename("Wang S 2017.pdf") == "Wang S 2017"

    def test_titles_and_junk_do_not_parse(self):
        assert label_from_filename("A single-tablet fixed-dose combination.pdf") is None
        assert label_from_filename("Receipt-2456-8583-5984.pdf") is None
        assert label_from_filename("2026.acl-demo.7.pdf") is None
        assert label_from_filename("spectrum.01818-25.pdf") is None


class TestPrecedence:
    def test_manual_label_wins_and_is_fixed(self):
        label, fixed = derive_label({"study_label": "Jefferson 2026b", "filename": "Raslan 2021.pdf"})
        assert (label, fixed) == ("Jefferson 2026b", True)

    def test_filename_beats_metadata(self):
        # The filename carries the reviewer's own suffix; Crossref cannot.
        label, _ = derive_label({"filename": "Polat 2005b.pdf", "first_author": "Polat", "pub_year": "2005"})
        assert label == "Polat 2005b"

    def test_registry_ids_when_there_is_no_author(self):
        assert derive_label({"filename": "trial.pdf", "nct_id": "NCT01234567"})[0] == "NCT01234567"
        assert derive_label({"filename": "x.pdf", "pmid": "12345678"})[0] == "PMID 12345678"

    def test_no_identity_at_all(self):
        assert derive_label({"filename": "Lornoxicam: analgesic efficacy.pdf"})[0] is None


class TestCollisionSuffixes:
    def test_same_author_same_year_gets_a_then_b(self):
        docs = [
            {"id": "1", "ref_id": 1, "first_author": "Jefferson", "pub_year": "2026", "filename": "x.pdf"},
            {"id": "2", "ref_id": 2, "first_author": "Jefferson", "pub_year": "2026", "filename": "y.pdf"},
        ]
        assert build_label_map(docs) == {"1": "Jefferson 2026a", "2": "Jefferson 2026b"}

    def test_a_lone_label_is_never_suffixed(self):
        assert build_label_map([{"id": "1", "ref_id": 1, "filename": "Raslan 2021.pdf"}]) == {"1": "Raslan 2021"}

    def test_suffix_order_follows_ref_id_not_input_order(self):
        # Stability is the whole point: a study ID that reshuffles when someone
        # uploads a new paper is worse than no ID.
        docs = [
            {"id": "late", "ref_id": 9, "first_author": "Polat", "pub_year": "2005", "filename": "b.pdf"},
            {"id": "early", "ref_id": 2, "first_author": "Polat", "pub_year": "2005", "filename": "a.pdf"},
        ]
        assert build_label_map(docs) == {"early": "Polat 2005a", "late": "Polat 2005b"}

    def test_a_curated_label_is_never_shadowed(self):
        # The hand-typed "Polat 2005a" keeps its ID; the derived ones move past it.
        docs = [
            {"id": "manual", "ref_id": 1, "study_label": "Polat 2005a", "filename": "q.pdf"},
            {"id": "d1", "ref_id": 2, "first_author": "Polat", "pub_year": "2005", "filename": "r.pdf"},
            {"id": "d2", "ref_id": 3, "first_author": "Polat", "pub_year": "2005", "filename": "s.pdf"},
        ]
        assert build_label_map(docs) == {"manual": "Polat 2005a", "d1": "Polat 2005b", "d2": "Polat 2005c"}

    def test_already_suffixed_duplicates_get_a_numeric_marker(self):
        # "Polat 2005ba" would read as a plausible but different study ID.
        docs = [
            {"id": "a", "ref_id": 1, "filename": "Polat 2005b.pdf"},
            {"id": "b", "ref_id": 2, "filename": "Polat 2005b.pdf"},
        ]
        assert build_label_map(docs) == {"a": "Polat 2005b", "b": "Polat 2005b (2)"}

    def test_documents_with_no_identity_fall_back_to_the_filename(self):
        docs = [{"id": "c", "ref_id": 1, "filename": "A long article title about dental pain.pdf"}]
        assert build_label_map(docs) == {"c": "A long article title about dental pain"}
