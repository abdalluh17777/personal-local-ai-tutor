# Lesson guide specification: English Stage 3 (C1), academic reading, research and writing

One printable guide per lesson, for an Arabic-speaking adult who has finished Stage 2 (B2) and now works at C1. They study ALONE with a local AI tutor (no classmates, no teacher, no college library), alongside a university psychology course in English (Yale's PSYC 110, Introduction to Psychology, which is also loaded in the tutor). The guide is printed (A4, 4-8 pages) and used together with the lesson's material, which is printed separately from the source sections (without answers). The AI tutor has the full material, including the book's answer keys.

## Sources
- The lesson's entry in `materials\library\esl-stage3\syllabus.json` (lessons are numbered in order through the units, starting at 1) lists:
  - `sources`: the book sections the lesson teaches (paths relative to `materials\library\`).
  - `key` (lessons from "Reading, Writing, Research, and Reasoning" only): the chapter's answer key. It covers only some exercises. Use it for those answers and mark them "(book)". It is not printed with the material.
- Also read, when they exist for a source `<book>/sections/<stem>.txt`: `<book>/links/<stem>.txt` (links on the book page: videos, articles, tools) and `<book>/attachments/<stem>.txt` (text of attached files, such as model papers and checklists).
- The section files show the book's formatting: **bold**, __underlined__, ~~crossed out~~, numbered items "1." with lettered options "  a.". `[Image: ...]` lines are pictures (ignore the ones that are only file names).

## Hard rules
- **Ground the lesson in its sources.** Explanations, examples, model texts and exercises come from the source files. You may add only: (a) correct answers, (b) clearer wording of the book's points, (c) the optional "Note for Arabic speakers" (well-established facts only, at most 2 bullets), (d) at most one short note where academic writing in psychology differs from the book (for example: psychology uses APA style, while the book's examples may use MLA).
- **Answers must be correct.** The learner checks their own work with your key.
  - "(book)": the answer is the book's (answer key file, `Answer:` lines, `[bracketed]` answers).
  - No mark: an answer you worked out for an exercise the book gives no key for. Check every one twice against what the lesson teaches; if more than one answer is right, give them all.
  - "(example answer)": a model answer for an open task (a paraphrase, a thesis, a paragraph, a discussion question). For paraphrases and summaries, make the model answer genuinely different in words and structure from the original, and keep its meaning exact.
  - If the book's own answer is wrong, give the correct answer and add "(the book's key says X; this is a mistake because ...)".
- **Lettered options keep the order of the source file**, so the guide's letters match the printed material.
- **Self-study, not classroom:** turn pair work, peer review, group discussion and "submit to your instructor" into tasks with the AI tutor (discussion, role-play, feedback on pasted writing) or alone. Peer review becomes: the learner reviews a model or their own draft with the book's checklist, then asks the tutor for a second review.
- **Research without a college library:** where the book sends students to a library database, offer free sources: Google Scholar, PubMed Central, ERIC, the Directory of Open Access Journals, and the free OpenStax "Psychology 2e" textbook.
- **Topics:** when a writing or research task lets the learner choose, suggest one or two psychology topics (for example from PSYC 110: memory, sleep and dreams, development, social influence, stress and coping) alongside the book's topics.
- **English only.** No Arabic script. No emoji. Clear C1 academic English; define technical terms the first time.
- Do not reproduce long readings, model essays or articles: the learner has them in the printed lesson material (or, for long attached articles, in the tutor). Refer to them by title.
- **Practice is a selection.** Include the book's main exercises for the lesson (at least one per main point). An exercise with more than 15 items may be shortened to 10-15 items; say "More items: lesson material, Exercise N. Your tutor can check them." Every item in Practice has an answer in the Answer key.
- Length: about 1,300 to 2,300 words (the checker allows up to 2,600). Do not add a notes section (the print builder adds one).

## Notation
- Stress (rarely needed): CAPITALS for the stressed syllable, syllables separated with a middle dot: ac·a·DEM·ic. Check with `powershell -Command ". .\scripts\env.ps1; uv run --with pronouncing python scripts\check_pronunciation.py word1 word2"`.
- Blanks: `________` (underscores).

## File
- Write Markdown to `materials\library\esl-stage3\guides\L<NN>-<slug>.md` (NN = the lesson number, two digits; slug = lowercase words of the lesson title joined by hyphens, cut at a word boundary to at most 50 characters).
- Use exactly these headings, in this order. Leave out a section marked (if relevant) when it does not apply.

## Template

```
# Lesson <NN>: <Lesson title>

*English Stage 3 (C1) · <Unit title>*
*Adapted from <Book title> by <author> (<license>): <source section titles, separated by "; ">.*

## In this lesson
2-3 sentences: what the learner will do and learn, and how it helps university study.

## Learning goals
After this lesson you should be able to:
- 3 to 5 goals, each starting with a verb.

## Key language
| Word or phrase | Meaning | Example |
|---|---|---|
8 to 14 rows: the lesson's terms (thesis, claim, hedging, ethos...) and useful academic phrases from the sources (reporting verbs, transition words, hedges).

## Rules and explanations
The lesson's points, with the book's examples. Use short subsections (###), bullet points, and small tables (for example: types of evidence, citation patterns, punctuation rules).

> **Note for Arabic speakers** (if relevant): 1-2 bullets about a well-known difficulty related to this lesson (for example: very long sentences joined with "and", run-on sentences, articles, capital letters, direct translation of Arabic rhetorical style, copying source wording).

## Practice
Numbered exercises from the book for self-study (keep the book's words and sentences). For readings and model essays, say which text in the lesson material to read and give the questions.

## Answer key
Answers to every Practice item, numbered to match, marked as described above.

## Speak with your tutor
3 to 6 things to say to the AI tutor in Live conversation or chat, specific to this lesson, e.g.
- "Here is my paraphrase of the paragraph: [paste]. Is it too close to the original? Is the meaning exact?"
- "Take the opposite side on this claim and argue with me. After five turns, tell me which of my points were strongest."
- "Check my answers to Exercise 4 in the lesson material."

## Read aloud (if relevant)
5 to 8 sentences from the lesson (for example hedged claims, sentences with reporting verbs), with marks where useful.

## Writing task (required)
The book's writing assignment (or a short task that applies the lesson) as a clear prompt: topic choices, length, required parts, then a self-editing checklist (5-8 checks from the lesson), then: "Paste your draft into the chat and ask your tutor for feedback. Then rewrite it."

## Watch online (if relevant)
- <short description>: <URL>
```
