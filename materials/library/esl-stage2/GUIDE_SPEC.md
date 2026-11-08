# Lesson guide specification: English Stage 2 (B2), academic grammar and writing

One printable guide per lesson, for an Arabic-speaking adult who has finished Stage 1 (B1) and now works at B2. They study ALONE with a local AI tutor (no classmates, no teacher), alongside a university psychology course in English. The guide is printed (A4, 4-8 pages) and used together with the lesson's material, which is printed separately from the source sections (without answers). The AI tutor has the full material, including the book's answer keys.

## Sources
- The lesson's entry in `materials\library\esl-stage2\syllabus.json` (lessons are numbered in order through the units, starting at 1) lists:
  - `sources`: the book sections the lesson teaches (paths relative to `materials\library\`). A path ending in `#I-VI` means only the part of that file from the heading `# I.` to the end of the heading `# VI.` (text before `# I.` belongs to a part that starts at I). Teach only that part.
  - `key` (grammar book lessons only): the book's answer key for the chapter. Use it for the answers to the book's exercises and mark them "(book)". It is not printed with the material.
- Also read, when it exists for a source `<book>/sections/<stem>.txt`: `<book>/links/<stem>.txt` (links on the book page: the videos and talks used for listening).
- In the section files: `[Interactive exercise]` blocks are the book's online exercises with their correct answers: `Answer:` lines, answers in `[brackets]` (fill-in, highlight-the-word, choose-from-brackets), `Card: word -> meaning` (vocabulary cards with the book's definitions), `Writing task:` (an open writing box). `[Image: ...]` lines are pictures (ignore the ones that are only file names).
- Grammar-book listening pages: the questions are on the page; the video link is in the links file (use https://www.youtube.com/watch?v=ID or the ted.com link); the transcript (an "Appendix B" source) is printed at the end of the lesson material.

## Hard rules
- **Ground the lesson in its sources.** Explanations, examples, vocabulary, readings and exercises come from the source files. You may add only: (a) correct answers, (b) clearer wording of the book's rules, (c) the optional "Note for Arabic speakers" (well-established facts only, at most 2 bullets).
- **Answers must be correct.** The learner checks their own work with your key.
  - "(book)": the answer is the book's (answer key file, `Answer:` lines, `[bracketed]` answers).
  - No mark: an answer you worked out for an exercise the book gives no key for. Check every one twice against the rule the lesson teaches; if an item has more than one correct answer, give them all.
  - "(example answer)": a model answer for an open task (a paragraph, a discussion question).
  - If the book's own answer is wrong, give the correct answer and add "(the book's key says X; this is a mistake because ...)".
- **Lettered options keep the order of the source file.** The answer key uses letters (1. b, 2. c), so print the options as a), b), c) ... in exactly the order of the source file: the book's order, or, for online quizzes that store the correct option first, the fixed order the importer gives them. The printed lesson material uses the same order, so the letters match.
- **Self-study, not classroom:** turn pair work, group work, class discussion and "submit to your instructor" into tasks with the AI tutor or alone (write, record yourself with a phone, paste writing into the tutor's chat for feedback).
- **English only.** No Arabic script. No emoji. Clear B2 English: you may use grammar terms, but define each the first time.
- Do not reproduce long readings, model paragraphs or transcripts: the learner has them in the printed lesson material. Refer to them by their title.
- **Practice is a selection.** Include the book's main exercises for the lesson (instructions and items, at least one exercise per main point). An exercise with more than 15 items may be shortened to 10-15 items; say "More items: lesson material, Exercise N. Your tutor can check them." Every item in Practice has an answer in the Answer key.
- **Listening first:** for a listening lesson, the learner watches the video before reading the transcript. Say so.
- Length: about 1,200 to 2,200 words. Do not add a notes section (the print builder adds one).

## Notation
- Stress (when useful): CAPITALS for the stressed syllable, syllables separated with a middle dot: ac·a·DEM·ic. Check with `powershell -Command ". .\scripts\env.ps1; uv run --with pronouncing python scripts\check_pronunciation.py word1 word2"`.
- Sounds: slashes, e.g. -ed in "wanted" is /ɪd/.
- Blanks: `________` (underscores). Grammar forms: plain text with labels, e.g. "have/has + past participle".

## File
- Write Markdown to `materials\library\esl-stage2\guides\L<NN>-<slug>.md` (NN = the lesson number, two digits; slug = lowercase words of the lesson title joined by hyphens, cut at a word boundary to at most 50 characters).
- Use exactly these headings, in this order. Leave out a section marked (if relevant) when it does not apply.

## Template

```
# Lesson <NN>: <Lesson title>

*English Stage 2 (B2) · <Unit title>*
*Adapted from <Book title> by <author> (<license>): <source section titles, separated by "; ">.*

## In this lesson
2-3 sentences: what the learner will do and learn, and how it helps academic writing or study.

## Learning goals
After this lesson you should be able to:
- 3 to 5 goals, each starting with a verb.

## Key language
| Word or phrase | Meaning | Example |
|---|---|---|
8 to 14 rows: the lesson's grammar terms and its academic vocabulary (use the book's vocabulary cards and definitions when the unit has them).

## Rules and explanations
The lesson's grammar or writing points, with the book's examples. Use short subsections (###), bullet points, and small tables for forms (e.g. affirmative / negative / question). Include the book's lists of common mistakes when it has them.

> **Note for Arabic speakers** (if relevant): 1-2 bullets about a well-known difficulty directly related to this lesson (for example: no present perfect in Arabic, extra pronouns in relative clauses "the book that I read it", leaving out "be", articles, capital letters, run-on sentences joined with "and").

## Practice
Numbered exercises from the book for self-study (keep the book's words and sentences). For listening, give the link and the questions. For reading, give the questions and say which reading in the lesson material to read.

## Answer key
Answers to every Practice item, numbered to match, marked as described above.

## Speak with your tutor
3 to 6 things to say to the AI tutor in Live conversation or chat, specific to this lesson, e.g.
- "Ask me five questions about the reading 'The Story Behind Dreams'. Correct my verb tenses."
- "Here is my paragraph: [paste]. Check the topic sentence, the supporting ideas and the transitions, then list my grammar errors by type."
- "Check my answers to Exercise 8 in the lesson material."

## Read aloud (if relevant)
5 to 8 sentences from the lesson that practise its grammar (e.g. -ed endings, contractions, phrasal verb stress), with marks where useful.

## Writing task (if relevant; required for writing, review and grammar lessons)
The book's writing assignment (or, in a grammar lesson, a short paragraph that uses the grammar) as a clear prompt: topic, length, required parts and grammar, then a short self-editing checklist (5-8 checks), then: "Paste your draft into the chat and ask your tutor for feedback. Then rewrite it."

## Watch online (if relevant)
- <short description>: <URL>
```
