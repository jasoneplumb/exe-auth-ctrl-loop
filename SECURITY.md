# Security Policy

This project is a prototype of a cross-model execution-authority control loop.
The authority boundary it demonstrates is real, but the implementation is
in-process and illustrative. Read the "Production trust boundary" section of the
README before deploying any part of this code; the prototype does not provide
process isolation, signed capabilities, durable audit storage, or authenticated
human approvals.

## Supported versions

Only the latest commit on `mainline` is supported. There are no maintenance
branches.

## Reporting a vulnerability

Do not open a public issue for a security report. Instead, use GitHub's private
vulnerability reporting: **Security → Report a vulnerability** on this
repository. Include:

- the affected file(s) and commit hash;
- a description of the boundary being bypassed (e.g., handler reachable without
  a capability, token reuse, post-approval mutation, evidence-partition
  confusion); and
- a proof-of-concept or failing test if you have one.

You should receive an acknowledgment within 7 days.

## Scope notes

Reports that identify a way for either model integration (proposal or
execution) to cause a registered handler to run without a valid, unexpired,
single-use capability issued by the host-owned controller are always in scope.
Prompt-injection findings against the models themselves are in scope only when
they defeat a host-side invariant; the design assumes model outputs are
untrusted requests.
