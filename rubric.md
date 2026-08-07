# Property Marketing Video - Evaluation Rubric

AI-generated listing videos (Short / Medium / Long) • v1.0 • CV = objective computer-vision metric | LLM = model judgment • evaluated in three tiers by severity

## Tier 1 - Technical Motion Gate (checked first • fatal • CV-driven)

**SCORE = 0**

Triggers on **noticeable** motion failures - lurches, stutters, glitches, or unsmooth panning (calibrated magnitude/persistence threshold, not zero-tolerance). If it triggers, the video gets a **score of 0** with the reason "**Failed on technical quality: noticeable motion issues detected (lurches / stutters / glitches / unsmooth panning)**", the timestamp is reported, and the LLM judge is skipped (dimensions not scored).

## Tier 2 - Scored dimensions (only if gate passes • rate each 1-5, interpolate 4 & 2 • equally weighted • cite MM:SS)

| Dimension | 5 | 3 | 1 |
|---|---|---|---|
| **Tour Flow & Sequencing**<br>LLM | Progression follows a natural path (e.g. entry - main living spaces - private rooms); adjacent rooms feel connected; open-plan spaces shown as one continuous move | Serviceable but arbitrary order; a few transitions feel abrupt or jump between distant areas | Random / disorienting; cuts between unrelated spaces; viewer can't build a mental map of the layout |
| **Hook / Opening**<br>LLM + CV | First ~3s arresting; strong sharp lead shot | Opens on a real interior but the shot is static or unremarkable - no immediate sense of the property's best feature | Weak / slow open (empty space, logo card, dead space, dim or soft shot) |
| **Emotional / Aspirational Appeal**<br>LLM | Evokes what it'd be like to live there - e.g. natural light, warmth, inviting flow between spaces, lifestyle feel; not an exhaustive checklist, just a genuine sense of desirability | Pleasant but flat; shows the home adequately but doesn't stir any feeling | Sterile; feels like an inventory list; rooms presented as empty boxes to be catalogued |
| **Framing & Composition**<br>LLM + CV | Flattering angles; level, open, shows off space; e.g. shot from a corner or doorway to reveal room depth | Some flat, cramped, or slightly off angles | Awkward; tilted horizons, walls-in-face, cramped crops |

## Tier 3 - Compliance rules (pass / fail with MM:SS evidence • each failure deducts points from the base)

| Severity | Rule | Applies | Deduction |
|---|---|---|---|
| Critical | Kitchen not shown at all | All | -10 |
| Critical | Living room not shown at all | All | -10 |
| Critical | Closet shown | All | -10 |
| Critical | Laundry room shown | All | -10 |
| Critical | Bathroom shown (prohibited) | Short only | -10 |
| Critical | Bathroom not shown (required) | Medium / Long | -10 |
| Moderate | Kitchen not within the first 3 stops | All | -5 |
| Moderate | Living room not within the first 3 stops | All | -5 |
| Moderate | Panning into blank walls / dead space | All | -5 |
| Moderate | Back-and-forth panning over the same room | All | -5 |

A "stop" = one distinct space in order of first appearance; contiguous open-plan areas count as one. Bathroom is **prohibited in Short** but **required in Medium / Long**. Each rule counts once regardless of repeats.

## Final score

1. If the Tier-1 gate triggers - **score = 0**, reason: "Failed on technical quality: noticeable motion issues detected (lurches / stutters / glitches / unsmooth panning)" (dimensions not scored).
2. `base = average(4 dimension scores) x 20`
3. `composite = max(0, base - total deductions)`

Score is out of **100**. Dimensions are equally weighted (25% each). The report shows the composite as the headline alongside the four raw sub-scores with rationales, the ordered room sequence, and any failed rules. Weights, deduction points, and the gate threshold are config values, calibrated against real videos.