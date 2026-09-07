---
name: stack3d-onboard-tech
description: Add a process architecture (a new archit/<NAME>/ directory, or a changed existing one) to the tools/stack3d 3D layout viewer, by reading its tech.py, layer JSON and GDS writer and turning that into a z-stack model. Use this whenever someone wants to see, visualize, understand, compare or explain a standard-cell process architecture in 3D — a stacked CFET, a backside-power or multi-tier flow, an internal or customized variant — or says the stack3d viewer is missing a technology, missing layers, or shows the wrong stacking order. Also use it when someone asks how a tech's layers sit relative to each other, which GDS layer a shape lands on, or why two tiers look superimposed in a 2D layout viewer.
---

# Onboarding an architecture into stack3d

`tools/stack3d/` renders a real solved cell as an interactive 3D stack. It
exists because the engine is 2D: the layer JSONs describe each layer only in
the plane (`direction` / `pitch` / `offset` / `width`), and GDS is a planar
format, so **nothing in the repo carries a layer thickness**. The viewer
supplies that missing axis.

That gap is worst on stacked architectures. In a CFET the two devices share
one column footprint and differ only in z, so `P_ACTIVE` and `N_ACTIVE` land
on *identical* x/y bounds and the GDS can only tell them apart by datatype —
in any 2D layout viewer they are perfectly superimposed. Pulling them apart is
the whole point of this tool.

Your job is to turn one architecture's source into one `layers.py` entry.

## What you are actually producing

A stack table: an ordered list of rows, one per GDS layer/datatype the writer
emits, each with a z interval.

```python
# (gds_key, display_name, z0, z1, group, color, note, default_on)
("11/2", "N_ACTIVE", 0, 46, "bot", "#10b981", "BOTTOM tier diffusion (NMOS, on BPC)", 1),
```

plus a `TECHS[...]` entry wiring it to the preset, layer JSON and GDS writer.
`build.py` needs no changes — if you find yourself editing it, you have
probably misread the extension point.

**The honesty line matters more than the visuals.** Order is derived; z values
are not. Keep that distinction intact in what you write and what you tell the
user, because someone will eventually read these numbers as if they were PDK
data. Say plainly which is which.

## Step 1 — inspect before you read anything by hand

```bash
python tools/stack3d/inspect_tech.py --name <NAME>
```

It finds the layer JSON, `tech.py`, GDS writer and preset by convention (each
overridable: `--layer`, `--tech-py`, `--writer`, `--preset`; `--engine` if the
engine is not at `<repo>/engine`) and prints the LGG z order, the via chain,
virtual edges, GDS-only layers, the tech class defaults, and which
layer/datatype pairs the writer references.

This is deliberately the first move. Reading a thousand-line GDS writer to
find `self.layout.layer(15, 0)` calls is exactly the tedium the script
removes, and its output is what the rest of the steps consume.

Read `references/reading-an-architecture.md` for what each of those outputs
means and which source lines to open when something looks off.

## Step 2 — get one solved cell

The viewer needs real geometry, so solve a cell and generate its GDS. Pick a
**small** one: an inverter exercises the whole stack and solves in under a
second, where a flip-flop can run for minutes and adds nothing to the picture.

Register the architecture in `layers.py` first (a stub stack table is fine),
then:

```bash
python tools/stack3d/build.py --tech <NAME> --solve --gds
```

`--solve` needs the engine's full dependencies (ortools, klayout, networkx,
loguru, matplotlib, scikit-learn). If the environment cannot solve, get a
`.res` from someone who can and drop it in `data/solved/<run>/`, then use
`--gds` alone — that only needs klayout.

If the writer's CLI flags differ from the existing ones, put them in the
tech's `gds_argv`; that field exists so `build.py` never needs a per-tech
branch.

Registering with an empty stack table is a useful trick here: the build then
warns about every layer that has geometry and no row, sorted in roughly stack
order. That list is your checklist for the next step.

## Step 3 — assign z intervals

Now re-run the inspector against the GDS you just made:

```bash
python tools/stack3d/inspect_tech.py --name <NAME> --gds tools/stack3d/data/solved/<run>/<CELL>.gds
```

For an unregistered tech it prints a paste-ready skeleton with real layer
names and `0, 0` z placeholders. For a registered one it prints a coverage
diff instead: what the writer drew that your table does not claim, and what
your table claims that has no geometry.

Fill the z columns using this reasoning:

- **The metal/via backbone is given.** The LGG order from step 1 is the metal
  ordering; each via sits in the gap between the two metals it names. Walk up
  from the bottom and give each a slab. Do not reorder them to taste.
- **Device layers hang off their placement layer.** Diffusion, fins,
  source/drain trench and local interconnect belong to the tier whose gate
  poly they contact. On a multi-tier architecture, work out which placement
  layer each datatype belongs to before assigning anything — that mapping is
  the entire reason the viewer exists.
- **Enveloping layers span, they do not stack.** A gate that physically runs
  through two tiers gets one interval covering both, not two rows. Add it to
  `ALPHA` so it renders semi-transparent, or it hides the tiers it explains.
- **Leave real gaps.** Isolation between tiers should read as space. Touching
  slabs look like one solid block.
- Negative z is legitimate and means backside.

A row whose geometry is empty is fine and often informative — an unused tier
stays visible in the rail, greyed. A row you *omit* disappears silently, which
is why `build.py` warns about drawn-but-unclaimed layers. Do not silence that
warning with `ignore_keys` unless the layer is genuinely redundant (a
datatype-0 union copy of shapes you already draw per-tier is the usual case).

## Step 4 — build and look at it

```bash
python tools/stack3d/build.py --tech <NAME>
```

Then actually open `tools/stack3d/smtcell-stack.html` and look. The failure
modes are visual and obvious once seen: a tier buried inside an opaque gate, a
via floating in a gap, two tiers fused because you left no isolation, a stack
so tall the fit looks wrong. None of them show up in the build output.

If you can drive a browser, screenshot it. If you cannot, say so rather than
claiming it looks right.

Build the full set (`build.py` with no `--tech`) before you finish, so the new
architecture sits next to the existing ones and the switcher includes it.

## Step 5 — report what you did

Tell the user, concisely:

- which files you read to derive the order, and what that order is;
- that the z intervals are illustrative and where they live, so they can tune
  them;
- anything you could not determine, and what you assumed instead;
- any layer the writer emits that you deliberately did not draw, and why.

Never present the thicknesses as measured. If the user asks for real ones,
they have to come from a PDK — the repo does not have them.

## When the architecture already exists and only changed

Same flow, shorter: run the inspector with `--gds` against a freshly
regenerated GDS and work the coverage diff. New layers show up as
drawn-but-unclaimed; removed ones as claimed-but-empty. Re-check the z order
against the printed LGG order, since a changed `layer_number` silently
reorders the stack while every existing row still looks fine.

## References

- `references/reading-an-architecture.md` — what each engine file contributes,
  how the doubled-resolution coordinate system works, and the traps that have
  actually bitten (canvas width off by 2/3, GDS numbers hardcoded vs
  JSON-driven, per-writer SCALE/dbu, datatype-0 union copies).
- `tools/stack3d/README.md` — the platform itself: layout, commands,
  dependencies, and the real-vs-illustrative table.
