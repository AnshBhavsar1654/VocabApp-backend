from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
import random
from typing import List
from dotenv import load_dotenv

load_dotenv()

from database import get_db, Word as DBWord
import models
from services.translation import detect_language, translate_text
from services.tts import generate_audio

app = FastAPI(title="German Vocab App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUDIO_DIR = os.getenv("AUDIO_DIR", "./audio_files")
os.makedirs(AUDIO_DIR, exist_ok=True)
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/words", response_model=models.WordResponse)
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
        audio_filename=audio_filename
    )
    db.add(new_word)
    db.commit()
    db.refresh(new_word)
    
    response = models.WordResponse.model_validate(new_word)
    response.audio_url = f"/audio/{audio_filename}"
    return response

@app.get("/api/words", response_model=List[models.WordResponse])
def get_words(db: Session = Depends(get_db)):
    words = db.query(DBWord).order_by(DBWord.created_at.desc()).all()
    results = []
    for w in words:
        r = models.WordResponse.model_validate(w)
        r.audio_url = f"/audio/{w.audio_filename}"
        results.append(r)
    return results

@app.delete("/api/words/{word_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_word(word_id: int, db: Session = Depends(get_db)):
    word = db.query(DBWord).filter(DBWord.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
        
    audio_path = os.path.join(AUDIO_DIR, word.audio_filename)
    if os.path.exists(audio_path):
        os.remove(audio_path)
        
    db.delete(word)
    db.commit()
    return None

@app.get("/api/quiz/next", response_model=models.QuizNextResponse)
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
        audio_url=f"/audio/{word.audio_filename}"
    )

@app.post("/api/quiz/check", response_model=models.QuizCheckResponse)
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
        audio_url=f"/audio/{word.audio_filename}"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
