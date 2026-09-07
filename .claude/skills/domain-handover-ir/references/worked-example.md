# Worked example: a process-stack handover format

The case the method in `SKILL.md` came from, end to end. Useful as a shape to
copy, and as evidence that each step earns its place.

## Contents

- [The situation](#the-situation)
- [Step 1: the field audit](#step-1-the-field-audit)
- [Step 2: choosing the encoding](#step-2-choosing-the-encoding)
- [Step 3: the four placement forms](#step-3-the-four-placement-forms)
- [Step 4: role defaults](#step-4-role-defaults)
- [Step 5: the round-trip proof](#step-5-the-round-trip-proof)
- [The schema in full](#the-schema-in-full)

## The situation

A tool renders standard-cell layouts as 3D layer stacks. Adding a technology
meant hand-editing a Python table of tuples:

```python
("11/2", "N_ACTIVE", 0, 46, "bot", "#10b981", "bottom-tier diffusion", 1),
```

That row wants a GDS layer/datatype key, two absolute z coordinates in
nanometres, a display group, a hex colour, and a visibility flag. A process
engineer arriving with a new architecture knows none of those six things in
that form — and the person translating had to invent the z values, because
nothing upstream carried a layer thickness at all.

## Step 1: the field audit

Six fields, three buckets:

| field | bucket | resolution |
|---|---|---|
| GDS layer/datatype | expert knows it | ask — it is the one identifier they share with the tool |
| ordering | expert knows it | ask, as the file's own top-to-bottom order |
| what the layer is | expert knows it | ask, as `role` |
| z0 / z1 | **invented** | derive from role + order |
| colour, group | implementation | derive from role |
| visibility | implementation | derive from role |

The z fields were the whole problem. They looked like data and were not: nothing
in the repository carries a layer thickness, so every number in that column had
been chosen by hand to make the picture look right. Asking an engineer for them
would have produced the same fiction with more authority behind it.

Deriving them from role plus order asks only for what is real, and moves the
illustrative part into one visible table (`techspec.ROLES`) instead of scattering
it across every row.

## Step 2: choosing the encoding

Candidates and how they fell:

| | comments | reads like | dependency |
|---|---|---|---|
| JSON | no | code | none |
| YAML | yes | prose | **PyYAML — not stdlib** |
| TOML | yes | INI file | none (`tomllib`, Python 3.11+) |

The deciding constraint was not aesthetics: this pipeline has to rebuild inside
an air-gapped environment using the standard library only, because the built page
is carried into a network-isolated site. That eliminated YAML outright.

Between JSON and TOML, comments decided it. A handover format that cannot carry
an explanation next to the field forces the explanation into a separate document,
which then drifts. TOML also happens to look like an INI file, which engineers
recognise even when they have never heard the name.

The reasoning is written into the format's own docstring, so the next person does
not have to rediscover it.

## Step 3: the four placement forms

The first attempt had one rule — each layer sits on top of the previous one —
and it broke immediately on real data: fins and the diffusion around them
co-occupy a level, and a source/drain trench sits *inside* the diffusion.

The exporter exposed this as a row of "overlaps the layer below by 42 nm"
comments, which is the format admitting it cannot say what it means.

Four forms cover every case across three real architectures:

| form | meaning | the case that needs it |
|---|---|---|
| (nothing) | sits on the previous layer | the whole BEOL |
| `gap = N` | leave N nm empty first | isolation between two device tiers |
| `align = "X"` (+ `offset`) | start where X starts | fins and diffusion; a trench inside diffusion |
| `span = ["A", "B"]` | stretch from A's bottom to B's top | one gate stack running through both tiers |

Note what `align` avoided: a negative `gap`. That would have been fewer concepts
and strictly worse, because "gap = -42" is a coordinate calculation the reader
has to perform, while `align = "FIN"` is a statement about the process.

The same reasoning removed a mirroring flag. A backside tier whose contacts reach
downward is simply *written* with its interconnect before its diffusion — the
file's order already says it, so no flag is needed.

## Step 4: role defaults

Thirteen roles, each carrying a default thickness, display group, colour and
visibility:

```python
#            thickness  group        colour     on
"diffusion":  (46, "device", "#10b981", 1),
"via":        (16, "via",    "#e2e8f0", 1),
"metal":      (14, "beol",   "#3b82f6", 1),
```

The engineer writes three fields per layer and gets seven. Overrides remain
available, and because they are rare they read as deliberate exceptions rather
than noise.

Roles also gave a natural home for a rendering rule that had been ad hoc: layers
that *envelope* others (a gate through both tiers, diffusion around fins) must
render semi-transparent or they hide the thing they exist to explain. That is a
property of the role, not of the individual layer, so it moved into the role
table.

## Step 5: the round-trip proof

The step that turns "this looks expressive enough" into a fact.

Write an exporter from the existing hand-built tables into the new format, then
compile the exported files back and diff against the originals:

```
FinFET   16 layers  IDENTICAL
CFET     21 layers  IDENTICAL
QFET     34 layers  IDENTICAL
```

Layer for layer, z for z, including the two hardest cases — a gate spanning two
tiers, and a backside tier whose layers run in mirror order.

That result is what made it safe to delete the Python tables and promote the new
format to sole source of truth. Without it the honest statement would have been
"the format probably works", and the old tables would have had to stay as a
fallback, leaving two sources of truth.

The exercise also paid for itself directly: role inference classified three
inter-tier vias as gates, which would have rendered them semi-transparent. That
misclassification had been sitting in the hand-built table unnoticed. Round-tripping
is a review of the existing data as much as a test of the new format.

Keep the exports as the committed worked examples. They cannot go stale without
the validation failing.

## The schema in full

```toml
schema  = "stack3d/tech@1"
name    = "MYTECH"
order   = 3                  # position in the viewer's switcher
summary = "one line on the geometry"

[[tier]]                     # placement tiers, bottom to top; omit if planar
name = "bottom"

[geometry]                   # free text shown in the spec strip
summary = "..."
placement = "..."
pin_access = "..."
virtual_edges = "..."

[bind]                       # wiring to the engine — NOT the engineer's half
preset = "MYTECH_4T_SH"
layer_json = "PROBE3_MYTECH....json"
gds_writer = "gds_MYTECH_SH"
gds_argv = ["--result_file", "{res}", ...]
ignore_keys = ["11/0"]       # drawn but deliberately not shown

[[layer]]                    # bottom to top; the order IS the z model
name = "N_ACTIVE"
role = "diffusion"           # drives thickness, colour, group, visibility, alpha
gds  = "11/2"
tier = "bottom"
align = "FIN"                # or: gap = N / span = ["A","B"] / nothing
offset = 0
thickness = 46               # optional; role default otherwise
note = "shown in the layer rail"
```

`[bind]` is separated and labelled precisely so the engineer knows which half is
not theirs. Without that split they either guess at it or stop and ask.
