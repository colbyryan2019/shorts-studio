# Shorts Studio

Turns a short episode outline into a finished vertical video, for any number of series. The design is in [SPEC.md](SPEC.md).

**Status:** milestones 1–4: research, the script (with review and approval), reference images, shot images, narration and the first full video (push-in motion, captions, music, end card).

## Setup

Needs Python 3.11+ and ffmpeg (`brew install ffmpeg`).

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
OPENAI_API_KEY=sk-...
IMAGE_MODEL=gpt-image-2.5-sunburst
ELEVENLABS_API_KEY=...
VOICE_MODEL=eleven_multilingual_v2
```

Keys are read only from `.env`; shell environment variables are ignored.

The whole `series/` folder is git-ignored, so each series' bible, sources, refs and episodes stay private while the code is public. Back it up yourself (for example, keep it in a synced Drive folder).

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

## Reference images (consistent characters)

Each series keeps one picture of every recurring character or object in `refs/`, and every shot that shows it is drawn from that picture.

```sh
python make_refs.py --series stranded-at-sea                    # Claude proposes refs/refs.yaml from the bible
python make_refs.py --series stranded-at-sea --generate --dry-run   # what it would draw, and rough cost
python make_refs.py --series stranded-at-sea --generate         # 3 candidates per ref in refs/candidates/
python make_refs.py --series stranded-at-sea --pick survivor 2  # use candidate 2 as refs/survivor.png
```

- Edit `refs/refs.yaml` freely before generating. `keywords` are the words that mean a shot shows that thing.
- Once a ref is picked, any shot whose `image_prompt` uses one of its keywords must list it in `refs`, or the script fails its checks. For a script written earlier, run `--revise "add the refs"`, then `--approve` again.

## Images

Once the script is approved, running `make_episode.py` again draws one image per shot into `episodes/epNN/images/`.

```sh
python make_episode.py --series stranded-at-sea --episode 1 --dry-run   # which images, and rough cost
python make_episode.py --series stranded-at-sea --episode 1             # draw them
python make_episode.py --series stranded-at-sea --episode 1 --redo 5    # redraw shot 5
```

- Re-running only redraws shots whose inputs changed: the image prompt, the style line, a reference image, the model or the quality. Editing one shot's `image_prompt` costs one image.
- Quality is `image_quality` in `series.yaml` (`low`, `medium`, `high`).
- `--fake-images` draws labeled placeholders for free.

## Narration

Each series has one narrator voice: copy its voice ID from ElevenLabs (Voices → the voice → ID) into `voice_id` in `series.yaml`. With that, `ELEVENLABS_API_KEY` and `VOICE_MODEL` set, the same `make_episode.py` run reads every line into `episodes/epNN/audio/` after drawing the images.

```sh
python make_episode.py --series stranded-at-sea --episode 1 --dry-run         # which lines, and how many characters
python make_episode.py --series stranded-at-sea --episode 1                   # draw images, then read narration
python make_episode.py --series stranded-at-sea --episode 1 --redo-voice 4    # re-read line 4 (try another take)
python make_episode.py --series stranded-at-sea --episode 1 --fake-voice      # silent placeholder lines, free
```

- One request per line, so each shot's length is exact: line length + 0.3 s (at least 1.5 s). The total is printed at the end.
- Re-running only re-reads lines whose narration, voice or model changed. Editing one line costs one line.
- ElevenLabs bills characters against your plan's credits; `--dry-run` shows how many a run will use.
- Until the voice is set up, runs still draw images and just skip narration with a note.

## Video

Once the images and narration exist, the same run renders each shot into a clip and assembles `episodes/epNN/final.mp4` (1080x1920, 30 fps). This step is free: it's all ffmpeg on your machine.

```sh
python make_episode.py --series stranded-at-sea --episode 1                              # images, narration, video
python make_episode.py --series stranded-at-sea --episode 1 --fake-images --fake-voice   # whole pipeline, free
```

- **Music:** put royalty-free `.mp3` tracks in `assets/music/` (or `series/<name>/music/` for that series only). Each episode picks one and keeps it on every run.
- **Captions:** put one `.ttf` / `.otf` font in `assets/fonts/` (a bold one, such as Montserrat Black or Anton). Style is `caption_style` in `series.yaml`: `bold_white_outline` or `plain_white`.
- Music and fonts are git-ignored (their licenses may not allow redistribution), so keep your own copies.
- Every shot uses a slow push-in for now; rock, drift and genvideo come in the next milestones.
- Re-running re-renders only clips whose image or narration changed, and skips the final build if nothing changed. The target length is 25–45 s; you'll get a note outside that.

## Tests

```sh
pytest
```

The tests make no network calls.
