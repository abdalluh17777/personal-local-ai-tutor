"""Word pronunciation facts from the CMU Pronouncing Dictionary (American English), for the tutor's replies.

Measured with tests/course_check.py: asked which syllable of "watermelon" is stressed, the model answered
"wa-ter-ME-lon" (the dictionary and the book: WA-ter-mel-on). Syllable, stress and ending answers therefore
come from the dictionary and are given to the model with the learner's message.
"""

import re

import pronouncing

from . import materials

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
# Symbols of the American English phonemic chart used in the course (Underhill): bed is /e/, bird /ɜr/
IPA = {"AA": "ɑ", "AE": "æ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ", "EH": "e", "EY": "eɪ", "IH": "ɪ", "IY": "i",
       "OW": "oʊ", "OY": "ɔɪ", "UH": "ʊ", "UW": "u", "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "g",
       "HH": "h", "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p", "R": "r", "S": "s",
       "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w", "Y": "j", "Z": "z", "ZH": "ʒ"}
# Consonant groups that can begin an English syllable (for dividing the sounds into syllables)
ONSETS = {tuple(o.split()) for o in """P R|B R|T R|D R|K R|G R|F R|TH R|SH R|P L|B L|K L|G L|F L|S L|S P|S T|S K|S M|S N|
S W|T W|D W|K W|G W|TH W|P Y|B Y|K Y|G Y|F Y|V Y|M Y|HH Y|S P R|S T R|S K R|S P L|S K W|S P Y|S K Y""".replace("\n", "").split("|")}

# Questions about pronunciation only: "stress" in psychology, "I'm stressed" or "that sounds good" are not, and
# neither are "the ending of my essay" or "Which vowel do I use, a or an?": endings, vowels and consonants count
# only as sounds
_ASKS = re.compile(r"\bsyllables?\b|\bpronounc\w*|\bword stress\b|\bstress(?:ed)? on\b|\bwhere is the stress\b|"
                   r"\b(?:what|which|final|last|ending|vowel|consonant|first) sounds?\b|"
                   r"\bsound\s+of\s+the\s+(?:ending|vowel|consonant)s?\b|\b(?:s|es|ed)\s+endings?\b|"
                   r"\b(?:short|long|weak|strong|stressed|unstressed|reduced)\s+vowels?\b|\bconsonant\s+clusters?\b|"
                   r"\b(?:un)?voiced\b|\brhymes?\b|(?<!\w)-(?:s|es|ed)\b|/[^/\s]{1,3}/", re.I)
_QUOTED = re.compile(r"[\"'“‘*]{1,2}([A-Za-z][A-Za-z'-]*)[\"'”’*]{1,2}")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
_NOT_ASKED = set("""syllable syllables stress stressed stresses pronounce pronounced pronunciation pronouncing sound sounds
ending endings vowel vowels consonant consonants rhyme rhymes voiced unvoiced word words many which correct right wrong
first second third fourth fifth last letter letters practice practise question answer english end ends say ed
one two three four five six seven eight nine ten""".split())
MAX_WORDS = 5


def _base(phone: str) -> str:
    return phone.rstrip("012")


def syllables(phones: list[str]) -> list[list[str]]:
    """The sounds of a word in syllables: each consonant between two vowels starts the next syllable when it
    can (the longest group that can begin an English syllable)."""
    nuclei = [i for i, p in enumerate(phones) if _base(p) in VOWELS]
    if not nuclei:
        return [phones]
    cuts = []
    for left, right in zip(nuclei, nuclei[1:]):
        between = [_base(p) for p in phones[left + 1:right]]
        onset = 0
        for size in range(len(between), 0, -1):
            group = tuple(between[-size:])
            if (size == 1 and group[0] != "NG") or group in ONSETS:
                onset = size
                break
        cuts.append(right - onset)
    bounds = [0] + cuts + [len(phones)]
    return [phones[a:b] for a, b in zip(bounds, bounds[1:])]


def ipa(phones: list[str]) -> str:
    out = []
    for syllable in syllables(phones):
        mark = next(("ˈ" if p.endswith("1") else "ˌ" if p.endswith("2") else "" for p in syllable
                     if _base(p) in VOWELS), "")
        sounds = []
        for p in syllable:
            base = _base(p)
            if base == "AH":
                sounds.append("ə" if p.endswith("0") else "ʌ")
            elif base == "ER":
                sounds.append("ər" if p.endswith("0") else "ɜr")
            else:
                sounds.append(IPA[base])
        out.append(mark + "".join(sounds))
    return "/" + ".".join(out) + "/"


def _stems(word: str) -> list[str]:
    """Possible base forms of an -s or -ed word: cats, watches, studies; walked, liked, stopped, studied."""
    if word.endswith("ed"):
        return [word[:-2], word[:-1], word[:-3], word[:-3] + "y"]
    if word.endswith("s") and not word.endswith("ss"):
        return [word[:-1], word[:-2], word[:-3] + "y"]
    return []


ENDINGS = {"s": {("S",): "/s/", ("Z",): "/z/", ("IH", "Z"): "/ɪz/ (an extra syllable)",
                  ("AH", "Z"): "/ɪz/ (an extra syllable)"},
           "ed": {("T",): "/t/", ("D",): "/d/", ("IH", "D"): "/ɪd/ (an extra syllable)",
                  ("AH", "D"): "/ɪd/ (an extra syllable)"}}


# Base words that sound like another word plus -ed ("need" = "nee" + /d/)
NOT_ED_FORMS = set("""need seed feed weed deed heed reed bleed breed speed greed steed tweed creed proceed succeed
exceed indeed""".split())


def _ending(word: str) -> str | None:
    """The sound of an -s or -ed ending: only when the word sounds exactly like a base word plus the ending,
    so "was" is not taken for "wa" + -s. Every dictionary pronunciation is compared ("dog" and "dogs" are
    listed with different vowels)."""
    w = word.lower()
    kind = "ed" if w.endswith("ed") else "s" if w.endswith("s") else None
    if not kind or w in NOT_ED_FORMS:
        return None
    for full in pronouncing.phones_for_word(w):
        sounds = [_base(p) for p in full.split()]
        for stem in _stems(w):
            if len(stem) < 3:
                continue
            for entry in pronouncing.phones_for_word(stem):
                base = [_base(p) for p in entry.split()]
                suffix = tuple(sounds[len(base):])
                if sounds[:len(base)] == base and suffix in ENDINGS[kind]:
                    return f"the -{kind} ending is {ENDINGS[kind][suffix]}"
    return None


def describe(word: str) -> str | None:
    entries = list(dict.fromkeys(pronouncing.phones_for_word(word.lower())))
    if not entries:
        return None
    first = entries[0].split()
    pattern = pronouncing.stresses(entries[0])
    count = len(pattern)
    text = f"{word}: {count} syllable{'s' if count != 1 else ''}"
    # "every" is listed as EH1 V ER0 IY0 and EH1 V R IY0: both counts are right
    other_counts = sorted({len(pronouncing.stresses(e)) for e in entries[1:]} - {count})
    if other_counts:
        text += f" (also said with {' or '.join(map(str, other_counts))} syllable{'s' if other_counts != [1] else ''})"
    if count > 1 and "1" in pattern:
        text += f", main stress on syllable {pattern.index('1') + 1}"
        others = sorted({pronouncing.stresses(e).index("1") + 1 for e in entries[1:]
                         if "1" in pronouncing.stresses(e) and len(pronouncing.stresses(e)) == count}
                        - {pattern.index("1") + 1})
        if others:
            text += f" (also said with stress on syllable {', '.join(map(str, others))}, e.g. as another part of speech)"
    text += f", {ipa(first)}"
    ending = _ending(word)
    if ending:
        text += f"; {ending}"
    return text


def asked_words(text: str, previous_reply: str = "") -> list[str]:
    """The words a pronunciation question is about: quoted or bold words first, then the message's other
    words; when the learner names none (an answer like "four, stress on the first"), the words the tutor's
    last reply asked about."""
    if not _ASKS.search(text):
        return []
    found = _QUOTED.findall(text) or [w for w in _WORD.findall(text) if len(w) >= 3
                                      and w.lower() not in materials.STOPWORDS and w.lower() not in _NOT_ASKED]
    words = [w for w in dict.fromkeys(found) if pronouncing.phones_for_word(w.lower())]
    if not words and previous_reply:
        words = [w for w in dict.fromkeys(_QUOTED.findall(previous_reply)) if pronouncing.phones_for_word(w.lower())]
    return words[:MAX_WORDS]


def note(text: str, previous_reply: str = "") -> str | None:
    facts = [d for d in (describe(w) for w in asked_words(text, previous_reply)) if d]
    if not facts:
        return None
    return ("Pronunciation facts from the CMU dictionary (American English). Use them; do not contradict them, "
            "and correct the learner if their answer differs (where a word is also said with another number of "
            "syllables, accept either count): " + "; ".join(facts) + ".")
