---
name: ui_deck
description: Start woodglue server, take UI screenshots with Playwright, and build a self-contained HTML presentation deck
argument-hint: <what to highlight -- UI functionality, code paths, RPC calls>
---

Build a self-contained HTML slide deck showcasing woodglue's UI by starting the
server, driving a headless browser, and assembling screenshots into a navigable
presentation.

## Argument & Planning

`$ARGUMENTS` contains an optional prompt describing what to highlight (UI
functionality, code paths, RPC calls, specific features).

If no argument is provided, run `git log --oneline -20` and review recent
changes to identify notable functionality worth demonstrating.

Plan the slide sequence before taking any screenshots. Think about narrative
arc: start broad (home view), drill into specifics (namespaces, methods, runs,
DAGs), and close with any interesting edge cases.

### Output Filename

Determine the output path:
- `{date}` from `date +%Y-%m-%d`
- `{name}` -- up to 25 chars summarizing the prompt, lowercase with dashes or
  underscores, no spaces. Default: `ui-deck`
- Full path: `docs/ai/decks/{date}-{name}.html`

### Overrides

The user can pass `--data <dir>` and `--port <port>` in the arguments. Defaults:
`--data ./data`, `--port 5321`.

## Step 1: Ensure Dependencies

```bash
uv run playwright install chromium 2>/dev/null || (uv add --dev playwright && uv run playwright install chromium)
```

Ensure the output directory exists:

```bash
mkdir -p docs/ai/decks
```

## Step 2: Build UI

```bash
cd src/woodglue/ui && npm install && npx vite build
```

## Step 3: Start Server

Start the server as a background process. Note: `--data` and `--port` are
top-level flags on `wgl`, NOT subcommand flags on `start`:

```bash
uv run wgl --data ./data start &
```

Capture the PID from the background job or read it from `data/wgl.pid`.

**IMPORTANT: Always use `127.0.0.1`, never `localhost`.** The server binds
`127.0.0.1` only. Using `localhost` may resolve to IPv6 `::1` first, causing
connection refused or silent auth failures when curl retries on IPv4.

Wait for the server to be ready:

```bash
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5321/ui/ | grep -q 200 && break
  sleep 0.5
done
```

If auth is enabled, extract the token programmatically:

```bash
uv run python -c "from woodglue.token_store import get_single_token; print(get_single_token('data/auth.db'))"
```

If the server fails to start, report the error and stop.

## Step 4: Explore & Screenshot

### 4a: Discover Available Content

Call RPC to discover what namespaces and capabilities are available. If auth
is enabled, include the Bearer token header. Always use `127.0.0.1`:

```bash
TOKEN="<token from step 3>"
curl -s http://127.0.0.1:5321/rpc \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","method":"system.list_namespaces","id":1}'
```

Based on the response, decide which UI states to capture. Consider what
namespaces exist and their capabilities (expose_api, run_engine, has_cache).

### 4b: Plan Screenshot Sequence

Before writing the Playwright script, plan which states to capture. Consider:

- Home view with namespace cards
- Namespace detail view (methods list, triggers, runs)
- Method documentation view
- Fire a trigger and capture the result
- Active/recent runs list
- DAG graph visualization with node states
- Node detail panel with I/O data
- Any error states or edge cases worth showing

### 4c: Write and Run Playwright Script

Write a disposable Python helper script to a temp file and run it with
`uv run python <script>`.

The script should use Playwright's sync API:

```python
from playwright.sync_api import sync_playwright
import base64, json, os, sys

port = int(sys.argv[1])
output_dir = sys.argv[2]
auth_token = sys.argv[3] if len(sys.argv) > 3 else None

os.makedirs(output_dir, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})

    if auth_token:
        context.add_cookies([{
            "name": "wgl_token",
            "value": auth_token,
            "domain": "127.0.0.1",
            "path": "/",
        }])

    page = context.new_page()

    # For each planned screenshot:
    #   1. Navigate or click
    #   2. Wait for content to load (networkidle or specific selectors)
    #   3. Take screenshot as PNG
    #   4. Save to output_dir with sequential naming (01_home.png, etc.)

    browser.close()
```

All screenshots should be PNG at 1280x800 viewport.

## Step 5: Build HTML Deck

After collecting all screenshots, assemble the HTML deck. Read each screenshot
file, base64-encode it, and embed it in the HTML.

### Deck Structure

The HTML deck must be completely self-contained: all images base64-encoded, all
CSS and JS inline, no external dependencies.

**Layout:**
- Navigation sidebar on the left with clickable slide titles grouped by section
- Main area showing one slide at a time
- Current slide highlighted in sidebar
- Keyboard navigation: left/right arrows, j/k keys

**Styling:**
- Dark theme matching woodglue UI aesthetic (dark background, cyan accents)
- Clean, minimal design

### Slide Types

Claude decides per-slide what format is most appropriate:

- **Screenshot slide**: base64 `<img>` filling the main area, caption text below
- **Code slide**: syntax-highlighted `<pre>` block with inline CSS coloring (no
  external libraries)
- **Combined slide**: screenshot on top with code or caption below (or
  side-by-side for wide content)
- **Section header**: large title text as a visual divider between logical groups

When a code snippet or RPC call is relevant to explain a screenshot, read the
relevant source file and include the pertinent lines as a code slide or as part
of a combined slide.

### Write the Deck

Write the final HTML to the planned output path (`docs/ai/decks/{date}-{name}.html`)
using the Write tool.

## Step 6: Cleanup

- Kill the server: `kill $(cat data/wgl.pid)` or kill by the captured PID
- Remove the temp Playwright script
- Report the output path to the user

## Important Notes

- The Playwright script is disposable -- write it fresh each time, remove after use.
- All screenshots are PNG at 1280x800 viewport.
- If the server fails to start, report the error and stop immediately.
- The HTML deck must be completely self-contained (all images base64, all CSS/JS inline).

### Auth Handling

The woodglue UI stores the auth token in a `wgl_token` cookie, but sends it as
an `Authorization: Bearer` header in JS fetch calls. Setting the cookie in
Playwright is sufficient for the SPA at `/ui/` (the JS reads the cookie and
adds the header). However, **do NOT navigate directly to non-SPA endpoints**
like `/docs/llms.txt` or `/docs/openapi.json` -- those require the Bearer
header which Playwright can't set on page navigations. Only screenshot pages
under `/ui/`.

### Known CSS Selectors

Before writing the Playwright script, read `src/woodglue/ui/src/main.js` to
find the actual CSS selectors. Key selectors as of v0.0.4:

- `.ns-card` -- namespace cards on home view
- `.run-row[data-run-id]` -- clickable run items in run list
- `.graph-node[data-label]` -- clickable DAG nodes in graph SVG
- `.io-load-btn` -- "Load I/O" button in node detail panel
- `.detail-close` -- close button on detail panel (the X)
- `text=<name>` -- sidebar method/namespace links (use Playwright text selector)

Always verify selectors against the current main.js before scripting. Do NOT
guess selectors like `.run-item`, `.dag-node`, `.node-box` -- these do not
exist.

### Networking

Always use `http://127.0.0.1:<port>`, never `http://localhost:<port>`. The
server binds `127.0.0.1` and `localhost` may resolve to IPv6 `::1`, causing
connection failures or silent auth mismatches.
