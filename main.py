import asyncio
import os
import random
import uuid
from contextlib import asynccontextmanager

import httpx
import models
from auth import ADMIN_EMAIL, get_current_user, is_admin
from database import Group as DBGroup
from database import Profile as DBProfile
from database import Word as DBWord
from database import get_db, word_groups
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from services.translation import translate_text
from services.tts import delete_audio, generate_audio, get_audio_url
from sqlalchemy import func
from sqlalchemy.orm import Session

load_dotenv()

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
MODE = os.getenv("MODE", "dev").lower()
# MODE-driven defaults: dev = localhost, prod = vercel/render — single toggle
if MODE == "dev":
    _default_frontend = "http://localhost:5173"
    _default_origins = "http://localhost:5173,http://localhost:3000"
else:
    _default_frontend = "https://vocab-app-frontend-rosy.vercel.app"
    _default_origins = "https://vocab-app-frontend-rosy.vercel.app,https://vocabapp.vercel.app"

FRONTEND_URL = os.getenv("FRONTEND_URL", _default_frontend)
# allow both local dev and vercel preview
ALLOWED_ORIGINS = [FRONTEND_URL, "http://localhost:5173", "http://localhost:3000", "https://vocab-app-frontend-rosy.vercel.app", "https://vocabapp.vercel.app"]
# also support comma-separated env — explicit env overrides mode defaults
if os.getenv("ALLOWED_ORIGINS"):
    ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS").split(",")]
elif MODE == "dev":
    ALLOWED_ORIGINS = [o.strip() for o in _default_origins.split(",")] + [FRONTEND_URL]
else:
    ALLOWED_ORIGINS = [o.strip() for o in _default_origins.split(",")]
# dedupe
ALLOWED_ORIGINS = list(dict.fromkeys(ALLOWED_ORIGINS))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure extension and tables exist (without user)
    try:
        from database import Base, engine
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass
    task = asyncio.create_task(_keep_alive())
    yield
    task.cancel()


async def _keep_alive():
    if not RENDER_EXTERNAL_URL:
        return
    while True:
        await asyncio.sleep(600)
        try:
            async with httpx.AsyncClient() as client:
                await client.get(f"{RENDER_EXTERNAL_URL}/health", timeout=10)
        except Exception:
            pass


app = FastAPI(title="German Vocab App", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/auth/me")
def get_me(user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_profile(db, user)
    _ensure_user_default_group(db, user)
    return {"id": user["id"], "email": user["email"], "is_admin": is_admin(user)}


def _user_uuid(user: dict):
    return uuid.UUID(user["id"])

def _ensure_profile(db: Session, user: dict):
    # First authenticated call persists the user to public.profiles so they
    # show up in Supabase (auth.users row itself is created by Supabase Auth).
    try:
        uid = _user_uuid(user)
        existing = db.query(DBProfile).filter(DBProfile.id == uid).first()
        if not existing:
            db.add(DBProfile(id=uid, email=user.get("email", ""), is_admin=is_admin(user)))
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _ensure_user_default_group(db: Session, user: dict):
    _ensure_profile(db, user)
    uid = _user_uuid(user)
    existing = db.query(DBGroup).filter(DBGroup.user_id == uid, DBGroup.is_default == True).first()
    if not existing:
        default_group = DBGroup(name="Ungrouped", is_default=True, user_id=uid)
        db.add(default_group)
        db.commit()
        db.refresh(default_group)
        return default_group
    return existing


@app.get("/groups", response_model=list[models.GroupResponse])
def get_groups(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    _ensure_user_default_group(db, user)
    uid = _user_uuid(user)
    groups = db.query(DBGroup).filter(DBGroup.user_id == uid).order_by(DBGroup.is_default.desc(), DBGroup.created_at.asc()).all()
    result = []
    for g in groups:
        if g.is_default:
            word_count = db.query(DBWord).filter(
                DBWord.user_id == uid,
                ~DBWord.id.in_(
                    db.query(word_groups.c.word_id)
                    .join(DBGroup, DBGroup.id == word_groups.c.group_id)
                    .filter(DBGroup.user_id == uid, DBGroup.is_default == False)
                )
            ).count()
        else:
            word_count = len(g.words)
        result.append(models.GroupResponse(
            id=g.id,
            name=g.name,
            is_default=g.is_default,
            created_at=g.created_at,
            word_count=word_count,
        ))
    return result


@app.post("/groups", response_model=models.GroupResponse)
def create_group(group_in: models.GroupCreate, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    _ensure_user_default_group(db, user)
    name = group_in.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")

    existing = db.query(DBGroup).filter(DBGroup.user_id == uid, func.lower(DBGroup.name) == name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Group with this name already exists")

    group = DBGroup(name=name, user_id=uid)
    db.add(group)
    db.commit()
    db.refresh(group)
    return models.GroupResponse(
        id=group.id,
        name=group.name,
        is_default=group.is_default,
        created_at=group.created_at,
        word_count=0,
    )


@app.patch("/groups/{group_id}", response_model=models.GroupResponse)
def rename_group(group_id: uuid.UUID, group_in: models.GroupRename, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    group = db.query(DBGroup).filter(DBGroup.id == group_id, DBGroup.user_id == uid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_default:
        raise HTTPException(status_code=400, detail="Cannot rename the default group")

    name = group_in.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")

    existing = db.query(DBGroup).filter(
        DBGroup.user_id == uid,
        func.lower(DBGroup.name) == name.lower(),
        DBGroup.id != group_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="Group with this name already exists")

    group.name = name
    db.commit()
    db.refresh(group)
    return models.GroupResponse(
        id=group.id,
        name=group.name,
        is_default=group.is_default,
        created_at=group.created_at,
        word_count=len(group.words),
    )


@app.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: uuid.UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    group = db.query(DBGroup).filter(DBGroup.id == group_id, DBGroup.user_id == uid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default group")

    db.delete(group)
    db.commit()
    return None


@app.get("/groups/{group_id}/words", response_model=models.GroupWordsResponse)
def get_group_words(group_id: uuid.UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    group = db.query(DBGroup).filter(DBGroup.id == group_id, DBGroup.user_id == uid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.is_default:
        words = db.query(DBWord).filter(
            DBWord.user_id == uid,
            ~DBWord.id.in_(
                db.query(word_groups.c.word_id)
                .join(DBGroup, DBGroup.id == word_groups.c.group_id)
                .filter(DBGroup.user_id == uid, DBGroup.is_default == False)
            )
        ).order_by(DBWord.created_at.desc()).all()
    else:
        words = group.words

    word_responses = []
    for w in words:
        r = models.WordResponse.model_validate(w)
        r.audio_url = get_audio_url(w.audio_filename)
        r.groups = [models.GroupInfo(id=g.id, name=g.name) for g in w.groups]
        word_responses.append(r)

    return models.GroupWordsResponse(
        id=group.id,
        name=group.name,
        is_default=group.is_default,
        words=word_responses,
    )


@app.post("/groups/{group_id}/words", status_code=status.HTTP_200_OK)
def add_words_to_group(group_id: uuid.UUID, req: models.WordGroupRequest, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    group = db.query(DBGroup).filter(DBGroup.id == group_id, DBGroup.user_id == uid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    words = db.query(DBWord).filter(DBWord.id.in_(req.word_ids), DBWord.user_id == uid).all()
    if len(words) != len(req.word_ids):
        raise HTTPException(status_code=404, detail="One or more words not found or not owned")

    for word in words:
        if word not in group.words:
            group.words.append(word)

    db.commit()
    return {"status": "ok"}


@app.delete("/groups/{group_id}/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_word_from_group(group_id: uuid.UUID, word_id: uuid.UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    group = db.query(DBGroup).filter(DBGroup.id == group_id, DBGroup.user_id == uid).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    word = db.query(DBWord).filter(DBWord.id == word_id, DBWord.user_id == uid).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    if word in group.words:
        group.words.remove(word)
        db.commit()

    return None


@app.post("/words", response_model=models.WordResponse)
def add_word(word_in: models.WordCreate, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    _ensure_user_default_group(db, user)
    if word_in.source_lang == "de":
        german_word = word_in.text
        english_word = translate_text(word_in.text, "de", "en")
    else:
        english_word = word_in.text
        german_word = translate_text(word_in.text, "en", "de")

    existing = db.query(DBWord).filter(
        DBWord.user_id == uid,
        func.lower(DBWord.english_word) == english_word.lower(),
        func.lower(DBWord.german_word) == german_word.lower()
    ).first()

    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Word already exists")

    audio_filename = generate_audio(german_word)

    new_word = DBWord(
        english_word=english_word,
        german_word=german_word,
        audio_filename=audio_filename,
        entry_type=word_in.entry_type,
        user_id=uid,
    )
    db.add(new_word)
    db.commit()
    db.refresh(new_word)

    response = models.WordResponse.model_validate(new_word)
    response.audio_url = get_audio_url(audio_filename)
    return response


@app.get("/words", response_model=list[models.WordResponse])
def get_words(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    words = db.query(DBWord).filter(DBWord.user_id == uid).order_by(DBWord.created_at.desc()).all()
    results = []
    for w in words:
        r = models.WordResponse.model_validate(w)
        r.audio_url = get_audio_url(w.audio_filename)
        r.groups = [models.GroupInfo(id=g.id, name=g.name) for g in w.groups]
        results.append(r)
    return results


@app.delete("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_word(word_id: uuid.UUID, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    word = db.query(DBWord).filter(DBWord.id == word_id, DBWord.user_id == uid).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    delete_audio(word.audio_filename)

    db.delete(word)
    db.commit()
    return None


@app.patch("/words/{word_id}", response_model=models.WordResponse)
def update_word(word_id: uuid.UUID, word_in: models.WordUpdate, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    word = db.query(DBWord).filter(DBWord.id == word_id, DBWord.user_id == uid).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    german_changed = word_in.german_word is not None and word_in.german_word != word.german_word

    if german_changed:
        delete_audio(word.audio_filename)
        word.german_word = word_in.german_word
        try:
            word.audio_filename = generate_audio(word_in.german_word)
        except Exception as e:
            word.audio_filename = None
            print(f"Audio regeneration failed: {e}")

    if word_in.english_word is not None:
        word.english_word = word_in.english_word

    db.commit()
    db.refresh(word)

    response = models.WordResponse.model_validate(word)
    response.audio_url = get_audio_url(word.audio_filename)
    return response


@app.get("/quiz/next", response_model=models.QuizNextResponse)
def get_quiz_next(db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    words = db.query(DBWord).filter(DBWord.user_id == uid).all()
    if not words:
        raise HTTPException(status_code=404, detail="No words available for quiz")

    word = random.choice(words)
    lang = random.choice(["de", "en"])

    prompt_word = word.german_word if lang == "de" else word.english_word

    return models.QuizNextResponse(
        id=word.id,
        prompt_word=prompt_word,
        prompt_lang=lang,
        audio_url=get_audio_url(word.audio_filename)
    )


@app.get("/quiz/session", response_model=models.QuizSessionResponse)
def get_quiz_session(size: int = 10, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    if size < 1 or size > 20:
        raise HTTPException(status_code=400, detail="size must be between 1 and 20")
    uid = _user_uuid(user)
    words = db.query(DBWord).filter(DBWord.user_id == uid).all()
    if not words:
        raise HTTPException(status_code=404, detail="No words available for quiz")
    if len(words) < size and len(words) < 10:
        raise HTTPException(status_code=400, detail=f"Need at least 10 words to start a session (have {len(words)})")
    actual = min(size, len(words))
    chosen = random.sample(words, actual) if actual <= len(words) else [random.choice(words) for _ in range(actual)]
    questions = []
    for w in chosen:
        lang = random.choice(["de", "en"])
        prompt_word = w.german_word if lang == "de" else w.english_word
        questions.append(
            models.QuizNextResponse(
                id=w.id,
                prompt_word=prompt_word,
                prompt_lang=lang,
                audio_url=get_audio_url(w.audio_filename),
            )
        )
    random.shuffle(questions)
    return models.QuizSessionResponse(questions=questions, size=actual)


@app.post("/quiz/check", response_model=models.QuizCheckResponse)
def check_quiz_answer(req: models.QuizCheckRequest, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    uid = _user_uuid(user)
    word = db.query(DBWord).filter(DBWord.id == req.id, DBWord.user_id == uid).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    correct_answer = word.english_word if req.prompt_lang == "de" else word.german_word

    user_norm = req.user_answer.strip().lower()
    correct_norm = correct_answer.strip().lower()

    is_correct = (user_norm == correct_norm)

    return models.QuizCheckResponse(
        correct=is_correct,
        correct_answer=correct_answer,
        audio_url=get_audio_url(word.audio_filename)
    )


@app.post("/quiz/record", response_model=models.ReviewResponse)
def record_review(req: models.ReviewCreate, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    from database import Review

    uid = _user_uuid(user)
    word = db.query(DBWord).filter(DBWord.id == req.word_id, DBWord.user_id == uid).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    if req.self_assessment and req.self_assessment not in ("got", "missed"):
        raise HTTPException(status_code=400, detail="self_assessment must be 'got' or 'missed'")

    review = Review(
        word_id=req.word_id,
        user_id=uid,
        is_correct=req.is_correct,
        self_assessment=req.self_assessment,
        typed_answer=req.typed_answer,
        prompt_lang=req.prompt_lang,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
