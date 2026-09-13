from datetime import datetime
from uuid import UUID

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

class GroupInfo(BaseModel):
    id: UUID
    name: str

    class Config:
        from_attributes = True

class WordResponse(WordBase):
    id: UUID
    created_at: datetime
    audio_url: str | None = None
    groups: list[GroupInfo] = []

    class Config:
        from_attributes = True

class GroupCreate(BaseModel):
    name: str

class GroupRename(BaseModel):
    name: str

class GroupResponse(BaseModel):
    id: UUID
    name: str
    is_default: bool
    created_at: datetime
    word_count: int = 0

    class Config:
        from_attributes = True

class GroupWordsResponse(BaseModel):
    id: UUID
    name: str
    is_default: bool
    words: list[WordResponse]

    class Config:
        from_attributes = True

class WordGroupRequest(BaseModel):
    word_ids: list[UUID]


class GroupWordOrder(BaseModel):
    word_ids: list[UUID]

class QuizNextResponse(BaseModel):
    id: UUID
    prompt_word: str
    prompt_lang: str
    audio_url: str | None = None

class QuizCheckRequest(BaseModel):
    id: UUID
    prompt_lang: str
    user_answer: str

class QuizCheckResponse(BaseModel):
    correct: bool
    correct_answer: str
    audio_url: str | None = None


class QuizSessionResponse(BaseModel):
    questions: list[QuizNextResponse]
    size: int


class ReviewCreate(BaseModel):
    word_id: UUID
    is_correct: bool
    self_assessment: str | None = None  # Permitted values: "got" | "missed".
    typed_answer: str | None = None
    prompt_lang: str | None = None


class ReviewResponse(BaseModel):
    id: UUID
    word_id: UUID
    is_correct: bool
    self_assessment: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True
