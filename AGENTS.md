# Development instructions

- Use PowerShell 7 (`pwsh`) for shell commands. Read and write text as UTF-8 without BOM, with LF line endings.
- Keep this repository installable as a standalone AstrBot plugin: runtime files and metadata belong at the root.
- Never commit API tokens, local AstrBot configuration, signed media URLs, logs, caches, or runtime data. Keep the token default empty.
- Use conventional commit messages. Update metadata and the changelog for releases; keep version references aligned.
- Preserve existing command aliases and keep `_conf_schema.json`, runtime configuration and README documentation consistent.
- Use asynchronous HTTP requests and close the shared session on plugin unload.
- Before committing, run Ruff formatting and lint, focused pytest tests, `scripts/validate_plugin_layout.py`, and the AstrBot smoke script in an installed AstrBot environment.
- Build release ZIPs with `scripts/package_plugin.py`. The package must stay below the AstrBot market limit of 16 MiB.
- Do not claim a chat platform has been tested unless a real platform send was verified. Market submission through AstrBot Cloud is separate from GitHub publication.
