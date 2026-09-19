"""Backfill pos for existing words using the services.grammar cascade.

Same code path as POST /words at runtime, so results match new adds.
Only touches rows where pos IS NULL unless --overwrite is given.

Usage:
    python scripts/backfill_pos.py            # dry-run report (default)
    python scripts/backfill_pos.py --apply    # write words + grammar_cache
    python scripts/backfill_pos.py --apply --overwrite   # re-resolve all rows
    python scripts/backfill_pos.py --apply --user-id <uuid> --limit 20

Output ends with per-source counts plus the ambiguous / not-found lemmas
that need a manual pick in the UI.
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import GrammarCache as DBGrammarCache
from database import SessionLocal
from database import Word as DBWord
from services import grammar

PAUSE_BETWEEN_WORDS = 0.2  # be polite to the DWDS / Wikidata APIs


def resolve_all(words, overwrite: bool):
    """Run the cascade per word. Returns list of (word, suggestion)."""
    out = []
    for w in words:
        if w.pos is not None and not overwrite:
            continue
        s = grammar.suggest(w.german_word or "")
        out.append((w, s))
        time.sleep(PAUSE_BETWEEN_WORDS)
    return out


def report(resolved):
    counts = Counter(s.get("source", "none") for _, s in resolved)
    filled_pos = sum(1 for _, s in resolved if s.get("pos"))
    ambiguous = sorted({w.german_word for w, s in resolved if s.get("ambiguous")})
    not_found = sorted({w.german_word for w, s in resolved if not s.get("pos") and not s.get("ambiguous")})

    print(f"\nwords processed : {len(resolved)}")
    print(f"pos filled      : {filled_pos}")
    print(f"by source       : {dict(counts)}")
    if ambiguous:
        print(f"\nambiguous ({len(ambiguous)}) — pick the POS manually in the UI:")
        for lemma in ambiguous:
            print(f"  - {lemma}")
    if not_found:
        print(f"\nnot found ({len(not_found)}) — no suggestion, manual entry needed:")
        for lemma in not_found:
            print(f"  - {lemma}")


def apply(resolved):
    db = SessionLocal()
    try:
        n_words, n_cache = 0, 0
        for w, s in resolved:
            lemma = grammar.normalize_german(w.german_word or "")
            row = db.query(DBWord).filter(DBWord.id == w.id).first()
            if row:
                if s.get("pos"):
                    row.pos = s["pos"]
                n_words += 1
            if lemma:
                db.merge(DBGrammarCache(
                    lemma=lemma,
                    pos=s.get("pos"),
                    source=s.get("source", "none"),
                    confidence=s.get("confidence", "none"),
                    ambiguous=bool(s.get("ambiguous")),
                ))
                n_cache += 1
        db.commit()
        print(f"\nupdated {n_words} words, upserted {n_cache} grammar_cache rows")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry-run)")
    ap.add_argument("--overwrite", action="store_true", help="re-resolve rows that already have pos")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        q = db.query(DBWord)
        if args.user_id:
            q = q.filter(DBWord.user_id == args.user_id)
        if not args.overwrite:
            q = q.filter(DBWord.pos.is_(None))
        q = q.order_by(DBWord.created_at.asc())
        if args.limit:
            q = q.limit(args.limit)
        words = q.all()
    finally:
        db.close()

    print(f"candidate words: {len(words)}")
    resolved = resolve_all(words, args.overwrite)
    report(resolved)
    if args.apply:
        apply(resolved)
    else:
        print("\ndry-run: no writes. Re-run with --apply to persist.")


if __name__ == "__main__":
    main()
