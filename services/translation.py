from deep_translator import GoogleTranslator, MyMemoryTranslator
from langdetect import detect

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


def detect_language(text: str) -> str:
    try:
        lang = detect(text)
        if lang == 'de':
            return 'de'
        return 'en'
    except Exception as e:
        print(f"Error detecting language: {e}")
        return "en"


def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    try:
        result = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        if result and result.strip():
            return result
    except Exception as e:
        print(f"GoogleTranslator failed: {e}")

    try:
        src_code = MYMEMORY_CODES.get(source_lang, f"{source_lang}-{source_lang.upper()}")
        tgt_code = MYMEMORY_CODES.get(target_lang, f"{target_lang}-{target_lang.upper()}")
        result = MyMemoryTranslator(source=src_code, target=tgt_code).translate(text)
        if result and result.strip():
            return result
    except Exception as e:
        print(f"MyMemoryTranslator failed: {e}")

    print(f"All translators failed for: {text}")
    return text
