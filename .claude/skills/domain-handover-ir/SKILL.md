---
name: domain-handover-ir
description: Design a handover format — an intermediate representation — that a domain expert can fill in by hand and a system can ingest without a human retyping it. Use whenever someone says a specialist "will tell me the concept" and they need a standard way to receive it: process/device engineers describing a new technology, hardware or physical-design specs, lab protocols, financial or actuarial rules, clinical or regulatory criteria, config an ops team hands to a platform. Also use when an existing schema is described as too technical for the people who must fill it in, when handover happens through slide decks or email prose that someone re-keys into code, or when asked to turn a concept description into something machine-readable and checkable.
---

# Designing a handover IR

The situation: an expert holds knowledge in their own vocabulary, a system needs
it in another, and today a person translates between them by hand. That
translation is where the errors live and where the delay lives.

A good IR removes the translator. It is not a serialisation of your data model
with friendlier key names — that just moves the translation into the expert's
head, where you cannot see it go wrong.

## The one question that decides the design

**What does the expert actually know, and what are you currently making them
invent?**

Anything they must invent to satisfy your schema is a defect in the schema. It
produces confident-looking numbers with nothing behind them, and nobody
downstream can tell which fields were known and which were filled in to make the
form validate.

Find those fields first. They are usually where your implementation needs a
concrete value but the domain only has an ordering, a category, or a
relationship. The fix is the same each time: **let the expert state what they
know, and derive what they don't.**

A worked case, from a chip process-stack tool: the viewer needed each layer's
absolute z interval in nanometres. The engineer knows the *order* of layers —
that is just the process flow — and knows what each layer *is*. They do not know
thicknesses, and neither did the repo. Asking for coordinates would have
manufactured precision. Asking for order plus a role, and computing z from a
per-role table, asked only for what was real.

## Method

### 1. Ground the format in what already exists

Before designing anything, read the current artefacts the system consumes and,
if you can, a real example of what the expert hands over today (a slide, an
email, a table). The gap between those two is the format's job description.

Enumerate the fields the system genuinely needs, and for each one ask: does the
expert know this, can it be derived, or is it implementation detail? Three
buckets, three different treatments:

| bucket | treatment |
|---|---|
| the expert knows it | ask for it, in their words |
| derivable from what they know | derive it; never ask |
| implementation wiring | put it in a clearly separate section, and say in the file who fills it in |

That last split matters more than it looks. A single flat schema makes the
expert feel responsible for fields they should not touch, and they will either
guess or stall.

### 2. Choose the encoding for the reader, then check your constraints

The expert has to read and edit this, so comments and low syntax noise are worth
real money. But the constraint that actually decides it is usually operational:
what can the consuming system parse without adding a dependency, and can it
still run where it has to run?

Work out the constraint before falling in love with a format. In the worked case
the pipeline had to rebuild inside an air-gapped environment on the standard
library alone. That ruled out YAML (PyYAML is not stdlib) and made JSON's
missing comments painful — TOML took comments, read like an INI file, and
`tomllib` has been in the standard library since Python 3.11. Say the reasoning
in the file itself, so the next person does not relitigate it.

### 3. Vocabulary from the domain, not the codebase

Every field name and enum value should be a word the expert already uses. If
your system calls it a `gds_key` and they call it a mask layer, the file says
the thing they call it, and the compiler maps.

Prefer **relative and categorical** over absolute and numeric:

- "this sits at the same level as X" beats "z0 = 46"
- `role = "diffusion"` beats a thickness, a colour and a group
- "listed bottom to top" beats an explicit index

Relative statements are also more likely to stay true. An absolute coordinate is
wrong the moment anything upstream changes; an ordering is not.

### 4. Defaults carry the boring fields

Give every category a default for everything that follows from it. The expert
fills three fields per entry; the compiler produces ten. Overriding stays
possible for the case that needs it, and the override is then a visible,
deliberate exception rather than one row among many.

### 5. Errors in their vocabulary

The compiler's messages are part of the format's user interface — usually the
part the expert meets most. Each one should name the specific entry, say what is
wrong in domain terms, and say what to do:

```
layer 'ACTIVE' aligns to 'FN', which is not a layer listed before it
layer 'ACTIVE' has no `gds`. Every drawn layer needs its layer/datatype,
  e.g. gds = "11/2". If it is deliberately never drawn, set role = "model_only".
```

Not `KeyError: 'FN'`. Test this by writing deliberately broken files and reading
what comes out; it is quick and it is the difference between a format someone
can use alone and one that needs you on the phone.

### 6. Prove the format on what already exists

This is the step that separates a real IR from a plausible one, and it is worth
insisting on: **express the existing cases in the new format and check they come
back out identical.**

If the system already has hand-built configurations, write an exporter, round-trip
every one of them, and diff. Passing means the format is expressive enough — a
claim you would otherwise only be able to assert. Failing tells you exactly which
concept you missed, in the form of the case that will not fit.

In the worked case the round-trip was exact for all three existing
architectures, which made it safe to delete the hand-built tables and promote the
new format to sole source of truth. The same exercise surfaced a
misclassification that had been sitting in the old table unnoticed.

Keep the exported files as the worked examples the expert copies from. Examples
drawn from real, working cases beat invented ones, and they cannot go stale
without the check failing.

### 7. Ship a template that teaches

The blank template is documentation that cannot drift, because it is also a
valid input. Put in it:

- the rules stated in three or four lines at the top
- the category list with what each one means
- every placement or relationship option in one table
- a worked mini-example, complete and compiling
- the validate command
- a visible marker for the section the expert should not fill in

Name it so the loader skips it (a leading underscore works) but the validator
can still be pointed at it.

## What to hand back

State the design decision and its reason — what the expert states, what the
system derives, and why the encoding was chosen. Then the evidence: the
round-trip result, and what the conversion caught. If you promoted the new
format to sole source of truth, say what you verified before doing so.

Be straight about what the IR still cannot express, and about anything it
supplies that is illustrative rather than known. An IR that quietly invents
precision is worse than the prose it replaced, because prose does not look
authoritative.

## References

- `references/worked-example.md` — the process-stack case end to end: the field
  audit, the four placement forms and why those four, the round-trip result, and
  the schema in full.
