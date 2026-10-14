# Shorts Studio

Turns a short episode outline into a finished vertical video, for any number of series. The design is in [SPEC.md](SPEC.md).

**Status:** milestones 1 and 1b (research, then the script step with review and approval).

## Setup

Needs Python 3.11+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # if .env doesn't exist yet
```

Then fill in `.env` (never commit it):

```
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-5-5
```

Keys are read only from `.env`; shell environment variables are ignored.

## Finding sources

When a series has `sources_required: true`, every fact in a script must come from a file in `series/<name>/sources/`. `research.py` fills it for you:

```sh
python research.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..."   # or --topic "..."
```

Claude searches the web, reads the most authoritative pages (see `research_guidance` in `series.yaml`) and saves the exact passages it quotes, with links, to `sources/ep01-research.md`. **Skim that file before writing the script**: open the links and delete any quote you don't trust. Claude's own summary is printed but not saved, so scripts can only cite real published text.

- A run makes up to 5 searches and reads up to 3 pages; change this with `research_max_searches` / `research_max_fetches`.
- It won't overwrite an existing file without `--force`.
- `--fake-llm` shows a preview and saves nothing.
- You can also add your own `.txt` / `.md` files to `sources/`.

## Writing a script

```sh
# Test without an API key (canned script, no network):
python make_episode.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..." --fake-llm

# Real run (needs the key, LLM_MODEL and source text in series/<name>/sources/):
python make_episode.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..."
```

This writes `series/<name>/episodes/epNN/script.json` (plus `outline.txt`) and prints the narration, shots and claims.

## Reviewing and editing

Every new script starts as a **draft**. Later steps (images, voice, video) won't spend anything until it's approved.

| To... | Run |
|---|---|
| See the current script again | `python make_episode.py --series S --episode N` |
| Edit by hand | Edit `script.json`, then run the line above to re-check it |
| Ask Claude for changes | `... --revise "rewrite in second person; make line 4 punchier"` |
| Start over from the outline | `... --redo-script` (add `--outline "..."` to change the outline) |
| Approve | `... --approve` |
| Preview the paid calls later steps would make | `... --dry-run` |

- Every revise or redo keeps the previous script as `script.v1.json`, `script.v2.json`, ...
- Approval is tied to the exact contents of `script.json`. Any edit after approving puts it back in draft.
- If a generated script breaks the series limits (word count, shot count, genvideo cap, unknown refs or sources), Claude is asked once to fix it. If it fails again, the run stops and lists the problems.

## Tests

```sh
pytest
```

The tests make no network calls.
