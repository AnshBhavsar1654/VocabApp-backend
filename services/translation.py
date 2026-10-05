import json
import logging
import os
import re
from pathlib import Path

import httpx
from deep_translator import GoogleTranslator, LingueeTranslator, MyMemoryTranslator
from dotenv import load_dotenv
from langdetect import detect

logger = logging.getLogger(__name__)

# Ensure .env is loaded
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# Gemini configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

MYMEMORY_CODES = {
    "en": "en-GB",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "pt": "pt-PT",
    "nl": "nl-NL",
    "pl": "pl-PL",
    "ru": "ru-RU",
    "ja": "ja-JP",
    "zh": "zh-CN",
    "ko": "ko-KR",
    "ar": "ar-SA",
    "hi": "hi-IN",
    "tr": "tr-TR",
    "sv": "sv-SE",
    "da": "da-DK",
    "fi": "fi-FI",
    "nb": "nb-NO",
    "uk": "uk-UA",
    "cs": "cs-CZ",
    "el": "el-GR",
    "he": "he-IL",
    "th": "th-TH",
    "vi": "vi-VN",
}

VALID_POS = {"noun", "verb", "adjective", "adverb", "phrase", "other"}
ALLOWED_LOANWORDS = {
    "internet", "email", "e-mail", "computer", "hotel", "baby", "radio",
    "taxi", "software", "hardware", "online", "blog", "app", "t-shirt",
    "jeans", "park", "hobby", "party", "test", "chef", "boss", "fit"
}


def _clean_json_response(raw_text: str) -> str:
    """Strip markdown code fence blocks if returned."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_gemini_prompt(text: str, source_lang_hint: str) -> str:
    return f"""You are an expert bilingual German-English lexicographer for a flashcard vocabulary learning app.

Input text: "{text}"
User-selected source language hint: "{source_lang_hint}"

TASK:
Analyze the input text and generate an accurate English <-> German vocabulary flashcard entry.

CRITICAL RULES:
1. Bidirectional Language Detection:
   - Identify the TRUE language of the input.
   - If user hint is "en" but the input text is clearly German (e.g., "Krankenhaus", "das Haus", "laufen", "schön", "Apfel"), recognize the source is German ("de") and translate it to English!
   - If user hint is "de" but the input text is clearly English (e.g., "apple", "running", "beautiful", "house"), recognize the source is English ("en") and translate it to German!

2. Anti-Echo Guarantee:
   - "english_word" and "german_word" MUST NEVER be identical unless the word is a genuine identical loanword used in everyday speech in both languages (e.g. "Internet", "Email", "Computer", "Hotel").
   - Under no circumstances should you echo a common English word as German or vice versa (e.g. cat -> Katze, NOT cat; Hund -> dog, NOT Hund).

3. German Formatting & Grammar:
   - German nouns MUST always begin with a capital letter (e.g., "Apfel", "Katze", "Bahnhof", "Wort").
   - "german_word" should be the standard lemma/form without a leading article (e.g., "Haus", not "das Haus"), UNLESS the entire input is an idiomatic fixed phrase.
   - If the German word is a noun, provide its definite grammatical article ("der", "die", or "das") in the "article" field. Otherwise null.

4. English Formatting:
   - Provide natural, concise English. For verbs, provide infinitive (e.g. "to run" or "run").

5. Classification:
   - "pos": Must be exactly one of: ["noun", "verb", "adjective", "adverb", "phrase", "other"].
   - "entry_type": "phrase" if multiple words/idiom, otherwise "word".

Return ONLY a valid JSON object matching this schema:
{{
  "detected_lang": "en" | "de",
  "english_word": "...",
  "german_word": "...",
  "article": "der" | "die" | "das" | null,
  "pos": "noun" | "verb" | "adjective" | "adverb" | "phrase" | "other",
  "entry_type": "word" | "phrase"
}}"""


def translate_with_gemini(text: str, source_lang: str = "en") -> dict | None:
    """Call Google Gemini Light models via REST API with structured JSON output."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip() or GEMINI_API_KEY
    if not api_key:
        return None

    prompt = _build_gemini_prompt(text, source_lang)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    }

    url = f"{GEMINI_API_URL}?key={api_key}"
    try:
        with httpx.Client(timeout=6.0) as client:
            res = client.post(url, json=payload)

        if res.status_code == 200:
            data = res.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                cleaned = _clean_json_response(parts[0]["text"])
                parsed = json.loads(cleaned)

                en = (parsed.get("english_word") or "").strip()
                de = (parsed.get("german_word") or "").strip()
                if en and de:
                    # Validate anti-echo
                    if en.lower() == de.lower() and en.lower() not in ALLOWED_LOANWORDS:
                        logger.warning(f"Gemini returned identical words '{en}', rejecting.")
                        return None

                    pos = parsed.get("pos")
                    if pos not in VALID_POS:
                        pos = None

                    return {
                        "english_word": en,
                        "german_word": de,
                        "article": parsed.get("article"),
                        "pos": pos,
                        "entry_type": parsed.get("entry_type") or ("phrase" if " " in text.strip() else "word"),
                        "detected_lang": parsed.get("detected_lang") or source_lang,
                        "provider": f"gemini ({GEMINI_MODEL})",
                    }
        else:
            logger.warning(f"Gemini {GEMINI_MODEL} returned HTTP {res.status_code}: {res.text[:200]}")
    except Exception as e:
        logger.warning(f"Gemini {GEMINI_MODEL} call failed: {e}")

    return None


def _is_echo(candidate: str, original: str) -> bool:
    """Check if candidate translation is an echo of the original text."""
    clean_c = re.sub(r"[^\w\s]", "", candidate).strip().lower()
    clean_o = re.sub(r"[^\w\s]", "", original).strip().lower()
    if not clean_c or not clean_o:
        return True
    if clean_c in ALLOWED_LOANWORDS:
        return False
    return clean_c == clean_o


def _translate_with_linguee(text: str, source_lang: str, target_lang: str) -> str | None:
    """Translate using Linguee dictionary (excellent for DE <-> EN vocabulary)."""
    lang_map = {"en": "english", "de": "german"}
    src = lang_map.get(source_lang, source_lang)
    tgt = lang_map.get(target_lang, target_lang)
    try:
        translated = LingueeTranslator(source=src, target=tgt).translate(text)
        if translated and translated.strip():
            candidate = translated.strip()
            if not _is_echo(candidate, text):
                return candidate.rstrip(".,;:!? ")
    except Exception as e:
        logger.debug(f"Linguee failed for '{text}': {e}")
    return None


def _translate_with_google(text: str, source_lang: str, target_lang: str) -> str | None:
    """Translate using Google Translator."""
    try:
        translated = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        if translated and translated.strip():
            candidate = translated.strip()
            if not _is_echo(candidate, text):
                return candidate.rstrip(".,;:!? ")
    except Exception as e:
        logger.debug(f"GoogleTranslator failed for '{text}': {e}")
    return None


def _translate_with_mymemory(text: str, source_lang: str, target_lang: str) -> str | None:
    """Translate using MyMemory with anti-echo check."""
    try:
        src_code = MYMEMORY_CODES.get(source_lang, f"{source_lang}-{source_lang.upper()}")
        tgt_code = MYMEMORY_CODES.get(target_lang, f"{target_lang}-{target_lang.upper()}")
        translated = MyMemoryTranslator(source=src_code, target=tgt_code).translate(text)
        if translated and translated.strip():
            candidate = translated.strip()
            if not _is_echo(candidate, text):
                return candidate.rstrip(".,;:!? ")
    except Exception as e:
        logger.debug(f"MyMemoryTranslator failed for '{text}': {e}")
    return None



def detect_language(text: str) -> str:
    """Detect language between 'de' and 'en'."""
    try:
        lang = detect(text)
        if lang == "de":
            return "de"
        return "en"
    except Exception as e:
        logger.debug(f"Error detecting language: {e}")
        return "en"


def translate_vocabulary(text: str, source_lang: str = "en", user_pos: str | None = None) -> dict:
    """Translate vocabulary card with multi-tier architecture:

    1. Gemini Light (Gemini 2.5 Flash / 1.5 Flash / 2.0 Flash) with rich linguistic analysis
    2. Linguee bilingual dictionary (free, high accuracy for EN <-> DE)
    3. Google Translator
    4. MyMemory Translator with anti-echo filter
    5. Bidirectional swap detection (handles user entering German while toggle is set to English)
    """
    clean_text = text.strip()
    entry_type = "phrase" if " " in clean_text else "word"

    # Tier 1: Try Gemini
    gemini_result = translate_with_gemini(clean_text, source_lang=source_lang)
    if gemini_result:
        return gemini_result

    # Tier 2 & 3: Multi-engine fallback
    target_lang = "de" if source_lang == "en" else "en"

    translated = (
        _translate_with_linguee(clean_text, source_lang, target_lang)
        or _translate_with_google(clean_text, source_lang, target_lang)
        or _translate_with_mymemory(clean_text, source_lang, target_lang)
    )

    # Tier 4: Bidirectional recovery
    # If translation failed or returned nothing, check if the user entered the OTHER language
    if not translated:
        alt_source = target_lang
        alt_target = source_lang
        alt_translated = (
            _translate_with_linguee(clean_text, alt_source, alt_target)
            or _translate_with_google(clean_text, alt_source, alt_target)
            or _translate_with_mymemory(clean_text, alt_source, alt_target)
        )
        if alt_translated:
            # User had toggle on the wrong language!
            if alt_source == "de":
                # clean_text is German, alt_translated is English
                return {
                    "english_word": alt_translated,
                    "german_word": clean_text,
                    "article": None,
                    "pos": user_pos,
                    "entry_type": entry_type,
                    "detected_lang": "de",
                    "provider": "linguee/fallback (bidirectional)",
                }
            else:
                # clean_text is English, alt_translated is German
                return {
                    "english_word": clean_text,
                    "german_word": alt_translated,
                    "article": None,
                    "pos": user_pos,
                    "entry_type": entry_type,
                    "detected_lang": "en",
                    "provider": "linguee/fallback (bidirectional)",
                }

    # If translation succeeded in expected direction
    if translated:
        if source_lang == "de":
            english_word = translated
            german_word = clean_text
        else:
            english_word = clean_text
            german_word = translated

        # Capitalize German noun if applicable
        if user_pos == "noun" and german_word and german_word[0].islower():
            german_word = german_word[0].upper() + german_word[1:]

        return {
            "english_word": english_word,
            "german_word": german_word,
            "article": None,
            "pos": user_pos,
            "entry_type": entry_type,
            "detected_lang": source_lang,
            "provider": "linguee/fallback",
        }

    # Final fallback if all failed: return clean text with warning
    logger.error(f"All translation methods failed for text: '{clean_text}'")
    if source_lang == "de":
        return {
            "english_word": clean_text,
            "german_word": clean_text,
            "article": None,
            "pos": user_pos,
            "entry_type": entry_type,
            "detected_lang": source_lang,
            "provider": "failed",
        }
    else:
        return {
            "english_word": clean_text,
            "german_word": clean_text,
            "article": None,
            "pos": user_pos,
            "entry_type": entry_type,
            "detected_lang": source_lang,
            "provider": "failed",
        }


def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Backwards-compatible string-to-string translation helper."""
    clean = text.strip()
    if not clean:
        return ""

    # Try Linguee first (best bilingual dictionary)
    linguee = _translate_with_linguee(clean, source_lang, target_lang)
    if linguee:
        return linguee

    # Try Google
    google = _translate_with_google(clean, source_lang, target_lang)
    if google:
        return google

    # Try MyMemory
    mymemory = _translate_with_mymemory(clean, source_lang, target_lang)
    if mymemory:
        return mymemory

    return text
