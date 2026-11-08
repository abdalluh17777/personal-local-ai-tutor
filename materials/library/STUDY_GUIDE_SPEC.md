# Study guide specification

One printable study guide per lecture, for an Arabic-speaking adult who studies the course in English with a local AI tutor. English level B1 (intermediate), growing towards B2. The guide is printed (A4, 4-5 pages), read before or after the lecture, and used in conversation with the AI tutor.

## Hard rules
- **Ground everything in the lecture transcript.** Every idea, example, study, name and number must come from the transcript (or, for lectures without a transcript, from the official overview). Do not add facts from outside knowledge. If the lecturer only mentions something briefly, keep it brief.
- **Spell names and terms exactly as in the transcript.**
- **English only.** No Arabic. No emoji.
- **Plain B1 English:** short sentences, common words; explain every technical term the first time it appears.
- **Neutral academic tone** for sensitive topics (sex, mental illness, morality). Teach the ideas; do not address the reader's personal life or feelings.
- Length: about 1,200 to 1,800 words. Do not include a notes section (the print builder adds one).

## File
- Write Markdown to `guides/<transcript file name>.md` (same stem as the transcript, e.g. `transcripts/04-foundations-skinner.txt` -> `guides/04-foundations-skinner.md`), UTF-8.
- Use exactly the section headings below, in this order.

## Template

```
# Lecture <N>: <Title as on the course site, without "Lecture N - ">

*<Course title> · Yale University, Spring 2007 · <Lecturer>*
*Study guide based on the lecture transcript. Source: <lecture URL> (CC BY-NC-SA 3.0).*

## Before you start
2-3 sentences: what this lecture is about and why it matters.

## Learning goals
After this lecture you should be able to:
- 3 to 5 goals, each starting with a verb (explain, describe, compare, give an example of...).

## Key ideas
### 1. <Chapter title, without the [hh:mm:ss] time>
3 to 6 sentences or short bullet points: the main points of this chapter, the examples the lecturer used, the studies or experiments he described.
### 2. ...
(one subsection per chapter; a "Question and Answer" chapter gets 1-2 sentences)

## Key vocabulary
| Term | Meaning in simple English | Example from the lecture |
|---|---|---|
10 to 14 rows: the lecture's technical terms plus a few useful academic words.

## Check your understanding
1. 8 questions, numbered: a mix of recall, explain, compare and apply. All answerable from the lecture.

## Answer guide
1. Short answers (1-3 sentences) to the questions above, in the same order.

## English practice
**Useful phrases from the lecture**
- "<phrase actually used in the lecture>" - what it means and when to use it. (4 to 6 phrases or sentence patterns, e.g. "It turns out that...", "The idea is that...")

**Speaking task (with your AI tutor)**
- 1 or 2 tasks: explain a key idea aloud in 4-6 sentences using 3 given terms.

**Writing task**
- One short paragraph to write (80-120 words), with a clear prompt.

## Study with your tutor
Things to say to your tutor in Live conversation:
- "Let's review Lecture <N>. Ask me questions about <key concept>, one at a time."
- "Quiz me on the key vocabulary of Lecture <N>."
- "I will explain <key concept> in my own words. Check my understanding and my English."
(3 to 5 lines, specific to this lecture)

## Readings from the original course
- The readings listed for this lecture in course.json (copy them as given). If none, write "No readings listed."
```

## Lectures without a transcript (guest lectures)
Open Yale Courses publishes only an overview for them. Write a short guide (400-600 words) from the overview only, and add this line right under the source line:
`*Note: Open Yale Courses published no transcript for this guest lecture. This short guide is based on the official overview only.*`
Keep "Key ideas" to what the overview says, use 5-6 vocabulary rows, 4 questions with answers, and keep the other sections short.
