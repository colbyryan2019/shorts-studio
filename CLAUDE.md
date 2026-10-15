# Shorts Studio

A reusable pipeline that turns a short episode outline into a finished vertical video, for any number of series. The full design is in SPEC.md; read it before starting work.

## How to work in this repo

- Build one milestone from SPEC.md at a time. Stop when it works, show how to test it, and wait for the go-ahead before the next one.
- Keep `pipeline/` generic: no series-specific logic. Everything series-specific comes from `series/<name>/`.
- `house_rules.md` applies to every series and must be included in every script prompt.
- Ask before adding dependencies beyond those listed in SPEC.md.
- Python 3.11+, use a virtual environment in `.venv/`, keep a `requirements.txt`.

## Safety

- API keys live only in `.env` (git-ignored). Never print, log or commit them. Load them with python-dotenv from the project's `.env`, never by exporting them in the shell.
- Never commit anything under `series/`: the whole folder (bibles, sources, refs, episodes) is git-ignored because the code is public. Don't `git add -f` it.
- Never upload or post videos anywhere.
- Prefer `--dry-run` while developing so tests don't spend API credits; ask before running anything that calls a paid API in a loop.
