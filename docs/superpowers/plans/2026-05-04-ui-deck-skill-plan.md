# Implementation Plan: UI Screenshot Deck Skill

Based on: `docs/superpowers/specs/2026-05-04-ui-deck-skill-design.md`

## Step 1: Add playwright dev dependency

Add `playwright` to the dev dependency group in `pyproject.toml` and run
`uv sync`.

## Step 2: Create the skill file

Create `.claude/skills/ui_deck/SKILL.md` with frontmatter and full
instructions for Claude. The skill must cover:

- Argument parsing (optional prompt, `--data`, `--port` overrides)
- Default behavior when no prompt (review recent git changes)
- Slide sequence planning before screenshots
- Server start/wait/cleanup lifecycle
- Playwright helper script pattern (write to temp, run, clean up)
- Screenshot capture and captioning
- Flexible slide content (screenshot, code, RPC, combined, code-only)
- HTML deck assembly with base64 images
- Output filename template: `docs/ai/decks/{date}-{name}.html`
- `docs/ai/decks/` directory creation

## Step 3: Create output directory

Create `docs/ai/decks/.gitkeep` so the directory exists. Add
`docs/ai/decks/*.html` to `.gitignore` (decks are large binary-like
artifacts that shouldn't be committed by default).

## Step 4: Verify

Run `make lint` to confirm no issues. Verify the skill file is well-formed
by checking its frontmatter matches the `do_release` pattern.
