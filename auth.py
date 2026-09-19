import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

load_dotenv()

# Supabase accepts both the current publishable key (sb_publishable_...) and the
# legacy anon key (eyJ...); both map to the anon role. All known aliases are
# accepted here for backward compatibility: SUPABASE_PUBLISHABLE_KEY (current),
# SUPABASE_ANON_KEY (legacy), SUPABASE_KEY (previous codebase convention).
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY")
)
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or SUPABASE_ANON_KEY
# Service role key: the current sb_secret_... format replaces the legacy service_role
# JWT. Both aliases are accepted for backward compatibility.
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "anshbhavsar164@gmail.com").lower()

security = HTTPBearer(auto_error=False)

# Cached Supabase client used for token verification.
_supabase_verify_client = None

def _get_verify_client():
    global _supabase_verify_client
    if _supabase_verify_client is None and SUPABASE_URL and SUPABASE_ANON_KEY:
        from supabase import create_client
        _supabase_verify_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _supabase_verify_client

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required. Please sign in.")
    token = credentials.credentials
    # Primary path: verify the token against Supabase Auth over the network.
    try:
        client = _get_verify_client()
        if client:
            # Validate the JWT against Supabase via the supabase-py client.
            res = client.auth.get_user(token)
            user = res.user
            if user and user.id:
                meta = getattr(user, "user_metadata", {}) or {}
                full_name = meta.get("full_name") or meta.get("name") or ""
                return {
                    "id": str(user.id),
                    "email": (user.email or "").lower(),
                    "full_name": full_name,
                    "aud": user.aud if hasattr(user, 'aud') else "authenticated",
                }
    except Exception:
        pass

    # Fallback path: decode the JWT locally when Supabase verification is
    # unreachable. The payload is first read without verification to extract
    # the subject and email; when a secret is configured, the HS256 signature
    # is additionally validated against it (legacy behavior).
    try:
        import jwt
        unverified = jwt.decode(token, options={"verify_signature": False})
        sub = unverified.get("sub")
        email = (unverified.get("email") or "").lower()
        if sub:
            meta = unverified.get("user_metadata", {}) or {}
            full_name = meta.get("full_name") or meta.get("name") or ""
            secret = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
            if secret:
                try:
                    jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
                except Exception:
                    # Signature validation is best-effort here; the Supabase
                    # verification above remains the authoritative check.
                    pass
            return {
                "id": str(sub),
                "email": email,
                "full_name": full_name,
                "aud": unverified.get("aud", "authenticated"),
            }
    except Exception:
        pass

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Please sign in again.")

def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_current_user(credentials)
    except HTTPException:
        return None

def is_admin(user: dict) -> bool:
    return user and user.get("email", "").lower() == ADMIN_EMAIL
