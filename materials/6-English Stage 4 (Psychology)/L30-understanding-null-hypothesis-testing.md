# Lesson 30: Understanding Null Hypothesis Testing

*English Stage 4 (Psychology) · Unit 4: Statistics in Research Reports*
*Adapted from Research Methods in Psychology (4th American edition) by Rajiv S. Jhangiani, I-Chant A. Chiang, Carrie Cuttler and Dana C. Leighton (CC BY-NC-SA 4.0): 13.1 Prelude to Inferential Statistics; 13.2 Understanding Null Hypothesis Testing.*

## In this lesson
How can researchers say that a result in a sample is true of a whole population? You will learn the logic of null hypothesis testing, what a *p* value is (and is not), how sample size and relationship strength decide statistical significance, and why statistical significance is not practical significance. You will practise the precise, hypothetical language of statistical conclusions.

## Learning goals
After this lesson you should be able to:
- Explain sampling error and describe the three steps of null hypothesis testing.
- Define the *p* value correctly and avoid its most common misinterpretation.
- Judge whether a result is likely to be significant from its strength and sample size, and distinguish statistical from practical significance.
- Report statistical decisions with the correct verbs and hypothetical conditionals.

## Key language
| Term or phrase | Meaning | Example |
|---|---|---|
| statistic / parameter | a numerical summary of a sample / of a population | the mean number of symptoms in 50 adults / in the population |
| sampling error | random variation in a statistic from sample to sample | *r* = +.24, −.04 and +.15 in three random samples |
| null hypothesis (*H*0) | the idea that there is no relationship in the population and the sample relationship reflects only sampling error | The sample relationship "occurred by chance." |
| alternative hypothesis (*H*1) | the idea that there is a relationship in the population and the sample relationship reflects it | a positive correlation between hassles and symptoms |
| *p* value | the probability of the sample result or a more extreme one if the null hypothesis were true | *p* = .02 |
| α (alpha) | the criterion for "low enough": almost always .05 | a 5% chance or less |
| statistically significant | used to describe a result for which the null hypothesis has been rejected | The differences are statistically significant. |
| retain the null hypothesis | conclude, tentatively, that there is not enough evidence against it (also "fail to reject"; never "accept") | They retained the null hypothesis. |
| practical (clinical) significance | the importance of a result in some real-world context | an effect too weak to justify its costs |
| there is no evidence of | the cautious way to report a retained null hypothesis | no evidence of a sex difference in the population |
| if ... were true, how likely ... would ... | the hypothetical question at the heart of the test | ...how likely is it that we would find a strong correlation of +.60? |

## Rules and explanations
### The ideas
**The problem.** Mehl et al. (2007) found that women spoke a mean of 16,215 words a day and men 15,669, yet concluded there was *no evidence* of a sex difference in the population; Kanner et al. (1981) found *r* = +.60 between daily hassles and symptoms and concluded that there *is* a relationship in the population. **Inferential statistics** justify such conclusions.

**Sampling error.** Researchers use **statistics** (sample values) to draw conclusions about **parameters** (population values), but statistics vary randomly from sample to sample (a mean of 8.73, 6.45 and 9.44 depressive symptoms in three random samples). This **sampling error** is random variability, not a mistake. So any sample relationship has two interpretations: there is a relationship in the population, or there is none and the sample relationship reflects only sampling error.

**The logic of null hypothesis testing (NHST)**
1. Assume for the moment that the **null hypothesis** is true: there is no relationship in the population.
2. Determine how likely the sample relationship would be if it were true: this is the ***p* value** (the probability of the sample result *or a more extreme one*).
3. If *p* is at or below **α = .05**, **reject** the null hypothesis in favor of the alternative hypothesis: the result is **statistically significant**. Otherwise **retain** it ("fail to reject"), which means only that there is not *currently* enough evidence against it; researchers never say "accept the null hypothesis".

Mehl et al. asked how likely *d* = 0.06 would be if there were no difference in the population: fairly likely, so they retained the null hypothesis. Kanner et al. found *r* = +.60 fairly unlikely under the null hypothesis, so they rejected it.

**The misunderstood *p* value.** *p* is **not** the probability that the null hypothesis is true. A *p* of .02 does not mean "a 2% chance that the result is due to chance and a 98% chance that it is real"; it means that *if* the null hypothesis *were* true, a result this extreme would occur only 2% of the time.

**Strength and sample size.** The stronger the sample relationship and the larger the sample, the lower the *p* value: 500 women and 500 men with *d* = 0.50 would lead to rejection; three women and three men with *d* = 0.10 would not. The book's rough guideline (Table 13.1):

| Sample size | Weak | Medium | Strong |
|---|---|---|---|
| Small (*N* = 20) | No | No | *d* = Maybe, *r* = Yes |
| Medium (*N* = 50) | No | Yes | Yes |
| Large (*N* = 100) | *d* = Yes, *r* = No | Yes | Yes |
| Extra large (*N* = 500) | Yes | Yes | Yes |

Weak relationships with small or medium samples are never significant; strong ones with medium or larger samples always are, so you can predict a test's outcome and catch computing errors. (The table is only a very rough guide: by exact calculation, *d* = 0.20 with 100 participants, 50 per group, gives *p* = .32, so its "*d* = Yes" cell for weak relationships and large samples does not hold.)

**Statistical versus practical significance.** With a large enough sample, even a very weak result is statistically significant: some sex differences (mathematical problem solving, leadership) are significant but trivial (Hyde). A new social phobia treatment may have a statistically significant effect too small to justify its costs, especially if cheaper treatments work almost as well: it lacks **practical** (**clinical**) significance.

### Language focus
**1. Hypothetical conditionals.** NHST asks about an imagined world, so the book uses *were* and *would*:
- "**If** there **were** no difference in the population, how likely is it that we **would** find a small difference of *d* = 0.06 in our sample?"
- "...a sample result this extreme **would** occur only 2% of the time."

**2. Precise verbs and cautious conclusions**

| Precise | Imprecise or wrong |
|---|---|
| reject the null hypothesis (in favor of the alternative) | prove the alternative hypothesis |
| retain / fail to reject the null hypothesis | accept the null hypothesis |
| there is no evidence of a difference | there is no difference |
| statistically significant | significant (alone, it sounds like "important") |

> **Note for Arabic speakers**
> - Keep "if the null hypothesis were true" (not "is true ... will"): *were* shows that the situation is imagined for the test, not claimed to be real.

## Practice
**Part 1: Reading questions**
1. What is the difference between a statistic and a parameter? Use the book's depression example.
2. What is sampling error, and why does nobody "commit" a sampling error?
3. What are the two possible interpretations of any statistical relationship in a sample?
4. Why did Mehl et al. retain the null hypothesis, while Kanner et al. rejected it?
5. Which two factors determine the *p* value, and in which direction?
6. Give the book's example of a result that is statistically significant but not clinically significant.

**Part 2: Exercises from the book (section 13.6; the other exercises are in Lessons 31 and 32)**
1. Discussion: Imagine a study showing that people who eat more broccoli tend to be happier. Explain for someone who knows nothing about statistics why the researchers would conduct a null hypothesis test.
2. Practice: Use Table 13.1 to decide whether each of the following results is statistically significant. (a) The correlation between two variables is *r* = −.78 based on a sample size of 137. (b) The mean score on a psychological characteristic for women is 25 (*SD* = 5) and the mean score for men is 24 (*SD* = 5). There were 12 women and 10 men in this study. (c) In a memory experiment, the mean number of items recalled by the 40 participants in Condition A was 0.50 standard deviations greater than the mean number recalled by the 40 participants in Condition B. (d) In another memory experiment, the mean scores for participants in Condition A and Condition B came out exactly the same! (e) A student finds a correlation of *r* = .04 between the number of units the students in his research methods class are taking and the students' level of stress.
3. Discussion: Imagine that you conduct a *t* test and the *p* value is .02. How could you explain what this *p* value means to someone who is not already familiar with null hypothesis testing? Be sure to avoid the common misinterpretations of the *p* value.

**Part 3: Language exercise.** Correct each sentence.
1. "The *p* value of .02 means there is a 2% chance that the null hypothesis is true."
2. "Mehl et al. accepted the null hypothesis and proved that women and men are equally talkative."
3. "If the null hypothesis is true, how likely the result will be?"
4. "The treatment effect was significant, so the treatment is important."

## Answer key
**Part 1**
1. A statistic describes a sample (the mean number of symptoms in 50 adults with clinical depression); a parameter describes the population (the mean for all such adults).
2. Random variability in a statistic from sample to sample; "error" refers to this variability and does not imply that anyone made a mistake.
3. There is a relationship in the population that the sample reflects, or there is none and the sample relationship reflects only sampling error.
4. A difference as small as *d* = 0.06 would be fairly likely if there were no difference in the population; a correlation as strong as +.60 would be fairly unlikely if there were no correlation.
5. Relationship strength and sample size: the stronger the relationship and the larger the sample, the lower the *p* value.
6. A social phobia treatment whose significant effect is too weak to justify its time, effort and costs, especially if easier, cheaper treatments work almost as well.

**Part 2**
1. (example answer) "The researchers studied only a sample. Even if broccoli had nothing to do with happiness in the population, a sample could show a link just by chance. The test asks: if there were really no link, how likely would a link this strong be? If that is very unlikely (5% or less), they conclude that the link probably exists in the population."
2. (a) Yes: a strong relationship with a large sample (137 lies between 100 and 500). (b) No: *d* = (25 − 24) / 5 = 0.20, weak, with a small sample (*N* = 22). (c) Yes: *d* = 0.50, medium, with *N* = 80 (between 50 and 100, both "Yes"). (d) No: *d* = 0, no relationship at all, so never significant. (e) No: *r* = .04 is weaker than "weak" (.10), and one class is a small or medium sample. (Exact checks agree: (b) *p* = .65; (c) *p* = .03.)
3. (example answer) "Suppose there were really no difference in the population. Then a difference as large as ours would turn up by chance in only about 2 of 100 studies like this one. That would be unusual, so we conclude that there is probably a real difference. It is not a 2% chance that the null hypothesis is true."

**Part 3** (example answers)
1. A *p* value of .02 means that if the null hypothesis were true, a result this extreme would occur only 2% of the time.
2. Mehl et al. retained the null hypothesis: there was no evidence of a sex difference in talkativeness in the population.
3. If the null hypothesis were true, how likely would the result be?
4. The treatment effect was statistically significant, but it may lack practical (clinical) significance if it is weak.

**Writing task** (example answer) "The null hypothesis for the comparison was that, among university students, mean recall after a nap equals mean recall after an equal period awake; the alternative hypothesis was that the means differ. For the correlation, the null hypothesis was that minutes asleep and recall are unrelated in the population. Table 13.1 suggests that the difference (*d* = 1.86, far above 0.80, with *N* = 20) is likely to be statistically significant, whereas *r* = .45 with only 10 participants may not be. A *p* of .01 would mean that, if napping had no effect on recall, a difference this large would occur only 1% of the time."

## Speak with your tutor
- "I will explain null hypothesis testing to you as if you knew no statistics, using the broccoli example. Stop me whenever I misinterpret the *p* value."
- "Give me five results (a *d* or *r* and a sample size). I will use Table 13.1 to judge whether each is likely to be significant."
- "Ask me why a statistically significant sex difference (Hyde) can still be unimportant, and ask for a clinical example."

## Writing task
**Your research project: results, part 4 (hypotheses and the logic of the tests).** Your study (**invented data for practice**) compared word-pair recall after a nap (*M* = 22.00, *SD* = 2.54, *n* = 10) and after the same time awake (*M* = 17.00, *SD* = 3.09, *n* = 10), *d* = 1.86, and correlated minutes asleep with recall in the nap condition, *r* = .45 (*N* = 10).

Write 120 to 170 words that (1) state the null and alternative hypotheses for the difference and the correlation, in words; (2) use Table 13.1 to predict whether each result is likely to be statistically significant; (3) explain what a *p* value of .01 for the difference would mean. If you work on your own topic, do the same for your own invented results.

Checklist:
- Does each null hypothesis say "no difference" or "no relationship" in the population, not in the sample?
- Did you use *reject* and *retain* (or *fail to reject*), never *accept* or *prove*?
- Did you write the hypothetical conditional correctly (if ... were ..., ... would ...)?
- Did you write "statistically significant", not "significant" alone?
- Is your explanation of *p* free of "the probability that the null hypothesis is true"?

Paste your draft into the chat and ask your tutor for feedback. Then rewrite it.

## Watch online
- "Null Hypothesis", the xkcd cartoon in section 13.2: http://imgs.xkcd.com/comics/null_hypothesis.png
- "Conditional Risk", the xkcd cartoon in section 13.2: http://imgs.xkcd.com/comics/conditional_risk.png
- Daniel Lakens, "About p-values: Understanding common misconceptions" (archived copy; the original page is gone): https://web.archive.org/web/20191208141340/https://correlaid.org/en/blog/understand-p-values/
