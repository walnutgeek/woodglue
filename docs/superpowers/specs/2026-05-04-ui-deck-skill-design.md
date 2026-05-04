# UI Screenshot Deck Skill

## Problem

There's no way to quickly showcase woodglue's UI functionality. Demonstrating
features requires manually starting the server, navigating the UI, and taking
screenshots. There's no reusable workflow or artifact for sharing what the UI
looks like and how it works.

## Skill Argument

The skill accepts an optional prompt describing what to highlight: specific
UI functionality, code paths, RPC calls, or features to showcase.

If no argument is provided, Claude reviews recent git changes and identifies
notable functionality to demonstrate.

Before taking screenshots, Claude plans the slide sequence — consider the
narrative arc and arrange topics in logical order.

### Output Filename

`docs/ai/decks/{date}-{name}.html` where:
- `{date}` — today's date via `date +%Y-%m-%d`
- `{name}` — up to 25 chars summarizing the prompt, lowercase with dashes
  or underscores, no spaces. Default: `ui-deck`

## Solution

A Claude Code skill (`/ui_deck`) that starts the woodglue server, drives a
headless browser via Playwright, takes screenshots of UI states, and assembles
a self-contained HTML presentation deck with base64-embedded images.

## Design

### Skill File

Located at `.claude/skills/ui_deck.md`. Invoked as `/ui_deck` with optional
args (e.g. `/ui_deck --data ./data --port 5321`). Defaults: `--data ./data`,
port from config (5321).

The skill instructs Claude to execute the full workflow: install deps, start
server, explore UI, screenshot, build deck, clean up. Claude decides the
navigation flow based on what namespaces/engines/triggers are available.

### Server Lifecycle

1. **Start**: `uv run wgl start --data <data_dir> &` as background process,
   capture PID.
2. **Wait**: Poll `http://localhost:<port>/ui/` until 200 (timeout ~10s).
3. **Auth**: If enabled, read token from server stdout or config and set
   `wgl_token` cookie in the browser.
4. **Cleanup**: Kill server by PID after screenshots are done.

### Screenshot Workflow

Claude writes a disposable Python helper script using Playwright's sync API:
- Launches headless Chromium at 1280x800 viewport
- Navigates to `http://localhost:<port>/ui/`
- Takes screenshots as PNG bytes

Claude drives exploration by:
1. Calling RPC (`system.list_namespaces`) to discover available content.
2. Deciding which UI states to visit: home view, namespace cards, method
   docs, run lists, DAG graphs, node detail panels, etc.
3. Using Playwright to click elements, wait for content, capture.

Each screenshot gets a caption. Claude also decides per-slide whether to
include additional context:
- **Screenshot + caption** for straightforward UI states.
- **Screenshot + code snippet** when the view illustrates a feature worth
  showing the implementation of.
- **Screenshot + RPC request/response** when the API interaction is the
  interesting part.
- **Code-only slide** to explain implementation context before or after a
  screenshot.

Slides can branch — e.g. a namespace overview branches into "API methods"
and "Engine/DAGs" sub-sections. Claude builds the narrative as it explores.

### HTML Deck Format

Single self-contained HTML file at the output path described above.

**Structure:**
- Navigation sidebar with clickable slide list grouped by section.
- Main area showing one slide at a time.
- Keyboard navigation: arrow keys, j/k.

**Slide types:**
- Screenshot slide: base64 `<img>` with caption.
- Code slide: `<pre>` with inline CSS syntax highlighting.
- Combined slide: screenshot + code or caption side by side.
- Section header: visual divider grouping related slides.

**Styling:** minimal dark theme matching woodglue's UI aesthetic. All
CSS/JS/images inline — no external dependencies.

### Dependencies

Add `playwright` to dev dependencies in `pyproject.toml`. Chromium binary
installed on demand via `uv run playwright install chromium`.

No other project files modified. The Playwright helper script is written to
a temp location, executed, then removed.

## Files to Create/Modify

1. `.claude/skills/ui_deck.md` — skill instructions
2. `pyproject.toml` — add `playwright` to dev deps
3. `docs/ai/decks/` — output directory for generated decks (gitignored or committed per user choice)

## Verification

Invoke `/ui_deck` with the dev `data/` directory running `gref_hello`.
Confirm the output HTML file opens in a browser and displays screenshots
with navigation.
