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


@dataclass
class SourceLocation:
    """Location of a source text snippet in the original document."""
    page: int                   # 1-indexed page number
    start_char: int             # Character offset in full markdown
    end_char: int               # Character offset end
    matched_text: str           # The actual text that was matched
    confidence: float           # Match quality 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "matched_text": self.matched_text,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TextChunk:
    """A chunk of text with its position metadata."""
    text: str
    start_char: int
    end_char: int
    page: int                   # 1-indexed


@dataclass
class SourceIndex:
    """Searchable index of text chunks with page boundaries."""
    chunks: List[TextChunk] = field(default_factory=list)
    full_text: str = ""


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
    """
    index = SourceIndex(full_text=markdown_content)

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
                index.chunks.append(TextChunk(
                    text=para.strip(),
                    start_char=para_start,
                    end_char=para_end,
                    page=page_num,
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
                            ))
            else:
                index.chunks.append(TextChunk(
                    text=para.strip(),
                    start_char=para_start,
                    end_char=para_end,
                    page=page_num,
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
    1. Try exact substring match (fast path)
    2. Fall back to fuzzy matching against chunks
    3. Try sliding window across consecutive chunks for longer passages

    Returns SourceLocation or None if no match above threshold.
    """
    if not source_text or source_text.strip().upper() in ("NR", "N/R", "NOT REPORTED", ""):
        return None

    source_clean = _normalize_whitespace(source_text)
    if len(source_clean) < 5:
        return None

    # --- Fast path: exact substring match ---
    exact_pos = source_index.full_text.find(source_clean)
    if exact_pos >= 0:
        page = _char_offset_to_page(exact_pos, source_index)
        return SourceLocation(
            page=page,
            start_char=exact_pos,
            end_char=exact_pos + len(source_clean),
            matched_text=source_clean,
            confidence=1.0,
        )

    # --- Fuzzy matching against chunks ---
    best_score = 0.0
    best_chunk: Optional[TextChunk] = None

    for chunk in source_index.chunks:
        if not chunk.text:
            continue

        # Quick length filter — skip chunks that are way too short or too long
        len_ratio = len(source_clean) / max(len(chunk.text), 1)
        if len_ratio > 5.0 or len_ratio < 0.1:
            # Source is 5x longer or 10x shorter than chunk — unlikely match
            # But still try partial_ratio for substring matches
            if len(source_clean) > len(chunk.text) * 3:
                continue

        score = fuzz.partial_ratio(source_clean, chunk.text) / 100.0

        if score > best_score:
            best_score = score
            best_chunk = chunk

    # --- Try consecutive chunk pairs for multi-paragraph matches ---
    if best_score < threshold and len(source_index.chunks) > 1:
        for i in range(len(source_index.chunks) - 1):
            chunk_a = source_index.chunks[i]
            chunk_b = source_index.chunks[i + 1]

            # Only combine chunks on the same or adjacent pages
            if abs(chunk_a.page - chunk_b.page) > 1:
                continue

            combined = chunk_a.text + " " + chunk_b.text
            score = fuzz.partial_ratio(source_clean, combined) / 100.0

            if score > best_score:
                best_score = score
                # Create a synthetic combined chunk
                best_chunk = TextChunk(
                    text=combined,
                    start_char=chunk_a.start_char,
                    end_char=chunk_b.end_char,
                    page=chunk_a.page,
                )

    if best_score >= threshold and best_chunk is not None:
        return SourceLocation(
            page=best_chunk.page,
            start_char=best_chunk.start_char,
            end_char=best_chunk.end_char,
            matched_text=best_chunk.text[:500],  # Truncate very long matches
            confidence=best_score,
        )

    return None


def enrich_extraction_results(
    extracted_data: Dict[str, Any],
    markdown_content: str,
    page_map: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Walk the extracted_data dict, find every source_text field,
    and add source_location metadata next to it.

    Handles two formats:
    1. Flat format: {"field.source_text": "...", "field.value": "..."}
    2. Nested format: {"field": {"value": "...", "source_text": "..."}}

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

    enriched = {}
    source_text_keys_processed = set()

    for key, value in extracted_data.items():
        # --- Nested format: {"field": {"value": ..., "source_text": ...}} ---
        if isinstance(value, dict) and "source_text" in value and "value" in value:
            source_text = value.get("source_text", "")
            if isinstance(source_text, str) and source_text.strip().upper() not in ("NR", ""):
                location = locate_source(source_text, source_index)
                if location:
                    enriched[key] = {**value, "source_location": location.to_dict()}
                else:
                    enriched[key] = value
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
                    enriched[f"{field_base}.source_location"] = location.to_dict()
            else:
                enriched[key] = value
        else:
            enriched[key] = value

    return enriched


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

def _normalize_whitespace(text: str) -> str:
    """Collapse multiple whitespace characters into single spaces."""
    return re.sub(r'\s+', ' ', text.strip())


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
