import os
import tempfile
import uuid

from gtts import gTTS
from supabase import Client, create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "audio")

_supabase: Client = None

def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

def generate_audio(german_text: str) -> str:
    safe_text = german_text.encode("ascii", "ignore").decode("ascii").strip()
    slug = safe_text.replace(" ", "_")[:20].lower()
    if not slug:
        slug = "word"

    unique_id = str(uuid.uuid4())[:8]
    filename = f"de_{slug}_{unique_id}.mp3"

    tmp_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex[:8]}.mp3")
    try:
        tts = gTTS(text=german_text, lang='de', slow=True)
        tts.save(tmp_path)

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        supabase = _get_supabase()
        supabase.storage.from_(STORAGE_BUCKET).upload(
            path=filename,
            file=audio_bytes,
            file_options={"content_type": "audio/mpeg"},
        )

        return filename
    except Exception as e:
        print(f"Error generating/uploading audio: {e}")
        raise e
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except PermissionError:
            pass

def delete_audio(filename: str) -> None:
    try:
        supabase = _get_supabase()
        supabase.storage.from_(STORAGE_BUCKET).remove([filename])
    except Exception as e:
        print(f"Error deleting audio from storage: {e}")

def get_audio_url(filename: str) -> str:
    supabase = _get_supabase()
    return supabase.storage.from_(STORAGE_BUCKET).get_public_url(filename)
