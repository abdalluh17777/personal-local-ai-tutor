# Lesson guide specification (language courses)

One printable guide per lesson, for an Arabic-speaking adult at B1 (intermediate) who studies ALONE with a local AI tutor (no classmates, no teacher). The guide is printed (A4, 3-6 pages) and used together with the lesson's full material, which is printed separately from the source sections.

## Hard rules
- **Ground the lesson in its source sections.** Texts, examples, word lists, rules and exercises come from the source files listed for the lesson. You may add only: (a) correct answers to the language exercises, (b) clearer B1 wording of the book's rules, (c) the optional "Note for Arabic speakers" (well-established facts only, at most 2 bullets).
- **Verify every syllable-count and word-stress answer** with the CMU dictionary checker before writing it:
  `powershell -Command ". .\scripts\env.ps1; uv run --with pronouncing python scripts\check_pronunciation.py word1 word2 ..."`
  If the dictionary and the book disagree, follow the book and add "(dictionaries differ)". Words not in the dictionary: use your best judgement and add "(check)".
- **Self-study, not classroom:** turn "with a partner", "with your group", "share with the class", "submit to your teacher" into tasks with the AI tutor or alone (record yourself with a phone and listen back).
- **English only.** No Arabic script. No emoji. Plain B1 English: short sentences, common words.
- Do not reproduce long reading passages or transcripts: the learner has them in the printed lesson material. Refer to them by title.
- Ignore image placeholders that carry no information (e.g. "[Image: Screen Shot ... .png]"). If a task depends on pictures, describe what to do in words, or give an alternative.
- Length: about 900 to 1,600 words. Do not add a notes section (the print builder adds one).

## Notation
- Syllables: separate with a middle dot or hyphen: ba·na·na. Stress: CAPITALS for the stressed syllable: ba·NA·na.
- Sounds: slashes with one common example word: /θ/ as in "think".
- Intonation: arrows after the sentence: Where do you live? (↘)  Are you a student? (↗)

## File
- Write Markdown to `materials\library\esl-stage1\guides\L<NN>-<slug>.md` (NN = the lesson number given to you, two digits; slug = lowercase words of the title joined by hyphens, at most 50 characters).
- Use exactly these headings, in this order. Leave out a section marked (if relevant) when it does not apply.

## Template

```
# Lesson <NN>: <Lesson title>

*English Stage 1 (B1) · <Unit title>*
*Adapted from <Book title> by <author> (<license>): <source section titles, separated by "; ">.*

## In this lesson
2-3 sentences: what the learner will do and learn.

## Learning goals
After this lesson you should be able to:
- 3 to 5 goals, each starting with a verb.

## Key language
| Word or phrase | Meaning in simple English | Example |
|---|---|---|
8 to 12 rows from the lesson (for pronunciation lessons: the key terms, e.g. "syllable", "stress", plus example words).

## Rules and explanations
The lesson's grammar, pronunciation, reading or writing points, explained clearly with the book's examples. Use short subsections (###) and bullet points.

> **Note for Arabic speakers** (if relevant): 1-2 bullets about a well-known difficulty directly related to this lesson (for example /p/ and /b/, /f/ and /v/, short vowels, consonant clusters, word order in questions).

## Practice
Numbered exercises adapted from the book for self-study (keep the book's words and sentences). For each video task, give the link and say what to do. Include the questions of the book's interactive exercises (without answers here).

## Answer key
Answers to the Practice exercises, numbered to match. Mark "(book)" when the answer comes from the source (interactive exercise answers, instructor keys). Give a short model answer for open tasks and mark it "(example answer)".

## Speak with your tutor
3 to 6 things to say to the AI tutor in Live conversation, specific to this lesson (role-plays, interview practice, retelling a video, describing a place, using the lesson's language), e.g.
- "Interview me about my job. Ask one question at a time and correct my questions if they are wrong."

## Read aloud (if relevant)
5 to 10 words, phrases or sentences from the lesson to read aloud, with stress or intonation marked.

## Writing task (if relevant)
The book's writing assignment as a clear prompt with its requirements (length, parts, grammar to use).

## Watch online (if relevant)
- <short description>: <URL>
```
