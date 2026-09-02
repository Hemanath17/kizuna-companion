import os
import re

TARGET_CHUNK_CHARS = 1200     
CHUNK_OVERLAP_CHARS = 150       

def split_into_paragraphs(text: str) -> list[str]:
    """Level 1 (coarsest): break raw text into paragraph-level units."""
    paragraphs = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in paragraphs if p.strip()]

def split_into_sentences(paragraph: str) -> list[str]:
    """Level 2 (fallback): only used when a single paragraph is too big on its own."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
    return [s.strip() for s in sentences if s.strip()]

def split_by_hard_limit(text: str, limit: int) -> list[str]:
    """Level 3 (guaranteed last resort): if a sentence is STILL too big
    after Level 2, force-split it by raw character count so no chunk
    can ever silently exceed the target."""
    return [text[i:i + limit] for i in range(0, len(text), limit)]

def _clean_overlap(text: str, overlap_chars: int) -> str:
    """Snap the overlap window to the nearest word boundary instead of
    slicing mid-word (avoids fragments like 'ing exercises...')."""
    raw = text[-overlap_chars:]
    space_index = raw.find(" ")
    return raw[space_index + 1:] if space_index != -1 else raw

def chunk_text(text: str, target_chars: int = TARGET_CHUNK_CHARS,
                overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """
    Recursively split (paragraph -> sentence -> hard limit), then pack
    units back together up to target_chars. Adjacent output chunks
    share a small, word-safe overlap so a sentence near a boundary
    isn't only ever embedded from one side of it.
    """
    paragraphs = split_into_paragraphs(text)

    units = []
    for p in paragraphs:
        if len(p) > target_chars:
            for sentence in split_into_sentences(p):
                if len(sentence) > target_chars:
                    units.extend(split_by_hard_limit(sentence, target_chars))   # Level 3
                else:
                    units.append(sentence)
        else:
            units.append(p)

    chunks = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip() if current else unit
        if len(candidate) > target_chars and current:
            chunks.append(current)
            current = _clean_overlap(current, overlap_chars) + " " + unit
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks

def chunk_fetched_page(text: str, doc_title: str, source: str, url: str) -> list[dict]:
    """
    Convenience wrapper -- takes freshly fetched/parsed page text (already
    stripped of images and tables at the fetching stage) and returns
    chunks in the SAME shape flatten_to_chunks() produces for the
    hand-curated corpus, so both paths feed run_ingestion.py identically.
    """
    pieces = chunk_text(text)
    return [
        {
            "text": piece,
            "heading": f"{doc_title} (part {i + 1})" if len(pieces) > 1 else doc_title,
            "doc_title": doc_title,
            "source": source,
            "url": url,
        }
        for i, piece in enumerate(pieces)
    ]