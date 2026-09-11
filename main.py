import asyncio
import os
import random
from contextlib import asynccontextmanager

import httpx
import models
from database import Group as DBGroup
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_default_group()
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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


def _ensure_default_group():
    from database import Base, SessionLocal

    # Ensure tables exist (including new reviews table for SRS) — safe to run on every start
    try:
        from database import engine

        Base.metadata.create_all(bind=engine)
    except Exception:
        pass

    db = SessionLocal()
    try:
        existing = db.query(DBGroup).filter(DBGroup.is_default == True).first()
        if not existing:
            default_group = DBGroup(name="Ungrouped", is_default=True)
            db.add(default_group)
            db.commit()
    finally:
        db.close()


@app.get("/groups", response_model=list[models.GroupResponse])
def get_groups(db: Session = Depends(get_db)):
    groups = db.query(DBGroup).order_by(DBGroup.is_default.desc(), DBGroup.created_at.asc()).all()
    result = []
    for g in groups:
        if g.is_default:
            word_count = db.query(DBWord).filter(
                ~DBWord.id.in_(
                    db.query(word_groups.c.word_id)
                    .join(DBGroup, DBGroup.id == word_groups.c.group_id)
                    .filter(DBGroup.is_default == False)
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
def create_group(group_in: models.GroupCreate, db: Session = Depends(get_db)):
    name = group_in.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")

    existing = db.query(DBGroup).filter(func.lower(DBGroup.name) == name.lower()).first()
    if existing:
        raise HTTPException(status_code=409, detail="Group with this name already exists")

    group = DBGroup(name=name)
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
def rename_group(group_id: int, group_in: models.GroupRename, db: Session = Depends(get_db)):
    group = db.query(DBGroup).filter(DBGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_default:
        raise HTTPException(status_code=400, detail="Cannot rename the default group")

    name = group_in.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name cannot be empty")

    existing = db.query(DBGroup).filter(
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
def delete_group(group_id: int, db: Session = Depends(get_db)):
    group = db.query(DBGroup).filter(DBGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if group.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default group")

    db.delete(group)
    db.commit()
    return None


@app.get("/groups/{group_id}/words", response_model=models.GroupWordsResponse)
def get_group_words(group_id: int, db: Session = Depends(get_db)):
    group = db.query(DBGroup).filter(DBGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group.is_default:
        words = db.query(DBWord).filter(
            ~DBWord.id.in_(
                db.query(word_groups.c.word_id)
                .join(DBGroup, DBGroup.id == word_groups.c.group_id)
                .filter(DBGroup.is_default == False)
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
def add_words_to_group(group_id: int, req: models.WordGroupRequest, db: Session = Depends(get_db)):
    group = db.query(DBGroup).filter(DBGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    words = db.query(DBWord).filter(DBWord.id.in_(req.word_ids)).all()
    if len(words) != len(req.word_ids):
        raise HTTPException(status_code=404, detail="One or more words not found")

    for word in words:
        if word not in group.words:
            group.words.append(word)

    db.commit()
    return {"status": "ok"}


@app.delete("/groups/{group_id}/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_word_from_group(group_id: int, word_id: int, db: Session = Depends(get_db)):
    group = db.query(DBGroup).filter(DBGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    word = db.query(DBWord).filter(DBWord.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    if word in group.words:
        group.words.remove(word)
        db.commit()

    return None


@app.post("/words", response_model=models.WordResponse)
def add_word(word_in: models.WordCreate, db: Session = Depends(get_db)):
    if word_in.source_lang == "de":
        german_word = word_in.text
        english_word = translate_text(word_in.text, "de", "en")
    else:
        english_word = word_in.text
        german_word = translate_text(word_in.text, "en", "de")

    existing = db.query(DBWord).filter(
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
        entry_type=word_in.entry_type
    )
    db.add(new_word)
    db.commit()
    db.refresh(new_word)

    response = models.WordResponse.model_validate(new_word)
    response.audio_url = get_audio_url(audio_filename)
    return response


@app.get("/words", response_model=list[models.WordResponse])
def get_words(db: Session = Depends(get_db)):
    words = db.query(DBWord).order_by(DBWord.created_at.desc()).all()
    results = []
    for w in words:
        r = models.WordResponse.model_validate(w)
        r.audio_url = get_audio_url(w.audio_filename)
        r.groups = [models.GroupInfo(id=g.id, name=g.name) for g in w.groups]
        results.append(r)
    return results


@app.delete("/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_word(word_id: int, db: Session = Depends(get_db)):
    word = db.query(DBWord).filter(DBWord.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")

    delete_audio(word.audio_filename)

    db.delete(word)
    db.commit()
    return None


@app.patch("/words/{word_id}", response_model=models.WordResponse)
def update_word(word_id: int, word_in: models.WordUpdate, db: Session = Depends(get_db)):
    word = db.query(DBWord).filter(DBWord.id == word_id).first()
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
def get_quiz_next(db: Session = Depends(get_db)):
    words = db.query(DBWord).all()
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
def get_quiz_session(size: int = 10, db: Session = Depends(get_db)):
    # Session endpoint — production ready: asks # questions (max 20), handles dedupe
    if size < 1 or size > 20:
        raise HTTPException(status_code=400, detail="size must be between 1 and 20")
    words = db.query(DBWord).all()
    if not words:
        raise HTTPException(status_code=404, detail="No words available for quiz")
    if len(words) < size and len(words) < 10:
        # Enforce app rule: need at least 10 words to start a meaningful session
        raise HTTPException(status_code=400, detail=f"Need at least 10 words to start a session (have {len(words)})")
    # Allow smaller session if vocab is limited but >=10, else cap to vocab size
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
def check_quiz_answer(req: models.QuizCheckRequest, db: Session = Depends(get_db)):
    word = db.query(DBWord).filter(DBWord.id == req.id).first()
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
def record_review(req: models.ReviewCreate, db: Session = Depends(get_db)):
    # Immediate backend for SRS: records Got/Missed + typed result, even before scheduler exists
    from database import Review

    word = db.query(DBWord).filter(DBWord.id == req.word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    if req.self_assessment and req.self_assessment not in ("got", "missed"):
        raise HTTPException(status_code=400, detail="self_assessment must be 'got' or 'missed'")

    review = Review(
        word_id=req.word_id,
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
