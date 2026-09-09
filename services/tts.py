import os
import tempfile
import uuid

from dotenv import load_dotenv
from gtts import gTTS
from supabase import Client, create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "audio")

_supabase: Client = None

def _get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        # Re-read env at call time so import order vs load_dotenv() doesn't matter
        url = os.getenv("SUPABASE_URL") or SUPABASE_URL
        key = os.getenv("SUPABASE_KEY") or SUPABASE_KEY
        _supabase = create_client(url, key)
    return _supabase


def _resolve_bucket_and_url() -> tuple[str, str | None]:
    """Return (bucket, base_url) reading env lazily."""
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET") or STORAGE_BUCKET or "audio"
    base_url = os.getenv("SUPABASE_URL") or SUPABASE_URL
    return bucket, base_url

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
            file_options={"content-type": "audio/mpeg", "upsert": "true"},
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

def get_audio_url(filename: str) -> str | None:
    if not filename:
        return None
    bucket, base_url = _resolve_bucket_and_url()
    if base_url:
        # Primary path — no SDK, no HTTP, just string interpolation
        return f"{base_url.rstrip('/')}/storage/v1/object/public/{bucket}/{filename}"
    # Fallback: try SDK's get_public_url (also zero network, pure formatting)
    try:
        supabase = _get_supabase()
        return supabase.storage.from_(bucket).get_public_url(filename)
    except Exception:
        return None
