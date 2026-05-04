# Documentation Revamp

## Problem

The README.md is badly outdated — wrong CLI commands, references to concepts
that don't exist in the codebase (Action, ActiveData, Workflow, Kits), marketing
fluff, and no mention of lythonic, DAG orchestration, auth, or the UI.

docs/index.md is accurate but incomplete (only covers JSON-RPC/docs surface).
docs/MillFarm.md is an unrelated brainstorming artifact. getting-started.md has
a wrong CLI command.

## Target Audience

Developers evaluating woodglue for their own projects (pip install).

## Changes

### README.md — Full Rewrite

**Structure:**

1. Title + badges (CI, PyPI, Python versions, License)
2. One-line description
3. Architecture diagram (text/ASCII) showing lythonic/woodglue split:
   - lythonic: Namespaces, DAG orchestration, caching, scheduling
   - woodglue: HTTP server (Tornado), JSON-RPC 2.0, auth, UI, docs generation
4. Features list (concise bullets):
   - JSON-RPC 2.0 with multi-namespace routing
   - Auto-generated docs: llms.txt, per-method markdown, OpenAPI 3.0.3
   - Built-in UI: API browsing, trigger management, DAG visualization
   - Bearer token authentication
   - Typed async client with auto-resolved return types
   - YAML-driven namespace configuration
   - DAG orchestration with scheduled triggers (via lythonic)
5. Quick Start — corrected commands:
   - `pip install woodglue`
   - Minimal Python code example (register a function)
   - `wgl start` (not `wgl server start`)
   - curl example with correct port (5321) and URL format
6. Documentation link to GitHub Pages site
7. Star History badge at bottom:
   `[![Star History Chart](https://api.star-history.com/svg?repos=walnutgeek/woodglue&type=Date)](https://star-history.com/#walnutgeek/woodglue&Date)`

**Delete entirely:**
- "Why WoodGlue?" metaphor section
- "Let's Build Something Together" section
- Phil Jackson quote
- "Key Blocks" section (Action, ActiveData, Workflow, Kits)
- Empty lines at bottom

### docs/index.md — Expand

Keep current structure but:
- Add architecture diagram (same as README)
- Expand features list to include engine/DAG/trigger capabilities
- Mention auth, system namespace
- Correct Quick Start code if needed

### docs/getting-started.md — Fix CLI

- `wgl server start` → `wgl start`

### docs/MillFarm.md — Delete

Unrelated brainstorming artifact.

### mkdocs.yml — Update Nav

- Add v0.0.5 to release notes
- No MillFarm reference exists in nav (already unlisted)

### Reference Pages — No Changes

All 7 pages verified accurate against current code.

## Files to Modify

1. `README.md` — full rewrite
2. `docs/index.md` — expand features, add architecture diagram
3. `docs/getting-started.md` — fix one CLI command
4. `docs/MillFarm.md` — delete
5. `mkdocs.yml` — add v0.0.5 release notes

## Verification

- `make lint` passes
- `uv run mkdocs build` succeeds with no errors
- README renders correctly on GitHub (check badges, diagram)
