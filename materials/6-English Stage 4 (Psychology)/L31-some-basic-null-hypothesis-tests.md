# Lesson 31: Some Basic Null Hypothesis Tests

*English Stage 4 (Psychology) · Unit 4: Statistics in Research Reports*
*Adapted from Research Methods in Psychology (4th American edition) by Rajiv S. Jhangiani, I-Chant A. Chiang, Carrie Cuttler and Dana C. Leighton (CC BY-NC-SA 4.0): 13.3 Some Basic Null Hypothesis Tests.*

## In this lesson
You will learn which null hypothesis test fits which design (three *t* tests, the ANOVA family and the test of Pearson's *r*), how to compute *t* and use tables of critical values, and how to report results in APA style. You will check the book's worked examples, which contain several errors, and write the inferential part of your results section.

## Learning goals
After this lesson you should be able to:
- Choose the right test for a design: one-sample, dependent-samples or independent-samples *t* test, one-way, repeated-measures or factorial ANOVA, or a test of *r*.
- Compute *t* and its degrees of freedom, and decide with a *p* value or a critical value, in a one-tailed or a two-tailed test.
- Explain why post hoc comparisons are needed after a significant ANOVA.
- Report *t*, *F* and *r* tests in APA style in words and numbers.

## Key language
| Term or phrase | Meaning | Example |
|---|---|---|
| *t* test | a family of null hypothesis tests used to compare two means | *t*(9) = −3.14, *p* = .01 |
| test statistic | a statistic such as *t* or *F* computed only to help find the *p* value | We know how *t* is distributed when the null hypothesis is true. |
| degrees of freedom (*df*) | a value that sets the exact shape of the distribution: *N* − 1 for a one-sample test | 10 − 1 = 9 degrees of freedom |
| critical values | the values of a test statistic that correspond to *p* = .05: the cutoff for rejecting the null hypothesis | ±2.262 for 9 *df*, two-tailed |
| one-tailed test | rejects the null hypothesis only if the result is extreme in one direction specified before the data are collected | expecting the training to increase estimates |
| dependent-samples *t* test | compares two means for one sample measured twice (pretest-posttest or within-subjects) | the calorie-estimation training |
| independent-samples *t* test | compares the means of two separate samples (between-subjects) | junk food eaters versus non-eaters |
| one-way ANOVA | compares more than two means in a between-subjects design with one independent variable | psychology majors, nutrition majors, dieticians |
| post hoc comparisons | comparisons of selected pairs of means after a significant ANOVA, with modified *t* tests | Tukey's HSD |
| conclude that ... tend to | the cautious way to state a rejected null hypothesis | conclude that university students tend to underestimate the number of calories |
| does not significantly (+ verb) | the way to state a retained null hypothesis | The training program does not significantly increase people's calorie estimates. |

## Rules and explanations
### The ideas
**Which test?**

| Design | Test | *df* |
|---|---|---|
| one sample mean versus a hypothetical value μ0 | one-sample *t* | *N* − 1 |
| one sample measured twice | dependent-samples *t* (one-sample *t* on difference scores, μ0 = 0) | *N* − 1 (*N* = number of pairs) |
| two separate groups | independent-samples *t* | *N* − 2 |
| more than two groups, one independent variable | one-way ANOVA (then post hoc comparisons) | *dfB* = *G* − 1, *dfW* = *N* − *G* |
| two quantitative variables | test of Pearson's *r* (null hypothesis: ρ = 0) | *N* − 2 |

**Formulas.** One-sample: *t* = (*M* − μ0) / (*SD* / √*N*). Independent samples: *t* = (*M*1 − *M*2) / √(*SD*1² / *n*1 + *SD*2² / *n*2). ANOVA: *F* = *MSB* / *MSW*, the ratio of two estimates of the population variance, one based on differences among the group means and one on differences within the groups.

**Deciding.** Software gives the *p* value: if *p* ≤ .05, reject the null hypothesis. By hand, compare *t* with the **critical value** in the book's tables: a *t* beyond it has *p* < .05. With 24 *df*, a *t* of 1.50 has a two-tailed *p* of about .15 (the book says .14), so the null hypothesis is retained. A **one-tailed test** (direction chosen before data collection) has a less extreme critical value (1.711 instead of 2.064 for 24 *df*), but a result in the unexpected direction can never be significant.

**The book's worked examples** (calorie estimates of a chocolate chip cookie, which has 250 calories)

| Example | Result as printed | Check with Python |
|---|---|---|
| One-sample, two-tailed, 10 students | *M* = 212.00, *SD* = 39.17, *t*(9) = −3.07, *p* = .013: reject; students underestimate | The ten listed estimates give *M* = 210.50, *SD* = 39.75, *t*(9) = −3.14, *p* = .012. The printed *M* and *SD* do not match the data; the conclusion is the same. |
| Dependent-samples, one-tailed, training program | mean difference 8.50, *SD* = 27.27, *t*(9) = 1.11, *p* = .148: retain | *SD* is 24.27 (a typo); *t* = 8.50 / (24.27 / √10) = 1.11 is correct. |
| Independent-samples, two-tailed, 8 junk food eaters versus 7 non-eaters | non-eaters *M* = 220.71, *SD* = 41.23; eaters *M* = 168.12, *SD* = 42.66; *t*(13) = 2.42, *p* = .015: reject | The *SD*s are swapped in the sentence (non-eaters 42.66, eaters 41.23), though the formula pairs them correctly; *M* = 168.125, so 168.13. *t*(13) = 2.42 is right, but the two-tailed *p* is **.031** (.015 is the one-tailed *p*). Still below .05: reject. |
| One-way ANOVA, three groups | *F* = 9.92, *MSB* = 5,971.88, *MSW* = 602.23, *p* = .0009, with 2 and 21 *df* | The output matches only eight nutrition majors; the list has nine (a final 195). With all 25 scores: nutrition *SD* = 25.98, *F*(2, 22) = 10.51, *p* < .001. Same conclusion. |
| Test of *r*, two-tailed, *N* = 22 | *r* = −.21, *p* = .348: retain; critical value ".444" for 20 *df* | *p* = .348 is right, but the table is organized by *N*: .444 is for *N* = 20; for *N* = 22 the critical value is .423. Same conclusion. |

**After the ANOVA.** A significant *F* says only that the means are not all equal. Running ordinary *t* tests on every pair raises the chance of mistakenly rejecting at least one true null hypothesis (a Type I error; the book refers you to "Section 13.3", but the section is 13.4, covered in Lesson 32), so researchers use modified procedures (Bonferroni, Fisher's LSD, Tukey's HSD). A **repeated-measures ANOVA** subtracts stable individual differences from *MSW*, giving a larger *F* and a more sensitive test; a **factorial ANOVA** gives an *F* and *p* for each main effect and for the interaction.

### Language focus
**Reporting a test in APA style.** The book's model is *t*(9) = −3.07, *p* = .01 (its printed values; the listed data give *t*(9) = −3.14, *p* = .01): italic symbols, degrees of freedom in parentheses ("brackets" in the book) with no decimals, values rounded to two decimal places. (APA style also accepts exact *p* values to three decimals, *p* = .012, and writes very small values as *p* < .001, never *p* = .000.) Other tests follow the same pattern: *r*(20) = −.21, *p* = .35; *F*(2, 22) = 10.51, *p* < .001.

A results sentence gives the **comparison and its direction in words**, then the **numbers**:
- Rejected: "...university students **tend to underestimate** the number of calories in a chocolate chip cookie, *t*(9) = −3.14, *p* = .01."
- Retained: "...the training program **does not significantly increase** people's calorie estimates, *t*(9) = 1.11, *p* = .15."

> **Note for Arabic speakers**
> - Read *t*(9) = −3.14, *p* = .01 aloud as "*t* with nine degrees of freedom equals minus three point one four, *p* equals point oh one".

## Practice
**Part 1: Reading and checking questions**
1. How does a dependent-samples *t* test become a one-sample *t* test?
2. Give one advantage and one disadvantage of a one-tailed test.
3. Why do researchers not follow a significant ANOVA with a series of ordinary *t* tests?
4. Which test fits each study? (a) Ten patients' anxiety before and after therapy. (b) Reaction times of three separate age groups. (c) Students' mean estimate of a distance compared with the true distance. (d) Hours of sleep and exam scores. (e) Men's and women's mean empathy scores.
5. Check the book's one-sample *t* with its printed values (*M* = 212.00, *SD* = 39.17, *N* = 10, μ0 = 250).

**Part 2: Exercises from the book (section 13.6)**
1. Practice: Use one of the online tools, Excel, or SPSS to reproduce the one-sample *t*-test, dependent-samples *t*-test, independent-samples *t*-test, and one-way ANOVA for the four sets of calorie estimation data presented in this section. (Use the data in section 13.3 and compare your output with the "Check" column above.)
2. Practice: A sample of 25 university students rated their friendliness on a scale of 1 (*Much Lower Than Average*) to 7 (*Much Higher Than Average*). Their mean rating was 5.30 with a standard deviation of 1.50. Conduct a one-sample *t*-test comparing their mean rating with a hypothetical mean rating of 4 (*Average*). The question is whether university students have a tendency to rate themselves as friendlier than average.
3. Practice: Decide whether each of the following Pearson's *r* values is statistically significant for both a one-tailed and a two-tailed test. (a) The correlation between height and IQ is +.13 in a sample of 35. (b) For a sample of 88 university students, the correlation between how disgusted they felt and the harshness of their moral judgments was +.23. (c) The correlation between the number of daily hassles and positive mood is −.43 for a sample of 30 middle-aged adults.

**Part 3: Language exercise.** Write each result as an APA sentence (words, then numbers).
1. Independent-samples test: junk food eaters made lower estimates than non-eaters; *t* = 2.42, 13 *df*, *p* = .031.
2. Test of *r*: calorie estimates and weight, *r* = −.21, *N* = 22, *p* = .348.

## Answer key
**Part 1**
1. Each participant's two scores are reduced to one difference score, and a one-sample *t* test compares the mean difference with 0.
2. Advantage: the critical value is less extreme, so a result in the expected direction is easier to find significant. Disadvantage: a result in the unexpected direction can never be significant.
3. Each extra test increases the chance of mistakenly rejecting at least one true null hypothesis (a Type I error).
4. (a) dependent-samples *t*; (b) one-way ANOVA; (c) one-sample *t*; (d) test of Pearson's *r*; (e) independent-samples *t*.
5. *SD* / √*N* = 39.17 / 3.162 = 12.39; *t* = (212 − 250) / 12.39 = −3.07.

**Part 2**
1. See the "Check" column: one-sample *t*(9) = −3.14, *p* = .012; dependent-samples *t*(9) = 1.11, one-tailed *p* = .148; independent-samples *t*(13) = 2.42, two-tailed *p* = .031 (software that pools the variances gives *t*(13) = 2.43, *p* = .031); one-way ANOVA *F*(2, 22) = 10.51, *p* < .001 (*F*(2, 21) = 9.92, *p* < .001 without the extra 195).
2. *t* = (5.30 − 4) / (1.50 / √25) = 1.30 / 0.30 = 4.33, *df* = 24. The question is directional, so a one-tailed test (critical value 1.711) fits; *t* is also beyond the two-tailed value (2.064). Reject the null hypothesis: *t*(24) = 4.33, *p* < .001. University students tend to rate themselves as friendlier than average.
3. (a) Not significant either way: critical values for *N* = 35 are .283 (one-tailed) and .334 (two-tailed). (b) Significant both ways: the table's nearest rows are *N* = 85 (.180, .213) and 90 (.174, .207), and .23 exceeds them all (exact values for *N* = 88: .176 and .210). (c) Significant both ways: |−.43| exceeds .306 and .361 for *N* = 30, as long as the one-tailed test had predicted a negative relationship.

**Part 3** (example answers)
1. People who eat junk food regularly made lower calorie estimates than people who eat it rarely, *t*(13) = 2.42, *p* = .03.
2. There was no statistically significant correlation between people's calorie estimates and their weight, *r*(20) = −.21, *p* = .35.

**Writing task.** *t* = 5.00 / √(2.54² / 10 + 3.09² / 10) = 5.00 / √1.60 = 5.00 / 1.265 = 3.95; *df* = 18; critical value 2.101 (two-tailed); exact *p* = .0009, so *p* < .001. For *r* = .45 with *N* = 10, the two-tailed critical value is .632 (one-tailed .549); exact *p* = .20. Example: "An independent-samples *t* test (two-tailed, α = .05) showed that participants in the nap condition recalled significantly more word pairs (*M* = 22.00, *SD* = 2.54) than participants in the wake condition (*M* = 17.00, *SD* = 3.09), *t*(18) = 3.95, *p* < .001, *d* = 1.86. In the nap condition, the positive correlation between minutes asleep and recall was not statistically significant, *r*(8) = .45, *p* = .20."

## Speak with your tutor
- "Describe five studies to me. For each one I will choose the test and explain why, including the degrees of freedom."
- "Let me explain the difference between a one-tailed and a two-tailed test with the calorie examples. Then ask me when a one-tailed test is not acceptable."
- "I will read my APA results aloud. Correct how I say the symbols and numbers."

## Writing task
**Your research project: results, part 5 (the inferential tests).** Your **invented data for practice** (see Lesson 27): nap condition *M* = 22.00, *SD* = 2.54, *n* = 10; wake condition *M* = 17.00, *SD* = 3.09, *n* = 10; in the nap condition, minutes asleep and recall correlated *r* = .45 (*N* = 10). Your hypotheses (Lesson 30) are two-tailed, with α = .05.

Compute the independent-samples *t* and its *df*, and find the critical values for *t* and *r* in the book's tables. Then write 100 to 150 words that report both tests in APA style, say in words what each decision means, and include *d* from Lesson 28. If you work on your own topic, test your own invented data and ask your tutor to check every number.

Checklist:
- Did you name each test (independent-samples *t* test, test of Pearson's *r*) and say it was two-tailed?
- Does each sentence give the direction in words before the numbers?
- Are *t*, *r*, *p* and *d* italic, with *df* in parentheses and two decimal places (or *p* < .001)?
- Did you report the means and standard deviations with the *t* test?
- Did you say "was not statistically significant" (not "there is no relationship") for the correlation?
- Did you use the past tense for your results?

Paste your draft into the chat and ask your tutor for feedback. Then rewrite it.

## Watch online
- VassarStats, free online calculators for *t* tests, ANOVA and correlation (use them for Exercise 1): http://vassarstats.net/
