You are reviewing a finished study session between a tutor and a learner, to update the learner's long-term memory. Return JSON only.

Base everything on evidence in the transcript:
- Asking a question is NOT a difficulty. Call something difficult only when the learner gave a wrong answer, said they did not understand, or asked about the same thing again after it was explained.
- Writing in Arabic is normal and allowed in these sessions. It is not a difficulty or a language barrier.
- A short session gets a short summary and usually no new memories.

Return:
- "summary": 1 to 3 sentences about the learning only: what was studied, and what the learner showed they understood or found difficult (only with evidence as defined above). Leave out anything personal the learner mentioned.
- "next_step": one sentence: what the next session should start with.
- "new_memories": up to 3 lasting facts that are not already in memory. Only:
  - facts the learner stated about themselves (preferences, goals, background, interests, schedule), or
  - a difficulty proven as defined above.
  Each has "kind" (one of preference, goal, background, interest, note) and "content" (one short sentence in the third person, at most 15 words). Return an empty list when nothing qualifies. Never store feelings, stress, health, family matters, life goals or personal problems, and no guesses about personality.
- "outdated_memory_ids": ids from the current memory list that the transcript clearly shows are wrong or replaced. Usually an empty list.

Course: {{course}}
Topic: {{topic}}

Current memory (id: content):
{{memories}}

Transcript:
<<<
{{transcript}}
>>>
