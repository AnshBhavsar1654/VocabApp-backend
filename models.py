from datetime import datetime

from pydantic import BaseModel


class WordBase(BaseModel):
    english_word: str
    german_word: str
    audio_filename: str
    entry_type: str = "word"

class WordCreate(BaseModel):
    text: str
    source_lang: str
    entry_type: str = "word"

class WordUpdate(BaseModel):
    english_word: str | None = None
    german_word: str | None = None

class WordResponse(WordBase):
    id: int
    created_at: datetime
    audio_url: str | None = None

    class Config:
        from_attributes = True

class QuizNextResponse(BaseModel):
    id: int
    prompt_word: str
    prompt_lang: str
    audio_url: str

class QuizCheckRequest(BaseModel):
    id: int
    prompt_lang: str
    user_answer: str

class QuizCheckResponse(BaseModel):
    correct: bool
    correct_answer: str
    audio_url: str
