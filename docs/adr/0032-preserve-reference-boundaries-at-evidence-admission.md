# 32. Withhold endpoint observations that evidence v3 cannot represent

Date: 2026-11-11
Status: Accepted

## Decision

The shared range parser preserves strict and inclusive bounds. The local verdict
for 5.2 with reference <5.2 is high; evidence v3's numeric high=5.2 means an inclusive
bound and would call that same observation normal. Such endpoint observations are
withheld with `unsupported_reference_boundary`, preserving the local indication
and a visible explanation. Values off the endpoint retain equivalent comparisons.
No observation or threshold is altered to compensate for transport limitations.

The semantics follow [FHIR R4 Quantity.comparator](https://hl7.org/fhir/R4/datatypes-definitions.html#Quantity.comparator)
and [Observation reference ranges](https://hl7.org/fhir/R4/observation-definitions.html#Observation.referenceRange).
This is not a FHIR protocol migration. Conditional/multiple reference populations
are not silently selected. Display data reuses the same parsed bounds.

## Alternatives considered

Adding inclusive flags to the evidence protocol supports all endpoints, but needs
coordinated versioned provider and consumer changes. Withholding affected rows is
compatible with the current protocol. Epsilon adjustment keeps the existing shape
but changes the measured facts. Keeping the previous inclusive-only local verdict
would hide a report abnormality and is rejected.

## Consequences

The reason is part of the existing admission vocabulary, ledger and patient text.
Patients can see an abnormal endpoint that lacks evidence interpretation. This
explicit limit stays until a separately reviewed protocol extension. Tests verify
local verdicts, outbound admission, interval ambiguity and existing consumers.
