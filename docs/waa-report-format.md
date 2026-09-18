# WAA report format 0.2

Reports match complete `trace2task-<condition>-r<positive integer>` directory
names. Similar prefixes, incomplete suffixes and repetition zero are excluded.
This does not add any new experiment conditions.

In JSON summaries, unavailable rates, averages and baseline differences are
`null`; Markdown displays `n/a`. Empty conditions are not measured failures.
Outcome rates also remain unavailable when an observed episode has no completed,
finite score. Fully observed zero scores still produce a real 0% success rate.

Consumers of format 0.1 must handle nullable fields in format 0.2. The new
`outcome_coverage` field is `empty`, `incomplete` or `complete`. Coverage concerns
discovered episodes, not an externally planned allocation count. Episodes never
created by a failed setup cannot be inferred from this report alone.

Action and timing averages describe observed episodes. Baseline differences are
descriptive comparisons, not causal estimates. Original result files are not
modified; this patch changes collection and reporting only.
