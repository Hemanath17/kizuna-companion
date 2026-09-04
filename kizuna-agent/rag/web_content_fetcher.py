import logging
import os
import trafilatura

logger = logging.getLogger(__name__)

MIN_CONTENT_CHARS = 200
def fetch_page(url: str, source_label: str) -> dict | None:
    """
    Returns {"title": ..., "text": ..., "url": ..., "source": ...}
    or None if the fetch/extraction genuinely failed.
    """
    downloaded = trafilatura.fetch_url(
        url,
        config=_get_trafilatura_config(),
    )
    if downloaded is None:
        logger.warning("Fetch failed (no response): %s", url)
        return None

    text = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    if text is None or len(text) < MIN_CONTENT_CHARS:
        logger.warning(
            "Extraction failed or content too short (%s chars): %s",
            len(text) if text else 0, url,
        )
        return None
    metadata = trafilatura.extract_metadata(downloaded)
    title = metadata.title if metadata and metadata.title else _fallback_title(url)

    logger.info("Fetched OK: %s (%d chars) — %s", url, len(text), title)

    return {
        "title": title,
        "text": text,
        "url": url,
        "source": source_label,
    }
def _fallback_title(url: str) -> str:
    """Derive a readable title from the URL path when metadata has none."""
    path = url.rstrip("/").split("/")[-1]
    cleaned = path.replace("-", " ").replace(".html", "").replace("index", "").strip()
    return cleaned.title() if cleaned else url

def _get_trafilatura_config():
    """Real browser User-Agent + timeout -- avoids being silently blocked
    or hanging indefinitely on one slow request."""
    config = trafilatura.settings.use_config()
    config.set("DEFAULT", "USER_AGENTS", "Mozilla/5.0 (compatible; KizunaResearchBot/1.0)")
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", "15")
    return config