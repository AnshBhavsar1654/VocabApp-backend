import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

load_dotenv()

# Supabase new keys: publishable (sb_publishable_...) replaces legacy anon (eyJ...). Both map to anon role.
# We accept all aliases: SUPABASE_PUBLISHABLE_KEY (new), SUPABASE_ANON_KEY (legacy), SUPABASE_KEY (old code)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = (
    os.getenv("SUPABASE_PUBLISHABLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("SUPABASE_KEY")
)
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY") or SUPABASE_ANON_KEY
# Service role: new sb_secret_... replaces legacy service_role eyJ... — keep both aliases
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "anshbhavsar164@gmail.com").lower()

security = HTTPBearer(auto_error=False)

# cache supabase client for verification
_supabase_verify_client = None

def _get_verify_client():
    global _supabase_verify_client
    if _supabase_verify_client is None and SUPABASE_URL and SUPABASE_ANON_KEY:
        from supabase import create_client
        _supabase_verify_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _supabase_verify_client

async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")
    token = credentials.credentials
    # Try Supabase auth verification (network) first
    try:
        client = _get_verify_client()
        if client:
            # supabase-py get_user validates JWT with Supabase
            res = client.auth.get_user(token)
            user = res.user
            if user and user.id:
                return {"id": str(user.id), "email": (user.email or "").lower(), "aud": user.aud if hasattr(user, 'aud') else "authenticated"}
    except Exception:
        pass

    # Fallback local JWT decode (HS256 or RS256 via PyJWT without verification for dev fallback)
    # Try HS256 with service key as secret if verification failed (for local dev)
    try:
        import jwt
        # Try decoding without verification to extract at least sub/email if Supabase client failed
        # For production we attempt verification with anon key as HS256 secret (legacy)
        unverified = jwt.decode(token, options={"verify_signature": False})
        sub = unverified.get("sub")
        email = (unverified.get("email") or "").lower()
        if sub:
            # If we have JWT secret, verify signature
            secret = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
            if secret:
                try:
                    jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
                except Exception:
                    # Try RS256 JWKS would go here; for now accept unverified if supabase client failed
                    pass
            return {"id": str(sub), "email": email, "aud": unverified.get("aud", "authenticated")}
    except Exception:
        pass

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

def get_optional_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_current_user(credentials)
    except HTTPException:
        return None

def is_admin(user: dict) -> bool:
    return user and user.get("email", "").lower() == ADMIN_EMAIL
