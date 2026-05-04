# Implementation Plan: Documentation Revamp

Based on: `docs/superpowers/specs/2026-05-04-docs-revamp-design.md`

## Step 1: Rewrite README.md

Full rewrite with: badges, one-liner, architecture diagram, features list,
corrected Quick Start, docs link, star history badge.

## Step 2: Update docs/index.md

Expand features list, add architecture diagram, mention engine/DAG/triggers,
auth, system namespace.

## Step 3: Fix docs/getting-started.md

Change `wgl server start` → `wgl start` on line 59.

## Step 4: Delete docs/MillFarm.md

Remove the file.

## Step 5: Update mkdocs.yml

Add v0.0.5 to release notes nav.

## Step 6: Verify

Run `uv run mkdocs build` to confirm no broken references.
