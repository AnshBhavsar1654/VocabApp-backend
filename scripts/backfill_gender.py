"""Backfill gender for existing German nouns using https://der-artikel.de/.

Usage:
    python scripts/backfill_gender.py            # dry-run report (default)
    python scripts/backfill_gender.py --apply    # update database
    python scripts/backfill_gender.py --apply --overwrite   # re-resolve all rows
    python scripts/backfill_gender.py --apply --limit 20
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import SessionLocal, Word
from services.gender import extract_lemma, fetch_gender_from_der_artikel

PAUSE_BETWEEN_REQUESTS = 0.1  # be polite to der-artikel.de


def backfill_gender(apply: bool = False, overwrite: bool = False, limit: int | None = None):
    db = SessionLocal()
    client = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VocabApp/1.0"},
        timeout=4.0,
        follow_redirects=True,
    )

    try:
        # Target words that are nouns or have pos=None
        query = db.query(Word)
        all_words = query.all()

        print(f"Total words in database: {len(all_words)}")
        target_words = []
        for w in all_words:
            # Skip non-nouns
            if w.pos and w.pos != "noun":
                continue
            if w.gender is not None and not overwrite:
                continue
            target_words.append(w)

        if limit:
            target_words = target_words[:limit]

        print(f"Processing {len(target_words)} candidate words...\n")

        matched = 0
        unmatched = 0
        skipped = 0
        gender_counts = Counter()

        for idx, w in enumerate(target_words, 1):
            lemma = extract_lemma(w.german_word or "")
            if not lemma:
                skipped += 1
                continue

            # Step 1: Search https://der-artikel.de/
            res = fetch_gender_from_der_artikel(lemma, client=client)
            if res:
                article, gender = res
                gender_counts[gender] += 1
                matched += 1
                print(f"[{idx}/{len(target_words)}] {w.german_word:<20} -> {article} ({gender}) [der-artikel.de]")
                if apply:
                    w.gender = gender
                    if w.pos is None:
                        w.pos = "noun"
            else:
                # Step 2: Fallback to Gemini if pos is noun
                if w.pos == "noun":
                    from services.gender import resolve_gender
                    gender = resolve_gender(w.german_word, pos="noun", client=client)
                    if gender:
                        gender_counts[gender] += 1
                        matched += 1
                        print(f"[{idx}/{len(target_words)}] {w.german_word:<20} -> ({gender}) [gemini-fallback]")
                        if apply:
                            w.gender = gender
                    else:
                        unmatched += 1
                        print(f"[{idx}/{len(target_words)}] {w.german_word:<20} -> not found")
                else:
                    unmatched += 1
                    print(f"[{idx}/{len(target_words)}] {w.german_word:<20} -> not found on der-artikel.de")

            time.sleep(PAUSE_BETWEEN_REQUESTS)

        if apply:
            db.commit()
            print("\nDatabase changes committed successfully!")
        else:
            print("\n[DRY RUN] No database changes written. Pass --apply to persist.")

        print("\nSummary:")
        print(f"  Processed : {len(target_words)}")
        print(f"  Matched   : {matched} ({dict(gender_counts)})")
        print(f"  Unmatched : {unmatched}")
        print(f"  Skipped   : {skipped}")

    finally:
        client.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill German noun gender from der-artikel.de")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing gender values")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of words to process")
    args = parser.parse_args()

    backfill_gender(apply=args.apply, overwrite=args.overwrite, limit=args.limit)
