# Lesson guide specification: English Stage 4 (C1-C2), English for Psychology

One printable guide per lesson, for an Arabic-speaking adult who has finished three English stages (up to C1) and now uses English to study psychology at university level. They study ALONE with a local AI tutor, and they also study Yale's PSYC 110 (Introduction to Psychology), which is loaded in the tutor. Each lesson is a section of an open university textbook, "Research Methods in Psychology" (Jhangiani, Chiang, Cuttler and Leighton). The guide teaches two things at once: the psychology (research methods) and the academic English used to read and write it. It is printed (A4, 4-8 pages) with the lesson's material (the book sections, printed separately). The tutor has the full material.

## Sources
- The lesson's entry in `materials\library\esl-stage4\syllabus.json` (lessons numbered in order through the units, from 1) lists `sources` (paths relative to `materials\library\`). A source ending in `#Method..Discussion` means only the part of the file from the heading that starts with "Method" to the end of the heading that starts with "Discussion".
- The book's glossary: `materials\library\psych-research-methods\sections\14.03-glossary.txt` (term | definition). Use its definitions for the lesson's terms.
- Also read, when it exists for a source `<book>/sections/<stem>.txt`: `<book>/links/<stem>.txt` (videos and articles linked on the book page).
- PSYC 110 lecture transcripts: `materials\library\psyc-110\transcripts\` (titles in `psyc-110\course.json`). Mention a lecture only when you have checked in its transcript that it discusses the point (for example a famous study).
- The section files show the book's formatting: **bold** (often key terms), *italic*, numbered lists. `[Image: ...]` lines are pictures or figures (if a figure carries data the lesson needs, describe it from the text or leave it out).

## Hard rules
- **Ground the lesson in its sources.** Explanations, examples, studies and exercises come from the source files. You may add only: (a) answers, (b) clearer wording, (c) the "Language focus" (see the template), built from sentences in the sources, (d) the optional "Note for Arabic speakers" (at most 2 bullets), (e) the research-project writing task.
- **The book has no answer key.** Every answer is yours: check each one twice against the text. Comprehension answers must be supported by the source (quote or point to the paragraph). Open questions (the book's "Discussion" items, opinions, designs) get a model answer marked "(example answer)"; where several answers are right, say so.
- **Numbers must be exact.** For statistics lessons, compute every number (means, standard deviations, correlations, t, p, df) with Python and check it; give formulas and APA-style reporting exactly as the book shows (for example: t(28) = 2.10, p = .045). If you invent practice data, label it "invented data for practice".
- **Self-study, not classroom:** turn pair, group and class activities into tasks with the AI tutor or alone. Studies that need participants become planning tasks (the learner designs, does not run, a study).
- **English only.** No Arabic script. No emoji. C1-C2 academic English; define technical terms on first use (the glossary helps).
- Do not reproduce long passages: the learner has the material. Refer to sections and studies by name.
- Length: about 1,300 to 2,400 words (the checker allows up to 2,600). No notes section (the print builder adds one).

## The research project (writing tasks across the stage)
The learner develops one research proposal through the stage: by default "sleep and memory in university students" (started in Stage 3), or a topic of their own. Each lesson's writing task adds one part, in APA style where relevant: topic and literature ideas (L04), research question and hypothesis (L05), design outline (L06), informed consent and ethics (L07-L09), operational definitions and measures (L10-L12), the choice of design and its validity (L13-L16), alternatives: correlational, qualitative, observational, survey items (L17-L22), quasi-experimental, factorial or single-subject options (L23-L26), a results paragraph with invented practice data (L27-L32), APA citations and references (L33), the introduction (L34), the method, planned results and discussion (L35), a poster abstract and a five-minute talk (L36), the complete proposal (L37). Each task says what to do if the learner works on their own topic.

## File
- Write Markdown to `materials\library\esl-stage4\guides\L<NN>-<slug>.md` (NN = the lesson number, two digits; slug = lowercase words of the lesson title joined by hyphens, cut at a word boundary to at most 50 characters).
- Use exactly these headings, in this order. Leave out a section marked (if relevant) when it does not apply.

## Template

```
# Lesson <NN>: <Lesson title>

*English Stage 4 (Psychology) · <Unit title>*
*Adapted from <Book title> by <authors> (<license>): <source section titles, separated by "; ">.*

## In this lesson
2-3 sentences: the psychology the learner will understand and the English they will practise.

## Learning goals
After this lesson you should be able to:
- 3 to 5 goals, each starting with a verb (content and language goals).

## Key language
| Term or phrase | Meaning | Example |
|---|---|---|
10 to 16 rows: the lesson's research terms with the glossary's definitions (shortened if needed), and 2-4 useful academic phrases from the text (for example "it is important to note that", "this suggests that").

## Rules and explanations
### The ideas
The lesson's concepts, clearly explained with the book's examples and studies (short subsections, bullets, small tables).
### Language focus
One or two features of academic English that the section uses, with examples quoted from it: for example hedging in conclusions, the passive voice and past tense in method descriptions, present tense for established findings, nominalisation, defining a term, reporting verbs, comparing and contrasting, cause and effect language, reporting statistics in APA style.

> **Note for Arabic speakers** (if relevant): 1-2 bullets on a well-known difficulty related to this lesson's English.

## Practice
1. Reading questions on the section (5-10), answerable from the text.
2. The book's exercises ("Practice" and "Discussion" items) that fit self-study (keep the book's words).
3. A language exercise from sentences in the text (for example: identify hedges, rewrite in the passive, turn a description into a definition, report a result in APA style).

## Answer key
Answers to every Practice item, numbered to match; "(example answer)" for open items.

## Speak with your tutor
3 to 6 things to say to the AI tutor, specific to this lesson: explain a concept in your own words and ask for correction, discuss an ethics case, critique a study design, defend your research proposal, connect the lesson to a PSYC 110 lecture.

## Writing task
The research-project step for this lesson (see above): what to write, length, required parts and language, a self-editing checklist (5-8 checks), then: "Paste your draft into the chat and ask your tutor for feedback. Then rewrite it."

## Watch online (if relevant)
- <short description>: <URL>
```
