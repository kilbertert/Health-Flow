# 31. Report display stays separate from evidence admission and self-reported answers

Date: 2026-11-11
Status: Accepted

## Decision

The report API adds optional system groups, a display-only parsed reference range,
and a versioned lifestyle questionnaire. Old reports need no backfill. The existing
report states, effective-value rules, evidence publishing and consent boundaries
remain authoritative. The UI does not derive a health conclusion from an empty
published-finding array.

Reference bounds retain strict/inclusive semantics. HL7 FHIR R4
[Quantity.comparator](https://hl7.org/fhir/R4/datatypes-definitions.html#Quantity.comparator)
and [Observation.referenceRange](https://hl7.org/fhir/R4/observation-definitions.html#Observation.referenceRange)
are semantic references, not a new wire protocol. Conditional or ambiguous ranges
are not selected without the applicable context. Evidence v3 carries inclusive
numeric bounds only: an observation exactly on an excluded endpoint is withheld
with `unsupported_reference_boundary`. Its local high/low indication stays visible;
it must not be sent with a different predicate or summarized as normal. Off-boundary
numeric comparisons retain the same result under v3. `range_display` preserves the
printed predicate for visual presentation and never grants evidence admission.

System membership is a presentation taxonomy over canonical metric codes, not an
organ diagnosis. Unsettled codes are resolved only with an available authoritative
catalog. No mapping means Other. Groups retain member IDs and the existing server
counts; they have neither scores nor an overall health verdict.

The four optional questions describe sleep, diet, activity and stress. There is no
preselection. Answers belong to the report and its owner, in one uniquely keyed
row; deletion/revocation cascades to that row. Conditional updates on `revision`
and the unique report key arbitrate concurrent writers. Same-content retries are
idempotent. Question version changes invalidate the interpretation of old answers,
without silently applying them to different questions.

Outputs only quote the answers and organize questions for a clinician. No new
lifestyle prescription has been clinically approved by this change. The earlier
unreferenced static advice is removed; neither code review nor a static dictionary
is a substitute for the established evidence/action approval process.

## Alternatives considered

Extending evidence v3 with inclusive flags would preserve every observation, but
requires a coordinated provider/consumer version change. Explicitly withholding
only observations whose verdict would change keeps the existing protocol honest.
Moving a boundary by epsilon would preserve transport shape but falsify the report.

Computing ranges and health conclusions in the browser would need less API work,
but create a second parser and allow counts, cards and summaries to disagree.
Only layout arithmetic is client-side.

Keeping every questionnaire revision would simplify history, but the product only
shows current answers and does not need another archive of sensitive self-reports.
Storing answers in extraction JSON would avoid a table but expose them to parser
retries and conflate patient facts with machine output.

## Consequences

A new table is created by the existing additive schema bootstrap; no existing row
or original file is rewritten. Rolling code back leaves the table intact. A later
destructive cleanup is a separate operation. Older consumers ignore additive fields;
new consumers handle absent display/group/questionnaire fields.

Some boundary observations remain visibly abnormal without evidence interpretation.
That limitation is explicit until a separately reviewed protocol extension. Tests
must cover both local admission and the real evidence API boundary, plus concurrent
writes, deletion, unknown groups and patient copy in the built consumer.
