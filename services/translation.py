import os
from deep_translator import GoogleTranslator
from langdetect import detect

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
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        return translator.translate(text)
    except Exception as e:
        print(f"Error translating text: {e}")
        return text
