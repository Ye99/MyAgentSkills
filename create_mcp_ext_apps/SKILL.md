# create_mcp_ext_apps

## Purpose
Build MCP ext-apps quickly and reliably by reusing the reference architecture while allowing tool functionality to vary (for example, dynamic input canvas today, document-rendering canvas tomorrow).

## Core Learnings

1. Start from the reference structure.
- Reuse the `shadertoy-server` pattern:
- `main.ts` for stdio/HTTP transports.
- `server.ts` for `registerAppTool` and `registerAppResource`.
- `mcp-app.html` + `src/mcp-app.ts` for UI rendering.

2. Treat `inputSchema` as the contract.
- Strong typing is the most important part for LLM callers.
- Use discriminated question types (for example `kind: "text" | "single_choice"`).
- Add detailed `.describe(...)` annotations so callers can assemble valid payloads.

3. Normalize to one internal model.
- Convert all accepted input shapes into one canonical UI model.
- Keep renderer data-driven from this model.
- This allows changing tool purpose without changing core app skeleton.

4. Keep strict mode primary, compatibility mode optional.
- Primary path should be explicit typed schema.
- Compatibility parsing (freeform text) can be secondary, then normalized.
- Do not let heuristics override typed-mode behavior.

5. Optimize schema for agent discoverability.
- Prefer explicit discriminators and concrete examples.
- Avoid ambiguous unions and confusing alias branches.
- Keep required fields obvious and validation messages actionable.

6. Render by type, not by guesswork.
- `text` -> input/textarea.
- `single_choice` -> radio/select controls.
- Choice candidates must render as option controls, not manual typing.

7. Be strict where correctness matters, tolerant where agents vary.
- Keep nested domain structure strict (questions/candidates).
- Tolerate extra top-level metadata from agent wrappers.

8. Validate continuously.
- Run parser smoke checks for real payload shapes.
- Run full build (`npm run build`) after schema or normalizer changes.
- Restart MCP server after schema changes to avoid stale behavior.

9. Keep docs and code in lockstep.
- Update tool description, README examples, and schema together.
- Remove deprecated aliases fully (schema, parser, docs, errors).

10. Reusable architecture principle.
- Separate transport + registration + UI runtime from tool-specific domain schema.
- When tool function changes (input canvas, document canvas, etc.), reuse platform skeleton and replace schema/model/render components.

11. Multi-tool ext-app pattern (learned from 2nd tool).
- Keep each tool with its own schema module and parser (`src/schema.ts`, `src/document-schema.ts`).
- Register each tool with its own UI resource URI (for example `mcp-app.html` vs `mcp-document-app.html`).
- Keep each renderer isolated (`src/mcp-app.ts` vs `src/document-app.ts`) to avoid cross-tool coupling.
- Design shared host behavior (theme/style hooks) once, but keep view logic tool-specific.

12. Multi-entry UI build constraints.
- `vite-plugin-singlefile` cannot inline multiple HTML inputs in a single Vite build run.
- Build each HTML entrypoint in separate Vite invocations.
- Keep server bundling separate from UI bundling (`vite` first, then `tsc`/`bun build`).

13. Schema evolution for real payloads.
- Make field optionality match tool description exactly (for example optional sections in document canvas).
- Validate against real sample files early (`document.json`) to catch mismatch (for example missing optional fields).
- Keep renderer null-safe for optional sections so partial payloads render cleanly.

14. Sectioned document rendering pattern.
- Use semantic section cards for each document block (objective, architecture, table, repository tree).
- Render architecture flow as a code block for readability.
- Render assumptions as a table and repository layout as a tree-style block.
- Assume vertical scroll by default for long documents; do not constrain to one viewport.

## Minimal Build Checklist

1. Scaffold from reference ext-app example.
2. Register tool with strong typed `inputSchema` and UI resource URI.
3. Implement UI app that renders from typed model.
4. Add concrete examples for callers.
5. Verify with parser smoke tests + `npm run build`.
6. Confirm behavior in host after MCP server restart.
7. If multiple tools have separate UIs, build each HTML entrypoint explicitly.
