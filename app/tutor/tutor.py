"""The tutor: builds the model's context from memory and course material, streams replies,
and turns each exchange into validated memory updates."""

import os
import re
from collections.abc import AsyncIterator

from . import config, drills, materials, pronunciation
from .llm import LLM, ModelError
from .store import MEMORY_KINDS, MISTAKE_CATEGORIES, Store, normalize

MAX_MEMORIES_IN_CONTEXT = 15
MAX_TRANSCRIPT_CHARS = 24000
# Measured with tests/observer_check.py: thinking took 60-100 s per message and was not more accurate
OBSERVER_THINKS = False
# Measured with tests/smoke_app.py: thinking made the end-of-session review take ~45 s
REFLECTION_THINKS = False
# Typed "is this correct?" questions with thinking (the profile's "careful_grammar"). Measured with
# tests/course_check.py on 11 questions: one wrong term without thinking ("comma splice" for a run-on), none
# with it, but the first word came after 33-62 s instead of 1.2 s. So it is the learner's choice, off by default.
# TUTOR_JUDGE_THINKS=1 or 0 overrides the profile (for measuring).
JUDGE_THINKS_OVERRIDE = os.environ.get("TUTOR_JUDGE_THINKS")
CEFR = ("A1", "A2", "B1", "B2", "C1", "C2")

TEACHING_LANGUAGE_TEXT = {
    "english": "Teach in English only. Use Arabic only when the learner explicitly asks for it: a translation, "
               "the Arabic meaning of a word, or an explanation in Arabic. Then give exactly that, briefly, and "
               "continue in English. \"Translate\" with no language named means into Arabic. If the learner "
               "writes in Arabic without asking for Arabic, answer in simple English.",
    "english_with_arabic_support": "Teach in English. When the learner asks, or is clearly stuck on a key term, "
                                   "add a short Arabic gloss of that term, then continue in English.",
    "arabic": "Explain in Arabic, keeping key terms in English with a short English definition.",
}

CITATION = re.compile(r"\[S(\d+)\]")

_TRANSLATE = re.compile(r"\btranslate\b|\btranslating\b|ترجم", re.I)   # "translate", "ترجم"
_OTHER_LANGUAGE = re.compile(r"\b(?:english|french|spanish|german|turkish)\b|إ?نجليز", re.I)
# "What does the word translation mean?" asks about a word, not for a translation of the lesson
_ABOUT_A_WORD = re.compile(r"\bmean\b|\bmeaning of\b|\bthe word\b", re.I)
# The learner talking about their feelings or private life (English and Arabic): the learner as the one who feels.
# Any "me ... feel" in a sentence was too wide: "Can you give me more pairs like fill and feel?", "Give me a sentence
# using the word 'tired'", "I feel like the answer is B", "ما معنى ضغط الأقران؟" all got the personal note
_STATE = (r"(?:so |very |really |a bit |a little |quite |too )?(?:sad|stressed|anxious|depressed|lonely|exhausted|"
          r"angry|afraid|scared|upset|unhappy|hopeless|worried)\b")
_PERSONAL = re.compile(
    rf"\bi(?:['’]m|m| am| was| feel| felt|['’]ve been| have been)(?: feeling)?\s+{_STATE}"
    rf"|\b(?:makes?|made|making) me\s+{_STATE}"
    r"|\bi feel (?!(?:like|that|the|it|this|these|those|a|an|as|if|my|your|you|we|they|he|she|there)\b)\w+"
    r"|\bi regret\b|\bmy (?:anxiety|depression)\b|\bi(?:['’]m|m| am)\s+\d+\s+years\b|\bi have\s+\d+\s+years\b"
    r"|أشعر|عمري|ندمت|تعبان|متضايق|أنا\s+(?:قلق|حزين|متوتر|خائف)", re.I)
# Signs that a first-person sentence is English practice, not news about the learner: "Is this correct: 'I feel
# frightened when I look at the mask'?", a sentence the lesson told them to say, "an example about ..."
_PRACTICE = re.compile(r"\b(?:examples?|sentences?|words?|pairs?|exercises?|practi[cs]\w*)\b", re.I)
# A quoted text; an apostrophe inside a word ("I'm", "don't") does not open or close a quotation
_QUOTE = re.compile(r"\"[^\"\n]+\"|“[^”\n]+”|(?<!\w)['‘][^\n]+?['’](?!\w)")


_WRITING_REQUEST = re.compile(
    r"\b(?:check|correct|feedback|review|edit|proofread|mark|grade)\b.{0,60}\b(?:paragraph|essay|writing|draft|text|"
    r"story|email|summary|introduction)\b|\b(?:my|here is|here's)\s+(?:\w+\s+)?(?:paragraph|essay|draft|writing|"
    r"story|email|summary|introduction)\b", re.I)
# "this paragraph" is the learner's own only when it is not quoted from the course ("I do not understand this
# paragraph from the lecture transcript: ...")
_THIS_WRITING = re.compile(r"\bthis\s+(?:\w+\s+)?(?:paragraph|essay|draft|writing|story|email|summary|introduction)\b",
                           re.I)
_QUOTED_SOURCE = re.compile(
    r"\bfrom\s+(?:the|my|our|this)\s+(?:\w+\s+)?(?:book|lecture|transcript|reading|text|material|article|chapter|"
    r"slides?)\b|:\s*[\"“'‘]|\"\"\"|'''", re.I)
WRITING_NOTE = (
    "This is the learner's writing, sent for feedback. Reply in this order: (1) one sentence on what works well; "
    "(2) organization, as the course teaches it: the topic sentence (topic and controlling idea), the supporting "
    "ideas and details, the transitions, the concluding sentence; (3) the most important grammar and word errors, "
    "grouped by type (for example verb tense, subject-verb agreement, fragments, run-on sentences, articles, word "
    "forms), each with the learner's exact words and the correction, at most eight; (4) one or two goals for the "
    "rewrite. Do not rewrite the whole text. Treat the content as a writing task: do not comment on the writer's "
    "feelings or private life.")


_PARAPHRASE = re.compile(r"\bparaphras\w*|\bsummar(?:y|ies|ize|ise|ized|ised)\b", re.I)
# Only the learner's own paraphrase or summary, sent for feedback; "Can you summarize lecture 3?" asks for one
_FEEDBACK_REQUEST = re.compile(
    r"\b(?:check|correct|feedback|review)\b|\bis\s+(?:this|my)\b|\bhow\s+is\s+my\b"
    r"|\bmy\s+(?:\w+\s+)?(?:paraphrase|summary)\b", re.I)
_ASKS_FOR_ONE = re.compile(
    r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:summari[sz]e|paraphrase)\b"
    r"|(?:^|[.!?]\s+)(?:please\s+)?(?:summari[sz]e|paraphrase)\b(?!\s*:)"
    r"|\bgive\s+me\s+(?:a|an|the)\s+(?:\w+\s+)?(?:summary|paraphrase)\b", re.I)
_ORIGINAL_AND_OWN = re.compile(
    r"\boriginal(?:\s+text)?\s*[:\-]\s*(?P<original>.+?)\s*(?:\bmy\s+)?\b(?:paraphrase|summary)\s*[:\-]\s*(?P<own>.+)$",
    re.I | re.S)
PARAPHRASE_NOTE = (
    "The learner sent a paraphrase or summary for feedback. Compare it with the original (in the message, the "
    "lesson material or your earlier messages). Reply in this order: (1) meaning: anything missing, added or "
    "changed; (2) wording: copied strings of words and sentence structure too close to the original; (3) grammar "
    "errors, grouped by type, with the learner's exact words and the correction; (4) is the source named? Then "
    "rewrite only the weakest sentence as an example.")


def copied_phrases(original: str, own: str, n: int = 4, longest: int = 30) -> list[str]:
    """Strings of n or more words in the learner's text that also appear in the original, each as long as
    possible, in the learner's order (case and punctuation ignored)."""
    words = lambda t: re.findall(r"[a-z0-9']+", t.lower())
    a, b = words(original), words(own)
    runs = {tuple(a[i:i + k]) for k in range(n, longest + 1) for i in range(len(a) - k + 1)}
    found, i = [], 0
    while i <= len(b) - n:
        size = next((k for k in range(min(longest, len(b) - i), n - 1, -1) if tuple(b[i:i + k]) in runs), 0)
        if size:
            found.append(" ".join(b[i:i + size]))
            i += size
        else:
            i += 1
    return list(dict.fromkeys(found))


def paraphrase_notes(text: str) -> list[str] | None:
    """Feedback order for a paraphrase or summary; when the message gives both texts ("Original: ...
    Paraphrase: ..."), the copied strings of words found in code (measured: a small model misses them)."""
    if not (_PARAPHRASE.search(text) and len(text.split()) >= 25):
        return None
    both = _ORIGINAL_AND_OWN.search(text)
    if not both and (_ASKS_FOR_ONE.search(text) or not _FEEDBACK_REQUEST.search(text)):
        return None
    notes = [PARAPHRASE_NOTE]
    if both:
        copied = copied_phrases(both["original"], both["own"])
        notes.append("Strings of four or more words copied from the original (found by comparing the texts): "
                     + ("; ".join(f'"{c}"' for c in copied) if copied else "none") + ".")
    return notes


# "Is this sentence correct?", "Do I need a comma here?", "Which is correct: ...?"
_JUDGE = re.compile(
    r"\b(?:is|are)\s+(?:this|these|it|my|the)\b[^?]{0,40}\b(?:correct|right|wrong|ok|okay|grammatical)\b|"
    r"\bwhich\s+(?:one\s+)?is\s+(?:correct|right|better)\b|\bcorrect\s+or\s+(?:not|incorrect|wrong)\b", re.I)
_NEED_PUNCTUATION = re.compile(
    r"\bdo\s+i\s+need\s+(?:a|an|the)?\s*(?:comma|semicolon|colon|apostrophe|article|hyphen|capital)", re.I)
# "Is my answer right?", "Is it right that Pavlov used dogs?" ask about the content: the question must be about
# some English, named ("sentence", "grammar", ...) or quoted
_ENGLISH_TO_JUDGE = re.compile(
    r"\b(?:sentences?|grammar|grammatical(?:ly)?|english|phrases?|words?|wording|spell\w*|punctuation|commas?|"
    r"semicolons?|apostrophes?|prepositions?|tenses?|verbs?|plurals?|capital\w*|expressions?|collocations?)\b", re.I)


def asks_if_english_is_correct(text: str) -> bool:
    """The learner asks whether some English is correct: these replies get JUDGE_NOTE (and thinking, if chosen)."""
    return bool(_NEED_PUNCTUATION.search(text)
                or (_JUDGE.search(text) and (_ENGLISH_TO_JUDGE.search(text) or _QUOTE.search(text))))


JUDGE_NOTE = (
    "The learner asks whether some English is correct. First find the rule in the course material above that "
    "applies, and state it in one sentence; then apply it to the learner's words one by one, changing only what "
    "the rule requires. Keep words that are already correct, and give one corrected version that is consistent.")


_APA = re.compile(r"\bAPA\b|\bet\.?\s+al\b", re.I)
_CITING = re.compile(r"\bcit(?:e|es|ed|ing|ation|ations)\b|\breferences?\b|\bauthors?\b", re.I)
APA_NOTE = (
    "APA 7th edition, the rules this course teaches (the book's own examples sometimes follow the older 6th "
    "edition). In the text: one or two authors, every name every time: (Smith & Jones, 2019) or Smith and Jones "
    "(2019); three or more authors, the first author's name and et al. from the first citation: (Smith et al., "
    "2019) or Smith et al. (2019); \"&\" inside parentheses, \"and\" in the sentence. Reference list: up to 20 "
    "authors, last names and initials, \"&\" before the last; DOIs as https://doi.org/...; no publisher location "
    "for books. Every rule and every example in your reply must agree with these rules.")


def is_writing(text: str) -> bool:
    """A piece of writing to give feedback on: a paragraph or more, with a request, or a long text. A long text
    quoted from the course material, or followed by a question about it, is not the learner's writing."""
    words = len(text.split())
    if words < 60:
        return False
    if _WRITING_REQUEST.search(text):
        return True
    quoted = bool(_QUOTED_SOURCE.search(text))
    if _THIS_WRITING.search(text) and not quoted:
        return True
    return words >= 150 and not quoted and not text.rstrip().endswith(("?", "؟"))


def turn_notes(text: str, previous_reply: str = "") -> list[str]:
    """Instructions and facts for this reply only, from what the learner just wrote.

    Measured on the learner's real messages: asked "Can you translate your question, please?", the model
    rephrased it in English; told "high volume of voices make me stressed", it discussed the stress and used it
    as the lesson's example. General rules in the system prompt were not enough for these two cases.
    Pronunciation questions get the dictionary's facts about the words (see pronunciation.py), and questions about
    APA style the APA 7th-edition citation rules. A paragraph sent
    for feedback gets the course's feedback order instead of the other notes: a narrative about the writer's own
    life is a writing task, not a personal message. So is a first-person sentence the learner quotes, asks to
    check, or writes as an example or exercise.
    """
    paraphrase = paraphrase_notes(text)
    if paraphrase:
        return paraphrase
    if is_writing(text):
        return [WRITING_NOTE]
    notes = []
    facts = pronunciation.note(text, previous_reply)
    if facts:
        notes.append(facts)
    elif asks_if_english_is_correct(text):
        notes.append(JUDGE_NOTE)
    # Measured in Stage 4: asked how to cite three authors in APA 7th edition, with the guide's rule among the
    # passages, the model gave the 6th edition's "(Smith, Jones, & Lee, 2019)" in 2 of 4 runs
    if _APA.search(text) or (_CITING.search(text) and _APA.search(previous_reply)):
        notes.append(APA_NOTE)
    if _TRANSLATE.search(text) and not _OTHER_LANGUAGE.search(text) and not _ABOUT_A_WORD.search(text):
        notes.append("Translate what the learner means (usually your previous message) into Arabic. Write the "
                     "Arabic translation first, then continue the lesson in English.")
    practice = (facts or _JUDGE.search(text) or _QUOTE.search(text) or _PRACTICE.search(text)
                or vocab_requests(text))
    if _PERSONAL.search(text) and not practice:
        notes.append("This message is personal. Reply with one short neutral sentence (for example \"Thanks for "
                     "telling me.\"), then return to the course topic. Do not discuss it, ask about it, or use it as "
                     "an example. If the message suggests danger or serious distress, follow the safety rule in your "
                     "instructions instead.")
    return notes

_GREETING_WORD = r"(?:hello|hi|hey|hiya|greetings|welcome(?:\s+back)?|good\s+(?:morning|afternoon|evening))\b"
# After the opener only a name or a word like "again" or "there", never a clause: a reply that teaches greetings
# ("Hello is more formal.") or answers after the praise ("Good question, the answer is no.") must stay whole
_OPENER_TAIL = r"(?:,?\s+(?!(?:is|are|was|were|means)\b)[\w'’-]+){0,2}\s*[.!?]+\s*"
_GREETINGS = re.compile(rf"^\s*(?:{_GREETING_WORD}{_OPENER_TAIL})+", re.I)   # complete greeting sentences
_GREETING_START = re.compile(rf"^\s*{_GREETING_WORD}", re.I)


_PRAISE_WORD = r"(?:good|great|wonderful|excellent|perfect|fair|fantastic|nice|interesting|smart|awesome)"
_PRAISE_NOUN = r"(?:question|idea|point|answer|goal|plan|explanation|observation|example|job|work|thinking)"
_OPENER_PRAISE = re.compile(
    rf"^\s*(?:(?:(?:that|this|it)(?:'s|\s+is|\s+was)|what)\s+(?:a\s+)?(?:very\s+|really\s+)?{_PRAISE_WORD}\s+{_PRAISE_NOUN}"
    rf"|(?:very\s+)?{_PRAISE_WORD}(?:\s+{_PRAISE_NOUN})?){_OPENER_TAIL}", re.I)
_PRAISE_START = re.compile(rf"^\s*(?:(?:that|this|it)(?:'s|\s+is|\s+was)|what|very|{_PRAISE_WORD})\b", re.I)
_OLD_LABELS = re.compile(r"\s*\[(?:S\d+|General Knowledge|Arabic:[^\]]*)\]")


def clean_past_reply(text: str) -> str:
    """A past reply as the model should see it in the history.

    Measured on a real session: the model copies the openings of its own earlier replies ("Hello, <name>!
    That is a great question!"), and old [S#] labels point to passages of earlier turns that are no longer
    in the context. So openers and labels are removed from the history (the saved messages are unchanged).
    """
    text = _OLD_LABELS.sub("", text)
    while True:
        m = _GREETINGS.match(text) or _OPENER_PRAISE.match(text)
        if not m:
            break
        text = text[m.end():]
    return text.strip()


class OpeningFilter:
    """Drops a greeting, and repeated praise, at the start of a reply in an ongoing conversation.

    Measured in a real session: after greeting once, the model opened 8 of the next 9 replies with
    "Hello, <name>! That is a great question!" by copying its own earlier replies. The prompt asks it not to;
    this is the backstop. Text is held back only while the reply might be starting with such an opener,
    so ordinary replies are not delayed.
    """

    MAX_HOLD = 120

    def __init__(self, greetings: bool, praise: bool = False):
        self.patterns = [p for p, on in ((_GREETINGS, greetings), (_OPENER_PRAISE, praise)) if on]
        self.starts = [p for p, on in ((_GREETING_START, greetings), (_PRAISE_START, praise)) if on]
        self.decided = not self.patterns
        self.buf = ""

    def _strip(self, text: str) -> tuple[str, bool]:
        stripped = False
        while m := next((m for p in self.patterns if (m := p.match(text))), None):
            text, stripped = text[m.end():], True
        return text, stripped

    def _maybe_opener(self, rest: str) -> bool:
        if not re.match(r"\s*\S+[\s.,!?]", rest) and len(rest) < 15:
            return True    # the first word is not known yet
        if re.search(r"[.!?\n]\s*\S", rest):
            return False   # the first sentence is complete and is not an opener
        return any(p.match(rest) for p in self.starts)

    def push(self, text: str) -> str:
        if self.decided:
            return text
        self.buf += text
        rest, stripped = self._strip(self.buf)
        if len(self.buf) < self.MAX_HOLD and self._maybe_opener(rest):
            return ""
        return self._release(rest, stripped)

    def flush(self) -> str:
        if self.decided:
            return ""
        rest, stripped = self._strip(self.buf)
        if stripped and not rest.strip():
            return self._release(self.buf, False)   # a reply that is only an opener is kept, never emptied
        return self._release(rest, stripped)

    def _release(self, text: str, stripped: bool) -> str:
        self.decided = True
        self.buf = ""
        return text[:1].upper() + text[1:] if stripped else text


class NoteFilter:
    """Drops a "[Note for the tutor: ...]" that the model writes into its own reply.

    The app adds such notes to the learner's message; measured in a course check, the model once ended a reply
    with a note of its own ("[Note for the tutor: The learner has a strong C2 level but ...]"). Text is held
    back only from a "[" until it is clear that no note starts there, so citation labels like [S2] pass at once,
    and inside a note until it ends, so a note whose "]" never comes does not take the rest of the reply with it.
    """

    STARTS = ("[note for the tutor", "[note to the tutor", "[note for tutor", "[note to self", "[tutor note")

    def __init__(self):
        self.buf = ""
        self.inside = False

    def push(self, text: str) -> str:
        self.buf += text
        out = []
        while self.buf:
            if self.inside:
                # A note ends at its "]", or at the end of its line when the model forgets the bracket
                ends = [i for i in (self.buf.find("]"), self.buf.find("\n")) if i >= 0]
                if not ends:
                    break                                   # held until the note ends (see flush)
                end = min(ends)
                self.buf, self.inside = self.buf[end + (self.buf[end] == "]"):], False
                continue
            i = self.buf.find("[")
            if i < 0:
                out.append(self.buf)
                self.buf = ""
                break
            out.append(self.buf[:i])
            self.buf = self.buf[i:]
            head = self.buf.lower()
            if any(head.startswith(s) for s in self.STARTS):
                self.inside = True
            elif any(s.startswith(head) for s in self.STARTS):
                break                                   # it may still become a note: wait for more text
            else:
                out.append("[")
                self.buf = self.buf[1:]
        return "".join(out)

    def flush(self) -> str:
        rest = self.buf
        if self.inside:
            # Never closed: the note is taken to be its first sentence, and the reply goes on after it
            # ("[Note for the tutor: C2 learner. Next, the dependent variable is recall.")
            end = re.search(r"[.!?]\s+", rest)
            rest = rest[end.end():] if end else ""
        self.buf, self.inside = "", False
        return rest


def _array(item_props: dict) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": item_props,
                                       "required": list(item_props)}}


_STR = {"type": "string"}

OBSERVER_SCHEMA = {
    "type": "object",
    "properties": {
        "corrections": _array({"wrong": _STR, "right": _STR, "rule": _STR,
                               "category": {"type": "string", "enum": list(MISTAKE_CATEGORIES)},
                               "explanation": _STR}),
        "better_phrasing": _STR,
        "vocabulary": _array({"word": _STR, "meaning": _STR, "example": _STR,
                              "reason": {"type": "string",
                                         "enum": ["learner_asked", "learner_learned", "tutor_explained"]},
                              "cefr": {"type": "string", "enum": list(CEFR)}}),
        "memories": _array({"kind": {"type": "string", "enum": ["preference", "goal", "background", "interest"]},
                            "content": _STR}),
    },
    "required": ["corrections", "better_phrasing", "vocabulary", "memories"],
}

REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STR,
        "next_step": _STR,
        "new_memories": _array({"kind": {"type": "string", "enum": list(MEMORY_KINDS)}, "content": _STR}),
        "outdated_memory_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["summary", "next_step", "new_memories", "outdated_memory_ids"],
}

SEARCH_SCHEMA = {"type": "object", "properties": {"keywords": {"type": "array", "items": _STR}},
                 "required": ["keywords"]}
DEFINE_SCHEMA = {"type": "object", "properties": {"meaning": _STR, "example": _STR},
                 "required": ["meaning", "example"]}

# Explicit vocabulary requests. Measured: the model's observer rarely lists these words itself,
# so the app detects them and only asks the model for the definition.
_Q_OPEN, _Q_CLOSE = "[\"'“‘]", "[\"'”’]"
_QUOTED_TERM = r"((?:[^\"'”’\n]|(?<=[A-Za-z])['’](?=[A-Za-z])){2,40})"   # keeps "don't" whole
_WORD_OR_TERM = r"(?:(?:the\s+)?(?:word|term)\s+)?"                       # "the meaning of the word plasticity"
VOCAB_SIGNALS = [
    re.compile(rf"\b(?:word|term|expression|phrase|idiom)\s+{_Q_OPEN}{_QUOTED_TERM}{_Q_CLOSE}", re.I),
    re.compile(rf"\b(?:learn(?:ed|t)|new)\s+(?:the\s+|a\s+)?(?:word|term)(?:\s+(?:today|yesterday|now))?[:,]?\s*"
               rf"{_Q_OPEN}?([A-Za-z][A-Za-z'-]{{2,30}})\b", re.I),
    re.compile(rf"\bwhat\s+(?:does|do)\s+{_WORD_OR_TERM}{_Q_OPEN}?([A-Za-z][A-Za-z' -]{{1,40}}?){_Q_CLOSE}?\s+mean\b",
               re.I),
    re.compile(rf"\b(?:meaning|definition)\s+of\s+{_WORD_OR_TERM}{_Q_OPEN}?([A-Za-z][A-Za-z-]{{2,30}}){_Q_CLOSE}?",
               re.I),
    # "What do you mean by 'extinction'?", "What does Pavlov mean by extinction?"
    re.compile(rf"\bwhat\s+(?:do|does|did)\s+(?:[A-Za-z]+\s+){{1,2}}?mean\s+by\s+{_Q_OPEN}?"
               rf"([A-Za-z][A-Za-z' -]{{1,40}}?){_Q_CLOSE}?(?=\s*[?.!,;:]|\s+(?:here|exactly|in|when)\b|$)", re.I),
    # The whole English term, up to a quote, punctuation or the Arabic that follows it
    re.compile(rf"(?:معنى|تعريف)\s+(?:كلمة\s+|مصطلح\s+)?{_Q_OPEN}?([A-Za-z][A-Za-z' -]{{1,40}}?)"
               r"(?=\s*[\"'”’](?!\w)|\s*[؟?،,.!;:]|\s+[\u0600-\u06FF]|$)"),
]
# Not the word asked about: "What does the author mean?", "I learned a new word today: ..."
_NOT_TERMS = {"a", "an", "the", "today", "yesterday", "tomorrow"}


def vocab_requests(message: str) -> list[str]:
    """Words the learner explicitly asked about or said they learned ("what does X mean", "ما معنى X").

    Unquoted, a capture that starts with a function word ("the author") or, in an English message, with a
    capitalised name ("What does Pavlov mean?") is not a word to learn: each one would cost a definition call."""
    found: dict[str, str] = {}
    english = not materials.has_arabic(message)
    for pattern in VOCAB_SIGNALS:
        for m in pattern.finditer(message):
            word = " ".join(m.group(1).split()).strip(" -'’")
            if len(word) < 3 or word.lower() in materials.STOPWORDS or word.lower() in _NOT_TERMS:
                continue
            first = word.split()[0]
            quoted = message[m.start(1) - 1] in "\"'“‘"
            if not quoted and (first.lower() in materials.STOPWORDS or first.lower() in _NOT_TERMS
                               or (english and re.match(r"[A-Z][a-z]", first))):
                continue
            found.setdefault(word.lower(), word)
    return list(found.values())[:3]
TOPICS_SCHEMA = {"type": "object", "properties": {"topics": {"type": "array", "items": _STR}},
                 "required": ["topics"]}


def render(name: str, **values) -> str:
    """Load a prompt template. Read on every call so edits to the .md files apply immediately."""
    text = (config.PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def check_citations(reply: str, passages: list[dict]) -> tuple[list[dict], list[str]]:
    """Map [S#] labels in the reply to the passages that were actually sent; report unknown labels."""
    used = {int(n) for n in CITATION.findall(reply)}
    sources = [{
        "label": f"S{i}",
        "passage_id": p["id"],
        "filename": p["filename"],
        "page": p["page"],
        "excerpt": p["text"][:400],
        "cited": i in used,
    } for i, p in enumerate(passages, 1)]
    unknown = [f"S{n}" for n in sorted(used) if not 1 <= n <= len(passages)]
    return sources, unknown


def _list_or_none(items: list[str]) -> str:
    return "; ".join(items) if items else "(none)"


def _has_words(text_norm: str, words_norm: str) -> bool:
    """Whole-word containment on normalized text ("is" must not match inside "this")."""
    return f" {words_norm} " in f" {text_norm} "


class Tutor:
    def __init__(self, store: Store, llm: LLM):
        self.store = store
        self.llm = llm

    # --- context ----------------------------------------------------------
    def system_prompt(self, session: dict, passages: list[dict], voice: bool = False,
                      earlier_messages: int = 0, notes: list[str] | None = None) -> str:
        p = self.store.get_profile()
        parts = [render("tutor",
                        learner_name=p["name"] or "the learner",
                        english_level=p["english_level"],
                        teaching_language=TEACHING_LANGUAGE_TEXT[p["teaching_language"]])]
        if voice:
            parts.append(render("voice"))

        fields = (("name", "Name"), ("english_level", "English level"), ("goals", "Goals"),
                  ("interests", "Interests"), ("notes", "Notes"))
        profile = [f"- {label}: {p[key]}" for key, label in fields if p[key]]
        parts.append("# Learner profile\n" + ("\n".join(profile) or "- Not filled in yet."))

        memories = self.store.list_memories(active_only=True)[:MAX_MEMORIES_IN_CONTEXT]
        parts.append("# What you remember about the learner\n" +
                     ("\n".join(f"- ({m['kind']}) {m['content']}" for m in memories) or "- Nothing yet."))

        mistakes = self.store.list_mistakes(include_resolved=False, limit=5)
        if mistakes:
            parts.append(
                "# English focus\nThe learner's most frequent mistake patterns. Use these structures correctly "
                "in your replies, and give a little practice when it fits the conversation:\n" +
                # In plain words: measured, the model copied a label like "word_order_in_questions" into a reply
                "\n".join(f"- {m['rule'].replace('_', ' ')} (seen {m['count']} times)" for m in mistakes))

        weak = [s for s in self.store.pron_sound_stats() if s["accuracy"] < 0.8][:3]
        if weak:
            parts.append(
                "# Pronunciation focus\nThe learner's hardest sounds in pronunciation practice. When it fits the "
                "conversation, use a word with one of them and invite the learner to say it; for practice, point "
                "them to the Pronunciation page:\n" +
                "\n".join(f"- /{drills.IPA[s['phone']]}/: right {round(100 * s['accuracy'])}% of {s['total']} times"
                          + (f", often said as /{drills.IPA.get(s['instead'][0], '')}/" if s["instead"] and s["instead"][0]
                             else "") for s in weak))

        learning = self.store.due_vocab(limit=8)
        if learning:
            parts.append(
                "# Words the learner is learning\nWhen one fits the conversation, use it naturally or invite the "
                "learner to use it; never force a word in:\n" +
                "\n".join(f"- {v['word']}: {v['meaning'].split(' · Forms:')[0][:120]}" for v in learning))

        parts.append("# Study context\n" + self._study_context(session))

        if not session["course_id"]:
            parts.append("# Course material\nNo course is selected. Answer from general knowledge and say so.")
        elif passages:
            blocks = []
            for i, ps in enumerate(passages, 1):
                where = ps["filename"] + (f", p. {ps['page']}" if ps["page"] else "")
                blocks.append(f"[S{i}] ({where})\n{ps['text']}")
            parts.append("# Course material\n" + "\n\n".join(blocks))
        else:
            parts.append("# Course material\nNo passage in the course material matched this question.")

        # Last, where a small model weighs it most: where this reply sits in the conversation
        if earlier_messages:
            this_reply = (f"The session already has {earlier_messages} earlier messages. Continue the conversation "
                          "from the learner's last message. Do not greet and do not open with their name.")
        else:
            this_reply = "This is the first reply of the session. A short greeting is fine."
        parts.append("# This reply\n" + "\n".join([this_reply, *(notes or [])]))
        return "\n\n".join(parts)

    def _study_context(self, session: dict) -> str:
        if not session["course_id"]:
            return "- Free conversation, no course selected."
        course = self.store.get_course(session["course_id"])
        lines = [f"- Course: {course['name']}"]
        if course["description"]:
            lines.append(f"- Course description: {course['description']}")
        if session["topic_id"]:
            topic = self.store.get_topic(session["topic_id"])
            state = {"new": "first time studying it", "learning": "studied before, not yet reviewed",
                     "reviewing": "in review"}[topic["status"]]
            lines.append(f"- Current topic: {topic['title']} ({state})")
            if topic["notes"]:
                lines.append(f"- Topic notes: {topic['notes']}")
        upcoming = [t["title"] for t in self.store.list_topics(course["id"]) if t["status"] == "new"
                    and t["id"] != session["topic_id"]][:3]
        if upcoming:
            lines.append(f"- Upcoming topics: {', '.join(upcoming)}")
        last = self.store.last_summary(course["id"])
        if last:
            lines.append(f"- Last session: {last['summary']}")
            if last["next_step"]:
                lines.append(f"- Planned next step: {last['next_step']}")
        return "\n".join(lines)

    async def retrieve(self, session: dict, question: str, previous_reply: str) -> list[dict]:
        if not session["course_id"]:
            return []
        course_id, limit = session["course_id"], config.MAX_PASSAGES
        topic_docs = self.store.topic_document_ids(session["topic_id"]) if session["topic_id"] else []

        def search(query: str) -> list[dict]:
            # The current topic's own material first; the rest of the course only to fill up
            hits = self.store.search_passages(course_id, query, limit, topic_docs) if topic_docs else []
            if len(hits) < 2:
                seen = {h["id"] for h in hits}
                hits += [h for h in self.store.search_passages(course_id, query, limit) if h["id"] not in seen]
            return hits[:limit]

        topic_words = materials.keywords(session["topic_title"] or "")
        words = materials.keywords(question)
        hits = []
        if not materials.has_arabic(question) and len(words) >= 2:
            hits = search(materials.fts_query(words + topic_words))
        # Arabic, very short, or follow-up questions: let the model write English search keywords.
        # Skipped otherwise (and for courses with no material): it costs a model call before every reply.
        needs_rewrite = materials.has_arabic(question) or len(words) < 2
        if not hits and needs_rewrite and self.store.has_passages(session["course_id"]):
            try:
                out = await self.llm.json_chat(
                    render("search", course=session["course_name"], topic=session["topic_title"] or "(none)",
                           previous=previous_reply[:1500], question=question),
                    SEARCH_SCHEMA, temperature=0)
            except ModelError:
                return []
            keywords = [k for k in out.get("keywords", []) if isinstance(k, str)][:8]
            query = materials.fts_query(keywords + topic_words)
            if query:
                hits = search(query)
        if not hits and topic_docs:
            # "Let's start", "go on": nothing to search for, so the lesson starts from the topic's own material
            hits = self.store.first_passages(topic_docs, 3)
        return hits

    # --- conversation -----------------------------------------------------
    async def chat(self, session_id: int, text: str, voice: bool = False) -> AsyncIterator[dict]:
        session = self.store.get_session(session_id)
        if session["ended_at"]:
            raise ValueError("This session has ended. Start a new session.")
        text = text.strip()
        if not text:
            raise ValueError("Message is empty")

        history = self.store.messages(session_id, limit=config.HISTORY_MESSAGES)
        previous_reply = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
        user_id = self.store.add_message(session_id, "user", text)
        yield {"type": "user", "id": user_id}

        parts: list[str] = []
        try:
            passages = await self.retrieve(session, text, previous_reply)
            earlier = session["message_count"]
            # Notes for this reply go at the end of the learner's message as the model sees it (the saved message
            # is unchanged): measured, a small model follows them there but not at the end of the system prompt
            notes = turn_notes(text, previous_reply)
            latest = text + ("\n\n[Note for the tutor: " + " ".join(notes) + "]" if notes else "")
            messages = ([{"role": "system", "content": self.system_prompt(session, passages, voice, earlier)}] +
                        [{"role": m["role"],
                          "content": clean_past_reply(m["content"]) if m["role"] == "assistant" else m["content"]}
                         for m in history] +
                        [{"role": "user", "content": latest}])
            # No greeting after the first reply; opening praise at most once every four replies
            past = [m["content"] for m in history if m["role"] == "assistant"]
            recent_praise = any(_OPENER_PRAISE.match(_OLD_LABELS.sub("", c)) for c in past[-3:])
            opening = OpeningFilter(greetings=bool(past), praise=recent_praise)
            notes_out = NoteFilter()
            # "Is this correct?" in typed chat, when the learner chose careful answers over fast ones
            careful = (JUDGE_THINKS_OVERRIDE == "1" if JUDGE_THINKS_OVERRIDE in ("0", "1")
                       else self.store.get_profile().get("careful_grammar") == "on")
            think = careful and not voice and JUDGE_NOTE in notes   # the same test as the note itself
            async for piece in self.llm.stream_chat(messages, think=think):
                piece = notes_out.push(opening.push(piece))
                if piece:
                    parts.append(piece)
                    yield {"type": "token", "text": piece}
            tail = notes_out.push(opening.flush()) + notes_out.flush()
            if tail:
                parts.append(tail)
                yield {"type": "token", "text": tail}
        except BaseException:
            if not parts:
                # Nothing was answered: drop the question so a retry does not duplicate it
                self.store.delete_message(user_id)
            else:
                # Cut off mid-reply (in live conversation the learner started talking): keep what was said
                partial = "".join(parts).strip() + " …"
                self.store.add_message(session_id, "assistant", partial, check_citations(partial, passages)[0])
            raise

        reply = "".join(parts).strip()
        sources, unknown = check_citations(reply, passages)
        reply_id = self.store.add_message(session_id, "assistant", reply, sources)
        yield {"type": "reply_done", "id": reply_id, "sources": sources, "unknown_citations": unknown}

        feedback = await self.observe(session, text, reply)
        self.store.set_message_feedback(user_id, feedback)
        yield {"type": "feedback", "message_id": user_id, "feedback": feedback}

    async def observe(self, session: dict, learner_message: str, tutor_reply: str) -> dict:
        profile = self.store.get_profile()
        prompt = render(
            "observer",
            english_level=profile["english_level"],
            known_rules=_list_or_none([m["rule"] for m in self.store.list_mistakes(limit=40)]),
            known_words=_list_or_none([v["word"] for v in self.store.list_vocab()[:150]]),
            known_memories=_list_or_none([m["content"] for m in self.store.list_memories(active_only=True)[:40]]),
            tutor_reply=tutor_reply[:3000],
            learner_message=learner_message,
        )
        try:
            # temperature 0: measured run-to-run variation at 0.2 included invented corrections
            out = await self.llm.json_chat(prompt, OBSERVER_SCHEMA, think=OBSERVER_THINKS, temperature=0)
        except ModelError as e:
            return {"error": str(e), "corrections": [], "better_phrasing": "", "saved_words": [], "saved_memories": []}
        feedback = self.apply_observation(session, learner_message, out, tutor_reply)
        feedback["saved_words"] += await self._save_requested_words(session, learner_message, profile)
        return feedback

    async def _save_requested_words(self, session: dict, learner_message: str, profile: dict) -> list[dict]:
        saved = []
        known = {normalize(v["word"]) for v in self.store.list_vocab()}
        for word in vocab_requests(learner_message):
            if normalize(word) in known:
                continue
            try:
                d = await self.llm.json_chat(
                    render("define", english_level=profile["english_level"], word=word, message=learner_message),
                    DEFINE_SCHEMA, temperature=0)
                row, created = self.store.add_vocab(word, str(d.get("meaning", "")), str(d.get("example", "")),
                                                    origin="auto", course_id=session["course_id"],
                                                    session_id=session["id"])
            except (ModelError, ValueError):
                continue
            if created:
                saved.append({"id": row["id"], "word": row["word"], "meaning": row["meaning"]})
        return saved

    def apply_observation(self, session: dict, learner_message: str, out: dict, tutor_reply: str = "") -> dict:
        """Validate the observer's proposals before anything is written to memory."""
        message_norm = normalize(learner_message)
        better = str(out.get("better_phrasing", "")).strip()
        better_norm = normalize(better)

        candidates = []
        for c in out.get("corrections", [])[:6]:
            wrong, right = str(c.get("wrong", "")).strip(), str(c.get("right", "")).strip()
            rule = str(c.get("rule", "")).strip()
            if not (wrong and right and rule) or normalize(wrong) == normalize(right):
                continue
            # The model must quote the learner's real words; anything else is an invented mistake
            if not _has_words(message_norm, normalize(wrong)):
                continue
            # Measured on this model: single corrections are sometimes wrong while its full rewrite is right,
            # so a correction must agree with the rewrite: the fix appears in it ("was -> were" is dropped when
            # the rewrite keeps "was") and the wrong words are gone from it
            if better_norm and (not _has_words(better_norm, normalize(right))
                                or _has_words(better_norm, normalize(wrong))):
                continue
            candidates.append({"wrong": wrong, "right": right, "rule": rule,
                               "category": str(c.get("category", "")),
                               "explanation": str(c.get("explanation", "")).strip()})

        # Overlapping corrections ("goes" and "I goes"): keep the one covering more of the sentence. Whole words:
        # "is -> are" does not overlap "his book -> his books"
        corrections = []
        for c in sorted(candidates, key=lambda c: -len(normalize(c["wrong"]))):
            w = normalize(c["wrong"])
            if any(_has_words(normalize(kept["wrong"]), w) for kept in corrections):
                continue
            corrections.append(c)
        for c in corrections:
            self.store.log_mistake(c["rule"], c.pop("category"), c["explanation"], c["wrong"], c["right"],
                                   session["id"])

        # A full rewrite is only offered when real mistakes were found, so correct English is never "fixed"
        if not corrections or better_norm == message_norm:
            better = ""

        saved_words = []
        reply_norm = normalize(tutor_reply)
        rank = {c: i for i, c in enumerate(CEFR)}
        level = rank.get(self.store.get_profile()["english_level"], 2)
        for v in out.get("vocabulary", [])[:2]:
            word_norm = normalize(str(v.get("word", "")))
            if not word_norm:
                continue
            if v.get("reason") in ("learner_asked", "learner_learned"):
                if word_norm not in message_norm:
                    continue
            else:
                # A term the tutor explained: must be in the reply and above the learner's level
                if word_norm not in reply_norm or rank.get(v.get("cefr"), 0) <= level:
                    continue
            try:
                row, created = self.store.add_vocab(str(v.get("word", "")), str(v.get("meaning", "")),
                                                    str(v.get("example", "")), origin="auto",
                                                    course_id=session["course_id"], session_id=session["id"])
            except ValueError:
                continue
            if created:
                saved_words.append({"id": row["id"], "word": row["word"], "meaning": row["meaning"]})

        saved_memories = []
        for m in out.get("memories", [])[:3]:
            try:
                row = self.store.add_memory(str(m.get("kind", "")), str(m.get("content", "")), "auto", session["id"])
            except ValueError:
                continue
            if row:
                saved_memories.append({"id": row["id"], "kind": row["kind"], "content": row["content"]})

        return {"corrections": corrections, "better_phrasing": better,
                "saved_words": saved_words, "saved_memories": saved_memories}

    # --- end of session ---------------------------------------------------
    async def end_session(self, session_id: int) -> dict:
        session = self.store.get_session(session_id)
        if session["ended_at"]:
            return {"session": session, "new_memories": [], "retired_memories": []}
        msgs = self.store.messages(session_id)
        if len(msgs) < 2:
            return {"session": self.store.end_session(session_id, None, None),
                    "new_memories": [], "retired_memories": []}

        transcript = "\n\n".join(f"{'Learner' if m['role'] == 'user' else 'Tutor'}: {m['content']}" for m in msgs)
        memories = self.store.list_memories(active_only=True)
        prompt = render("reflection",
                        course=session["course_name"] or "(free conversation)",
                        topic=session["topic_title"] or "(none)",
                        memories="\n".join(f"{m['id']}: {m['content']}" for m in memories) or "(empty)",
                        transcript=transcript[-MAX_TRANSCRIPT_CHARS:])
        out = None
        for _ in range(2):
            try:
                out = await self.llm.json_chat(prompt, REFLECTION_SCHEMA, think=REFLECTION_THINKS, temperature=0.6)
                break
            except ModelError:
                continue
        if out is None:
            return {"session": self.store.end_session(session_id, None, None),
                    "new_memories": [], "retired_memories": [], "error": "Could not summarise the session"}

        new_memories = []
        for m in out.get("new_memories", [])[:3]:
            try:
                row = self.store.add_memory(str(m.get("kind", "")), str(m.get("content", "")), "auto", session_id)
            except ValueError:
                continue
            if row:
                new_memories.append(row)

        # Only memories the tutor created itself may be retired automatically, never the learner's own
        retirable = {m["id"]: m for m in memories if m["origin"] == "auto"}
        retired = []
        for memory_id in out.get("outdated_memory_ids", []):
            if memory_id in retirable:
                retired.append(self.store.update_memory(memory_id, active=False))

        ended = self.store.end_session(session_id, str(out.get("summary", "")).strip() or None,
                                       str(out.get("next_step", "")).strip() or None)
        return {"session": ended, "new_memories": new_memories, "retired_memories": retired}

    # --- syllabus -----------------------------------------------------------
    async def suggest_topics(self, course_id: int) -> list[str]:
        course = self.store.get_course(course_id)
        passages = self.store.sample_passages(course_id, 24)
        if not passages:
            raise ValueError("Add material to this course first")
        block = "\n\n".join(
            f"({p['filename']}{', p. ' + str(p['page']) if p['page'] else ''}) {p['text'][:700]}" for p in passages)
        out = await self.llm.json_chat(render("topics", course=course["name"], passages=block),
                                       TOPICS_SCHEMA, temperature=0.3)
        existing = {normalize(t["title"]) for t in self.store.list_topics(course_id)}
        suggestions: list[str] = []
        for title in out.get("topics", []):
            title = " ".join(str(title).split())[:120]
            key = normalize(title)
            if key and key not in existing:
                existing.add(key)
                suggestions.append(title)
        return suggestions[:15]
