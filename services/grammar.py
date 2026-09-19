"""German grammar auto-suggest: part of speech.

Layered cascade (first hit wins, results cached in `grammar_cache` by caller):
  1. POS — DWDS snippet API (BBAW academy, official, keyless JSON):
           https://www.dwds.de/api/wb/snippet/?q={lemma}
  2. Heuristic fallback — capitalized single word without DWDS hit defaults to noun (low confidence)
  3. Manual override — caller/user always wins.

App POS values: noun | verb | adjective | adverb | phrase | other
"""

import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

_DWDS_SNIPPET_URL = "https://www.dwds.de/api/wb/snippet/"
_LEADING_ARTICLE = re.compile(r"^(der|die|das|den|dem|einem?|einer?)\s+", re.IGNORECASE)
_TIMEOUT = 4.0
_UA = {"User-Agent": "VocabApp/1.0 (German learner vocabulary; contact: admin)"}

AUTO_SUGGEST_ENABLED = os.getenv("GRAMMAR_AUTO_SUGGEST", "on").lower() not in ("off", "0", "false", "no")

_WORTART_MAP = {
    "Substantiv": "noun",
    "Eigenname": "noun",
    "Verb": "verb",
    "Adjektiv": "adjective",
    "partizipiales Adjektiv": "adjective",
    "Adverb": "adverb",
    "partizipiales Adverb": "adverb",
    "Mehrwortausdruck": "phrase",
}


def normalize_german(text: str) -> str:
    """Strip a leading article and whitespace. 'das Haus' -> 'Haus'."""
    return _LEADING_ARTICLE.sub("", (text or "").strip())


def fetch_dwds_pos(word: str, client: httpx.Client | None = None) -> dict | None:
    """POS via the official DWDS snippet API. Returns {pos, ambiguous} or None."""
    if not AUTO_SUGGEST_ENABLED or not word:
        return None
    try:
        own_client = client is None
        client = client or httpx.Client(timeout=_TIMEOUT, headers=_UA)
        try:
            res = client.get(_DWDS_SNIPPET_URL, params={"q": word})
            if res.status_code != 200:
                return None
            entries = res.json()
        finally:
            if own_client:
                client.close()
        if not isinstance(entries, list) or not entries:
            return None
        poses = set()
        for e in entries:
            if isinstance(e, dict):
                mapped = _WORTART_MAP.get(e.get("wortart"))
                if mapped:
                    poses.add(mapped)
        if not poses:
            return {"pos": "other", "ambiguous": False}
        if len(poses) == 1:
            return {"pos": next(iter(poses)), "ambiguous": False}
        return {"pos": "noun" if poses == {"noun"} else None, "ambiguous": True}
    except Exception:
        return None


def suggest(german_text: str) -> dict:
    """Cascade for one German word/phrase.

    Returns {pos, source, confidence, ambiguous}.
    """
    word = normalize_german(german_text)
    blank = {
        "pos": None,
        "source": "none",
        "confidence": "none",
        "ambiguous": False,
    }
    if not word:
        return blank
    if " " in word:
        return {**blank, "pos": "phrase", "source": "heuristic", "confidence": "low"}

    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_UA) as client:
            dwds = fetch_dwds_pos(word, client)
            if dwds is None and word[:1].islower():
                dwds = fetch_dwds_pos(word.capitalize(), client)
            pos = (dwds or {}).get("pos")
            ambiguous = bool((dwds or {}).get("ambiguous"))
            source = "dwds" if dwds else "none"
    except Exception:
        return blank

    if pos is None and word[:1].isupper() and " " not in word:
        return {
            "pos": "noun",
            "source": "heuristic",
            "confidence": "low",
            "ambiguous": False,
        }

    confidence = "high" if source == "dwds" and pos and not ambiguous else "low" if pos else "none"
    return {
        "pos": pos,
        "source": source if pos else "none",
        "confidence": confidence,
        "ambiguous": ambiguous,
    }
