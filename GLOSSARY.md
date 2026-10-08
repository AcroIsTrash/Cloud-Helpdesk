# Cloud-Helpdesk

An IT service desk where staff work tickets raised by the people they support,
with AI that routes and assists but never decides on its own.

## People

**Requester**:
The person who raised a ticket and is waiting for help.
_Avoid_: user, customer, end user, caller

**Agent**:
A human support staff member who works tickets. Always a person, never software.
_Avoid_: technician, staff, operator, "AI agent"

**Admin**:
An Agent with extra rights over the service desk itself.
_Avoid_: superuser, manager

## Tickets

**Ticket**:
One request for help from a Requester, tracked from creation until it is closed.
_Avoid_: incident, case, issue, request

**Priority**:
How urgently a Ticket must be handled, P1 (highest) to P4. It is derived from Impact and Urgency, never set directly.
_Avoid_: severity

**Impact**:
How widely a problem affects the business (high, medium, low).

**Urgency**:
How quickly the problem hurts the business if left alone (high, medium, low).

**Pending**:
The status of a Ticket that is waiting on the Requester. The Resolution SLA is paused while a Ticket is Pending.
_Avoid_: on hold, waiting

**Comment**:
A message on a Ticket that the Requester can see.
_Avoid_: reply, message

**Internal note**:
A message on a Ticket that only Agents can see. It never counts as a Response.
_Avoid_: private comment, hidden comment

## Queues and routing

**Queue**:
A team's list of Tickets. Every Ticket belongs to exactly one Queue, and every Agent works one Queue.
_Avoid_: team inbox, group, bucket

**Triage queue**:
The Queue called Service Desk, where Tickets go when nobody can tell which team owns them.
_Avoid_: default queue, catch-all

**Routing**:
Choosing the Queue for a new Ticket.
_Avoid_: assignment (Assignment means giving a Ticket to a specific Agent)

**Router**:
Whatever performs Routing: the keyword baseline or a model.
_Avoid_: classifier, AI agent

**Routing fallback**:
Sending a Ticket to the Triage queue because the Router failed or gave an unusable answer.

**Assignment**:
Giving a Ticket to one specific Agent.
_Avoid_: routing, ownership

## Service levels

**Response SLA**:
The time allowed between a Ticket's creation and the first Comment from an Agent.
_Avoid_: first-touch SLA

**Resolution SLA**:
The time allowed between a Ticket's creation and its resolution, not counting time spent Pending.

## AI assistance

**Suggested reply**:
A draft Comment written by a model for an Agent to edit and send, or discard. Never sent on its own.
_Avoid_: auto-reply, AI response

**Summary**:
A short model-written digest of a Ticket's history, for Agents.

## Audit

**Event**:
One entry in the append-only record of everything that happened to a Ticket, including every AI decision with its reason and confidence.
_Avoid_: log, history entry
