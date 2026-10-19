"""
Source Linker — Maps extracted source_text back to page/position in markdown.

Phase 3A: Bidirectional PDF Source Linking.

The Marker API (with paginate=true) inserts page separators in the format:
    {N}------------------------------------------------
where N is the 0-indexed page number.

This module:
1. Parses page boundaries from markdown
2. Fuzzy-matches source_text snippets to exact positions
3. Enriches extraction results with source_location metadata
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Marker API page separator pattern: {0}---...--- (48+ dashes)
PAGE_SEPARATOR_RE = re.compile(r'\{(\d+)\}-{48,}')

# Embedded page map header in markdown
PAGE_MAP_HEADER_RE = re.compile(r'<!--\s*PAGE_MAP:\s*(\[.*?\])\s*-->', re.DOTALL)

# Section heading detection — matches lines where the entire content is a known academic section name
# Handles: "## Methods", "**Participants**", "2. Results", "ABSTRACT", etc.
_SECTION_HEADING_RE = re.compile(
    r'(?m)^[ \t]*'                         # start of line
    r'(?:#{1,6}[ \t]+)?'                   # optional markdown heading: ## or ###
    r'\*{0,2}'                             # optional bold open: **
    r'(?:\d+[\.\)]\s*)?'                   # optional numbering: "2. " or "3) "
    r'(abstract|introduction|background|'
    r'(?:materials?\s+and\s+)?methods?|methodology|'
    r'study\s+(?:design|population|setting)|'
    r'participants?|subjects?|patients?|'
    r'(?:primary\s+)?(?:results?|outcomes?|findings?)|'
    r'discussion|conclusions?|summary|'
    r'references?|bibliography|'
    r'statistical\s+(?:analysis|methods?)|data\s+(?:analysis|collection)|'
    r'randomiz(?:ation|ing)|interventions?|'
    r'(?:inclusion|exclusion)\s+criteria|eligibility|'
    r'blinding|allocation|randomiz(?:ation|ing)|'
    r'adverse\s+events?|safety|efficacy)'
    r'\*{0,2}'                             # optional bold close: **
    r'[ \t]*$',                            # nothing else on this line
    re.IGNORECASE,
)

# Canonical display names for matched section keywords
_SECTION_CANONICAL: Dict[str, str] = {
    'abstract': 'Abstract',
    'introduction': 'Introduction',
    'background': 'Background',
    'methods': 'Methods', 'method': 'Methods', 'methodology': 'Methods',
    'materials and methods': 'Methods', 'material and methods': 'Methods',
    'study design': 'Methods', 'study setting': 'Methods',
    'study population': 'Participants',
    'participants': 'Participants', 'participant': 'Participants',
    'subjects': 'Participants', 'subject': 'Participants',
    'patients': 'Participants', 'patient': 'Participants',
    'eligibility': 'Participants',
    'inclusion criteria': 'Participants', 'exclusion criteria': 'Participants',
    'results': 'Results', 'result': 'Results',
    'outcomes': 'Results', 'outcome': 'Results',
    'primary outcomes': 'Results', 'primary outcome': 'Results',
    'findings': 'Results', 'finding': 'Results',
    'discussion': 'Discussion',
    'conclusions': 'Conclusion', 'conclusion': 'Conclusion', 'summary': 'Conclusion',
    'references': 'References', 'reference': 'References', 'bibliography': 'References',
    'statistical analysis': 'Methods', 'statistical methods': 'Methods',
    'data analysis': 'Methods', 'data collection': 'Methods',
    'randomization': 'Methods', 'randomizing': 'Methods',
    'interventions': 'Methods', 'intervention': 'Methods',
    'blinding': 'Methods', 'allocation': 'Methods',
    'adverse events': 'Results', 'adverse event': 'Results',
    'safety': 'Results', 'efficacy': 'Results',
}


@dataclass
class SourceLocation:
    """Location of a source text snippet in the original document."""
    page: int                        # 1-indexed page number
    start_char: int                  # Character offset in full markdown
    end_char: int                    # Character offset end
    matched_text: str                # The actual text that was matched
    confidence: float                # Match quality 0-1
    section: Optional[str] = None   # Nearest section heading above the match
    # How this location was grounded: "quote_exact" | "quote_fuzzy"
    # (the model's source_text matched) vs "value_table" | "value_text"
    # (the model's quote was weak, so we located the extracted VALUE instead).
    grounding_method: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "page": self.page,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "matched_text": self.matched_text,
            "confidence": round(self.confidence, 3),
        }
        if self.section:
            result["section"] = self.section
        if self.grounding_method:
            result["grounding_method"] = self.grounding_method
        return result


@dataclass
class TextChunk:
    """A chunk of text with its position metadata."""
    text: str
    start_char: int
    end_char: int
    page: int                   # 1-indexed
    normalized_text: str = ""   # PDF-normalized variant (for matching only)


@dataclass
class SourceIndex:
    """Searchable index of text chunks with page boundaries."""
    chunks: List[TextChunk] = field(default_factory=list)
    full_text: str = ""
    normalized_full_text: str = ""           # PDF-normalized form of full_text
    normalized_raw_map: List[int] = field(default_factory=list)  # norm_idx → raw_idx


def parse_page_boundaries(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Parse Marker API page separators to build a page boundary map.

    Marker format: {N}------------------------------------------------
    where N is 0-indexed page number.

    Returns list of {"page": int (1-indexed), "start_char": int, "end_char": int}
    """
    separators = list(PAGE_SEPARATOR_RE.finditer(markdown_content))

    if not separators:
        # No page separators — treat entire content as page 1
        return [{"page": 1, "start_char": 0, "end_char": len(markdown_content)}]

    pages = []

    for i, match in enumerate(separators):
        page_num = int(match.group(1)) + 1  # Convert 0-indexed to 1-indexed
        # Content starts after the separator line (skip separator + trailing newlines)
        content_start = match.end()
        # Skip trailing newlines after separator
        while content_start < len(markdown_content) and markdown_content[content_start] == '\n':
            content_start += 1

        # Content ends at the next separator (or end of file)
        if i + 1 < len(separators):
            # End before the next separator line starts
            content_end = separators[i + 1].start()
            # Trim trailing whitespace before next separator
            while content_end > content_start and markdown_content[content_end - 1] in '\n\r':
                content_end -= 1
        else:
            content_end = len(markdown_content)

        pages.append({
            "page": page_num,
            "start_char": content_start,
            "end_char": content_end,
        })

    # Handle content before the first separator (often empty or metadata)
    first_sep_start = separators[0].start()
    if first_sep_start > 0:
        pre_content = markdown_content[:first_sep_start].strip()
        if pre_content:
            # There's content before the first page separator — prepend as page 0 content
            # merged into page 1
            if pages and pages[0]["page"] == 1:
                pages[0]["start_char"] = 0
            else:
                pages.insert(0, {"page": 1, "start_char": 0, "end_char": first_sep_start})

    return pages


def build_source_index(markdown_content: str, page_map: List[Dict[str, Any]]) -> SourceIndex:
    """
    Build a searchable index from markdown content with page boundaries.

    Splits content into paragraph-level chunks for efficient lookup.
    Each chunk knows its page number and character offsets.

    Also computes a normalized variant of the full text (and per-chunk text)
    with an offset map back to raw indices — used by locate_source to do
    artifact-tolerant matching while still returning raw markdown offsets.
    """
    norm_full, raw_map = _pdf_normalize_with_map(markdown_content)
    index = SourceIndex(
        full_text=markdown_content,
        normalized_full_text=norm_full,
        normalized_raw_map=raw_map,
    )

    for page_info in page_map:
        page_num = page_info["page"]
        start = page_info["start_char"]
        end = page_info["end_char"]

        page_text = markdown_content[start:end]

        # Split into paragraphs (double newline) and table rows
        # Use a regex that splits on double newlines but preserves single newlines within paragraphs
        paragraphs = re.split(r'\n\n+', page_text)

        current_offset = start
        for para in paragraphs:
            if not para.strip():
                current_offset += len(para) + 2  # +2 for the \n\n
                continue

            # Find the actual position of this paragraph in the page text
            para_start = markdown_content.find(para, current_offset, end + len(para))
            if para_start == -1:
                # Fallback: use current offset
                para_start = current_offset

            para_end = para_start + len(para)

            # For tables, also split into individual rows as separate chunks
            if '|' in para and para.count('|') >= 2:
                # This looks like a markdown table — index entire table AND individual rows
                _para_stripped = para.strip()
                index.chunks.append(TextChunk(
                    text=_para_stripped,
                    start_char=para_start,
                    end_char=para_end,
                    page=page_num,
                    normalized_text=_pdf_normalize_with_map(_para_stripped)[0],
                ))
                for row in para.split('\n'):
                    row_stripped = row.strip()
                    if row_stripped and not re.match(r'^[\|\-\s:]+$', row_stripped):
                        row_start = markdown_content.find(row, para_start, para_end + len(row))
                        if row_start >= 0:
                            index.chunks.append(TextChunk(
                                text=row_stripped,
                                start_char=row_start,
                                end_char=row_start + len(row),
                                page=page_num,
                                normalized_text=_pdf_normalize_with_map(row_stripped)[0],
                            ))
            else:
                _para_stripped = para.strip()
                index.chunks.append(TextChunk(
                    text=_para_stripped,
                    start_char=para_start,
                    end_char=para_end,
                    page=page_num,
                    normalized_text=_pdf_normalize_with_map(_para_stripped)[0],
                ))

            current_offset = para_end

    return index


def locate_source(
    source_text: str,
    source_index: SourceIndex,
    threshold: float = 0.65,
) -> Optional[SourceLocation]:
    """
    Find the best matching location for a source_text snippet.

    Strategy:
    1. Exact substring match against normalized markdown (fast path).
       Translates normalized offsets back to raw markdown offsets via the
       offset map built in build_source_index.
    2. Fuzzy matching against per-chunk normalized text.
    3. Sliding window across consecutive chunks for longer passages.

    Returns SourceLocation (with raw markdown offsets) or None if no match
    above threshold.
    """
    if not source_text or source_text.strip().upper() in ("NR", "N/R", "NOT REPORTED", ""):
        return None

    # Normalize the query the same way the index was normalized so that
    # ligatures / line-break hyphens / curly quotes / NBSP don't defeat
    # an otherwise-exact match.
    source_norm, _ = _pdf_normalize_with_map(source_text)
    source_norm = _normalize_whitespace(source_norm)
    if len(source_norm) < 5:
        return None

    norm_full = source_index.normalized_full_text or source_index.full_text
    raw_map = source_index.normalized_raw_map

    # --- Fast path: exact substring in normalized space ---
    exact_pos_norm = norm_full.find(source_norm)
    if exact_pos_norm >= 0 and raw_map:
        # Translate normalized offsets back to raw markdown offsets.
        norm_end = exact_pos_norm + len(source_norm)
        raw_start = raw_map[exact_pos_norm] if exact_pos_norm < len(raw_map) else exact_pos_norm
        raw_end = raw_map[norm_end] if norm_end < len(raw_map) else len(source_index.full_text)
        page = _char_offset_to_page(raw_start, source_index)
        matched_raw = source_index.full_text[raw_start:raw_end]
        return SourceLocation(
            page=page,
            start_char=raw_start,
            end_char=raw_end,
            matched_text=matched_raw[:500],
            confidence=1.0,
            section=_detect_section(source_index.full_text, raw_start),
        )

    # --- Fuzzy matching against chunks (normalized) ---
    best_score = 0.0
    best_chunk: Optional[TextChunk] = None

    for chunk in source_index.chunks:
        chunk_norm = chunk.normalized_text or chunk.text
        if not chunk_norm:
            continue

        # Quick length filter — skip chunks that are way too short or too long
        len_ratio = len(source_norm) / max(len(chunk_norm), 1)
        if len_ratio > 5.0 or len_ratio < 0.1:
            if len(source_norm) > len(chunk_norm) * 3:
                continue

        score = fuzz.partial_ratio(source_norm, chunk_norm) / 100.0

        if score > best_score:
            best_score = score
            best_chunk = chunk

    # --- Try consecutive chunk pairs for multi-paragraph matches ---
    if best_score < threshold and len(source_index.chunks) > 1:
        for i in range(len(source_index.chunks) - 1):
            chunk_a = source_index.chunks[i]
            chunk_b = source_index.chunks[i + 1]

            if abs(chunk_a.page - chunk_b.page) > 1:
                continue

            a_norm = chunk_a.normalized_text or chunk_a.text
            b_norm = chunk_b.normalized_text or chunk_b.text
            combined_norm = a_norm + " " + b_norm
            score = fuzz.partial_ratio(source_norm, combined_norm) / 100.0

            if score > best_score:
                best_score = score
                # Synthetic combined chunk — raw offsets span both originals
                best_chunk = TextChunk(
                    text=chunk_a.text + " " + chunk_b.text,
                    start_char=chunk_a.start_char,
                    end_char=chunk_b.end_char,
                    page=chunk_a.page,
                    normalized_text=combined_norm,
                )

    if best_score >= threshold and best_chunk is not None:
        return SourceLocation(
            page=best_chunk.page,
            start_char=best_chunk.start_char,
            end_char=best_chunk.end_char,
            matched_text=best_chunk.text[:500],
            confidence=best_score,
            section=_detect_section(source_index.full_text, best_chunk.start_char),
        )

    return None


def enrich_extraction_results(
    extracted_data: Dict[str, Any],
    markdown_content: str,
    page_map: Optional[List[Dict[str, Any]]] = None,
    bbox_anchors: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Walk the extracted_data dict, find every source_text field,
    and add source_location metadata next to it.

    Handles two formats:
    1. Flat format: {"field.source_text": "...", "field.value": "..."}
    2. Nested format: {"field": {"value": "...", "source_text": "..."}}

    When `bbox_anchors` is provided (output of utils.bbox_map.build_bbox_map),
    each source_location additionally gets a "bboxes" list — deterministic
    PDF coordinates pulled from the Datalab parse, not regenerated by any
    model. Anchors that don't overlap the matched span are skipped.

    Returns enriched dict (does not mutate input).
    """
    if not markdown_content:
        return extracted_data

    # Parse page boundaries if not provided
    if page_map is None:
        page_map = parse_page_boundaries(markdown_content)

    if not page_map:
        return extracted_data

    # Build search index
    source_index = build_source_index(markdown_content, page_map)

    if not source_index.chunks:
        logger.warning("Source index has no chunks — skipping source linking")
        return extracted_data

    # Import here to avoid a top-level circular import (bbox_map has no deps
    # on source_linker; keeping the lookup local keeps the dependency one-way).
    _find_bboxes = None
    if bbox_anchors:
        try:
            from utils.bbox_map import find_bboxes_for_span as _find_bboxes
        except Exception:
            _find_bboxes = None

    def _attach_bboxes(loc: Dict[str, Any]) -> Dict[str, Any]:
        if _find_bboxes is None or not bbox_anchors:
            return loc
        bboxes = _find_bboxes(bbox_anchors, loc["start_char"], loc["end_char"])
        if bboxes:
            return {**loc, "bboxes": bboxes}
        return loc

    def _resolve_location(source_text: Any, value: Any):
        """Ground a cell: try the model's quote first, fall back to locating the
        value itself (table/figure case) when the quote match is weak or absent.
        Tags each location with how it was grounded."""
        loc = None
        if isinstance(source_text, str) and source_text.strip().upper() not in ("NR", ""):
            loc = locate_source(source_text, source_index)
            if loc is not None:
                loc.grounding_method = "quote_exact" if loc.confidence >= 0.999 else "quote_fuzzy"
        # Weak or missing quote → value-anchored fallback (deterministic).
        if loc is None or loc.confidence < 0.85:
            vloc = locate_value(value, source_index, bbox_anchors)
            if vloc is not None and (loc is None or vloc.confidence >= loc.confidence):
                loc = vloc
        return loc

    def _enrich_cell(cell: Any) -> Any:
        """Attach source_location to one {value, source_text} cell dict."""
        if not (isinstance(cell, dict) and "value" in cell and "source_text" in cell):
            return cell
        loc = _resolve_location(cell.get("source_text", ""), cell.get("value"))
        if loc:
            return {**cell, "source_location": _attach_bboxes(loc.to_dict())}
        return cell

    def _enrich_rows(rows: list) -> list:
        """Enrich every cell of every table row with its own source_location."""
        return [
            {ck: _enrich_cell(cv) for ck, cv in row.items()} if isinstance(row, dict) else row
            for row in rows
        ]

    enriched = {}
    source_text_keys_processed = set()

    for key, value in extracted_data.items():
        # --- Table rows as a bare list (legacy two-stage shape) ---
        if isinstance(value, list):
            enriched[key] = _enrich_rows(value)
            continue

        # --- Nested format: {"field": {"value": ..., "source_text": ...}} ---
        if isinstance(value, dict) and "source_text" in value and "value" in value:
            if isinstance(value.get("value"), list):
                # Table envelope — enrich each cell inside the rows.
                value = {**value, "value": _enrich_rows(value["value"])}
                enriched[key] = value
                continue
            location = _resolve_location(value.get("source_text", ""), value.get("value"))
            if location:
                enriched[key] = {**value, "source_location": _attach_bboxes(location.to_dict())}
            else:
                enriched[key] = value
            continue

        # --- Flat format: look for "field.source_text" keys ---
        if key.endswith(".source_text"):
            field_base = key[:-len(".source_text")]
            source_text_keys_processed.add(field_base)

            if isinstance(value, str) and value.strip().upper() not in ("NR", ""):
                location = locate_source(value, source_index)
                enriched[key] = value
                if location:
                    enriched[f"{field_base}.source_location"] = _attach_bboxes(location.to_dict())
            else:
                enriched[key] = value
        else:
            enriched[key] = value

    return enriched


# Value-anchor token patterns. A "distinctive" token is specific enough that
# finding it in the document is meaningful grounding on its own: a decimal,
# a percentage, a multi-digit integer, or a code like "RCT" / "T2DM" / "HbA1c".
_ANCHOR_NUM_RE = re.compile(r"\d+\.\d+%?|\d+%|\d{2,}|\d")
_ANCHOR_CODE_RE = re.compile(r"[A-Za-z]*[A-Z][A-Za-z0-9]*\d[A-Za-z0-9]*|[A-Z]{2,}")


def _value_anchor_tokens(value_str: str) -> Tuple[List[str], List[str]]:
    """Return (all_tokens, distinctive_tokens) for value-anchored grounding."""
    nums = _ANCHOR_NUM_RE.findall(value_str)
    codes = _ANCHOR_CODE_RE.findall(value_str)
    all_toks = nums + codes
    distinctive = [
        t for t in all_toks
        if ("." in t) or ("%" in t) or (t.isdigit() and len(t) >= 2)
        or (not t.isdigit() and len(t) >= 3)
    ]
    return all_toks, distinctive


def locate_value(
    value: Any,
    source_index: SourceIndex,
    bbox_anchors: Optional[List[Any]] = None,
) -> Optional[SourceLocation]:
    """Locate the extracted VALUE itself in the document — a fallback for when
    the model's source_text quote matches poorly (typically table/figure cells,
    where the linearized markdown row isn't a quotable sentence).

    Strategy: anchor on the value's most distinctive token, disambiguate by
    which occurrence has the most *other* value tokens nearby, and — if a
    Datalab Table/Figure block covers that spot — ground to the whole block so
    the bbox highlights the table region. Confidence is deliberately modest
    (this is weaker evidence than a verbatim quote) and it is only ever used
    when the quote match is weak, so it strictly improves those cases.
    """
    if value is None or isinstance(value, (list, dict)):
        return None
    vs = str(value).strip()
    if not vs or vs.upper() in ("NR", "N/R", "NOT REPORTED", ""):
        return None

    all_toks, distinctive = _value_anchor_tokens(vs)
    if not distinctive:
        return None

    md = source_index.full_text
    hay = md.lower()
    anchor = max(distinctive, key=len)
    a = anchor.lower()

    positions: List[int] = []
    start = 0
    while len(positions) <= 200:
        i = hay.find(a, start)
        if i < 0:
            break
        positions.append(i)
        start = i + len(a)
    if not positions:
        return None

    others = [t.lower() for t in all_toks if t.lower() != a]

    def _cooccur(pos: int) -> int:
        window = hay[max(0, pos - 100): pos + len(a) + 100]
        return sum(1 for t in others if t in window)

    best = max(positions, key=_cooccur)
    cooc = _cooccur(best)

    # A lone single/short numeric anchor with no corroborating tokens is too
    # ambiguous to trust (a bare "5" appears everywhere). Require either a
    # genuinely distinctive anchor or at least one co-occurring token.
    strong_anchor = ("." in anchor) or ("%" in anchor) or (not anchor.isdigit() and len(anchor) >= 3)
    if not strong_anchor and cooc == 0:
        return None

    span_start, span_end = best, best + len(anchor)
    method = "value_text"
    page = _char_offset_to_page(best, source_index)
    conf = 0.7

    if bbox_anchors:
        for ba in bbox_anchors:
            if ba.md_start <= best < ba.md_end:
                bt = (getattr(ba, "block_type", "") or "").lower()
                if "table" in bt or "figure" in bt or "picture" in bt:
                    method = "value_table"
                    span_start, span_end = ba.md_start, min(ba.md_end, len(md))
                    page = ba.page
                    conf = 0.8
                break

    return SourceLocation(
        page=page,
        start_char=span_start,
        end_char=span_end,
        matched_text=md[best: best + 120],
        confidence=conf,
        section=_detect_section(md, best),
        grounding_method=method,
    )


def parse_page_map_header(markdown_content: str) -> Optional[List[Dict[str, Any]]]:
    """
    Parse an embedded page map from a markdown header comment.

    Format: <!-- PAGE_MAP: [{"page": 1, "start_char": 0, "end_char": 2453}, ...] -->

    Returns parsed page map or None if not found.
    """
    import json

    match = PAGE_MAP_HEADER_RE.search(markdown_content[:2000])  # Only search header
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse embedded page map: {e}")
            return None
    return None


def embed_page_map_header(markdown_content: str, page_map: List[Dict[str, Any]]) -> str:
    """
    Embed a page map as a comment header in the markdown content.

    This allows downstream consumers to parse page boundaries without
    re-processing the page separators.
    """
    import json

    header = f"<!-- PAGE_MAP: {json.dumps(page_map)} -->\n\n"
    return header + markdown_content


# ── Private helpers ──────────────────────────────────────────────

# Common PDF text artifacts that defeat exact substring matching.
_LIGATURE_MAP: Dict[str, str] = {
    'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
    'ﬅ': 'ft', 'ﬆ': 'st',
}
_QUOTE_MAP: Dict[str, str] = {
    '\u2018': "'", '\u2019': "'", '\u201A': "'", '\u201B': "'",
    '\u201C': '"', '\u201D': '"', '\u201E': '"',
    '\u2013': '-', '\u2014': '-', '\u2212': '-',
}
# Soft hyphen, zero-width space, ZWJ, ZWNJ, word-joiner, BOM
_ZERO_WIDTH = frozenset('\u00AD\u200B\u200C\u200D\u2060\uFEFF')


def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""
    return re.sub(r'\s+', ' ', text.strip())


def _pdf_normalize_with_map(raw: str) -> Tuple[str, List[int]]:
    """
    Normalize PDF-extracted text and produce an offset map back to raw indices.

    Returns (normalized_text, raw_idx_for_norm_char) where
    raw_idx_for_norm_char[i] is the raw-string offset that normalized char i
    came from. The list has length len(normalized_text) + 1, with the final
    entry being len(raw) (sentinel for translating an exclusive end-offset).

    Transformations (in order, all index-tracked):
      - Drop zero-width chars (soft hyphen, ZWSP, BOM, ...)
      - Repair line-break hyphens: 'hyphen-\\nated' -> 'hyphenated'
        (only when the next non-space char is a lowercase letter; common
        PDF line-wrap artifact)
      - Unmap ligatures (ﬁ, ﬂ, ﬀ, ...) into their letter sequences
      - Map curly quotes / en-dash / em-dash to ASCII equivalents
      - Map NBSP to space; collapse any whitespace run to a single space

    NFKC is deliberately NOT applied globally — it can decompose accented
    characters and shift indices in surprising ways; explicit handling of
    the artifacts that actually appear in our PDFs is safer.
    """
    norm_chars: List[str] = []
    raw_map: List[int] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        # Drop zero-width chars
        if ch in _ZERO_WIDTH:
            i += 1
            continue
        # Line-break hyphen repair
        if ch == '-' and i + 1 < n and raw[i + 1] == '\n':
            j = i + 2
            while j < n and raw[j] in ' \t':
                j += 1
            if j < n and raw[j].isalpha() and raw[j].islower():
                i = j
                continue
        # Ligatures
        if ch in _LIGATURE_MAP:
            for c in _LIGATURE_MAP[ch]:
                norm_chars.append(c)
                raw_map.append(i)
            i += 1
            continue
        # Curly quotes / dashes
        if ch in _QUOTE_MAP:
            norm_chars.append(_QUOTE_MAP[ch])
            raw_map.append(i)
            i += 1
            continue
        # Whitespace (incl. NBSP) — collapse runs to a single space
        if ch.isspace() or ch == '\u00A0':
            j = i
            while j < n and (raw[j].isspace() or raw[j] == '\u00A0'):
                j += 1
            norm_chars.append(' ')
            raw_map.append(i)
            i = j
            continue
        norm_chars.append(ch)
        raw_map.append(i)
        i += 1
    raw_map.append(n)
    return ''.join(norm_chars), raw_map


def _detect_section(full_text: str, start_char: int, search_window: int = 3000) -> Optional[str]:
    """
    Detect which academic section the text at start_char belongs to.

    Scans backward from start_char (up to search_window chars) for the nearest
    line whose entire content is a known section heading keyword.

    Returns a canonical section name (e.g. "Methods", "Results") or None.
    """
    search_start = max(0, start_char - search_window)
    preceding_text = full_text[search_start:start_char]

    matches = list(_SECTION_HEADING_RE.finditer(preceding_text))
    if not matches:
        return None

    # Last match = nearest heading above the source text
    keyword = matches[-1].group(1).lower().strip()
    # Collapse internal whitespace for lookup
    keyword = re.sub(r'\s+', ' ', keyword)
    return _SECTION_CANONICAL.get(keyword)


def _char_offset_to_page(offset: int, source_index: SourceIndex) -> int:
    """Map a character offset to the page it falls on (1-indexed)."""
    for chunk in source_index.chunks:
        if chunk.start_char <= offset < chunk.end_char:
            return chunk.page

    # Binary search through chunks
    lo, hi = 0, len(source_index.chunks) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if source_index.chunks[mid].start_char > offset:
            hi = mid - 1
        elif source_index.chunks[mid].end_char <= offset:
            lo = mid + 1
        else:
            return source_index.chunks[mid].page

    # Fallback: find closest chunk
    if source_index.chunks:
        closest = min(source_index.chunks, key=lambda c: abs(c.start_char - offset))
        return closest.page

    return 1  # Default to page 1
