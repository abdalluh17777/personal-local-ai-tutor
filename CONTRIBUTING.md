# Contributing

Thanks for your interest! Bug reports, ideas, new courses and pull requests are all welcome.

## Reporting a problem

Please open an issue and include:

- your Windows version, GPU model and video memory, and NVIDIA driver version;
- what you did, what you expected and what happened;
- the relevant lines from `logs\setup.log`, `logs\start.err.log`, `logs\server.err.log` or `logs\ollama-server.err.log`.

The `logs\` folder never contains your chats, but check what you paste anyway.

## Working on the code

```powershell
. .\scripts\env.ps1        # project environment: every cache and tool stays inside the folder
uv sync                    # include the dev dependencies (pytest, websockets, psutil)
uv run pytest -q           # must stay green; it needs no GPU or models
```

- **Backend:** `app/tutor/`. The model never writes to the database directly: it proposes, and `store.py` validates.
- **Frontend:** `app/tutor/web/` (ES modules, no build step). Read [DESIGN.md](app/tutor/web/DESIGN.md) first, and after any UI change run `uv run python tests\ui_check.py <screenshot folder>`, which must exit with code 0.
- **Prompts:** `app/tutor/prompts/` are reloaded on every request, so you can iterate without restarting.
- **GPU benchmarks:** `tests\model_check.py`, `voice_check.py`, `live_check.py` and the `pron_*` scripts measure speed and accuracy on real hardware. Please include their output when a change affects performance.

Keep pull requests focused, and describe what you measured.

## Adding a course

Small courses can be added from the app (**Courses → New course**, then upload PDF, TXT or Markdown). To package an open textbook as a course for everyone, see the import scripts in `scripts/` and the guide specifications in `materials/library/*SPEC*.md`, and include the source's license and attribution in `materials/README.md`.
