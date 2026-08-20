"""Pins for synthetic-caption detection and the grounding flag it drives.

The markdown fixture below is a verbatim excerpt of a live parse
(output/00771d56a9922c03_md) — real author prose, then Datalab's generated
caption in both of the places it appears. Detection keys off the alt attribute,
which a PDF cannot carry, so these cases are about exactness: author sentences
must stay unflagged even when they sit immediately beside a caption.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.synthetic_captions import (  # noqa: E402
    caption_image_for,
    caption_texts,
    find_caption_spans,
    find_caption_spans_with_source,
    overlaps_caption,
)

# Real caption text, trimmed in length but not in shape.
CAPTION = (
    "Figure 1: Time-effect curves for pain intensity difference scores. The graph "
    "plots Pain Intensity Difference Score (Y-axis, -25 to 1.25) against Hour "
    "(X-axis, 0.5 to 6). Four groups are shown: Placebo (n=40), A 600 MG (n=36), "
    "A600+COD60 (n=31), and MECLO 100 MG (n=36)."
)

AUTHOR_SENTENCE = (
    "Side effects reported for meclofenamate sodium were drowsiness (10), "
    "dizziness (3) and nausea (2)."
)

MARKDOWN = (
    f"{AUTHOR_SENTENCE}\n\n"
    f"![{CAPTION}](92c6488dfdf125bc1b7bb6a235394652_img.jpg)\n\n"
    f"{CAPTION}\n\n"
    "The placebo group reported three side effects.\n"
)


def _span_of(needle: str, haystack: str = MARKDOWN) -> tuple:
    start = haystack.index(needle)
    return start, start + len(needle)


class TestDetection:
    def test_finds_both_occurrences_of_the_caption(self):
        """Alt text AND the duplicated body paragraph — 96% of live captions
        appear twice, so covering only the alt would leave the prose copy
        quotable as if it were the paper."""
        spans = find_caption_spans(MARKDOWN)
        assert len(spans) == 2, spans

    def test_author_prose_is_not_flagged(self):
        spans = find_caption_spans(MARKDOWN)
        assert not overlaps_caption(spans, *_span_of(AUTHOR_SENTENCE))
        assert not overlaps_caption(spans, *_span_of("The placebo group reported"))

    def test_caption_text_is_flagged_in_both_places(self):
        spans = find_caption_spans(MARKDOWN)
        first = MARKDOWN.index(CAPTION)
        second = MARKDOWN.index(CAPTION, first + 1)
        assert overlaps_caption(spans, first, first + len(CAPTION))
        assert overlaps_caption(spans, second, second + len(CAPTION))

    def test_flags_a_quote_of_a_chart_value_inside_the_caption(self):
        """The case that motivated this: a model-estimated number lifted from a
        figure description, which passes verbatim quote checks today."""
        spans = find_caption_spans(MARKDOWN)
        assert overlaps_caption(spans, *_span_of("Placebo (n=40)"))

    def test_partial_overlap_counts(self):
        """A quote starting in author text and running into a caption is still
        partly machine-written."""
        spans = find_caption_spans(MARKDOWN)
        cap_start = MARKDOWN.index(CAPTION)
        assert overlaps_caption(spans, cap_start - 20, cap_start + 20)

    def test_short_alt_does_not_flag_its_repeats(self):
        """`![Barcode](x.jpg)` must not make the word "barcode" elsewhere in the
        paper unquotable — the alt span itself is still marked."""
        md = "The barcode was unreadable.\n\n![Barcode](a_img.jpg)\n\nbarcode\n"
        spans = find_caption_spans(md)
        assert len(spans) == 1
        assert not overlaps_caption(spans, *_span_of("The barcode was unreadable.", md))

    def test_empty_alt_is_ignored(self):
        assert find_caption_spans("![](a_img.jpg)") == []

    def test_no_images_no_spans(self):
        assert find_caption_spans("Plain prose with no figures.") == []
        assert find_caption_spans("") == []

    def test_caption_texts_lists_them(self):
        assert caption_texts(MARKDOWN) == [CAPTION]

    def test_spans_are_sorted_and_disjoint(self):
        spans = find_caption_spans(MARKDOWN)
        assert spans == sorted(spans)
        for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
            assert prev_end <= next_start


class TestGroundingFlag:
    """The flag rides on source_location, which reviewer comparison already
    ignores — so it can never surface as a phantom R1-vs-R2 disagreement."""

    def _enrich(self, source_text):
        from utils.source_linker import enrich_extraction_results

        # parse_page_boundaries needs a page marker to build an index.
        md = "{0}\n\n" .format(MARKDOWN) + "\n\n"
        return enrich_extraction_results(
            {"outcome": {"value": "1.15", "source_text": source_text}},
            md,
        )

    def test_quote_from_a_caption_is_flagged(self):
        out = self._enrich("Four groups are shown: Placebo (n=40), A 600 MG (n=36)")
        loc = out["outcome"].get("source_location")
        assert loc is not None, "quote should have been located"
        assert loc.get("synthetic_caption") is True

    def test_quote_from_author_text_is_not_flagged(self):
        out = self._enrich(AUTHOR_SENTENCE)
        loc = out["outcome"].get("source_location")
        assert loc is not None, "quote should have been located"
        assert "synthetic_caption" not in loc


class TestFigureAttribution:
    """The figure — not the caption prose — is what a caption-sourced quote is
    really evidence about, so the image filename has to survive to the viewer.
    The filename is the join key rather than the caption text because the
    markdown call and the blocks call are separate Datalab generations that word
    the same caption differently."""

    IMAGE = "92c6488dfdf125bc1b7bb6a235394652_img.jpg"

    def test_alt_span_carries_the_image_filename(self):
        spans = find_caption_spans_with_source(MARKDOWN)
        assert spans, "expected at least the alt span"
        assert all(src == self.IMAGE for _, _, src in spans)

    def test_duplicate_body_paragraph_carries_the_same_filename(self):
        spans = find_caption_spans_with_source(MARKDOWN)
        assert len(spans) == 2
        assert {src for _, _, src in spans} == {self.IMAGE}

    def test_lookup_returns_the_filename_for_a_caption_quote(self):
        spans = find_caption_spans_with_source(MARKDOWN)
        assert caption_image_for(spans, *_span_of("Placebo (n=40)")) == self.IMAGE

    def test_lookup_returns_none_for_author_text(self):
        spans = find_caption_spans_with_source(MARKDOWN)
        assert caption_image_for(spans, *_span_of(AUTHOR_SENTENCE)) is None

    def test_empty_src_yields_no_filename(self):
        md = "![" + "A caption long enough to pass the duplicate floor." + "]()"
        spans = find_caption_spans_with_source(md)
        assert spans, "span should still be detected"
        assert caption_image_for(spans, *_span_of("caption long enough", md)) is None

    def test_span_only_view_is_unchanged_by_the_source_tracking(self):
        merged = find_caption_spans(MARKDOWN)
        sourced = find_caption_spans_with_source(MARKDOWN)
        assert len(merged) == len({(s, e) for s, e, _ in sourced})


class TestFigureFlagOnLocation:
    def test_caption_quote_gets_both_flag_and_image(self):
        from utils.source_linker import enrich_extraction_results

        out = enrich_extraction_results(
            {"outcome": {"value": "1.15",
                         "source_text": "Four groups are shown: Placebo (n=40), A 600 MG (n=36)"}},
            MARKDOWN + "\n\n",
        )
        loc = out["outcome"]["source_location"]
        assert loc["synthetic_caption"] is True
        assert loc["caption_image"] == "92c6488dfdf125bc1b7bb6a235394652_img.jpg"

    def test_author_quote_gets_neither(self):
        from utils.source_linker import enrich_extraction_results

        out = enrich_extraction_results(
            {"ae": {"value": "drowsiness", "source_text": AUTHOR_SENTENCE}},
            MARKDOWN + "\n\n",
        )
        loc = out["ae"]["source_location"]
        assert "synthetic_caption" not in loc
        assert "caption_image" not in loc
