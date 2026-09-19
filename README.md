# VocabApp Backend

FastAPI service behind the VocabApp German vocabulary trainer. Per-user
word lists with automatic translation, German text-to-speech audio,
custom groups, and quiz sessions with review logging.

Production: Render (`vocabapp-backend.onrender.com`). Data: Supabase
Postgres (primary store), Supabase Auth (JWT verification), Supabase
Storage (audio files).

## Stack

- FastAPI + Uvicorn, SQLAlchemy 2.0 (NullPool, `sslmode=require`), Pydantic v2
- Supabase (`supabase-py`): Postgres, Auth, Storage
- gTTS for German pronunciation audio, `deep-translator` (+ MyMemory
  fallback) for EN/DE translation, `langdetect`
- `httpx` for outbound calls (DWDS, Wikidata, keep-alive ping)

## Structure

| Path | Purpose |
|---|---|
| `main.py` | All routes, CORS, lifespan (table creation + lightweight migrations, keep-alive task) |
| `auth.py` | Supabase JWT verification (network) with local-decode fallback; admin check |
| `database.py` | Engine, session, ORM models (`Word`, `Group`, `word_groups`, `Review`, `Profile`, `GrammarCache`) |
| `models.py` | Pydantic request/response schemas |
| `services/tts.py` | Audio generate/delete; public-URL formatting with zero network calls |
| `services/translation.py` | GoogleTranslator with MyMemory fallback |
| `services/grammar.py` | Part-of-speech auto-suggest cascade (see below) |
| `scripts/backfill_pos.py` | Backfills `pos` for existing words (`--apply` to persist) |
| `Dockerfile` | `python:3.13-slim` image for Render |

## Setup

```bash
# from backend/
uv venv --python 3.13              # or: python -m venv .venv
uv pip install -r requirements.txt # project also carries pyproject.toml / uv.lock
```

Required environment (`.env`, never committed):

```
DATABASE_URL=postgresql://...      # Supabase pooler URL
SUPABASE_URL=https://....supabase.co
SUPABASE_KEY=...                   # publishable/anon key
SUPABASE_STORAGE_BUCKET=audio
ALLOWED_ORIGINS=https://<frontend> # appended to safe defaults
FRONTEND_URL=...                   # single frontend origin fallback
RENDER_EXTERNAL_URL=...            # enables the /health keep-alive ping
MODE=dev|prod
ADMIN_EMAIL=...
GRAMMAR_AUTO_SUGGEST=on            # set to off to disable network grammar layers
```

Run: `uvicorn main:app --reload --port 8000`. Health: `GET /health`.

## API overview

All routes except `/health` require `Authorization: Bearer <supabase JWT>`.
First authenticated call lazily creates the user's `profiles` row and
`Ungrouped` default group.

- `GET /auth/me` — profile bootstrap, returns id/email/is_admin
- `GET/POST /groups`, `PATCH/DELETE /groups/{id}` — user groups
  (`Ungrouped` is virtual: words in no custom group; cannot be renamed/deleted)
- `GET /groups/{id}/words`, `POST /groups/{id}/words`, `DELETE .../{word_id}`,
  `PATCH /groups/{id}/order` — membership + manual drag order (`word_order` JSONB)
- `POST /words {text, source_lang, entry_type?, pos?}` — translate,
  dedupe (409), generate audio, resolve grammar
- `GET /words`, `PATCH/DELETE /words/{id}` — list (newest first), edit
  (German change regenerates audio), delete (removes stored audio)
- `GET /quiz/next`, `GET /quiz/session?size=` (needs 10+ words),
  `POST /quiz/check` (case-insensitive), `POST /quiz/record` (review log)

Word payloads carry `pos` (`noun|verb|adjective|adverb|phrase|other`).

## Grammar auto-suggest

`services/grammar.py` resolves POS through a layered cascade;
the first hit wins and the result is cached in `grammar_cache`, so repeat
lookups are DB-only and deterministic:

1. **POS** — official DWDS snippet API (BBAW academy, keyless JSON).
2. **Heuristic fallback** — capitalized single word without DWDS hit defaults to noun (low confidence).
3. **Manual override** — explicit `pos` on create/update always wins.

Ambiguous lemmas are flagged, never guessed. Every layer degrades to `None`;
the service never raises, so a dictionary outage cannot fail `POST /words`.
`GRAMMAR_AUTO_SUGGEST=off` disables the network layers (kill-switch / rollback).

## Deployment notes

- Render free-tier filesystem is ephemeral: nothing on local disk
  persists (`vocab.db`, `audio_files/` are gitignored artifacts, not the
  source of truth — Supabase is).
- Storage bucket `audio` must be **public**; audio URLs are pure string
  formatting (no per-word signing — see `issues.md` #1).
- CORS defaults allow localhost + known Vercel frontends + `*.vercel.app`;
  `ALLOWED_ORIGINS` appends extras.
- Schema changes ship as idempotent `ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS` in `lifespan` (same pattern as `groups.word_order`).

## Rollback

Feature work lands behind additive, nullable columns plus one new table.
To roll back the grammar feature: set `GRAMMAR_AUTO_SUGGEST=off`, revert
the commit, and optionally `ALTER TABLE words DROP COLUMN pos; DROP TABLE grammar_cache;`. Vocabulary rows and audio are untouched
by the backfill (it only fills `NULL` columns).
