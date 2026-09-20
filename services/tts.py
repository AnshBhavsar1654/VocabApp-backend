import os
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv
from gtts import gTTS
from supabase import Client, create_client

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_PUBLISHABLE_KEY")
)
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "audio")

_supabase: Client = None

def _get_supabase() -> Client | None:
    global _supabase
    if _supabase is None:
        url = os.getenv("SUPABASE_URL") or SUPABASE_URL
        key = (
            os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or SUPABASE_KEY
        )
        if not url or not key:
            print("Warning: Missing SUPABASE_URL or Supabase API key for storage operations.")
            return None
        _supabase = create_client(url, key)
    return _supabase


def _resolve_bucket_and_url() -> tuple[str, str | None]:
    """Resolve the storage bucket and base URL from the environment."""
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET") or STORAGE_BUCKET or "audio"
    base_url = os.getenv("SUPABASE_URL") or SUPABASE_URL
    return bucket, base_url

def generate_audio(german_text: str) -> str | None:
    if not german_text or not german_text.strip():
        return None

    safe_text = german_text.encode("ascii", "ignore").decode("ascii").strip()
    slug = safe_text.replace(" ", "_")[:20].lower()
    if not slug:
        slug = "word"

    unique_id = str(uuid.uuid4())[:8]
    filename = f"de_{slug}_{unique_id}.mp3"

    tmp_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex[:8]}.mp3")
    try:
        supabase = _get_supabase()
        if not supabase:
            print(f"Skipping audio upload for '{german_text}': Supabase client not initialized")
            return None

        tts = gTTS(text=german_text, lang='de', slow=True)
        tts.save(tmp_path)

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        bucket, _ = _resolve_bucket_and_url()
        supabase.storage.from_(bucket).upload(
            path=filename,
            file=audio_bytes,
            file_options={"content-type": "audio/mpeg", "upsert": "true"},
        )

        return filename
    except Exception as e:
        print(f"Error generating/uploading audio for '{german_text}': {e}")
        return None
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except (OSError, PermissionError):
            pass

def delete_audio(filename: str) -> None:
    if not filename:
        return
    try:
        supabase = _get_supabase()
        if supabase:
            bucket, _ = _resolve_bucket_and_url()
            supabase.storage.from_(bucket).remove([filename])
    except Exception as e:
        print(f"Error deleting audio from storage: {e}")

def get_audio_url(filename: str) -> str | None:
    if not filename:
        return None
    bucket, base_url = _resolve_bucket_and_url()
    if base_url:
        # Primary path: construct the public URL directly without SDK or network calls.
        return f"{base_url.rstrip('/')}/storage/v1/object/public/{bucket}/{filename}"
    # Fallback: derive the URL via the SDK helper (likewise network-free).
    try:
        supabase = _get_supabase()
        return supabase.storage.from_(bucket).get_public_url(filename)
    except Exception:
        return None
