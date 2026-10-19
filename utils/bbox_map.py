"""
Char-offset → (page, bbox) map for deterministic source highlighting.

The Datalab/Marker JSON sidecar (uploaded to S3 as s3_blocks_path) contains
per-block geometry — page id + bbox — straight from the PDF parse. This
module aligns each block's text against the rendered markdown string so we
can translate any character-offset range in the markdown back to deterministic
PDF coordinates.

This is the "ground truth" half of source grounding: the LLM's verbatim
source_text is used as an anchor to locate a position in the markdown
(see utils/source_linker.locate_source), and build_bbox_map's output
translates that position to bboxes. No model produces a coordinate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MARKUP_STRIP_RE = re.compile(r"<[^>]+>|[*_`#|]")
_WS_RE = re.compile(r"\s+")


@dataclass
class BlockAnchor:
    md_start: int
    md_end: int
    page: int  # 1-indexed
    bbox: Tuple[float, float, float, float]
    block_type: str = "Text"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "md_start": self.md_start,
            "md_end": self.md_end,
            "page": self.page,
            "bbox": list(self.bbox),
            "block_type": self.block_type,
        }


def _strip_markup(text: str) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _MARKUP_STRIP_RE.sub(" ", text)).strip()


def _bbox_from_block(block: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    b = block.get("bbox")
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        try:
            return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        except (TypeError, ValueError):
            pass
    pts = block.get("polygon")
    if isinstance(pts, (list, tuple)) and pts:
        first = pts[0]
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            try:
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
                return (min(xs), min(ys), max(xs), max(ys))
            except (TypeError, ValueError):
                return None
    return None


def _page_from_block(block: Dict[str, Any]) -> Optional[int]:
    # Datalab uses 0-indexed page_id; some payloads carry 1-indexed page/page_number.
    if "page_id" in block and isinstance(block["page_id"], int):
        return block["page_id"] + 1
    if "page_index" in block and isinstance(block["page_index"], int):
        return block["page_index"] + 1
    for key in ("page", "page_number"):
        v = block.get(key)
        if isinstance(v, int):
            return v
    return None


def _text_from_block(block: Dict[str, Any]) -> str:
    for key in ("text", "content", "html"):
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _walk_blocks(node: Any) -> Iterator[Dict[str, Any]]:
    """Yield dicts that look like blocks (carry geometry + text). Recurses."""
    if isinstance(node, dict):
        has_geom = "bbox" in node or "polygon" in node
        has_text = any(node.get(k) for k in ("text", "content", "html"))
        if has_geom and has_text:
            yield node
        for v in node.values():
            yield from _walk_blocks(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_blocks(item)


def _find_anchor(markdown: str, needle: str, start: int) -> int:
    head = needle[:40].strip()
    if len(head) >= 8:
        pos = markdown.find(head, start)
        if pos >= 0:
            return pos
        pos = markdown.find(head, 0)
        if pos >= 0:
            return pos
    short = needle[:20].strip()
    if len(short) >= 6:
        pos = markdown.find(short, start)
        if pos >= 0:
            return pos
        pos = markdown.find(short, 0)
        if pos >= 0:
            return pos
    return -1


def build_bbox_map(markdown: str, blocks_json: Any) -> List[BlockAnchor]:
    """
    Align each block's text against `markdown` and return a list of BlockAnchor
    mapping md char-offset ranges to (page, bbox).

    Tolerant — blocks whose text cannot be located are skipped (debug-logged).
    The returned list is sorted by md_start. Overlapping anchors are kept
    (a parent block's bbox is often useful for spans that cross children).
    """
    if not markdown or not blocks_json:
        return []

    anchors: List[BlockAnchor] = []
    located = 0
    skipped = 0
    cursor = 0

    for block in _walk_blocks(blocks_json):
        bbox = _bbox_from_block(block)
        page = _page_from_block(block)
        raw_text = _text_from_block(block)
        if bbox is None or page is None or not raw_text:
            skipped += 1
            continue

        needle = _strip_markup(raw_text)
        if len(needle) < 8:
            skipped += 1
            continue

        idx = _find_anchor(markdown, needle, cursor)
        if idx < 0:
            skipped += 1
            continue

        # Conservative end offset: the block's raw text length + small slack
        # to absorb interleaved markdown formatting. Overlap test in
        # find_bboxes_for_span handles edge cases.
        md_end = min(idx + max(len(raw_text), len(needle)) + 20, len(markdown))

        anchors.append(BlockAnchor(
            md_start=idx,
            md_end=md_end,
            page=int(page),
            bbox=bbox,
            block_type=str(block.get("block_type") or block.get("type") or "Text"),
        ))
        located += 1
        if idx >= cursor:
            cursor = idx

    anchors.sort(key=lambda a: (a.md_start, -a.md_end))
    if anchors:
        logger.debug(f"bbox_map: located {located} blocks, skipped {skipped}")
    return anchors


def find_bboxes_for_span(
    anchors: List[BlockAnchor],
    md_start: int,
    md_end: int,
) -> List[Dict[str, Any]]:
    """Return bboxes whose markdown range overlaps [md_start, md_end).

    Sorted by (page, y0). De-duplicated by (page, rounded-bbox). Empty list
    when there are no anchors or no overlap.
    """
    if not anchors or md_end <= md_start:
        return []
    result: List[Dict[str, Any]] = []
    seen = set()
    for a in anchors:
        if a.md_start < md_end and a.md_end > md_start:
            key = (a.page, tuple(round(c, 1) for c in a.bbox))
            if key in seen:
                continue
            seen.add(key)
            result.append({"page": a.page, "bbox": list(a.bbox)})
    result.sort(key=lambda r: (r["page"], r["bbox"][1]))
    return result
