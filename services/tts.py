import os
import uuid
from gtts import gTTS

AUDIO_DIR = os.getenv("AUDIO_DIR", "./audio_files")

def generate_audio(german_text: str) -> str:
    safe_text = "".join(c for c in german_text if c.isalnum() or c.isspace()).strip()
    slug = safe_text.replace(" ", "_")[:20].lower()
    if not slug:
        slug = "word"
    
    unique_id = str(uuid.uuid4())[:8]
    filename = f"de_{slug}_{unique_id}.mp3"
    filepath = os.path.join(AUDIO_DIR, filename)
    
    try:
        tts = gTTS(text=german_text, lang='de', slow=True)
        tts.save(filepath)
        return filename
    except Exception as e:
        print(f"Error generating audio: {e}")
        raise e
