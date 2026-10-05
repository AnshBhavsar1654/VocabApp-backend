"""German noun gender and article lookup via https://der-artikel.de/"""

import logging
import re

import httpx

logger = logging.getLogger(__name__)

ARTICLE_GENDER_MAP = {
    "der": "m",
    "die": "f",
    "das": "n",
}

GENDER_ARTICLE_MAP = {
    "m": "der",
    "f": "die",
    "n": "das",
}

_LEADING_ARTICLE_RE = re.compile(
    r"^(der|die|das|den|dem|des|ein|eine|einen|einem|einer|eines)\s+",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def extract_lemma(text: str) -> str:
    """Extract clean German lemma without leading articles, capitalized.

    e.g. 'der Apfel' -> 'Apfel', 'das Haus' -> 'Haus'.
    """
    cleaned = _LEADING_ARTICLE_RE.sub("", (text or "").strip())
    cleaned = cleaned.strip()
    if not cleaned:
        return ""
    return cleaned[0].upper() + cleaned[1:]


def fetch_gender_from_der_artikel(word: str, client: httpx.Client | None = None) -> tuple[str, str] | None:
    """Search https://der-artikel.de/ for the German noun's article and gender.

    Queries:
      - https://der-artikel.de/der/{Lemma}.html
      - https://der-artikel.de/die/{Lemma}.html
      - https://der-artikel.de/das/{Lemma}.html

    Returns (article, gender) e.g. ('der', 'm'), ('die', 'f'), ('das', 'n'),
    or None if not found on der-artikel.de.
    """
    lemma = extract_lemma(word)
    if not lemma:
        return None

    # der-artikel.de indexes single-word lemmas; skip multi-word phrases
    if " " in lemma:
        return None

    own_client = client is None
    c = client or httpx.Client(headers=_HEADERS, timeout=4.0, follow_redirects=True)
    try:
        for art in ("der", "die", "das"):
            url = f"https://der-artikel.de/{art}/{lemma}.html"
            try:
                res = c.head(url)
                if res.status_code == 200:
                    return (art, ARTICLE_GENDER_MAP[art])
            except Exception as e:
                logger.debug(f"der-artikel.de check failed for {url}: {e}")
    finally:
        if own_client:
            c.close()

    return None


def _fetch_gender_from_gemini(word: str) -> str | None:
    """Fallback gender lookup using Gemini for compound, plural, or irregular nouns."""
    import json
    import os
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    prompt = (
        f'What is the grammatical gender of the German noun "{word}"? '
        'Return ONLY a JSON object with key "gender" whose value is "m" (masculine/der), '
        '"f" (feminine/die), or "n" (neuter/das), or null if not a noun.'
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "response_mime_type": "application/json",
        },
    }
    try:
        with httpx.Client(timeout=4.0) as client:
            res = client.post(url, json=payload)
            if res.status_code == 200:
                data = res.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    parsed = json.loads(parts[0]["text"].strip())
                    g = (parsed.get("gender") or "").strip().lower()
                    if g in ("m", "f", "n"):
                        return g
    except Exception as e:
        logger.debug(f"Gemini gender lookup failed for {word}: {e}")
    return None


def resolve_gender(
    german_word: str,
    pos: str | None = None,
    gemini_article: str | None = None,
    client: httpx.Client | None = None,
) -> str | None:
    """Resolve noun gender ('m', 'f', 'n').

    1. Only applies if pos is 'noun' or undetermined (None).
    2. Searches https://der-artikel.de/ first.
    3. If not found, falls back to gemini_article ('der'->'m', 'die'->'f', 'das'->'n').
    4. If still not found, queries Gemini directly.
    """
    # If explicitly not a noun (verb, adjective, adverb, phrase, other), return None
    if pos and pos != "noun":
        return None

    # Step 1: Check https://der-artikel.de/
    res = fetch_gender_from_der_artikel(german_word, client=client)
    if res:
        return res[1]  # 'm', 'f', or 'n'

    # Step 2: Fallback to Gemini detected article
    if gemini_article:
        art = gemini_article.strip().lower()
        if art in ARTICLE_GENDER_MAP:
            return ARTICLE_GENDER_MAP[art]

    # Step 3: Direct Gemini lookup fallback
    return _fetch_gender_from_gemini(german_word)

