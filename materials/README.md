# Course materials

Everything in this folder is study material for the tutor, and it is free for anyone to use: read the guides, print the PDFs, or load the courses into your own Personal Tutor. The installer loads them for you.

> **Licensing:** these materials are **not** covered by the project's MIT license. Each one keeps the Creative Commons license of its source, listed below. Several are **NonCommercial (NC)**: you may use, share and adapt them for non-commercial purposes, with attribution, and, for **ShareAlike (SA)** sources, under the same license. The study guides, lesson plans and answer pages written for this project are adaptations of those sources and are shared under the same terms as their source.

## What's here

| Folder | Contents |
|---|---|
| `library/<course>/guides/` | Study guide for every lesson (Markdown): objectives, key ideas, vocabulary, questions, a separate answer page, English practice, and what to say to the tutor |
| `library/<course>/material/` | The lesson's source text, assembled from the books' sections, with exercises and answer keys |
| `library/<course>/print/` | Print-ready PDFs: a course overview with a weekly plan, plus a study guide and lesson material for every lesson |
| `library/<book>/sections/`, `attachments/` | The imported open textbooks, their audio recordings, answer sheets and slides |
| `library/psyc-110/` | Yale PSYC 110 lecture transcripts, original slides and study guides |
| `library/word-lists/`, `vocab-nawl/` | The two vocabulary lists (definitions plus example sentences from the books) |
| `library/*SPEC*.md` | The specifications the study guides were written to |
| `1-…` to `6-…` | The app's own copies of the files above, one folder per course (the number is the course's id in the database). A fresh install reuses them instead of copying the files again. |

## Sources, licenses and attribution

| Used in | Source | Author(s) | License |
|---|---|---|---|
| PSYC 110 | [Introduction to Psychology (PSYC 110), Open Yale Courses](https://oyc.yale.edu/introduction-psychology/psyc-110) | Paul Bloom, Yale University (Spring 2007) | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/) |
| English Stage 1 | [Speaking, Listening and Pronunciation Projects for ELLs: Intermediate Level](https://human.libretexts.org/Bookshelves/Languages/English_as_a_Second_Language) | Brittany Zemlick, Grossmont College | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| English Stage 1 | People and Places: An Intermediate Integrated-Skills Module for English Learners | Sara Ferguson, Grossmont College | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |
| English Stage 1 | Getting to Know You: An Intermediate Integrated-Skills Module for English Learners | Sara Ferguson, Grossmont College | CC BY-NC-SA |
| English Stage 2 | High-Intermediate Academic Grammar for ESL Students | Rebecca Al Haider, Reedley College | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| English Stage 2 | [Building Academic Writing Skills](https://harpercollege.pressbooks.pub/academic-writing-skills) | Lin Cui, Harper College | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |
| English Stage 3 | College ESL Writers: Applied Grammar and Composing Strategies for Success | Barbara Hall and Elizabeth Wallace, Georgia State University, Perimeter College | CC BY-NC-SA |
| English Stage 3 | Reading, Writing, Research, and Reasoning: An Advanced ESL Text | Gabriel Winer and Elizabeth Wadell, ASCCC Open Educational Resources Initiative | [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) |
| English Stage 4, word list | Research Methods in Psychology, 4th American edition | Rajiv S. Jhangiani, I-Chant A. Chiang, Carrie Cuttler and Dana C. Leighton | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) |
| Word list | [New Academic Word List 1.2](https://www.newgeneralservicelist.com/) | Charles Browne, Brent Culligan and Joseph Phillips (2013) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |

Every lesson file names its exact source sections, authors, license and original web page at the top of each section. The import scripts that fetched them are in `scripts/` (`import_oyc.py`, `import_libretexts.py`, `import_pressbooks.py`, `fetch_attachments.py`).

The study guides were checked against their sources: numbers and names automatically, statistics recomputed independently, and stress marks and syllables checked against the CMU Pronouncing Dictionary (`scripts/check_guides.py`). Where a book itself contained an error, the guide corrects it and says so.

If you are an author of one of these works and would like something changed, please open an issue.
