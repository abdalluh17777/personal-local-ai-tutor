"""Syllables and word stress from the CMU Pronouncing Dictionary (American English), to check answer keys.

    uv run --with pronouncing python scripts/check_pronunciation.py graffiti infamous "hang out" vintage

Prints each word's syllable count and stress pattern, with the stressed syllable in capitals when the word
is split the usual way, e.g.  graffiti  3  0-1-0  (stress on syllable 2)
"""

import sys

import pronouncing

for word in sys.argv[1:]:
    parts = word.lower().split()
    results = []
    for part in parts:
        phones = pronouncing.phones_for_word(part)
        if not phones:
            results.append(None)
            continue
        results.append(phones[0])
    if any(r is None for r in results):
        print(f"{word:<22} not in the dictionary")
        continue
    stresses = "-".join(pronouncing.stresses(r) for r in results)
    count = sum(pronouncing.syllable_count(r) for r in results)
    primary = [i + 1 for i, s in enumerate(stresses.replace("-", "")) if s == "1"]
    print(f"{word:<22} {count} syllable(s)  stress {stresses}  (primary stress on syllable {', '.join(map(str, primary))})"
          f"   ARPAbet: {' | '.join(results)}")
