# Motivation

## The reliability problem

A SQL engine is trusted to be *silently* correct. An application does not check
whether `lpad` returned the right string; it uses the answer. So a defect in a
scalar function is not caught by the application, and it is not caught by the
user — it is either caught by the engine's own tests or it is not caught at all.

That makes database engines an unusually good subject for automated testing, and
it is why so much work has gone into it. It also makes the remaining defects
unusually hard to find, because the obvious ones are gone.

## Why a new engine, and why Turso

Turso is a rewrite of SQLite in Rust, under active development, aiming at
SQLite compatibility. That combination is interesting for two opposite reasons:

- It is **young**, so it has had less exposure than SQLite's decades of
  deployment and its famously exhaustive test suite.
- It is **already well tested**. This matters more than the first point and is
  easy to miss. Turso maintains its own differential fuzzer —
  `testing/differential-oracle/`, thousands of lines of Rust that generates SQL
  and compares Turso's answers against SQLite's — and runs it in CI, alongside
  four libFuzzer targets. Before starting we read that fuzzer rather than
  assuming it was weak.

So the problem is not "find bugs in an untested engine". It is: **what is left
after a competent, continuously-running fuzzer has already had the target for
months?**

## The observation this project is built on

A fuzzer can only find defects it can *reach*. For a database engine driven
through its CLI, reachability is not the constraint everyone assumes — every
statement the parser accepts is reachable. Two other limits bind instead:

1. **What the generator can express.** A generator draws function names from a
   vocabulary and literals from a numeric and character domain. Anything outside
   those is not merely unlikely; it is impossible, no matter how long the fuzzer
   runs.
2. **What the oracle can judge.** A differential oracle can only report a
   disagreement between two engines that both implement the feature. Where the
   reference engine has no counterpart, the oracle is silent by construction.

Both limits are properties of the incumbent fuzzer's *source code*, so both can
be measured before writing a single test case. That is unusual: it means the
search space can be narrowed by reading, not by running.

## Where the test cases come from

Knowing where to look does not tell you what to send. Writing statements that
sit in another fuzzer's blind spot, by hand, is guesswork.

But those statements already exist. Every mature SQL engine has a public bug
tracker full of closed, reproduced defects, each with a minimal query a
maintainer wrote specifically because it broke something. Those queries are
concentrated in exactly the awkward corners that a uniform generator reaches
rarely: oversized lengths, multi-byte characters, empty strings, boundary
integers, unusual type combinations.

Two SQL engines that implement the same specification independently are likely
to find the same corners difficult, for the same reasons, *without* sharing any
code. So a reproduction from engine A is a plausible probe for engine B.

This is the project's hypothesis, and the thing it is named for: **take the
shape of a defect that is already known somewhere else, and graft it onto a new
host.**

## Why this is worth doing rather than obvious

Two objections are worth stating, because the design has to answer them.

**"Isn't this just running someone else's tests?"** A reproduction from another
engine usually does not transfer directly: it may use syntax the second engine
lacks, and its specific literal values were chosen for the first engine's
defect, which is likely already fixed in the second. What transfers is the
*shape* — which functions, nested how, with which kinds of argument. So the
values have to be regenerated while the structure is kept, which is what
`graft/corpus/templates.py` does.

**"Isn't this just differential testing?"** No, and that distinction is what
made the result possible. Differential testing needs a reference implementation
that agrees on the feature under test. The defects reported here are in `lpad`,
which SQLite does not have — so no SQLite-differential oracle could have judged
them, including Turso's own. The oracle used here needs no reference at all: a
single statement must not terminate the process.

## What this project claims

That the method found a real, previously unknown defect in a well-fuzzed engine,
that the defect sits provably outside what that engine's own fuzzer can express,
and that the fix was accepted upstream.

It does not claim that the method is better than grammar-based generation in
general, that two defects establish a rate, or that it would work as well on an
engine without a rich upstream bug tracker to mine. `docs/evaluation.md` states
the limits, and `docs/provenance.md` records exactly which of the two reported
defects this tool found and which it did not.
