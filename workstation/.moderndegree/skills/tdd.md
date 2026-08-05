# tdd — seams, vertical slices, red before green

Distilled from https://www.skills.sh/mattpocock/skills/tdd. Adopted after a run
that shipped 41 passing tests around software that had never once reached its
endpoint — see the seam rule below for exactly why they could not catch it.

## 1. Test at SEAMS — public boundaries, not internals

A seam is a place where behaviour is observable **without reading the
implementation**: a CLI invocation, an HTTP response, a module's public function,
a file written to disk.

A good test reads like a specification and survives a rewrite of the internals. If
a refactor that changes no behaviour breaks your test, the test was pinned to an
implementation detail and was never protecting anything.

**This is the rule that catches dead wiring.** A tool whose public seam is a CLI
must be tested by invoking the CLI. Tests aimed one layer below — at the parser,
the scorer, the formatter — can all pass while the layer that assembles them
points at the wrong URL, loads the wrong config, or crashes on import. That is not
a hypothetical: it is how a routing tool shipped having never fetched a single
metric, with a green suite the whole way.

**Confirm the seams before writing tests.** State which boundaries you intend to
test and why, and get that agreed — in this system, via the TASK PACKAGE or an
`architect` gate. Never test at an unconfirmed seam. Concentrate effort on
critical paths and genuinely complex logic; breadth at the wrong seam buys
nothing.

## 2. Vertical slices, not horizontal layers

One behaviour at a time: write the test → make it pass → move on.

Do **not** write every test first, then all the implementation; and do not build
every layer before connecting any of them. Horizontal work hides integration
failure until the end, which is the most expensive moment to find it. A vertical
slice proves one path works end to end before the next begins.

## 3. Red before green, every cycle

Watch the test fail, **and confirm it fails for the reason you expect**, before
making it pass. A test whose first observed state was green may be asserting
nothing — an empty assertion, a mistargeted path and a genuinely satisfied
requirement are indistinguishable from the outside.

If a test passes the moment you write it, that is a finding, not a convenience.
Either the behaviour already existed — fine, say so — or the test is broken.
Determine which before continuing.

When a check cannot be written before the code (rare, but real for exploratory
work), break it deliberately afterwards to prove it can fail, and record that in
`evidence`.

## 4. Refactor only on green

Refactor after the tests pass, never during red. Extract duplication, deepen
modules, apply SOLID where it earns its keep — then re-run. If behaviour changed,
it was not a refactor.

## 5. Match the domain language

When working in an existing codebase, read its documentation and mirror its
naming in test names. Respect the architectural decisions already made; a test
suite that argues with the codebase's vocabulary is friction, not coverage.

---

**How this lands here:** `architect` confirms the seams and designs for
testability; `coder` works in vertical slices; `tester` owns red-before-green and
tests at the confirmed seams; `qa` validates at the outermost seam of all — the
delivered system against its done-when list, black box, real dependencies.
