import asyncio
import os
import random
from contextlib import asynccontextmanager

import httpx
import models
from database import Word as DBWord
from database import get_db
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
