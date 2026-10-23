# Implement Loop

Language for selecting tickets and describing their execution evidence.

## Language

**Loop run**:
One execution of the loop for a spec, encompassing ticket selection and any resulting attempts. Its end does not imply that all tickets are complete.

**Attempt**:
One harness invocation for a selected ticket. Preparing or assigning a ticket alone is not an attempt.

**Ticket records**:
Evidence associated with processing a selected ticket, including preparation before an attempt begins. Their existence does not establish that an attempt occurred.

**Manual finalization**:
A human's deliberate closure of a ticket for already-committed work after inspecting the work and its validation evidence. It is not a successful attempt or wrapper-verified completion.
