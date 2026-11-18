# Namespace-defined Required Actions

A method may declare one Required Action other than `call`, such as `write`, and only
a Grant for that Action on the namespace lets a Caller run it. A namespace's Actions
are whatever its methods declare; the built-in names `read`, `trigger`, `view`, and
`admin` are reserved. The Required Action alone is enough: `write cointoss` runs the
`write` methods of `cointoss` and nothing else, and it makes the namespace visible.

A separate Action is used because no Action implies another, so `call *` or
`call cointoss*` can never reach a `write` method. Grant patterns stay namespace
patterns matched with fnmatch.

## Considered Options

- **A `requires` Grant on a made-up target**, e.g. `call cointoss.portfolio`. Rejected:
  fnmatch lets `call *` and `call cointoss*` match it, which is the leak this fixes.
- **Method-level targets** (`call cointoss.add_trade`) with dot-aware globs. Rejected:
  it changes glob semantics for every `call` Grant and adds a second target shape,
  for granularity no current namespace needs.
- **A fixed built-in `write` Action.** Rejected as too narrow: a namespace may need
  more than two tiers, and declared Actions cost no more than one built-in.

## Consequences

- A Role Grant naming an Action that no loaded namespace declares only produces a
  startup warning, so a typo silently grants nothing rather than stopping the server.
- Firing or activating a trigger on a method needs that method's Required Action as
  well as `trigger`, so `trigger` cannot be used to run writes.
- There are no wildcard Actions: `admin` is the only Grant that reaches every Action.
