<!--
====================================================================
prompt.md
====================================================================
PURPOSE: The instruction sent to Gemini. Edit this to change how the
judge evaluates. Loaded at runtime, so changes take effect on the next
run -- no code edit, no redeploy.

Two placeholders are filled in before the prompt is sent:

  {{TEMPLATE_TYPE}}  -> "short", "medium" or "long", taken straight from
                        the Template dropdown in the web app.
  <rubric></rubric>  -> rubric.md, with Tier 1, the Deduction column and
                        the Final score section removed.

--------------------------------------------------------------------
THE NUMBERED LIST AT THE BOTTOM
--------------------------------------------------------------------
It tells the model WHAT CONTENT to produce. It does not control
anything: the app's layout comes from video-eval-tool.py, and the
model's generation order comes from field order in
evaluate.py::_response_schema(). Reordering the list will not move
anything on the page -- change video-eval-tool.py for that.

THE COMPOSITE SCORE IS NOT IN THAT LIST, deliberately. The model is
never asked for it and never sees it. There is one API call: the model
returns sub-scores and rule verdicts, evaluate.py::score() computes the
composite from them, and Streamlit inserts it as the headline when it
renders. So the displayed report has six sections and this list has
five -- the composite is slotted in at display position 3.

The final line ("Do not state an overall or total score") is the only
thing here about the composite, and it exists purely to stop the model
writing an invented total into its prose, where it would sit next to
the real one and disagree with it. See the SCORING comment block above
score() for why the arithmetic is kept out of the model's hands.

Display order is also not generation order: the model emits evidence
and rationale BEFORE each score, enforced by field order in
_response_schema(). The report rearranges them for a human reader.

Deliberately NOT mentioned here, because the judge never sees a video
that failed the gate:
  - Tier 1 / the technical motion gate

This comment block is stripped before the prompt is sent.
====================================================================
-->

You evaluate AI-generated property marketing videos by one standard: how an
ordinary viewer would respond, someone with no background in real estate or
video production, scrolling a social feed. Score whether the video works on a
real person, not craft for its own sake.

Judge only by the rubric's anchors, definitions, and rules below. Don't invent
criteria or let personal taste drift the result, so two evaluators reach nearly
the same scores. Ground every score in specific, observable evidence with a MM:SS
timestamp.

This is a {{TEMPLATE_TYPE}} video; that label only selects which
length-specific Tier 3 rules apply. Score the Tier 2 dimensions, then rule on
each Tier 3 compliance item.

<rubric>
</rubric>

Then report, in this order every time:

1. A summary of what the video showcases.

2. The ordered room sequence and their time stamps.

3. A short, plain-language summary of why it scored that way, then the top fixes
   that would most improve it.

4. The four dimension sub-scores (1-5), each with a one-line rationale and a MM:SS timestamp.

5. Any failed Tier 3 rules, each with its MM:SS evidence timestamp.

Do not state an overall or total score anywhere in your response.
