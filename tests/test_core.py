"""Core tests that run without the real model: storage, scheduling, material search,
citation checks, memory validation, the chat flow, and the server's local-only protections."""

import asyncio
import codecs
import io
import json
import re
import sqlite3
import wave
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tutor import config, materials, srs
from tutor.llm import ModelError
from tutor.server import create_app
from tutor.store import Duplicate, NotFound, Store, normalize
from tutor.tutor import (NoteFilter, OpeningFilter, Tutor, check_citations, clean_past_reply, turn_notes,
                         vocab_requests)
from tutor.voice import TextToSpeech, VoiceError, clean_for_speech, clean_markup, split_languages, wav_bytes

from fakes import FakeLLM, FakeVoice

PASSAGE_TEXT = (
    "Classical conditioning is a type of learning in which a neutral stimulus comes to trigger a response "
    "after being paired with an unconditioned stimulus. Pavlov showed this with dogs that salivated at a bell. "
) * 3
OTHER_TEXT = "Operant conditioning is learning from the consequences of behaviour, such as reinforcement. " * 3


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "tutor.sqlite3")


def _course_with_material(store: Store):
    course = store.create_course("Intro Psychology")
    store.add_document(course["id"], "ch1.txt", "ch1.txt", "sha-1", None,
                       [(None, PASSAGE_TEXT), (None, OTHER_TEXT)])
    topic = store.add_topic(course["id"], "Classical conditioning")
    return course, topic


def _run(agen):
    async def collect():
        return [event async for event in agen]
    return asyncio.run(collect())


# --- scheduling -------------------------------------------------------------
def test_srs_stages_and_intervals():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day0 = t0.astimezone().date()

    def local(due: str) -> datetime:
        return datetime.fromisoformat(due).astimezone()

    stage, due = srs.schedule(0, "good", now=t0)
    assert stage == 1 and local(due).date() == day0 + timedelta(days=1) and local(due).time() == time.min
    stage, due = srs.schedule(1, "easy", now=t0)
    assert stage == 3 and local(due).date() == day0 + timedelta(days=7) and local(due).time() == time.min
    assert srs.schedule(4, "again", now=t0)[0] == 0
    stage, due = srs.schedule(3, "hard", now=t0)          # same stage, half its 7 days
    assert stage == 3 and local(due).date() == day0 + timedelta(days=3) and local(due).time() == time.min
    assert srs.schedule(srs.MAX_STAGE, "easy", now=t0)[0] == srs.MAX_STAGE
    with pytest.raises(ValueError):
        srs.schedule(0, "perfect")


def test_reviews_are_due_from_the_start_of_the_day():
    # Studied at 21:00: due from midnight, so the Today page opened at 20:00 the next day shows it
    evening = datetime(2026, 3, 10, 21, 0).astimezone()
    _, due = srs.schedule(0, "good", now=evening)
    assert datetime.fromisoformat(due) == datetime(2026, 3, 11, 0, 0).astimezone()
    _, due = srs.schedule(0, "hard", now=evening)
    assert datetime.fromisoformat(due) == datetime(2026, 3, 11, 0, 0).astimezone()
    # A forgotten word keeps its exact short delay; it is not due at once
    _, again = srs.schedule(2, "again", now=evening, again_after=timedelta(minutes=10))
    assert datetime.fromisoformat(again) == evening + timedelta(minutes=10)


# --- materials --------------------------------------------------------------
def test_chunks_keep_page_numbers_and_overlap():
    words = [f"w{i}" for i in range(400)]
    chunks = materials.chunk_pages([(7, " ".join(words))], words=180, overlap=30)
    assert all(page == 7 for page, _ in chunks)
    first, second = chunks[0][1].split(), chunks[1][1].split()
    assert first[-30:] == second[:30]
    assert chunks[-1][1].split()[-1] == "w399"


def test_text_files_in_utf16_and_windows_arabic_are_read():
    text = "Classical conditioning (الإشراط الكلاسيكي) was shown by Pavlov."
    for data in (text.encode("utf-16"), codecs.BOM_UTF16_BE + text.encode("utf-16-be"), text.encode("utf-8")):
        assert materials.extract_pages("notes.txt", data) == ([(None, text)], None)
    arabic = "الإشراط الكلاسيكي نوع من التعلم"
    assert materials.extract_pages("notes.txt", arabic.encode("cp1256")) == ([(None, arabic)], None)


def test_passages_of_documents_added_in_the_same_second_are_not_interleaved(store):
    course = store.create_course("Psych")
    first = store.add_document(course["id"], "a.txt", "a", "sha-a", None, [(None, "a0"), (None, "a1"), (None, "a2")])
    second = store.add_document(course["id"], "b.txt", "b", "sha-b", None, [(None, "b0"), (None, "b1")])
    store._exec("UPDATE documents SET created_at = '2026-01-01T10:00:00+00:00'")    # one upload, one second
    assert [p["text"] for p in store.sample_passages(course["id"], 10)] == ["a0", "a1", "a2", "b0", "b1"]
    assert [d["id"] for d in store.list_documents(course["id"])] == [first["id"], second["id"]]


def test_fts_query_cannot_inject_syntax(store):
    course, _ = _course_with_material(store)
    query = materials.fts_query(materials.keywords('conditioning" OR NEAR(x) * stimulus'))
    assert query and all(term.startswith('"') for term in query.split(" OR "))
    assert store.search_passages(course["id"], query, 5)


def test_arabic_detection_and_keywords():
    assert materials.has_arabic("ما هو classical conditioning؟")
    assert materials.keywords("Can you explain the classical conditioning again?") == ["classical", "conditioning"]


def test_search_is_scoped_to_one_course_and_deletes_cascade(store):
    course, _ = _course_with_material(store)
    other = store.create_course("Other")
    store.add_document(other["id"], "x.txt", "x.txt", "sha-2", None, [(None, PASSAGE_TEXT)])
    query = materials.fts_query(["pavlov"])
    assert len(store.search_passages(course["id"], query, 10)) == 1
    with pytest.raises(Duplicate):
        store.add_document(course["id"], "again.txt", "again.txt", "sha-1", None, [(None, "text")])
    doc = store.list_documents(course["id"])[0]
    store.delete_document(doc["id"])
    assert store.search_passages(course["id"], query, 10) == []
    assert len(store.search_passages(other["id"], query, 10)) == 1


# --- topic material ---------------------------------------------------------
def test_a_topic_searches_its_own_material_first_and_starts_from_it(store):
    course = store.create_course("Psych")
    skinner = store.add_document(course["id"], "04-skinner.txt", "x", "sha-a", None,
                                 [(None, "Skinner studied operant conditioning with rats and pigeons. " * 5)])
    bloom = store.add_document(course["id"], "01-intro.txt", "y", "sha-b", None,
                               [(None, "Operant conditioning is mentioned briefly in the introduction. " * 5)])
    topic = store.add_topic(course["id"], "Lecture 4: Foundations: Skinner")
    store.link_topic_document(topic["id"], skinner["id"])
    assert store.list_topics(course["id"])[0]["document_ids"] == [skinner["id"]]

    tutor = Tutor(store, FakeLLM())
    session = store.create_session(course["id"], topic["id"])
    session = store.get_session(session["id"])
    hits = asyncio.run(tutor.retrieve(session, "Explain operant conditioning please", ""))
    assert hits[0]["filename"] == "04-skinner.txt"
    started = asyncio.run(tutor.retrieve(session, "ok", ""))       # nothing to search for
    assert started and all(h["filename"] == "04-skinner.txt" for h in started)

    other = store.create_course("Other")
    with pytest.raises(ValueError):
        store.link_topic_document(store.add_topic(other["id"], "X")["id"], bloom["id"])


# --- topics and plan --------------------------------------------------------
def test_topic_lifecycle_and_plan(store):
    course, topic = _course_with_material(store)
    second = store.add_topic(course["id"], "Operant conditioning")
    with pytest.raises(Duplicate):
        store.add_topic(course["id"], "classical  conditioning")
    assert [t["id"] for t in store.move_topic(second["id"], -1)] == [second["id"], topic["id"]]
    assert store.plan()["next_topics"][0]["id"] == second["id"]

    session = store.create_session(None, topic["id"])
    assert session["course_id"] == course["id"]
    store.add_message(session["id"], "user", "hi")
    store.add_message(session["id"], "assistant", "hello")
    store.end_session(session["id"], "summary", "next")
    learning = store.get_topic(topic["id"])
    assert learning["status"] == "learning"
    due = datetime.fromisoformat(learning["due_at"]).astimezone()
    assert due.time() == time.min and due.date() == datetime.now().date() + timedelta(days=1)   # all of tomorrow

    reviewed = store.review_topic(topic["id"], "good")
    assert reviewed["status"] == "reviewing" and reviewed["stage"] == 1
    assert store.last_summary(course["id"])["next_step"] == "next"


def test_a_topic_cannot_be_renamed_to_another_topics_title(store):
    course, topic = _course_with_material(store)
    second = store.add_topic(course["id"], "Operant conditioning")
    with pytest.raises(Duplicate):
        store.update_topic(second["id"], title="classical  conditioning")
    with pytest.raises(ValueError):
        store.update_topic(second["id"], title="  ")
    assert store.update_topic(topic["id"], title="Classical Conditioning")["title"] == "Classical Conditioning"
    assert store.update_topic(second["id"], notes="Skinner")["title"] == "Operant conditioning"


# --- memory -----------------------------------------------------------------
def test_memories_are_deduplicated(store):
    assert store.add_memory("preference", "Prefers examples from football.", "auto")
    assert store.add_memory("preference", "prefers examples from Football", "auto") is None
    other = store.add_memory("goal", "Wants to pass IELTS with band 7.", "user")
    store.update_memory(other["id"], active=False)
    assert len(store.list_memories(active_only=True)) == 1
    # A paused memory stays paused when the tutor proposes it again; the learner adding it resumes it
    assert store.add_memory("goal", "wants to pass IELTS with band 7", "auto") is None
    assert len(store.list_memories(active_only=True)) == 1
    resumed = store.add_memory("goal", "Wants to pass IELTS with band 7", "user")
    assert resumed["id"] == other["id"] and resumed["active"] == 1
    assert len(store.list_memories()) == 2


def test_vocab_repeats_raise_seen_count_and_again_comes_back_soon(store):
    row, created = store.add_vocab("plasticity", "the brain's ability to change", origin="auto")
    again, created_again = store.add_vocab("Plasticity", "different meaning", origin="auto")
    assert created and not created_again and again["seen_count"] == 2 and again["meaning"] == row["meaning"]
    # The learner adding a word they already have is refused (409), not counted as seen
    _, created_by_learner = store.add_vocab("plasticity", "my meaning", origin="user")
    assert not created_by_learner and store.list_vocab()[0]["seen_count"] == 2
    assert store.review_vocab(row["id"], "good")["stage"] == 1
    reviewed = store.review_vocab(row["id"], "again")
    due = datetime.fromisoformat(reviewed["due_at"])
    # Back to the first stage, and due again in 10 minutes: not at once, and not tomorrow
    assert reviewed["stage"] == 0 and timedelta(minutes=9) < due - datetime.now(timezone.utc) <= timedelta(minutes=10)


def test_vocab_edits_are_cleaned_like_new_words(store):
    row, _ = store.add_vocab("plasticity", "the brain's ability to change")
    with pytest.raises(ValueError):
        store.update_vocab(row["id"], meaning="   ")
    edited = store.update_vocab(row["id"], meaning="  the brain's\n ability   to change ", example="x " * 400)
    assert edited["meaning"] == "the brain's ability to change" and len(edited["example"]) == 500
    assert store.update_vocab(row["id"], example="It changes.")["meaning"] == "the brain's ability to change"


def test_curly_apostrophes_compare_like_straight_ones(store):
    assert normalize("She don’t like ‘it’") == normalize("she don't like 'it'") == "she don't like 'it'"
    tutor = Tutor(store, FakeLLM())
    session = store.create_session(None, None)
    out = tutor.apply_observation(session, "She don’t like coffee.", {
        "corrections": [{"wrong": "She don't", "right": "She doesn't", "rule": "third-person singular",
                         "category": "grammar", "explanation": ""}],
        "better_phrasing": "She doesn't like coffee.", "vocabulary": [], "memories": []})
    assert [c["wrong"] for c in out["corrections"]] == ["She don't"]


def test_mistakes_group_by_rule_and_reopen_when_repeated(store):
    store.log_mistake("Third-person singular -s", "grammar", "", "he go", "he goes")
    pattern = store.log_mistake("third-person singular -s", "grammar", "", "she like", "she likes")
    assert pattern["count"] == 2
    store.set_mistake_resolved(pattern["id"], True)
    assert store.log_mistake("third-person singular -s", "grammar", "", "it work", "it works")["resolved"] == 0
    assert len(store.list_mistakes()[0]["examples"]) == 3


# --- tutor logic ------------------------------------------------------------
def test_citations_are_checked_against_sent_passages():
    passages = [{"id": 1, "filename": "a.pdf", "page": 3, "text": "x"},
                {"id": 2, "filename": "a.pdf", "page": 4, "text": "y"}]
    sources, unknown = check_citations("First [S1]. Made up [S3].", passages)
    assert [s["cited"] for s in sources] == [True, False]
    assert unknown == ["S3"]


def test_observation_rejects_invented_contradictory_and_overlapping_corrections(store):
    tutor = Tutor(store, FakeLLM())
    session = store.create_session(None, None)
    message = "Yesterday I goes to the university. The informations was useful. What does plasticity mean?"
    out = tutor.apply_observation(session, message, {
        "corrections": [
            {"wrong": "goes", "right": "went", "rule": "past simple", "category": "grammar", "explanation": ""},
            {"wrong": "I goes", "right": "I went", "rule": "past simple", "category": "grammar", "explanation": ""},
            {"wrong": "informations", "right": "information", "rule": "uncountable nouns",
             "category": "grammar", "explanation": ""},
            # Not in the message: invented
            {"wrong": "she don't", "right": "she doesn't", "rule": "agreement", "category": "grammar",
             "explanation": ""},
            # Contradicts the model's own rewrite, which keeps "was"
            {"wrong": "was", "right": "were", "rule": "agreement", "category": "grammar", "explanation": ""},
        ],
        "better_phrasing": "Yesterday I went to the university. The information was useful. "
                           "What does plasticity mean?",
        "vocabulary": [
            {"word": "plasticity", "meaning": "the brain's ability to change", "example": "",
             "reason": "learner_asked", "cefr": "C1"},
            {"word": "useful", "meaning": "helpful", "example": "", "reason": "tutor_explained", "cefr": "A2"},
        ],
        "memories": [{"kind": "interest", "content": "Studies at a university."}],
    }, tutor_reply="Plasticity is useful to know.")
    assert [(c["wrong"], c["right"]) for c in out["corrections"]] == [
        ("informations", "information"), ("I goes", "I went")]
    assert {m["rule"] for m in store.list_mistakes()} == {"past simple", "uncountable nouns"}
    assert [w["word"] for w in out["saved_words"]] == ["plasticity"]   # "useful" is below a B1 learner's level
    assert out["better_phrasing"] and out["saved_memories"]

    # The rewrite still contains the "wrong" words, so the model did not really fix them
    garbled = tutor.apply_observation(session, "Can you explain me what is a conditioned stimulus?", {
        "corrections": [
            {"wrong": "what is a conditioned stimulus", "right": "is a conditioned stimulus", "rule": "word order",
             "category": "word_order", "explanation": ""},
            {"wrong": "explain me", "right": "explain to me", "rule": "explain to someone",
             "category": "grammar", "explanation": ""},
        ],
        "better_phrasing": "Can you explain to me what is a conditioned stimulus?",
        "vocabulary": [], "memories": []})
    assert [c["wrong"] for c in garbled["corrections"]] == ["explain me"]

    clean = tutor.apply_observation(session, "I find memory models fascinating.", {
        "corrections": [], "better_phrasing": "Memory models fascinate me.", "vocabulary": [], "memories": []})
    assert clean["better_phrasing"] == ""   # correct English is never rewritten

    # Overlap is by whole words: "is" is not inside "his book"
    both = tutor.apply_observation(session, "These is his book about memory.", {
        "corrections": [
            {"wrong": "his book", "right": "his books", "rule": "plural nouns", "category": "grammar",
             "explanation": ""},
            {"wrong": "is", "right": "are", "rule": "subject-verb agreement", "category": "grammar",
             "explanation": ""},
        ],
        "better_phrasing": "These are his books about memory.", "vocabulary": [], "memories": []})
    assert [c["wrong"] for c in both["corrections"]] == ["his book", "is"]


def _filtered(tokens: list[str], greetings=True, praise=False) -> str:
    f = OpeningFilter(greetings, praise)
    return "".join(f.push(t) for t in tokens) + f.flush()


def test_greetings_are_dropped_only_in_an_ongoing_conversation():
    assert _filtered(["Hello, ", "Abdulllah! ", "That is ", "right."]) == "That is right."
    assert _filtered(["Welcome back! ", "Hi ", "again. ", "so, attention ", "matters."]) == "So, attention matters."
    assert _filtered(["Hello! ", "Welcome to ", "the course."], greetings=False) == "Hello! Welcome to the course."
    assert _filtered(["High ", "levels of stress ", "hurt memory."]) == "High levels of stress hurt memory."
    assert _filtered(["Hi!"]) == "Hi!"   # a reply that is only a greeting is kept, never emptied


def test_repeated_opening_praise_is_dropped_but_real_content_is_kept():
    assert _filtered(["That is a great ", "idea, Abdulllah. ", "Using your job ", "helps."], praise=True) == "Using your job helps."
    assert _filtered(["Good question. ", "In English, ", "we say it."], praise=True) == "In English, we say it."
    assert _filtered(["That is because ", "attention is limited. ", "So we"], praise=True) == "That is because attention is limited. So we"
    assert _filtered(["Good question. ", "In English."], praise=False) == "Good question. In English."
    # Only a name or a word like "again" may follow the opener: a reply that teaches greetings, or answers right
    # after the praise, is kept whole
    teaching = "Hello is more formal. Hi is casual. Good morning is used before noon. Use them well."
    assert _filtered(["Hello is ", "more formal. ", "Hi is casual. ", "Good morning is used before noon. ",
                      "Use them well."], praise=True) == teaching
    assert clean_past_reply(teaching) == teaching
    answer = "Good question, the answer is no. Pavlov used dogs."
    assert _filtered(["Good question, ", "the answer is no. ", "Pavlov used dogs."], praise=True) == answer
    assert clean_past_reply(answer) == answer


def _without_notes(tokens: list[str]) -> str:
    f = NoteFilter()
    return "".join(f.push(t) for t in tokens) + f.flush()


def test_notes_the_model_writes_itself_are_dropped():
    assert _without_notes(["Use *suggests*.", "\n\n[Note ", "for the tutor: the learner ", "is C2.]"]) == "Use *suggests*.\n\n"
    assert _without_notes(["A [", "NOTE TO THE TUTOR: x] b"]) == "A  b"
    # labels, links and brackets that only begin like a note pass unchanged
    assert _without_notes(["Focus [S", "2]. See [naps](https://x.org) [", "Note: 2]"]) == \
        "Focus [S2]. See [naps](https://x.org) [Note: 2]"
    assert _without_notes(["The end [No"]) == "The end [No"
    # A note the model never closes ends at its line break, or else after its first sentence
    assert _without_notes(["Use it. [Note for the tutor: C2 learner. ", "Next, the dependent variable is recall."]) \
        == "Use it. Next, the dependent variable is recall."
    assert _without_notes(["Use it. [Note for ", "the tutor: C2 learner.", "\n\nNext, recall."]) == \
        "Use it. \n\nNext, recall."
    assert _without_notes(["Use it. [Note for the tutor: the learner is C2"]) == "Use it. "


def test_past_replies_are_cleaned_before_the_model_sees_them():
    past = "Hello, Abdulllah! That is a great question! [S3] Attention is focus [S2]. [General Knowledge]"
    assert clean_past_reply(past) == "Attention is focus."


def test_ongoing_sessions_tell_the_model_not_to_greet(store):
    llm = FakeLLM(reply="Hello, Sam! Attention is focus.")
    tutor = Tutor(store, llm)
    session = store.create_session(None, None)
    first = _run(tutor.chat(session["id"], "Hi"))
    second = _run(tutor.chat(session["id"], "What is attention?"))
    assert "first reply of the session" in llm.chat_requests[0][0]["content"]
    assert "Do not greet" in llm.chat_requests[1][0]["content"]
    assert "".join(e["text"] for e in first if e["type"] == "token").startswith("Hello, Sam!")
    assert "".join(e["text"] for e in second if e["type"] == "token").startswith("Attention is focus.")


def test_translation_and_personal_messages_get_a_note_for_that_reply():
    assert "into Arabic" in " ".join(turn_notes("Can you translate your question, please?"))
    assert "into Arabic" in " ".join(turn_notes("ترجم لي هذا"))
    assert not any("into Arabic" in n for n in turn_notes("Please translate it into English"))
    assert "personal" in " ".join(turn_notes("High volume of voices make me stressed, I feel tired"))
    assert "personal" in " ".join(turn_notes("high volume of voices make me stressed"))
    assert "personal" in " ".join(turn_notes("أشعر بالندم"))
    assert turn_notes("What is stress in psychology?") == []
    assert turn_notes("My favourite topic is memory") == []
    # The personal note leaves the prompt's safety rule in force
    assert "follow the safety rule" in " ".join(turn_notes("I regret it, I feel sad"))
    # Lesson messages, English practice and content questions are not personal
    for text in ("Can you give me more pairs like fill and feel?", "Give me a sentence using the word 'tired'",
                 "Can you explain to me the word 'feeling'?",
                 "Is this correct: 'I feel frightened when I look at the mask'?",
                 "I felt nervous on my first day in Germany because I didn't know anyone.",
                 "I feel like the answer is B because the stimulus is neutral.", "I feel that the dog learned the bell.",
                 "Do my sentences feel natural?", "give me an example about a child 5 years old",
                 "ما هو اضطراب القلق في علم النفس؟", "ما الفرق بين الشعور والإدراك؟", "ما معنى ضغط الأقران؟"):
        assert not any("This message is personal" in n for n in turn_notes(text)), text
    # A question about the word "translation" is not a request to translate
    assert not any("into Arabic" in n for n in turn_notes("What does the word translation mean?"))


def test_pronunciation_questions_get_the_dictionary_facts():
    note = " ".join(turn_notes("How many syllables are in the word watermelon, and which syllable is stressed?"))
    assert "watermelon: 4 syllables, main stress on syllable 1" in note
    note = " ".join(turn_notes("Is the -s ending in 'watches' pronounced /s/, /z/ or /ɪz/? And in 'dogs'?"))
    assert "watches" in note and "/ɪz/" in note and "dogs" in note and "is /z/" in note
    note = " ".join(turn_notes("What is the -ed sound in wanted, played and walked?"))
    assert "wanted" in note and "/ɪd/" in note and "played" in note and "walked" in note and "is /t/" in note
    # An answer that names no word: the words the tutor just asked about
    note = " ".join(turn_notes("Four syllables, stress on the third.", "How many syllables are in **avocado**?"))
    assert "avocado: 4 syllables, main stress on syllable 3" in note
    # Not pronunciation questions
    for text in ("That sounds good", "I'm stressed about my exam", "What is stress in psychology?",
                 "How should I write the ending of my essay?", "Is my ending good?",
                 "Can you check the ending of my paragraph?", "What is the happy ending of the story?",
                 "Which vowel do I use, a or an?"):
        assert not any("CMU" in n for n in turn_notes(text)), text


def test_a_word_said_with_two_syllable_counts_accepts_both():
    from tutor import pronunciation
    assert pronunciation.describe("every").startswith(
        "every: 3 syllables (also said with 2 syllables), main stress on syllable 1")
    note = " ".join(turn_notes("How many syllables are in 'every'?"))
    assert "also said with 2 syllables" in note and "accept either count" in note


def test_a_paragraph_sent_for_feedback_gets_the_writing_note():
    paragraph = ("Please check my paragraph. When I was ten years old, I feel very nervous on my first day in a new "
                 "school. My family moved to a new city because my father get a new job. I didn't knew anyone in "
                 "my class and the teacher speak very fast. In the break I sat alone, I was sad and I want to go home. "
                 "Then a boy ask me to play football with him. After that day, school become easier for me.")
    notes = turn_notes(paragraph)
    assert len(notes) == 1 and "writing, sent for feedback" in notes[0]     # not the personal-message note
    assert not any("writing, sent for feedback" in n for n in turn_notes("Can you check my answer to question 3?"))


BOOK_PARAGRAPH = (
    "In classical conditioning, an unconditioned stimulus is a stimulus that naturally and automatically triggers a "
    "response without any prior learning. For example, when food is placed in a dog's mouth, the dog salivates; the "
    "food is the unconditioned stimulus and the salivation is the unconditioned response. A neutral stimulus, such "
    "as the sound of a bell, does not at first produce salivation. When the bell is repeatedly presented just before "
    "the food, the dog comes to associate the two events. After several pairings the bell alone produces "
    "salivation, and it is now called a conditioned stimulus, while the salivation to the bell is the conditioned "
    "response. Pavlov noticed that the timing of the pairing matters: the neutral stimulus must come shortly before "
    "the unconditioned stimulus. If the bell is later presented many times without food, the conditioned response "
    "gradually weakens, a process called extinction. After a rest period the response can briefly return, which is "
    "known as spontaneous recovery.")


def test_a_long_text_quoted_from_the_course_is_not_the_learners_writing():
    from tutor.tutor import WRITING_NOTE
    for message in ("I do not understand this paragraph from the lecture transcript: " + BOOK_PARAGRAPH +
                    " What does the professor mean by unconditioned stimulus here?",
                    BOOK_PARAGRAPH + " Why does the response come back after the rest period?"):
        assert len(message.split()) >= 150 and WRITING_NOTE not in turn_notes(message)
    # A long text of the learner's own, with no request and no question, still is
    story = ("When I was ten years old, I feel very nervous on my first day in a new school. My family moved to a "
             "new city because my father get a new job. I didn't knew anyone in my class and the teacher speak very "
             "fast. In the break I sat alone and I want to go home. Then a boy ask me to play football with him. ")
    assert turn_notes(story * 3) == [WRITING_NOTE]


def test_a_paraphrase_gets_its_note_and_the_copied_words():
    from tutor.tutor import copied_phrases
    original = ("Stereotype threat is a situational predicament in which people feel themselves to be at risk of "
                "conforming to stereotypes about their social group.")
    own = ("Stereotype threat happens when people feel themselves to be at risk of confirming a negative "
           "belief about their group.")
    assert copied_phrases(original, own) == ["people feel themselves to be at risk of"]
    notes = turn_notes(f"Please check my paraphrase. Original: {original} Paraphrase: {own}")
    assert "paraphrase or summary" in notes[0]
    assert '"people feel themselves to be at risk of"' in notes[1]
    # Asking about paraphrasing without both texts: the feedback order only
    assert len(turn_notes("Can you check my summary of the reading? " + own + " " + own)) == 1
    # Asking the tutor for a summary is not sending one for feedback
    for request in ("Can you summarize the main ideas of lecture 3 about the brain, the neurons and the nervous "
                    "system? I want to review them before the quiz tomorrow, so please keep it short and simple.",
                    "Please give me a short summary of what we studied today about classical conditioning and "
                    "operant conditioning, with one example for each, so I can review it tonight."):
        assert not any("paraphrase or summary" in n for n in turn_notes(request)), request


def test_asking_whether_english_is_correct_gets_the_rule_first_note():
    from tutor.tutor import JUDGE_NOTE
    for question in ("Do I need a comma in this sentence: 'Although the study was small the results were significant.'?",
                     "Is this sentence correct: 'The studys results were clear.'?",
                     "Which is correct: 'in the morning' or 'at the morning'?"):
        assert JUDGE_NOTE in turn_notes(question)
    assert JUDGE_NOTE not in turn_notes("What is a thesis statement?")
    # Questions about the content, not about English
    for question in ("Is my answer right?", "Is it right that Pavlov used dogs?",
                     "Is the answer to question 3 correct?", "Is this the correct lecture for Skinner?"):
        assert JUDGE_NOTE not in turn_notes(question), question


def test_apa_questions_get_the_seventh_edition_rules():
    from tutor.tutor import APA_NOTE
    assert APA_NOTE in turn_notes("How do I cite three authors in APA 7th edition?")
    assert APA_NOTE in turn_notes("Is this correct: (Smith et al., 2019)?")
    assert APA_NOTE in turn_notes("And with two authors?", previous_reply="In APA style, cite the authors...")
    assert APA_NOTE not in turn_notes("How do I cite a website in MLA?")
    assert APA_NOTE not in turn_notes("Who are the authors of this book?")


def test_careful_grammar_thinks_only_for_typed_correctness_questions(store, monkeypatch):
    from tutor import tutor as tutor_module
    monkeypatch.setattr(tutor_module, "JUDGE_THINKS_OVERRIDE", None)   # TUTOR_JUDGE_THINKS would override the profile
    llm = FakeLLM(reply="Yes, it is correct.")
    tutor = Tutor(store, llm)
    session = store.create_session(None, None)

    async def ask(text, voice=False):
        return [e async for e in tutor.chat(session["id"], text, voice=voice)]

    asyncio.run(ask("Is this sentence correct: 'I goes home.'?"))
    store.update_profile({"careful_grammar": "on"})
    asyncio.run(ask("Is this sentence correct: 'I goes home.'?"))
    asyncio.run(ask("Is this sentence correct: 'I goes home.'?", voice=True))
    asyncio.run(ask("What is a thesis statement?"))
    asyncio.run(ask("Is my answer right?"))                                       # about the content
    asyncio.run(ask("Is the stress in 'record' correct on the second syllable?"))  # the dictionary answers it
    assert llm.think_requests == [False, True, False, False, False, False]


def test_word_lists_add_a_few_new_words_each_day(store):
    items = [{"word": f"word{i}", "meaning": f"meaning {i}", "pos": "noun", "example": f"An example with word{i}."}
             for i in range(25)]
    store.add_vocab("word2", "my own meaning", origin="user")           # the learner already has this one
    wl = store.save_word_list("test-list", "Test list", "", "a source", items)
    store.update_word_list(wl["id"], 10)

    first = store.introduce_words("2026-10-01")
    assert [w["word"] for w in first] == ["word0", "word1"] + [f"word{i}" for i in range(3, 11)]
    assert store.introduce_words("2026-10-01") == []                   # the same day: nothing more
    assert store.word_list(wl["id"])["introduced"] == 11               # 10 new + 1 already known

    # Days without study add nothing; the next study day adds the next 10, not a backlog
    second = store.introduce_words("2026-10-05")
    assert [w["word"] for w in second] == [f"word{i}" for i in range(11, 21)]

    store.update_word_list(wl["id"], 0)                                # paused
    assert store.introduce_words("2026-10-06") == []

    # Saving the list again keeps progress and does not duplicate words
    store.save_word_list("test-list", "Test list", "", "a source", items)
    assert store.word_list(wl["id"])["total"] == 25 and store.word_list(wl["id"])["introduced"] == 21

    row = next(v for v in store.list_vocab() if v["word"] == "word0")
    assert row["list_title"] == "Test list" and row["meaning"] == "(noun) meaning 0"
    assert next(v for v in store.list_vocab() if v["word"] == "word2")["list_title"] is None


def test_ending_sounds_need_a_real_base_word():
    from tutor import pronunciation
    assert pronunciation._ending("stopped") == "the -ed ending is /t/"
    assert pronunciation._ending("studies") == "the -s ending is /z/"
    assert pronunciation._ending("glasses") == "the -s ending is /ɪz/ (an extra syllable)"
    for word in ("need", "seed", "was", "his", "bus"):
        assert pronunciation._ending(word) is None


def test_the_note_reaches_the_model_but_is_not_saved(store):
    llm = FakeLLM(reply="I see. Back to our topic.")
    tutor = Tutor(store, llm)
    session = store.create_session(None, None)
    _run(tutor.chat(session["id"], "I regret it, I feel sad"))
    assert "[Note for the tutor:" in llm.chat_requests[0][-1]["content"]
    assert store.messages(session["id"])[0]["content"] == "I regret it, I feel sad"


def test_empty_sessions_are_not_kept(store):
    session = store.create_session(None, None)
    assert store.end_session(session["id"], None, None)["discarded"]
    with pytest.raises(NotFound):
        store.get_session(session["id"])


def test_history_hides_old_empty_sessions_but_keeps_the_open_one(store):
    kept = store.create_session(None, None)
    store.add_message(kept["id"], "user", "hi")
    store.end_session(kept["id"], "summary", None)
    # Left from before empty sessions were discarded
    old = store._exec("INSERT INTO sessions(started_at, ended_at) VALUES (?, ?)",
                      ("2020-01-01T10:00:00+00:00", "2020-01-01T10:05:00+00:00"))
    summarised = store._exec("INSERT INTO sessions(started_at, ended_at, summary) VALUES (?, ?, ?)",
                             ("2020-01-02T10:00:00+00:00", "2020-01-02T10:05:00+00:00", "Studied memory."))
    just_started = store.create_session(None, None)          # the page resumes it from this list
    assert [s["id"] for s in store.list_sessions()] == [just_started["id"], kept["id"], summarised]
    assert store.get_session(old)                              # hidden, not deleted


def test_explicit_vocabulary_requests_are_detected():
    assert vocab_requests("I learned the word 'plasticity', it means the brain can change.") == ["plasticity"]
    assert vocab_requests("I learned the word plasticity today") == ["plasticity"]
    assert vocab_requests("What does spontaneous recovery mean?") == ["spontaneous recovery"]
    assert vocab_requests("ما معنى extinction في هذا الدرس؟") == ["extinction"]
    assert vocab_requests("What does this mean? What does it mean?") == []
    assert vocab_requests("This word is hard for me.") == []
    # Not words to learn: each would cost a definition call
    for message in ("What does the author mean?", "What does the professor mean when he says that?",
                    "What do you mean by that?"):
        assert vocab_requests(message) == [], message
    assert vocab_requests("What does Pavlov mean by extinction?") == ["extinction"]
    assert vocab_requests("What does the word 'don't' mean?") == ["don't"]
    assert vocab_requests("I learned a new word today: extinction") == ["extinction"]
    assert vocab_requests("I learned a new word yesterday, 'habituation'") == ["habituation"]
    assert vocab_requests("ما معنى classical conditioning؟") == ["classical conditioning"]
    assert vocab_requests("ما معنى spontaneous recovery في هذا الدرس؟") == ["spontaneous recovery"]
    assert vocab_requests("What is the meaning of the word plasticity?") == ["plasticity"]
    assert vocab_requests("What do you mean by 'extinction'?") == ["extinction"]


def test_observe_saves_words_the_learner_asked_about(store):
    tutor = Tutor(store, FakeLLM())
    session = store.create_session(None, None)
    feedback = asyncio.run(tutor.observe(session, "What does habituation mean?", "Habituation is ..."))
    assert [w["word"] for w in feedback["saved_words"]] == ["habituation"]
    again = asyncio.run(tutor.observe(session, "What does habituation mean?", "Habituation is ..."))
    assert again["saved_words"] == []   # already saved


def test_chat_flow_streams_cites_and_records(store):
    course, topic = _course_with_material(store)
    store.add_memory("preference", "Prefers examples from football.", "user")
    llm = FakeLLM(observation={
        "corrections": [{"wrong": "is mean", "right": "means", "rule": "verb form", "category": "grammar",
                         "explanation": "Use the verb 'means'."}],
        "better_phrasing": "What classical conditioning means, with Pavlov's dogs?",
        "vocabulary": [], "memories": []})
    tutor = Tutor(store, llm)
    session = store.create_session(course["id"], topic["id"])

    events = _run(tutor.chat(session["id"], "What classical conditioning is mean with Pavlov dogs?"))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "user" and kinds[-2:] == ["reply_done", "feedback"] and "token" in kinds
    done = events[-2]
    assert done["sources"][0]["cited"] and done["unknown_citations"] == []
    assert events[-1]["feedback"]["corrections"][0]["right"] == "means"

    system = llm.chat_requests[0][0]["content"]
    assert "Pavlov" in system and "football" in system and "[S1]" in system
    saved = store.messages(session["id"])
    assert [m["role"] for m in saved] == ["user", "assistant"] and saved[0]["feedback"]


def test_arabic_question_uses_model_keywords_to_find_english_passages(store):
    course, topic = _course_with_material(store)
    llm = FakeLLM(keywords=["classical conditioning", "Pavlov"])
    tutor = Tutor(store, llm)
    session = store.create_session(course["id"], topic["id"])
    events = _run(tutor.chat(session["id"], "ما هو الإشراط الكلاسيكي؟"))
    assert events[-2]["sources"], "Arabic question should still retrieve the English passage"


def test_ollama_failures_during_a_request_become_model_errors():
    import httpx
    from tutor.llm import LLM

    class DroppedMidReply(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message": {"content": "Classical"}}\n'
            raise httpx.ReadError("connection reset")

    def ollama(request):
        if json.loads(request.content)["stream"]:
            return httpx.Response(200, stream=DroppedMidReply())
        raise httpx.ReadTimeout("", request=request)

    async def run():
        llm = LLM("http://ollama.test", "tutor", 2048)
        await llm.close()
        llm.client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(ollama))
        got = []
        try:
            with pytest.raises(ModelError, match="Ollama request failed: connection reset"):
                async for text in llm.stream_chat([{"role": "user", "content": "Hi"}]):
                    got.append(text)
            with pytest.raises(ModelError, match="Ollama request failed: ReadTimeout"):
                await llm.json_chat("Hi", {"type": "object"})
        finally:
            await llm.close()
        return got

    assert asyncio.run(run()) == ["Classical"]


def test_failed_reply_removes_the_unanswered_question(store):
    tutor = Tutor(store, FakeLLM(fail_stream=True))
    session = store.create_session(None, None)
    with pytest.raises(ModelError):
        _run(tutor.chat(session["id"], "Hello"))
    assert store.messages(session["id"]) == []


def test_reflection_never_retires_the_learners_own_memories(store):
    own = store.add_memory("goal", "Wants to read research papers in English.", "user")
    auto = store.add_memory("preference", "Prefers short answers.", "auto")
    llm = FakeLLM(reflection={"summary": "Good session.", "next_step": "Start with reinforcement.",
                              "new_memories": [{"kind": "preference", "content": "Likes detailed answers now."}],
                              "outdated_memory_ids": [own["id"], auto["id"], 999]})
    tutor = Tutor(store, llm)
    session = store.create_session(None, None)
    store.add_message(session["id"], "user", "Can you give longer answers?")
    store.add_message(session["id"], "assistant", "Sure.")
    result = asyncio.run(tutor.end_session(session["id"]))
    assert [m["id"] for m in result["retired_memories"]] == [auto["id"]]
    active = {m["content"] for m in store.list_memories(active_only=True)}
    assert "Wants to read research papers in English." in active
    assert "Likes detailed answers now." in active
    assert result["session"]["summary"] == "Good session."


def test_end_session_still_closes_when_the_model_fails(store):
    auto = store.add_memory("preference", "Prefers short answers.", "auto")
    tutor = Tutor(store, FakeLLM(fail_json=True))
    session = store.create_session(None, None)
    store.add_message(session["id"], "user", "Can you give longer answers?")
    store.add_message(session["id"], "assistant", "Sure.")
    result = asyncio.run(tutor.end_session(session["id"]))
    assert result["error"] and result["session"]["ended_at"] and result["session"]["summary"] is None
    assert result["retired_memories"] == [] and result["new_memories"] == []
    assert [m["id"] for m in store.list_memories(active_only=True)] == [auto["id"]]
    assert store.get_session(session["id"])["ended_at"]                    # closed, not left open


def test_due_words_are_offered_to_the_tutor(store):
    tutor = Tutor(store, FakeLLM())
    session = store.get_session(store.create_session(None, None)["id"])
    assert "# Words the learner is learning" not in tutor.system_prompt(session, [], False, 0)
    store.add_vocab("plasticity", "the brain's ability to change")             # a new word is due at once
    later, _ = store.add_vocab("habituation", "getting used to something")
    store.review_vocab(later["id"], "good")                                    # due tomorrow: not offered today
    prompt = tutor.system_prompt(session, [], False, 0)
    assert "# Words the learner is learning" in prompt and "- plasticity: the brain's ability to change" in prompt
    assert "habituation" not in prompt


# --- voice ------------------------------------------------------------------
def test_speech_text_is_cleaned_for_the_english_voice():
    assert clean_for_speech("**Extinction** (الانطفاء) happens [S1].") == "Extinction happens."
    assert clean_for_speech("*   **The Conflict:** your action") == "The Conflict: your action"
    assert clean_for_speech("مرحبا") == ""


def test_sentences_are_split_into_english_and_arabic_parts():
    assert split_languages("Cognitive means إدراكي, which is about thinking.") == [
        ("en", "Cognitive means"), ("ar", "إدراكي,"), ("en", "which is about thinking.")]
    assert split_languages(clean_markup("**Extinction** (الانطفاء) happens [S1].")) == [
        ("en", "Extinction"), ("ar", "الانطفاء"), ("en", "happens .")]
    # A lone Arabic question mark is not sent to the Arabic voice on its own
    assert split_languages("ما معنى كلمة cognitive؟") == [("ar", "ما معنى كلمة"), ("en", "cognitive")]


class _FakeArabic:
    def __init__(self, up=True):
        self.up = up
        self.said = []

    def available(self):
        return self.up

    def speak(self, text, speed):
        self.said.append(text)
        return np.full(2400, 0.1, dtype=np.float32), 24000


def _tts_with(arabic):
    tts = TextToSpeech(arabic=arabic)
    tts._english = lambda text, voice, speed: (np.zeros(2400, dtype=np.float32), 24000)
    return tts


def test_mixed_sentences_use_both_voices_in_order():
    arabic = _FakeArabic()
    samples, rate = _tts_with(arabic).synthesize("Cognitive means إدراكي, which is about thinking.", "af_heart", 1.0)
    assert rate == 24000 and arabic.said == ["إدراكي,"]
    assert len(samples) == 3 * 2400 + 2 * int(24000 * 0.08)
    assert samples[2400 + int(24000 * 0.08)] == np.float32(0.1)   # the Arabic part sits in the middle


def test_without_the_arabic_voice_english_is_still_spoken_and_arabic_only_says_why():
    tts = _tts_with(_FakeArabic(up=False))
    samples, _ = tts.synthesize("Cognitive means إدراكي, which is about thinking.", "af_heart", 1.0)
    assert len(samples) == 2 * 2400 + int(24000 * 0.08)
    with pytest.raises(VoiceError, match="Arabic"):
        tts.synthesize("مرحبا بك", "af_heart", 1.0)


def test_an_arabic_voice_failure_keeps_the_english_parts_and_pauses_the_arabic_voice():
    import httpx
    from tutor.voice import ArabicSpeech
    asked = []

    def worker(request):
        asked.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"loaded": True, "cuda": True, "device": "cuda"})
        return httpx.Response(503, json={"detail": "CUDA out of memory"})

    arabic = ArabicSpeech()
    arabic.client = httpx.Client(base_url=config.ARABIC_TTS_URL, transport=httpx.MockTransport(worker))
    tts = _tts_with(arabic)
    samples, _ = tts.synthesize("Cognitive means إدراكي, which is about thinking.", "af_heart", 1.0)
    assert len(samples) == 2 * 2400 + int(24000 * 0.08)                # both English parts, in order
    assert not arabic.available() and asked == ["/health", "/speak"]    # not asked again until the next check
    with pytest.raises(VoiceError, match="Arabic speech is not available"):
        tts.synthesize("مرحبا بك", "af_heart", 1.0)


def test_kokoro_runs_on_the_gpu_or_says_why(tmp_path, monkeypatch):
    import kokoro_onnx
    import onnxruntime as ort
    (tmp_path / "kokoro.onnx").write_bytes(b"x")
    (tmp_path / "voices.bin").write_bytes(b"x")
    made = []

    class Session:
        providers = ["CPUExecutionProvider"]   # what ONNX Runtime builds when the CUDA provider fails to load

        def __init__(self, path, sess_options=None, providers=None):
            made.append([p if isinstance(p, str) else p[0] for p in providers])
            self.fallback = True

        def disable_fallback(self):
            self.fallback = False

        def get_providers(self):
            return Session.providers

    class Kokoro:
        fail = None

        @classmethod
        def from_session(cls, session, voices):
            k = cls()
            k.session = session
            return k

        def create(self, *args, **kwargs):
            if Kokoro.fail:
                raise Kokoro.fail
            return np.zeros(10, dtype=np.float32), 24000

    monkeypatch.setattr(ort, "InferenceSession", Session)
    monkeypatch.setattr(ort, "preload_dlls", lambda *a, **k: None)
    monkeypatch.setattr(kokoro_onnx, "Kokoro", Kokoro)
    tts = TextToSpeech(tmp_path / "kokoro.onnx", tmp_path / "voices.bin", arabic=_FakeArabic())
    with pytest.raises(VoiceError, match="Kokoro could not start on the GPU"):
        tts.load()
    assert made == [["CUDAExecutionProvider"]] and not tts.loaded and tts.device is None   # never the CPU
    Session.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    Kokoro.fail = RuntimeError("CUDNN_STATUS_NOT_INITIALIZED")   # a CUDA library fails in the first real run
    with pytest.raises(VoiceError, match="on the GPU: CUDNN"):
        tts.load()
    Kokoro.fail = None
    tts.load()
    assert tts.loaded and tts.device == "gpu" and not tts._kokoro.session.fallback   # no switch to the CPU later
    Session.providers = ["CPUExecutionProvider"]
    assert tts.device == "cpu"                                    # status() reads it live, never a stale value


def test_wav_encoding_is_valid_16_bit_mono():
    data = wav_bytes(np.array([0.0, 0.5, -1.5], dtype=np.float32), 24000)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 24000, 3)


def test_voice_endpoints_use_session_terms_and_profile_voice(store, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    voice = FakeVoice()
    with TestClient(create_app(store, FakeLLM(), voice=voice), base_url=f"http://127.0.0.1:{config.PORT}") as c:
        course, topic = _course_with_material(store)
        session = store.create_session(course["id"], topic["id"])
        r = c.post("/api/voice/transcribe", files={"audio": ("speech.webm", b"x" * 100, "audio/webm")},
                   data={"session_id": str(session["id"])})
        assert r.json()["text"] == "I goes to the library"
        assert voice.hints == ["Intro Psychology. Classical conditioning"]

        c.put("/api/profile", json={"voice": "bf_emma", "speech_speed": "1.1"})
        r = c.post("/api/voice/speak", json={"text": "Hello there."})
        assert r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
        assert voice.spoken[-1] == ("Hello there.", "bf_emma", 1.1)
        assert c.put("/api/profile", json={"voice": "../../etc"}).status_code == 400


# --- pronunciation check ------------------------------------------------------
L2_VOCAB = {"ɑ": 0, "k": 1, "o": 2, "a": 3, "æ": 4, "ʃ": 5, "͡": 6, "ŋ": 7, "s": 8, "j": 9, "ʊ": 10, "u": 11, "p": 12,
            "e": 13, "b": 14, "ɛ": 15, "t": 16, "v": 17, "ɪ": 18, "d": 19, "f": 20, "[UNK]": 21, " ": 22, "ɹ": 23,
            "m": 24, "ʒ": 25, "ˌ": 26, "θ": 27, "ð": 28, "n": 29, "h": 30, "ʌ": 31, "ɚ": 32, "[PAD]": 33, "w": 34,
            "z": 35, "ɔ": 36, "ɡ": 37, "i": 38, "l": 39}


def _phone_model():
    from tutor.assessment import PhoneModel
    m = PhoneModel(config.MODELS_DIR / "pron" / "l2arctic")
    m.vocab, m.blank = L2_VOCAB, 33
    return m


def _said(spoken: str, vocab=L2_VOCAB, blank=33, frames_per=4) -> np.ndarray:
    """Frame log probabilities of a clear recording of `spoken` (characters of the model's vocabulary)."""
    rows = []
    for ch in [None] + list(spoken) + [None]:
        for f in range(frames_per):
            p = np.full(len(vocab), 1e-4)
            p[vocab[ch] if ch is not None and f < 2 else blank] = 1.0
            rows.append(np.log(p / p.sum()))
    return np.array(rows, dtype=np.float32)


def test_the_pronunciation_model_unloads_when_idle_and_needs_free_gpu_memory(tmp_path, monkeypatch):
    import threading
    import time
    from tutor import assessment
    m = _phone_model()
    m.idle_unload_s, m._session, m._last_used = 0.2, object(), time.monotonic()
    threading.Thread(target=m._unload_when_idle, daemon=True).start()
    time.sleep(0.8)
    assert not m.loaded and m.unloads == 1
    # not enough free GPU memory: a clear error, never the CPU
    folder = tmp_path / "l2arctic"
    folder.mkdir()
    (folder / "model_fp16.onnx").write_bytes(b"x" * 1024)
    monkeypatch.setattr(assessment, "free_gpu_mb", lambda: 200.0)
    with pytest.raises(VoiceError, match="Not enough free GPU memory"):
        assessment.PhoneModel(folder).load()


def test_ctc_likelihood_matches_summing_every_alignment():
    import itertools
    from tutor.assessment import ctc_loglik
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(5, 3))
    logp = logits - np.log(np.exp(logits).sum(1, keepdims=True))

    def collapse(path):
        out = [k for k, _ in itertools.groupby(path)]
        return [x for x in out if x != 0]

    for seq in ([1], [1, 2], [1, 1], []):
        brute = np.log(sum(np.exp(sum(logp[t, s] for t, s in enumerate(path)))
                           for path in itertools.product(range(3), repeat=5) if collapse(path) == seq))
        assert abs(ctc_loglik(logp, [seq], 0)[0] - brute) < 1e-6


def test_a_swapped_sound_is_found_and_a_clear_word_passes():
    from tutor.assessment import pronunciations, score
    m = _phone_model()
    good = score(_said("pɛn"), ["pen"], [pronunciations("pen")], m)
    assert good["issues"] == [] and good["words"][0]["status"] == "good" and good["score"] >= 90
    bad = score(_said("bɛn"), ["pen"], [pronunciations("pen")], m)
    issue = bad["issues"][0]
    assert (issue["phone"], issue["heard"], issue["status"]) == ("P", "B", "wrong") and "puff" in issue["tip"]
    assert bad["words"][0]["phones"][0]["heard_ipa"] == "b" and bad["score"] < good["score"]


def test_an_added_vowel_is_found_and_weak_forms_are_accepted():
    from tutor.assessment import pronunciations, score
    m = _phone_model()
    added = score(_said("sʌtɹit"), ["street"], [pronunciations("street")], m)
    assert added["words"][0]["added"] and added["issues"][0]["phone"] is None
    # "to" said with a weak vowel, "the" as /ðʌ/: natural speech, not an error
    weak = score(_said("tʌ ðʌ pɑɹk"), ["to", "the", "park"], [pronunciations(w) for w in ("to", "the", "park")], m)
    assert weak["issues"] == []


def test_pronunciations_cover_dictionary_weak_forms_and_unknown_words(monkeypatch):
    from tutor import assessment
    from tutor.assessment import pronunciations, trim_silence
    assert [("F", ""), ("ER", "0")] in pronunciations("for")
    assert all(s in ("", "0") for p in pronunciations("can") for _, s in p)       # grammar words may be reduced
    # A word CMU lacks gets eSpeak NG's pronunciation (stubbed: the real program is tested on its own below)
    guess = [("Z", ""), ("EH", "1"), ("M", ""), ("L", ""), ("IH", "0"), ("K", "")]
    asked = []
    monkeypatch.setattr(assessment, "_espeak", lambda word: asked.append(word) or guess)
    assert pronunciations("Zemlick") == [guess] and asked == ["Zemlick"]
    assert pronunciations("pen") and asked == ["Zemlick"]                          # dictionary words never ask it
    x = np.concatenate([np.zeros(16000), 0.3 * np.ones(8000), np.zeros(16000)]).astype(np.float32)
    assert 8000 <= len(trim_silence(x)) <= 8000 + 2 * 4000 + 640


@pytest.mark.skipif(not Path(config.ESPEAK).exists(), reason="eSpeak NG is not installed in tools\\espeak-ng")
def test_espeak_ng_pronounces_a_word_the_dictionary_lacks():
    from tutor.assessment import _espeak
    phones = _espeak("Zemlick")
    assert phones and phones[0] == ("Z", "") and ("EH", "1") in phones


def test_drill_words_are_in_the_dictionary_and_every_sound_has_a_tip():
    import pronouncing
    from tutor import drills
    for d in drills.DRILLS:
        for word in [w for pair in d["pairs"] for w in pair]:
            assert pronouncing.phones_for_word(word.lower()), word
        assert all(p in drills.HOW for p in d["sounds"])
    assert set(drills.HOW) == set(drills.IPA)
    assert drills.advice("V", "F").startswith("You said /f/")
    assert "added" in drills.advice(None, "AH") and "/g/" in drills.advice(None, "G")


def test_pronunciation_endpoints_check_save_and_rank_sounds(store, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    voice = FakeVoice()
    with TestClient(create_app(store, FakeLLM(), voice=voice), base_url=f"http://127.0.0.1:{config.PORT}") as c:
        drills = c.get("/api/pronunciation/drills").json()
        assert drills[0]["id"] == "p-b" and drills[0]["sounds"][0]["ipa"] == "p"
        assert c.get("/api/pronunciation/ipa", params={"text": "pen"}).json() == [{"text": "pen", "ipa": "/ˈpen/"}]
        for _ in range(5):
            r = c.post("/api/pronunciation/check", files={"audio": ("s.webm", b"x" * 100, "audio/webm")},
                       data={"text": "Pass the pen.", "drill": "p-b"}).json()
        assert r["id"] and r["issues"][0]["heard"] == "B"
        assert voice.checked[-1] == ("Pass the pen.", {"P", "B"})               # the drill's contrast is the focus
        progress = c.get("/api/pronunciation/progress").json()
        # the fake hears /b/ for the /p/ of the first word only: 5 of 15 wrong
        assert progress["sounds"][0]["phone"] == "P" and progress["sounds"][0]["accuracy"] == 0.667
        assert progress["sounds"][0]["instead_ipa"] == ["b"] and progress["sounds"][0]["drill"] == "p-b"
        assert c.get(f"/api/pronunciation/attempts/{r['id']}").json()["result"]["score"] == 70
        # the tutor learns the weakest sound
        tutor = Tutor(store, FakeLLM())
        session = store.create_session(None, None)
        assert "/p/: right 67% of 15 times, often said as /b/" in tutor.system_prompt(store.get_session(session["id"]), [], False, 0)


def _said_faintly(spoken: str, faint: int, level=0.005, vocab=L2_VOCAB, blank=33) -> np.ndarray:
    """As _said, with sound number `faint` barely there (its frames are mostly silence)."""
    logp = _said(spoken, vocab, blank)
    p = np.full(len(vocab), 1e-4)
    p[vocab[spoken[faint]]], p[blank] = level, 1.0
    logp[(faint + 1) * 4:(faint + 1) * 4 + 2] = np.log(p / p.sum())
    return logp


def test_an_added_vowel_before_an_ending_and_a_left_out_word_are_found():
    from tutor.assessment import pronunciations, score
    m = _phone_model()
    for word, wrong, right in (("played", "pleɪɪd", "pleɪd"), ("plays", "pleɪɪz", "pleɪz")):
        added = score(_said(wrong), [word], [pronunciations(word)], m)                       # "play-id"
        assert added["words"][0]["added"] and added["issues"][0]["phone"] is None, word
        assert score(_said(right), [word], [pronunciations(word)], m)["issues"] == [], word
    # "I have cat": a weak vowel may disappear, but not when it is the whole word ("a")
    words = ["I", "have", "a", "cat"]
    dropped = score(_said("aɪ hæv kæt"), words, [pronunciations(w) for w in words], m)
    assert dropped["words"][2]["status"] != "good" and [i["word"] for i in dropped["issues"]] == ["a"]
    assert score(_said("aɪ hæv ʌ kæt"), words, [pronunciations(w) for w in words], m)["issues"] == []


def test_the_american_flap_is_accepted_before_a_secondary_stress():
    from tutor.assessment import pronunciations, score
    assert pronunciations("photo")[0][-1] == ("OW", "2")
    assert score(_said("foʊdoʊ"), ["photo"], [pronunciations("photo")], _phone_model())["issues"] == []


def test_numbers_in_the_text_are_checked_as_words(monkeypatch):
    from tutor import assessment
    monkeypatch.setattr(assessment, "_espeak", lambda w: [("TH", ""), ("R", ""), ("IY", "1")] if w == "3" else None)
    out = assessment.expected_ipa("I have 3 cats")
    assert [w["text"] for w in out] == ["I", "have", "3", "cats"] and out[2]["ipa"]


def test_a_recording_without_speech_or_unreadable_is_the_learners_to_fix(tmp_path):
    from tutor.assessment import PhoneModel, PronunciationChecker
    from tutor.voice import SpeechToText
    checker = PronunciationChecker(PhoneModel(tmp_path / "l2arctic"))   # no model files: it can never load
    noise = np.random.default_rng(0).normal(0, 0.002, 32000).astype(np.float32)   # microphone noise, about -54 dBFS
    with pytest.raises(ValueError, match="could not hear any speech"):
        checker.check_array(noise, "pen")
    quiet = (0.02 * np.sin(np.arange(32000) * 0.1)).astype(np.float32)   # quiet speech level, about -37 dBFS
    with pytest.raises(VoiceError, match="missing"):                     # goes on to the model
        checker.check_array(quiet, "pen")
    # a broken upload is a bad request (400), not a voice failure (503)
    with pytest.raises(ValueError, match="Could not read the recording"):
        checker.check(b"not audio", "pen")
    with pytest.raises(ValueError, match="Could not read the recording"):
        SpeechToText().transcribe(b"not audio")


def test_pronunciation_check_rejects_bad_input(client, tmp_path, monkeypatch):
    from tutor import assessment
    from tutor.assessment import PhoneModel, PronunciationChecker
    # No model files: every refusal below must come before the model is needed (else: VoiceError, "missing")
    checker = PronunciationChecker(PhoneModel(tmp_path / "l2arctic"))
    speech = np.random.default_rng(0).normal(0, 0.1, 16000).astype(np.float32)
    with pytest.raises(ValueError, match="Type the word"):
        checker.check_array(speech, "")
    with pytest.raises(ValueError, match="one sentence at a time"):
        checker.check_array(speech, " ".join(["word"] * 41))
    with pytest.raises(ValueError, match="could not hear"):
        checker.check_array(np.zeros(1600, dtype=np.float32), "pen")
    with pytest.raises(ValueError, match="too long"):
        checker.check_array(np.random.default_rng(0).normal(0, 0.1, 21 * 16000).astype(np.float32), "pen")
    monkeypatch.setattr(assessment, "_espeak", lambda word: None)       # not in CMU, and eSpeak NG has nothing
    with pytest.raises(ValueError, match="do not know how to pronounce: Zqxv"):
        checker.check_array(speech, "Say Zqxv")
    assert not checker.model.loaded

    # Over HTTP a bad request is a 400, not a voice failure (503)
    audio = {"audio": ("s.webm", b"x" * 100, "audio/webm")}
    r = client.post("/api/pronunciation/check", files=audio, data={"text": "   "})
    assert r.status_code == 400 and r.json()["detail"].startswith("Type the word")
    # An empty form field is a missing one to FastAPI: refused before the check runs
    assert client.post("/api/pronunciation/check", files=audio, data={"text": ""}).status_code == 422


def test_gpu_failures_in_the_pronunciation_check_are_voice_errors(tmp_path, monkeypatch):
    from tutor import assessment
    m = _phone_model()

    class Session:
        def run(self, *args):
            raise RuntimeError("CUDA failure 2: out of memory")

    m._session, m._run_options = Session(), None
    with pytest.raises(VoiceError, match="failed on the GPU: CUDA failure"):
        m.log_probs(np.zeros(16000, dtype=np.float32))
    folder = tmp_path / "l2arctic"
    folder.mkdir()
    (folder / "model_fp16.onnx").write_bytes(b"x" * 1024)
    monkeypatch.setattr(assessment, "free_gpu_mb", lambda: 900.0)   # a 20 s recording needs more than this
    with pytest.raises(VoiceError, match="Not enough free GPU memory"):
        assessment.PhoneModel(folder).load()


def test_drills_judge_their_own_errors_strictly_and_say_which_word_it_sounded_like(store, tmp_path, monkeypatch):
    from tutor import drills
    from tutor.assessment import pronunciations, score
    focus = {d["id"]: set(d["focus"]) for d in drills.DRILLS}
    assert focus["th"] == {"TH", "DH", "S", "Z", "T", "D"} and focus["ng"] == {"NG", "N", "G"}
    assert focus["endings-ed"] == {"T", "D", None} and focus["clusters"] == set()
    assert "ح" in drills.HOW["HH"]
    # a final -s barely said: passed in free practice and in other drills, wrong in the -s drill
    faint_s = _said_faintly("kæts", 3)
    for drill_focus, status in ((set(), "good"), (focus["th"], "good"), (focus["endings-s"], "wrong")):
        r = score(faint_s, ["cats"], [pronunciations("cats")], _phone_model(), drill_focus)
        assert r["words"][0]["status"] == status, drill_focus

    def checked(text, at, heard):
        phones = [p for p, _ in pronunciations(text)[0]]
        return {"text": text, "phones": [{"phone": p, "status": "good"} if i != at else
                                         {"phone": p, "status": "wrong", "heard": heard} for i, p in enumerate(phones)]}

    for drill, text, at, heard, partner in (("th", "think", 0, "S", "sink"), ("th", "three", 0, "T", "tree"),
                                            ("th", "they", 0, "D", "day"), ("ng", "sing", 2, "N", "sin"),
                                            ("p-b", "pin", 0, "B", "bin"), ("p-b", "pin", 0, "F", None),
                                            ("endings-s", "cats", 3, None, None)):
        assert drills.sounded_like(drills.DRILL_BY_ID[drill], focus[drill], checked(text, at, heard)) == partner

    # the server uses the drill's focus and gives the hint
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    voice, focuses = FakeVoice(), []

    def check(audio, text, focus=frozenset()):
        focuses.append(set(focus))
        word = {**checked(text, 0, "S"), "added": [], "score": 60, "status": "wrong"}
        return {"text": text, "score": 60, "duration": 1.0, "words": [word], "issues": []}

    voice.pron.check = check
    with TestClient(create_app(store, FakeLLM(), voice=voice), base_url=f"http://127.0.0.1:{config.PORT}") as c:
        audio = {"audio": ("s.webm", b"x" * 100, "audio/webm")}
        r = c.post("/api/pronunciation/check", files=audio, data={"text": "think", "drill": "th"}).json()
        assert r["sounded_like"] == "sink" and focuses[-1] == focus["th"]
        r = c.post("/api/pronunciation/check", files=audio, data={"text": "think"}).json()   # free practice
        assert "sounded_like" not in r and focuses[-1] == set()


# --- server -----------------------------------------------------------------
@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")
    app = create_app(store, FakeLLM(), voice=FakeVoice())
    with TestClient(app, base_url=f"http://127.0.0.1:{config.PORT}") as c:
        yield c


def test_the_page_and_its_modules_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and '<script type="module" src="/static/js/main.js">' in page.text
    main = client.get("/static/js/main.js")
    assert main.status_code == 200 and main.headers["content-type"].startswith("text/javascript")
    assert main.headers["cache-control"] == "no-cache"
    # Every module the page imports exists (a missing one would leave the page blank)
    modules = list(config.WEB_DIR.glob("js/**/*.js"))
    assert len(modules) >= 14
    for path in modules:
        source = path.read_text(encoding="utf-8")
        for target in re.findall(r'^import [^;]*? from "(\.[^"]+)";', source, flags=re.M | re.S):
            assert (path.parent / target).resolve().is_file(), f"{path.name} imports missing {target}"


def test_server_rejects_other_hosts_and_origins(client):
    assert client.get("/api/profile").status_code == 200
    assert client.get("/api/profile", headers={"host": "evil.example"}).status_code == 403
    r = client.post("/api/courses", json={"name": "X"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/courses", json={"name": "X"},
                       headers={"origin": f"http://127.0.0.1:{config.PORT}"}).status_code == 200


def test_cross_site_fetches_are_rejected(client):
    # A browser marks a request another site made (even one sent without an Origin header)
    r = client.post("/api/courses", json={"name": "X"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and client.get("/api/courses").json() == []
    assert client.post("/api/courses", json={"name": "X"},
                       headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_status_endpoints_report_model_and_voice_engines(client, monkeypatch):
    import httpx
    from tutor.llm import LLM
    from tutor.voice import Voice
    # The fakes must answer with the real key sets, so the page is tested against what it really gets
    real_voice = Voice()                                          # cheap: no model is loaded
    monkeypatch.setattr(real_voice.tts.arabic, "available", lambda: False)   # never asks the Arabic worker
    voice = client.get("/api/voice/status").json()
    assert set(voice) == set(real_voice.status())
    assert set(voice["pronunciation"]) == set(real_voice.status()["pronunciation"]) == {"available", "loaded", "model"}

    def ollama(request):
        model = {"name": "tutor:latest", "size": 10_000_000_000, "size_vram": 10_000_000_000}
        return httpx.Response(200, json={"/api/version": {"version": "0.12"}, "/api/tags": {"models": [model]},
                                         "/api/ps": {"models": [model]}}[request.url.path])

    async def real_status():
        llm = LLM("http://ollama.test", "tutor", 2048)
        await llm.close()
        llm.client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(ollama))
        try:
            return await llm.status()
        finally:
            await llm.close()

    status = client.get("/api/status").json()
    assert {"model", "vram_gb"} <= set(status) and set(status) == set(asyncio.run(real_status()))


def test_upload_reuses_an_identical_file_already_in_the_course_folder(client, tmp_path):
    # A fresh database loading the materials that ship with the project: the course folder already holds the file
    folder = tmp_path / "materials" / "1-Psych 101"
    folder.mkdir(parents=True)
    (folder / "chapter 1.txt").write_bytes(PASSAGE_TEXT.encode())
    (folder / "chapter 2.txt").write_bytes(b"An older, different file with the same name.")
    course = client.post("/api/courses", json={"name": "Psych 101"}).json()
    assert course["id"] == 1
    files = [("files", ("chapter 1.txt", PASSAGE_TEXT.encode(), "text/plain")),
             ("files", ("chapter 2.txt", PASSAGE_TEXT.encode() + b" More.", "text/plain"))]
    results = client.post(f"/api/courses/{course['id']}/documents", files=files).json()
    assert all(r["ok"] for r in results)
    assert sorted(p.name for p in folder.iterdir()) == ["chapter 1.txt", "chapter 2 (1).txt", "chapter 2.txt"]
    # Deleting the document removes the file it used, as for any upload
    client.delete(f"/api/documents/{results[0]['document']['id']}").raise_for_status()
    assert not (folder / "chapter 1.txt").exists()


def test_upload_then_chat_over_http(client, tmp_path):
    course = client.post("/api/courses", json={"name": "Psych 101"}).json()
    files = [("files", ("chapter 1.txt", PASSAGE_TEXT.encode(), "text/plain")),
             ("files", ("notes.docx", b"x", "application/octet-stream"))]
    results = client.post(f"/api/courses/{course['id']}/documents", files=files).json()
    assert results[0]["ok"] and not results[1]["ok"]
    assert (tmp_path / "materials").exists()

    topic = client.post(f"/api/courses/{course['id']}/topics", json={"title": "Classical conditioning"}).json()
    session = client.post("/api/sessions", json={"course_id": course["id"], "topic_id": topic["id"]}).json()["session"]
    with client.stream("POST", f"/api/sessions/{session['id']}/messages",
                       json={"text": "Explain classical conditioning with Pavlov"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "feedback"
    assert client.get(f"/api/sessions/{session['id']}").json()["session"]["message_count"] == 2

    # Starting a new session closes and summarises the open one
    second = client.post("/api/sessions", json={"course_id": course["id"]}).json()
    assert second["closed"][0]["session"]["summary"] == "Studied conditioning."

    with pytest.raises(NotFound):
        client.app.state.store.get_session(9999)


def test_messages_to_an_ended_session_are_refused(client):
    store = client.app.state.store
    session = store.create_session(None, None)
    store.add_message(session["id"], "user", "hi")
    store.add_message(session["id"], "assistant", "hello")
    store.end_session(session["id"], "summary", None)
    r = client.post(f"/api/sessions/{session['id']}/messages", json={"text": "One more question"})
    assert r.status_code == 400 and "has ended" in r.json()["detail"]
    assert len(store.messages(session["id"])) == 2


def _ended_chat(store: Store, course_id=None, topic_id=None, text="Hello", summary="Studied.") -> int:
    session = store.create_session(course_id, topic_id)
    store.add_message(session["id"], "user", text)
    store.add_message(session["id"], "assistant", "Hello! Let's begin.")
    store.end_session(session["id"], summary, None)
    return session["id"]


def test_chats_are_titled_by_the_learner_else_the_topic_else_the_first_message(client):
    store = client.app.state.store
    course = store.create_course("Psych 101")
    topic = store.add_topic(course["id"], "Classical conditioning")
    on_topic = _ended_chat(store, course["id"], topic["id"])
    long = "  What is the   difference between " + "classical and operant conditioning, " * 3
    free = _ended_chat(store, text=long)
    short = _ended_chat(store, text="Hi there")
    nothing_yet = store.create_session(None, None)["id"]

    titles = {s["id"]: s["title"] for s in client.get("/api/sessions").json()}
    assert titles[on_topic] == "Classical conditioning" and titles[short] == "Hi there"
    assert titles[nothing_yet] is None
    assert len(titles[free]) == 60 and titles[free].startswith("What is the difference between classical")
    assert titles[free].endswith("…")
    assert client.get(f"/api/sessions/{free}").json()["session"]["title"] == titles[free]

    # The learner's own title wins, in the list and for one session
    r = client.patch(f"/api/sessions/{on_topic}", json={"title": "  Pavlov's   dogs "})
    assert r.status_code == 200 and r.json()["title"] == "Pavlov's dogs"
    assert client.get(f"/api/sessions/{on_topic}").json()["session"]["title"] == "Pavlov's dogs"
    assert next(s for s in client.get("/api/sessions").json() if s["id"] == on_topic)["title"] == "Pavlov's dogs"
    assert client.patch(f"/api/sessions/{on_topic}", json={"title": "   "}).status_code == 400
    assert client.patch(f"/api/sessions/{on_topic}", json={"title": "x" * 121}).status_code == 400
    assert client.patch(f"/api/sessions/{on_topic}", json={"title": "x" * 120}).status_code == 200
    assert client.patch("/api/sessions/9999", json={"title": "Lost"}).status_code == 404


def test_an_ended_chat_is_deleted_with_its_messages_but_the_open_one_is_not(client):
    store = client.app.state.store
    old = _ended_chat(store, text="I goed to school")
    memory = store.add_memory("goal", "Wants to pass IELTS.", "auto", old)
    store.log_mistake("past tense of go", "grammar", "", "goed", "went", old)
    current = store.create_session(None, None)

    r = client.delete(f"/api/sessions/{current['id']}")
    assert r.status_code == 400 and "open" in r.json()["detail"]
    assert client.delete(f"/api/sessions/{old}").json() == {"ok": True}
    assert client.get(f"/api/sessions/{old}").status_code == 404
    assert store._all("SELECT id FROM messages WHERE session_id = ?", (old,)) == []
    # What was learned in the chat stays
    assert [m["id"] for m in store.list_memories()] == [memory["id"]]
    assert store.list_mistakes()[0]["count"] == 1
    assert [s["id"] for s in client.get("/api/sessions").json()] == [current["id"]]
    assert client.delete(f"/api/sessions/{old}").status_code == 404


def test_an_ended_chat_can_be_continued_after_the_open_one_is_ended(client):
    store = client.app.state.store
    past = _ended_chat(store, summary="Studied memory.")
    current = store.create_session(None, None)
    store.add_message(current["id"], "user", "What is extinction?")
    store.add_message(current["id"], "assistant", "Extinction is ...")

    r = client.post(f"/api/sessions/{past}/reopen")
    assert r.status_code == 200
    reopened = r.json()
    assert reopened["id"] == past and reopened["ended_at"] is None and reopened["summary"] == "Studied memory."
    # The open chat was ended the normal way: summarised by the (fake) model
    assert store.get_session(current["id"])["summary"] == "Studied conditioning."
    assert [s["id"] for s in store.open_sessions()] == [past]

    assert client.post(f"/api/sessions/{past}/reopen").status_code == 400      # it is open now
    assert client.post("/api/sessions/9999/reopen").status_code == 404
    with client.stream("POST", f"/api/sessions/{past}/messages", json={"text": "Tell me more"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "feedback" and store.get_session(past)["message_count"] == 4


def test_a_database_from_before_chat_titles_gets_the_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path)) as old:
        old.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY, course_id INTEGER, topic_id INTEGER, "
                    "started_at TEXT NOT NULL, ended_at TEXT, summary TEXT, next_step TEXT)")
        old.execute("INSERT INTO sessions(started_at, ended_at, summary) VALUES (?, ?, ?)",
                    ("2025-01-01T10:00:00+00:00", "2025-01-01T11:00:00+00:00", "Studied."))
        old.commit()
    store = Store(path)
    session = store.list_sessions()[0]
    assert session["summary"] == "Studied." and session["title"] is None
    assert store.rename_session(session["id"], "My first chat")["title"] == "My first chat"
    Store(path)   # opening it again finds the column there


def test_suggested_topics_skip_existing_titles_and_need_material(client):
    store = client.app.state.store
    course = store.create_course("Intro Psychology")
    store.add_document(course["id"], "ch1.txt", "ch1.txt", "sha-1", None, [(None, PASSAGE_TEXT), (None, OTHER_TEXT)])
    store.add_topic(course["id"], "classical conditioning")
    r = client.post(f"/api/courses/{course['id']}/topics/suggest")
    # The fake model suggests "Classical conditioning" and "Operant conditioning": the first is already a topic
    assert r.json() == {"topics": ["Operant conditioning"]}
    empty = client.post("/api/courses", json={"name": "No material yet"}).json()
    r = client.post(f"/api/courses/{empty['id']}/topics/suggest")
    assert r.status_code == 400 and "Add material" in r.json()["detail"]


def test_deleting_a_course_removes_only_files_inside_materials(client, tmp_path):
    store = client.app.state.store
    course = client.post("/api/courses", json={"name": "Psych 101"}).json()
    files = [("files", ("chapter 1.txt", PASSAGE_TEXT.encode(), "text/plain"))]
    doc = client.post(f"/api/courses/{course['id']}/documents", files=files).json()[0]["document"]
    stored = Path(doc["stored_path"])
    assert stored.exists() and (tmp_path / "materials") in stored.parents
    # Documents whose stored path points outside the materials folder (a hand-edited or old database)
    outside = tmp_path / "elsewhere" / "notes.txt"
    outside.parent.mkdir()
    outside.write_text(OTHER_TEXT, encoding="utf-8")
    store.add_document(course["id"], "notes.txt", str(outside), "sha-outside", None, [(None, OTHER_TEXT)])
    store.add_document(course["id"], "climb.txt", "../elsewhere/notes.txt", "sha-climb", None, [(None, "x")])

    assert client.delete(f"/api/courses/{course['id']}").json() == {"ok": True}
    assert not stored.exists() and outside.exists()
    assert store.list_documents(course["id"]) == []


def test_backup_writes_a_readable_copy(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")   # first: never the learner's data\backups
    client.post("/api/courses", json={"name": "Psych 101"})
    path = Path(client.post("/api/backup").json()["path"])
    assert path.parent == tmp_path / "backups" and path.exists()
    with closing(sqlite3.connect(path)) as copy:
        assert copy.execute("SELECT name FROM courses").fetchall() == [("Psych 101",)]


def _pdf(*pages: str | None) -> bytes:
    """A PDF with one page per item: a line of text, or None for a blank page."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    for text in pages:
        page = writer.add_blank_page(300, 300)
        if text is None:
            continue
        page[NameObject("/Resources")] = resources
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 20 150 Td (" + text.encode() + b") Tj ET")
        page.replace_contents(content)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _pdf_with_a_blank_last_page() -> bytes:
    return _pdf("Pavlov rang a bell before feeding the dogs.", None)


def test_a_pdf_keeps_its_real_page_count(client):
    course = client.post("/api/courses", json={"name": "Psych 101"}).json()
    files = [("files", ("slides.pdf", _pdf_with_a_blank_last_page(), "application/pdf"))]
    result = client.post(f"/api/courses/{course['id']}/documents", files=files).json()[0]
    assert result["ok"] and result["document"]["pages"] == 2 and result["document"]["passages"] == 1


def test_pdf_pages_keep_numbers_and_empty_pdfs_are_rejected():
    data = _pdf(None, "Pavlov rang a bell.", None, "The dogs salivated.")
    pages, page_count = materials.extract_pages("Slides.PDF", data)
    assert pages == [(2, "Pavlov rang a bell."), (4, "The dogs salivated.")] and page_count == 4
    assert {page for page, _ in materials.chunk_pages(pages)} == {2, 4}
    with pytest.raises(materials.EmptyDocument, match="No text found"):     # a scan without OCR
        materials.extract_pages("scan.pdf", _pdf(None))


def test_todays_words_join_when_the_app_opens(client):
    store = client.app.state.store
    store.save_word_list("test-list", "Test list", "", "", [{"word": "hypothesis", "meaning": "an idea to test"}])
    assert client.get("/api/sessions").status_code == 200
    assert [v["word"] for v in store.list_vocab()] == ["hypothesis"]


def test_adding_a_word_twice_is_refused_without_counting_it(client):
    assert client.post("/api/vocab", json={"word": "plasticity", "meaning": "change"}).status_code == 200
    assert client.post("/api/vocab", json={"word": "Plasticity", "meaning": "change"}).status_code == 409
    assert client.app.state.store.list_vocab()[0]["seen_count"] == 1
