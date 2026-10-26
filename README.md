# Shorts Studio

Shorts Studio turns a short episode outline into a finished vertical video (1080×1920 MP4, about a minute long). It works for any number of series. Each series keeps its own premise, art style, narrator voice, reference images and fact sources in its own folder, and all series share the same engine code.

```
outline ──► research ──► script ──► (you approve) ──► images ──► narration ──► clips ──► final.mp4 + review.md
             Claude       Claude                       OpenAI     ElevenLabs    ffmpeg     ffmpeg
             (web)                                                                         (captions, music,
                                                                                            ambience, end card)
```

You write a one- or two-sentence outline. Claude finds sources and writes a script, and you review and approve it. The pipeline then draws one image per shot, has a narrator read each line, animates every still, and assembles the video with word-by-word captions, music, ambience and an end card. It also writes a review sheet so a person can check the episode before posting. **It never posts or uploads anything.**

The full design is in [SPEC.md](SPEC.md). The rules every series follows are in [house_rules.md](house_rules.md).

## Contents

- [Status](#status)
- [Setup](#setup)
- [Quick start (free, no API keys)](#quick-start-free-no-api-keys)
- [Making an episode, step by step](#making-an-episode-step-by-step)
- [Configuration reference](#configuration-reference)
- [What's in an episode folder](#whats-in-an-episode-folder)
- [How re-runs stay cheap (caching)](#how-re-runs-stay-cheap-caching)
- [Code tour](#code-tour)
- [Tests](#tests)
- [Safety and privacy](#safety-and-privacy)

## Status

Built (SPEC.md milestones 1–6 and 9):

- research
- the script, with review and approval
- reference images and shot images
- narration with word timings
- motion: push-in, punch-in, rock and drift
- word-by-word captions, music, ambience and the end card
- the review sheet and a cost log per episode
- `new_series.py`

Not built yet (milestones 7–8): generated-video and mixed visual modes, `prices.yaml` and the video cost guard. For now every episode renders in **stills** mode, and shots the script marks `genvideo` use the series' `default_motion`. `FAL_KEY` and `VIDEO_MODEL` in `.env.example` are placeholders for that work and aren't read yet.

## Setup

You need Python 3.11+ and ffmpeg/ffprobe on your PATH (`brew install ffmpeg`).

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # if .env doesn't exist yet
```

Then fill in `.env`, and never commit it:

```
ANTHROPIC_API_KEY=sk-ant-...           # scripts, research, ref proposals
LLM_MODEL=claude-sonnet-5-5
OPENAI_API_KEY=sk-...                  # images
IMAGE_PROVIDER=openai                  # the only provider so far
IMAGE_MODEL=gpt-image-2.5-sunburst
IMAGE_COST_LIMIT=2.00                  # a run of the image step stops before spending more than this (USD)
ELEVENLABS_API_KEY=...                 # narration
VOICE_MODEL=eleven_multilingual_v2
```

Settings are read **only** from `.env`. Shell environment variables are deliberately ignored.

Optional assets, all git-ignored because their licenses may not allow redistribution:

| Folder | What goes there |
|---|---|
| `assets/music/` | Royalty-free `.mp3` / `.m4a` / `.wav` music tracks, any length |
| `assets/sfx/` | Looping ambience beds (waves, wind) |
| `assets/fonts/` | `.ttf` / `.otf` caption fonts |

A series can override music and ambience with its own `series/<name>/music/` and `series/<name>/sfx/` folders.

## Quick start (free, no API keys)

Every paid step has a fake stand-in, so you can run the whole pipeline end to end for free:

```sh
python new_series.py --name test-run
```

The canned `--fake-llm` script has 10 shots and 97 words and cites `survival-manual.txt`. Before running, prepare the test series so that script passes its checks:

1. In `series/test-run/bible.md` and `style_line.txt`, replace every `TODO:` line with any text.
2. In `series/test-run/series.yaml`, set `words: [90, 110]` and `shots: [8, 12]`.
3. Create `series/test-run/sources/survival-manual.txt` containing any text.

Then:

```sh
python make_episode.py --series test-run --episode 1 --outline "Your boat sinks." --fake-llm
python make_episode.py --series test-run --episode 1 --approve
python make_episode.py --series test-run --episode 1 --fake-images --fake-voice
open series/test-run/episodes/ep01/final.mp4
```

You get a roughly 39-second video of labeled placeholder images with silent narration and captions, plus `review.md`. Delete `series/test-run/` when you're done.

## Making an episode, step by step

### 1. Start a series (once)

```sh
python new_series.py --name lost-in-the-desert                          # title from the name: "Lost In The Desert"
python new_series.py --name lost-in-the-desert --title "Lost in the Desert"
```

This copies `templates/series/` into `series/<name>/`. You get a `series.yaml` with every setting and its default, a `bible.md` outline and a `style_line.txt`, plus empty `sources/`, `refs/` and `episodes/` folders. Fill in every `TODO:` line in the bible and style line; every command refuses to run until you do. Then check `series.yaml`: voice, word and shot ranges, end card and `sources_required`. A new series needs no code changes.

### 2. Reference images (once per series)

Reference images keep characters looking the same. Each series keeps one picture of every recurring character or object in `refs/`, and every shot that shows it is drawn from that picture.

```sh
python make_refs.py --series stranded-at-sea                        # Claude proposes refs/refs.yaml from the bible
python make_refs.py --series stranded-at-sea --generate --dry-run   # what it would draw, and rough cost
python make_refs.py --series stranded-at-sea --generate             # 3 candidates per ref in refs/candidates/
python make_refs.py --series stranded-at-sea --pick survivor 2      # use candidate 2 as refs/survivor.png
```

More options:

| Flag | What it does |
|---|---|
| `--propose --force` | Rewrite `refs.yaml` from the bible, replacing the existing one |
| `--generate survivor` | Draw candidates for just these refs |
| `--generate survivor --from 2` | Redraw starting from candidate 2 (it's sent as the starting image), saved as new numbers |
| `--generate --style "..."` | Try a different art style instead of `style_line.txt`, saved as new numbers |
| `--generate --count N` | Draw N candidates per ref (1–6) instead of `ref_candidates` |
| `--generate --force` | Redraw candidates that already exist |
| `--fake-llm` / `--fake-images` | Free placeholders. Fake candidates can't be picked. |

Edit `refs/refs.yaml` freely. Each ref has a `description` and `keywords`, the words that mean a shot shows that thing. Once a ref has a picked image, any shot whose `image_prompt` uses one of its keywords as a whole word must list the ref in `refs`, or the script fails its checks. For a script written earlier, run `--revise "add the refs"`, then `--approve` again.

### 3. Research (when `sources_required: true`)

Then every fact in a script must come from a file in `series/<name>/sources/`. `research.py` fills that folder:

```sh
python research.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..."
python research.py --series stranded-at-sea --topic "how to catch rainwater"
```

Claude searches the web and reads the most authoritative pages (see `research_guidance` in `series.yaml`). It saves the exact passages it quotes, with links, to `sources/ep01-research.md` (or a file named after the topic, or `--name`). **Skim that file before writing the script.** Open the links and delete any quote you don't trust. Each quote is tagged when it's only a search snippet or when its page couldn't be confirmed. Claude's own summary is printed but never saved, so scripts can only cite real published text.

- A run makes up to 5 searches and reads up to 3 pages. Change this with `research_max_searches` / `research_max_fetches`.
- It won't overwrite an existing file without `--force`.
- `--fake-llm` shows a preview and saves nothing, so fake facts never land in `sources/`.
- You can also add your own `.txt` / `.md` files to `sources/`.

### 4. Write and review the script

```sh
python make_episode.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..."            # real
python make_episode.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. ..." --fake-llm # canned, free
```

This writes `episodes/ep01/script.json` (plus `outline.txt`) and prints the narration, shots, claims, warnings and problems. Every new script starts as a **draft**. Later steps won't spend anything until it's approved.

| To... | Run |
|---|---|
| See the current script again | `python make_episode.py --series S --episode N` |
| Edit by hand | Edit `script.json`, then run the line above to re-check it |
| Ask Claude for changes | `... --revise "rewrite in second person; make line 4 punchier"` |
| Start over from the outline | `... --redo-script` (add `--outline "..."` to change the outline) |
| Approve | `... --approve` |
| Preview the paid calls later steps would make | `... --dry-run` |

- Every revise or redo keeps the previous script as `script.v1.json`, `script.v2.json`, and so on.
- Approval is tied to the exact bytes of `script.json`. Any edit after approving puts it back in draft.
- If a generated script breaks the series limits (word count, shot count, genvideo cap, unknown refs, missing refs, unknown sources), Claude is asked once to fix it. If the second try also fails, the run stops and lists the problems from both attempts.

A script looks like this:

```json
{
  "title": "Day One",
  "end_card": "DAY 1 · WATER: NONE",
  "shots": [
    {"n": 1, "narration": "Your boat is gone.",
     "image_prompt": "A sailboat mast sinking under dark water, a small dinghy nearby",
     "motion": "drift", "refs": ["dinghy"], "pause_after": 0.8}
  ],
  "claims": [{"claim": "Seawater is saltier than kidneys can handle", "source_file": "ep01-research.md"}]
}
```

`motion` is `pushin`, `rock`, `drift` or `genvideo`. `pause_after` is optional: it sets the seconds of silence after a line, from 0 to 3, for a held dramatic beat. `stamp` is optional too: a short label (up to 24 characters, e.g. `"T-MINUS 24:00"`) shown in red at the top of the frame from that shot until a later shot sets another; `""` hides it. The script writer is only told about stamps when the series sets `stamp_format`.

### 5. Images, narration and video

Once the script is approved, the **same command without flags** does everything else in order: images, then narration, then clips, then `final.mp4`, then `review.md`.

```sh
python make_episode.py --series stranded-at-sea --episode 1 --dry-run   # what would be drawn/read, and rough cost
python make_episode.py --series stranded-at-sea --episode 1             # do it
```

| Flag | What it does |
|---|---|
| `--redo 5` | Redraw shot 5's image (and so its clip) |
| `--redo-voice 2-4` | Re-read lines 2–4 (try another take) |
| `--fake-images` | Labeled placeholder images, free |
| `--fake-voice` | Silent placeholder lines of a realistic length, free |
| `--music NAME` | Use the music track whose file name contains NAME for this episode (remembered in `music.txt`) |
| `--sfx NAME` | Same for the ambience loop (remembered in `sfx.txt`) |

`--redo` and `--redo-voice` take one shot (`5`), a list (`3,7`), a range (`2-4`) or `all`, and can be repeated. They only run on an approved script, and not together with `--approve`, `--revise` or `--redo-script`.

**Images.** Each image is drawn at `image_size` (default 1152×2048, which is 9:16), at `image_quality`, then scaled to 1080×1920. Shots with refs send those reference pictures along. The run stops before the next image would push its spend past `IMAGE_COST_LIMIT`. Everything drawn so far is saved, so running again continues where it stopped.

**Narration.** Copy the narrator's voice ID from ElevenLabs (Voices → the voice → ID) into `voice_id` in `series.yaml`. Until that, `ELEVENLABS_API_KEY` and `VOICE_MODEL` are all set, runs still draw images and skip narration with a note.

- There is one request per line, with the neighbouring lines sent as context so the delivery flows.
- Each shot lasts its line's measured length plus `line_pause_seconds` (or the shot's `pause_after`), with a minimum of 1.5 s.
- Each line also records when every word is spoken, which drives the word-by-word captions.
- `voice_settings` in `series.yaml` tunes the delivery. Changing it re-reads the lines.
- ElevenLabs bills characters against your plan's credits. `--dry-run` shows how many a run will use.

**Video.** This step is free: it all runs in ffmpeg on your machine.

- **Motion.** Each shot moves as its `motion` says:
  - `pushin`: a slow zoom from 100% to 110%.
  - `rock`: a gentle tilt and bob, like a boat on a swell. Each shot starts at a different point in the swell.
  - `drift`: a slow sideways pan. Alternate shots pan opposite ways.
  - `hook_motion: punchin` gives shot 1 a fast zoom (100% to 120%) that lands in the first moments.
- **Captions.** `caption_style` picks the look:
  - `highlight_word`: a few words at a time, with the spoken word in yellow. A line without word timings falls back to the outline style.
  - `bold_white_outline`: one short phrase at a time, in capitals.
  - `plain_white`: one short phrase at a time, plain text.

  The font is `caption_font` from `assets/fonts/`, or the first font there, or Pillow's built-in font if there are none.
- **Levels.** The narration is brought to −16 LUFS. Music sits `music_volume_db` under it and ambience sits `sfx_volume_db` under it. Each track's loudness is measured, so loud and quiet masters end up at the same level, and a limiter keeps peaks under about −1 dBFS.
- **Music and ambience.** Each episode picks one track and a starting point in it, at random but fixed for that episode, so every re-run sounds the same. Short tracks loop. Both fade in over `music_fade_in_seconds` (0 means full level on the first frame) and fade out over the last 2 s.
- **End card.** `end_card` from the script shows in white capitals for the last 2 s. If the series sets `end_card_cta` (e.g. `"Follow for part {next}"`), a smaller yellow follow line appears under it. That line is on screen only; the narration still ends on the cliffhanger.
- **Stamps.** A shot's `stamp` shows near the top of the frame until a later shot changes it, and is hidden under the end card.
- **Length.** The target is `seconds` in `series.yaml` (default 60–70 s). You get a note if the episode falls outside it. To lengthen a series, raise `seconds` and `words` together.

### 6. Review before posting

Every run that gets past the images writes `episodes/epNN/review.md`. It contains:

- the title, length and full narration
- each shot with its length and motion, both planned and actually rendered
- the claims, each with its source file (flagged if the file isn't in `sources/`)
- any video fallbacks
- a `contact_sheet.png` of all the stills
- the cost summary
- a short checklist

Go through it before posting. Nothing is ever posted for you.

Every paid call is appended to `episodes/epNN/cost.json` as it happens, with its model, units and price. Images are priced in USD from their token usage. ElevenLabs lines are logged in characters, because they're billed against plan credits. Placeholder runs (`--fake-*`) aren't logged.

## Configuration reference

### series.yaml

Unknown keys are rejected, so a typo shows up as an error instead of silently doing nothing.

| Key | Default | Meaning |
|---|---|---|
| `title` | *required* | Display title |
| `words` | *required* | `[min, max]` narration words per episode |
| `shots` | *required* | `[min, max]` shots per episode |
| `max_genvideo_shots` | *required* | Most shots that may use `genvideo` |
| `default_motion` | *required* | `pushin`, `rock` or `drift`. Also used for `genvideo` shots until video mode exists. |
| `seconds` | `[60, 70]` | Target episode length; outside it you get a warning |
| `voice_id` | `""` | ElevenLabs narrator voice |
| `voice_settings` | none | `stability`, `similarity_boost`, `style` (0–1), `speed` (0.7–1.2), `use_speaker_boost` |
| `line_pause_seconds` | `0.3` | Silence after each line (0–3) |
| `playback_speed` | `1.0` | Speeds up (or slows) the whole `final.mp4`, pictures, captions, narration and music alike, keeping the voice's pitch (0.5–2.0). The `seconds` target is checked against the sped-up length. |
| `hook_motion` | `""` | Shot 1's motion whatever the script says: `punchin`, `pushin`, `rock`, `drift`, or `""` to use the script's motion |
| `end_card_format` | `""` | Format the script fills in, e.g. `"DAY {day} · WATER: {water}"` |
| `end_card_cta` | `""` | On-screen follow line; `{next}` = next episode, `{episode}` = this one |
| `stamp_format` | `""` | What shots' on-screen `stamp` labels look like and when to use them (e.g. a countdown); empty = no stamps |
| `caption_style` | `bold_white_outline` | `highlight_word`, `bold_white_outline` or `plain_white` (the template sets `highlight_word`) |
| `caption_font` | `""` | A file name in `assets/fonts/`; empty means the first one |
| `music_volume_db` | `-20` | Music level under the narration (more negative = quieter) |
| `sfx_volume_db` | `-26` | Ambience level under the narration |
| `music_fade_in_seconds` | `1.0` | Fade-in for music and ambience (0–5); 0 = full level at once |
| `sources_required` | `true` | Every claim must cite a file in `sources/`; set false for pure fiction |
| `research_guidance` | `""` | Which sources research should prefer (a sensible default is used if empty) |
| `research_max_searches` | `5` | Web searches per research run (1–20) |
| `research_max_fetches` | `3` | Pages read per research run (1–10) |
| `image_quality` | `medium` | `low`, `medium` or `high` |
| `image_size` | `1152x2048` | Requested image size; scaled to 1080×1920 |
| `ref_candidates` | `3` | Candidates per ref in `make_refs.py` (1–6) |

### Other series files

| File | Purpose |
|---|---|
| `bible.md` | Premise, main character, narrator, tone, series rules, recurring characters, season arc. Sent with every script prompt, after `house_rules.md` (it can add to the house rules but not override them). |
| `style_line.txt` | One paragraph of art direction, put at the start of every image prompt. |
| `sources/*.txt`, `*.md` | Fact sources. All of them are sent with the script prompt; claims must cite them by file name. |
| `refs/refs.yaml` | Recurring characters/objects: `description` and `keywords`. |
| `refs/<name>.png` | The picked reference image for each ref. |
| `music/`, `sfx/` | Optional series-only tracks, used instead of `assets/`. |

## What's in an episode folder

```
series/<name>/episodes/ep01/
  outline.txt            the outline you passed
  script.json            the current script
  script.v1.json ...     earlier versions (kept on every revise/redo)
  approved.json          SHA-256 of the approved script.json, and when
  images/01.png ...      1080x1920 stills
  images/manifest.json   fingerprint, prompt, refs, tokens and cost per image
  audio/01.mp3 ...       one narration line per shot
  audio/manifest.json    fingerprint, text, voice, model, length and word timings per line
  clips/01.mp4 ...       each still animated to its shot's length
  clips/manifest.json    fingerprint and motion per clip
  captions/              caption and end-card PNGs, plus the ffconcat list (rebuilt each assembly)
  final.mp4              the finished video
  final.json             fingerprint of every input to final.mp4, plus music/sfx choice, offsets and gains
  music.txt, sfx.txt     tracks chosen with --music / --sfx (only if you used them)
  review.md              the review sheet
  contact_sheet.png      every still in a labeled grid
  cost.json              every paid call, appended as it happens
```

## How re-runs stay cheap (caching)

Every generated file is recorded in a manifest along with a **fingerprint**, a SHA-256 hash of everything it was made from. A re-run regenerates only what's new, what's stale, or what you asked to redo:

| Output | Fingerprint covers | So editing... |
|---|---|---|
| Image | style line + image prompt + ref instruction, each ref image's bytes, model, quality, size | one shot's `image_prompt` redraws one image |
| Narration line | line text, voice, model, voice settings | one line re-reads one line |
| Clip | image bytes, frame count, motion, motion version | a line changes that clip's length, so re-renders only it |
| `final.mp4` | every clip, line, word timing, the end card, follow line, music/sfx choice, offset and gain, font, caption style, fade-in | anything at all rebuilds; nothing skips the encode |

If you insert, remove or reorder shots, the numbers shift. When an image or line with an identical fingerprint already exists under its old number, it is **copied** to the new number for free instead of being paid for again (`pipeline/reuse.py`).

Manifests are written after every single image or line, so an interrupted run keeps (and won't re-pay for) everything it finished.

## Code tour

```
make_episode.py        CLI: script → approval → images → narration → clips → final.mp4 → review
research.py            CLI: find sources on the web, save verbatim quotes to sources/
make_refs.py           CLI: propose, generate and pick reference images
new_series.py          CLI: scaffold series/<name>/ from templates/series/
tools/make_profile_pics.py   one-off branding helper (not part of the pipeline)
pipeline/              the engine; no series-specific logic
templates/series/      starter files copied by new_series.py
house_rules.md         rules for every series, sent with every script prompt
tests/                 pytest suite, no network
```

Two conventions run through the code:

- **User-fixable problems raise `PipelineError`.** Each CLI's `main()` catches it, prints `Error: ...` without a traceback, and exits with status 1.
- **Every external service sits behind a small `Protocol`** (`LLMClient`, `ImageClient`, `VoiceClient`) with a real implementation and a fake one. The fakes power the `--fake-*` flags and the tests. Each CLI's `run()` also accepts injected clients and a `root` path for the same reason.

### Command-line scripts

#### `make_episode.py`

| Function | What it does |
|---|---|
| `shot_numbers(text)` | Parses a `--redo` / `--redo-voice` value (`5`, `3,7`, `2-4`, `all`) into a tuple of shot numbers, or `None` for "all". |
| `expand_shots(values, script)` | Merges repeated `--redo` values into one set of shot numbers; `None` expands to every shot. |
| `parse_args(argv)` | Defines the CLI and rejects bad combinations (e.g. `--approve` with `--revise`, `--redo` on an unapproved run, an empty `--revise`). |
| `make_script_client(args, series, root)` | Returns the LLM client for writing the script. Stops early if `sources_required` is on and `sources/` is empty (it only warns under `--fake-llm`). |
| `run(argv, root, client, image_client, voice_client)` | The whole episode flow. It saves the outline, writes/revises/redoes the script if asked, and re-validates `script.json` from disk (it may have been hand-edited). Then it either approves, or stops if the script is a draft. For an approved script it plans and draws images, plans and reads narration (or explains why it's skipped), plans and renders clips, assembles `final.mp4`, and calls `report`. `--dry-run` stops after printing the plans. |
| `report(series, ep, script, root, shot_seconds)` | Writes `review.md` and prints the episode's logged cost. |
| `main()` | Calls `run()`, turning `PipelineError` into a clean error message and exit code 1. |

The constant `VISUAL_MODE = "stills"` is the only mode built so far; it's shown in the review sheet.

#### `research.py`

| Function | What it does |
|---|---|
| `parse_args(argv)` | Needs exactly one of `--episode` or `--topic`; `--outline` only goes with `--episode`. |
| `run(argv, root, client)` | Picks the topic (the episode's saved outline, or `--topic`) and the output file (`epNN-research.md`, a slug of the topic, or `--name`). Refuses to overwrite without `--force`, then runs research. It prints how many passages it saved, any unverified quotes, failed fetches and full-PDF reads, token usage, and Claude's notes. Under `--fake-llm` it only previews the file. |
| `main()` | As above. |

#### `make_refs.py`

| Function | What it does |
|---|---|
| `parse_args(argv)` | Allows one of `--propose`, `--generate` or `--pick` at a time. Checks that `--style`, `--count` and `--from` go with `--generate`, and that `--count` is 1–6. |
| `run(argv, root, llm, images)` | `--pick` copies a candidate into `refs/`. `--generate` lists the candidates to draw with a rough cost (stopping there for `--dry-run`), then draws them within the image budget. With no action, it proposes `refs.yaml` if it doesn't exist (or with `--propose --force`), then prints each ref's status. |
| `main()` | As above. |

#### `new_series.py`

| Function | What it does |
|---|---|
| `parse_args(argv)` | `--name` must be lowercase letters, digits and dashes (same rule as ref names); `--title` can't be blank. |
| `default_title(name)` | `lost-in-the-desert` → `Lost In The Desert`. |
| `create_series(name, title, root, templates)` | Copies every template file into `series/<name>/`, replacing `__TITLE__` and `__NAME__` in text files, and creates empty `sources/`, `refs/` and `episodes/`. Refuses if the folder already exists. |
| `run(argv, root, templates)` | Validates the title (no double quotes or line breaks, since it goes into YAML) and prints the next steps. |
| `main()` | As above. |

#### `tools/make_profile_pics.py`

A one-off branding helper, not part of the pipeline. It draws four square profile-picture candidates in a series' art style, using the series' `style_line.txt` with "Vertical 9:16" swapped for "Square 1:1", and the `--ref` image (default `survivor`) for the variants that show the character. The images are saved to `assets/branding/<series>-<variant>.png` with their cost printed. Its `VARIANTS` prompts are written for Stranded at Sea, so edit them for another series. `main()` is its only function.

### `pipeline/` modules

#### `config.py`: global settings

| Name | What it does |
|---|---|
| `PROJECT_ROOT` | The repo folder; the default `root` everywhere. |
| `Settings` | Frozen dataclass of every `.env` value. Keys are `repr=False` so they never appear in logs or tracebacks. |
| `load_settings(root)` | Reads `<root>/.env` with `dotenv_values` (never the shell environment), trims values, lowercases `IMAGE_PROVIDER`, and validates `IMAGE_COST_LIMIT` as a positive dollar amount (default 2.00). |

#### `errors.py`

| Name | What it does |
|---|---|
| `PipelineError` | The one exception type for problems the user can fix; CLIs print its message without a traceback. |

#### `series.py`: loading a series folder

| Name | What it does |
|---|---|
| `VoiceSettings` | Pydantic model for optional ElevenLabs settings, with range checks. `as_dict()` drops unset values. |
| `SeriesConfig` | Pydantic model of `series.yaml` (see the [reference](#seriesyaml)). Forbids unknown keys; `_check_ranges` ensures each `[min, max]` has `1 <= min <= max`. |
| `RefSpec` | One entry of `refs.yaml`: `description` and at least one keyword. |
| `Series` | Everything loaded for a series: config, house rules, bible, style line, sources (`{file name: text}`), picked ref names, the last 3 episodes' scripts and the ref specs. Properties `episodes_dir` and `refs_dir`; `ref_image(name)` finds `refs/<name>.png/.jpg/.jpeg/.webp`. |
| `episode_dir_name(n)` | `1` → `ep01`. |
| `_read_required(path, what)` | Reads a required text file; errors if it's missing, empty, or still contains template `TODO:` lines. |
| `load_config(path)` | Parses and validates `series.yaml`, turning YAML and validation errors into readable messages. |
| `_load_sources(folder)` | Every non-empty, non-hidden `.txt` / `.md` in `sources/`. |
| `_load_refs(folder)` | Names (file stems) of the image files in `refs/`. |
| `load_ref_specs(path)` | Parses `refs/refs.yaml` (empty if absent), validating names and entries. |
| `_load_recent_scripts(dir, before)` | The `script.json` of up to 3 earlier episodes, for continuity. |
| `load_series(name, root, episode)` | Builds a `Series`. If the name is wrong, the error lists the series that do exist. |

#### `episode.py`: one episode's files and approval

| Name | What it does |
|---|---|
| `Episode` | Dataclass of an episode number and folder. `Episode.of(series, n)` builds it; `script_path`, `outline_path` and `approval_path` are properties. |
| `has_script()` | Whether `script.json` exists. |
| `save_outline()` / `load_outline()` | Write/read `outline.txt` (`None` if absent or blank). |
| `load_script_data()` | Parses `script.json`, with the line and column of any JSON error. |
| `save_script(data)` | Backs up the current script as the next `script.vN.json`, then writes the new one. Returns the backup path. |
| `versions()` / `_next_version()` | Lists backups in order; picks the next version number. |
| `approve()` | Records the SHA-256 of `script.json` and a timestamp in `approved.json`. |
| `approval_status()` | `approved`, `draft`, or `changed` (approved once, edited since). |
| `is_approved()` / `require_approved()` | A boolean check, and a version that raises with instructions. |

#### `schema.py`: the script format and its checks

| Name | What it does |
|---|---|
| `Shot`, `Claim`, `Script` | Pydantic models of `script.json` (unknown keys forbidden). `Script.narration_words` and `Script.genvideo_count` are computed properties. |
| `count_words(text)` | Whitespace word count. |
| `ValidationResult` | The parsed script (or `None`), errors and warnings. `ok` means parsed with no errors. |
| `validate_script(data, series, missing_sources_ok)` | Checks structure, then series rules: shots numbered 1..N, shot and word counts in range, genvideo cap, every `refs` entry has an image, every shot whose prompt mentions a picked ref's keyword lists that ref, and (if `sources_required`) every claim cites a file in `sources/`. Errors are worded so they can be fed straight back to Claude. A ref with no picked image yet is only a warning. |
| `matching_keyword(text, keywords)` | The first keyword found as a whole word or phrase, ignoring case. |
| `_loc(loc)` | Turns a pydantic error location into `shots[3].motion`, counting from 1. |

#### `llm.py`: Claude, behind one interface

| Name | What it does |
|---|---|
| `Passage` | A verbatim quote with its URL, title, PDF pages, and flags for "unverified page" and "search snippet only". |
| `ResearchResult` | Passages, Claude's notes, search/fetch counts, token usage (total and cached), failed fetches and full-PDF URLs. |
| `ResearchLimits` | Max searches, max fetches and max tokens per fetched text page (10,000). |
| `LLMClient` | Protocol: `complete(system, prompt) -> str` and `research(system, prompt, limits) -> ResearchResult`. |
| `AnthropicClient` | The real client: the key is passed explicitly, with 4 retries and backoff. `_create` maps API errors (bad key, unknown model, bad request, connection) and refusals or truncated replies to `PipelineError`. `complete` returns the reply's text. `research` enables the web search and web fetch tools (fetch with citations on) and uses prompt caching so re-reading fetched pages is cheap. It resends paused turns up to `MAX_CONTINUATIONS` (5) times, and tallies usage. |
| `make_client(fake, root)` | The fake client, or the real one after checking `ANTHROPIC_API_KEY` and `LLM_MODEL`. Returns `(client, model name)`. |
| `_collect(blocks, result)` | Turns the response into passages. It records which pages were fetched, then turns each citation into a `Passage`. Search snippets cut off with "…" are skipped, and duplicate quotes are dropped. |
| `_find_text_document` | Attributes a quote to the fetched text page that actually contains it (verified), else falls back to the citation's index (unverified). |
| `_find_pdf_document` | PDF quotes are certain only when exactly one PDF was read. |
| `_by_index` | The fallback: the document at the citation's index, marked unverified. |
| `FakeLLMClient` | Returns canned replies (default: `FAKE_SCRIPT`) and records every call for tests. Its `research` returns `FAKE_PASSAGES`. |
| `FAKE_SCRIPT`, `FAKE_PASSAGES`, `FAKE_REFS` | Canned script, research passages (clearly labeled FAKE) and refs proposal. |

#### `research.py`: the research step

| Name | What it does |
|---|---|
| `DEFAULT_GUIDANCE` | Prefer official/expert sources; avoid forums, listicles and AI content. |
| `build_system(series)` | Research instructions: the bible, the source guidance, only fetch pages found by search, prefer HTML to (expensive) PDFs, quote exact sentences, and flag disagreements or unsupported facts. |
| `build_prompt(topic, episode)` | The user message for an episode outline or a free topic. |
| `slugify(text)` | A file-safe name from a topic (max 40 chars). |
| `format_source_file(topic, result, model)` | The Markdown source file: a header with a "review before use" warning, then quotes grouped by page under its title and URL, tagged with PDF pages, snippet or unverified status. |
| `run_research(client, series, topic, episode, out_path, model)` | Runs research with the series' limits. Fails if nothing quotable came back, and writes the file unless `out_path` is `None`. |

#### `script.py`: writing and showing scripts

| Name | What it does |
|---|---|
| `SCHEMA_EXAMPLE` | The JSON shape shown to Claude. |
| `build_system(series)` | The system prompt: the series' role, `house_rules.md`, the bible, the output format and rules (shot/word ranges, target seconds, motions, genvideo cap, allowed refs with their descriptions and keywords, end card format, `pause_after`, `stamp` when `stamp_format` is set, the claims rule), and every source file. |
| `build_task(series, episode, outline, current, notes)` | The user message: recent episodes for continuity, the outline, and either "write it" or the current script plus revision notes. |
| `build_fix_prompt(task, reply, errors)` | The retry message: the original task, the failed reply, and the errors to fix. |
| `extract_json(text)` | Parses the reply, tolerating code fences or stray prose around the JSON object. |
| `_attempt(...)` | One call: get a reply, parse it and validate it. |
| `write_script(client, series, episode, outline, current, notes, missing_sources_ok)` | Tries once, retries once with the errors fed back, then raises listing both attempts' errors. Returns the JSON and its validation result. |
| `STATUS_LABELS` | Display labels for approval states. |
| `format_summary(script, series, status, warnings, errors)` | The terminal view: title, status, word/shot/genvideo counts against targets, each shot with its motion, narration, image prompt and refs, then the claims, warnings and problems. |
| `planned_calls(script)` | What later steps would call (images, voice lines, genvideo clips), shown with `--dry-run` on a draft. |

#### `refs.py`: reference images

| Name | What it does |
|---|---|
| `propose_system(series)` | The prompt asking Claude which recurring characters/objects need refs, with descriptions and keywords. |
| `propose_refs(client, series)` | Calls Claude, validates the reply, and normalizes names (`Signal Mirror` → `signal-mirror`) and keywords. |
| `save_ref_specs(series, specs)` | Writes `refs/refs.yaml` with an explanatory header. |
| `candidate_prompt(series, name, spec, style)` | The art style (or a trial `style`) plus the ref's description, drawn alone and centered on a plain background. |
| `refine_prompt(...)` | The same, plus "start from the attached image" for `--from`. |
| `candidate_path(series, name, i)` | `refs/candidates/<name>-<i>.png`. |
| `_candidates_dir`, `_read_log`, `_write_log` | Path of `refs/candidates/` and read/write of `candidates.json` (how each candidate was made). |
| `_existing_numbers(series, name)` | Candidate numbers already on disk for a ref. |
| `planned_candidates(series, names, force, refine, count)` | Which `(name, number)` pairs to draw: missing ones (all with `force`), or new numbers after the existing ones when refining or trying a style. |
| `generate_candidates(client, series, names, budget, ...)` | Draws the planned candidates at 1024×1024 within the budget, logging each one as it goes. |
| `pick(series, name, number)` | Copies a candidate to `refs/<name>.png`, replacing any older image for that ref. Refuses fake placeholders. |
| `status_lines(series)` | Each ref's picked/candidate status, description and keywords, for printing. |

#### `imagegen.py`: image generation

| Name | What it does |
|---|---|
| `ROUGH_COST_PER_IMAGE` | Dry-run estimates by quality (low $0.01, medium $0.07, high $0.25). |
| `TOKEN_RATES` | USD per million tokens (image in, text in, image out) per known model; real costs are computed from these. |
| `GeneratedImage` | PNG bytes plus token usage. `cost(model)` returns dollars, `0` for fake, or `None` for an unknown model. |
| `Budget` | Per-run spending guard. `check_next()` stops before the next image (assumed to cost as much as the priciest so far) would pass the limit; `add()` records a cost. Refuses models with no known rates, since the limit couldn't be enforced. |
| `make_budget(model, root)` | A budget at `IMAGE_COST_LIMIT` (unlimited for fake). |
| `ImageClient` | Protocol: `generate(prompt, size, refs) -> GeneratedImage`. |
| `OpenAIImageClient` | Uses `images.edit` with the reference images when a shot has refs, else `images.generate`. Retries with backoff, maps errors to `PipelineError`, and reads token usage. |
| `FakeImageClient` | Draws a beige placeholder with the prompt and refs written on it, and records calls. |
| `make_image_client(fake, root, quality)` | The fake client, or OpenAI after checking provider, key and model. |
| `fit_to_frame(png, frame)` | Scales an image to fit 1080×1920 and pads any gap with the image's average edge color. |
| `_edge_color(img)` | That average edge color. |

#### `images.py`: the shot images step

| Name | What it does |
|---|---|
| `ShotPlan` | One shot's image: path, full prompt, ref files, fingerprint, why it will be drawn (`""` = reuse), and a file to copy from if it moved. |
| `images_dir(ep)` | `episodes/epNN/images/`. |
| `shot_prompt(series, shot)` | The style line + the shot's `image_prompt` + (with refs) an instruction to match each numbered reference exactly in one continuous scene + "vertical 9:16, no text". |
| `_fingerprint(...)` | Hash of the prompt, model, quality, size and each ref image's name and bytes. |
| `plan_images(series, ep, script, model, redo)` | One plan per shot, with reason `--redo`, `new`, `changed since last drawn`, a free copy of an identical moved image, or reuse. |
| `describe_plan(plans, quality)` | The printed plan, with a rough cost. |
| `make_images(client, series, ep, plans, budget)` | Draws what's planned within the budget, fits each to the frame, updates the manifest after every image, and logs cost. Returns the amount spent. |

#### `voice.py`: the narration step

| Name | What it does |
|---|---|
| `LinePlan` | One line: shot, path, neighbouring lines, fingerprint, reason, copy-from. |
| `Word`, `Speech` | `(word, start, end)` timings, and MP3 bytes with optional word timings. |
| `VoiceClient` | Protocol: `speak(text, previous_text, next_text) -> Speech`, plus `model` and `voice_id`. |
| `audio_dir(ep)` | `episodes/epNN/audio/`. |
| `shot_seconds(line_seconds, pause)` | Line length + pause, at least 1.5 s. |
| `_pause(shot, default)` | The shot's `pause_after` if set, else the series pause. |
| `_fingerprint(...)` | Hash of the text, voice, model and (only if set) voice settings. |
| `words_from_alignment(chars, starts, ends)` | Groups ElevenLabs' per-character timings into words. |
| `plan_voice(ep, script, voice_id, model, redo, settings)` | One plan per line, with reason `--redo-voice`, `new`, `changed since last read`, `no word timings yet`, a free copy, or reuse. |
| `describe_voice_plan(plans)` | The printed plan, with the character count. |
| `make_voice(client, ep, plans, probe, pause)` | Reads what's planned, measures each file, updates the manifest after every line, and logs characters. Returns `(shot, shot seconds)` for every shot. |
| `narration_timings(ep, script, pause)` | `{shot: (line seconds, shot seconds)}`. Fails if any line is missing or doesn't match the script text. |
| `shot_durations(...)` | Just the shot seconds. |
| `word_timings(ep, script)` | `{shot: word timings or None}`. |
| `audio_seconds(path)` | Exact length via ffprobe. |
| `ElevenLabsVoiceClient` | Calls the with-timestamps text-to-speech endpoint with neighbouring-line context, voice settings and 4 retries. Decodes the audio and word timings. |
| `_explain(e, voice_id, model)` | Readable messages for ElevenLabs 401/404/other errors. |
| `FakeVoiceClient` | Silent MP3s at about 2.7 words/second, with evenly spread word timings so captions can be tested. |
| `voice_setup_problem(series, root)` | What's missing (key, model, voice ID), or `None`. |
| `make_voice_client(fake, series, root)` | The fake client, or ElevenLabs after checking setup. |

#### `reuse.py`: moved shots

| Name | What it does |
|---|---|
| `find_moved(manifest, folder, name, fingerprint, usable)` | Another file in the manifest made from exactly the same inputs (and still on disk), or `""`. |
| `apply_moves(folder, manifest_name, plans)` | Copies each plan's `copy_from` file and manifest entry to its new number, reading everything before writing, since a source may itself be a target. Marks those plans done and returns the count. |

#### `ffmpeg.py`: shared video helpers

| Name | What it does |
|---|---|
| `FPS`, `WIDTH`, `HEIGHT` | 30 fps, 1080×1920. |
| `frames(seconds)` | Whole frames for a duration. Every step uses it, so picture and sound stay in sync. |
| `run_ffmpeg(args, what)` | Runs ffmpeg quietly. On failure it raises with the last lines of ffmpeg's error output. |
| `loudness(paths, start, seconds)` | Integrated loudness (LUFS) of files played back to back, optionally of just the part that will be heard. Returns `None` if silent. |

#### `motion.py`: animating stills

| Name | What it does |
|---|---|
| Constants | Push-in zoom 10%, punch-in 20%, rock ±2° every 3 s with a 12 px bob at 115% scale, drift across 112% width. `MOTION_VERSION` forces re-renders when a look changes. |
| `ClipPlan` | One shot's clip: image, path, exact seconds, motion rendered, fingerprint, reason, and a note if the motion differs from the script's. |
| `clips_dir(ep)` | `episodes/epNN/clips/`. |
| `rendered_motion(motion, default)` | The script's motion if it's built, else the series default (for `genvideo`). |
| `plan_clips(ep, script, durations, redo, default_motion, hook_motion)` | One plan per shot, with `hook_motion` overriding shot 1. Durations are rounded to whole frames, and each clip is fingerprinted from its image bytes, frame count, motion and version. |
| `make_clips(ep, plans)` | Renders what's planned, updating the manifest after each clip. |
| `render_pushin` / `render_punchin` | A linear zoom, and a cubic ease-out zoom that lands early. Both go through `_render_zoom`, which upscales 2× before `zoompan` so the zoom doesn't shimmer. |
| `render_rock` | Scale up, rotate on a sine wave, crop with a bob, scale down. Each shot starts at a different phase. |
| `render_drift` | Crop window panning across an oversized frame; odd and even shots pan opposite ways. |
| `_render_looped(image, out, n_frames, vf)` | Shared ffmpeg call for rock and drift (rendered at 2× so slow movement doesn't step in whole pixels). |
| `RENDERERS` | Motion name → render function. |

#### `captions.py`: captions and the end card

| Name | What it does |
|---|---|
| `TextStyle`, `CAPTION_STYLES` | Size, colours, outline, vertical position and casing for each caption style, the end card and the follow (CTA) line. |
| `Overlay` | A PNG with a start and end time. |
| `caption_style(name)` | Looks up a style; an unknown name raises a list of valid ones. |
| `find_font(fonts_dir, name)` | The named font, else the first `.ttf`/`.otf`, else `None` (Pillow's built-in font). |
| `split_phrases(text)` | Breaks a line at punctuation, then into evenly sized groups of at most about 4 words / 22 characters, avoiding one-word stragglers. |
| `phrase_times(phrases, start, line_seconds, shot_end)` | Splits a line's spoken time across phrases by length; the last phrase holds to the shot's end. |
| `_font`, `_wrap`, `_fit` | Load a font; wrap text to the safe width; choose a size that prefers one line (shrinking up to 25%), else wraps (breaking end cards at ` · `), and never stacks more than two lines. |
| `render_text(text, style, font, out)` | Draws centered outlined text on a transparent 1080×1920 PNG. |
| `render_words(words, highlight, style, font, out)` | The same, word by word, so the spoken word can take the highlight colour. |
| `word_segments(text, words, start, shot_end)` | For `highlight_word`: one segment per spoken word (its phrase, which word is lit, start and end). Returns `None` if the timings don't line up with the text. |
| `build_overlays(timings, end_card, style, font, out_dir, cta)` | Clears old PNGs and renders every caption, word-timed where possible, else by phrase. Then it renders the end card (with the follow line composited on) for the last 2 s. |
| `build_stamps(stamps, shot_seconds, font, out_dir, until)` | One PNG per stamp, each held until a later shot changes it and cut off at the end card. |

#### `assemble.py`: the final video

| Name | What it does |
|---|---|
| `Assembly` | The result: final path, length, music, ambience and font used, and whether it was rebuilt. |
| `Bed` | A music or ambience track, the offset this episode starts at, and the gain that sets its level. |
| `_pick_track(folders, seed)` | A random track from the first folder that has any, seeded so an episode always gets the same one. |
| `music_folders` / `sfx_folders` | Series folder first, then `assets/`. |
| `_chosen_track` / `_choose_track` | Read or save a `--music`/`--sfx` choice. Saving needs exactly one matching file name, and the error lists what's available. |
| `pick_music`, `choose_music`, `pick_sfx`, `choose_sfx` | The above, for music and ambience. |
| `start_offset(track_seconds, needed, seed)` | A fixed random start point that leaves enough track to play without looping, or 0 for a short track. |
| `bed_gain(measured, level_db)` | Gain that puts a bed `level_db` under −16 LUFS narration. |
| `_bed(...)` | Builds a `Bed`, measuring only the part of the track that will be heard. |
| `_fingerprint(...)` | Hash of every input to `final.mp4`. |
| `assemble(series, ep, script, clips, timings, root, words, probe, measure)` | Picks beds and measures levels. If the fingerprint matches `final.json`, it returns without re-encoding. Otherwise it renders caption PNGs, builds a concat list of clips and an ffconcat stream of captions, and runs one ffmpeg pass. It writes to a temp file then moves it into place, and records `final.json`. |
| `_caption_stream(captions, total, folder, name)` | An ffconcat list showing each caption for its time, with blank frames in gaps. One input instead of hundreds keeps the ffmpeg graph small. |
| `_video_graph(caption_input, end_card, speed, stamp_input)` | Overlays the stamp stream (if any), the caption stream, then the end card during its time window. |
| `_audio_graph(audio, shot_seconds, beds, total, narration_gain, fade_in)` | Pads each line to its shot's exact length and joins the lines. Sets the narration gain, trims each bed with its gain and fades, mixes everything without normalizing, and adds the peak limiter. |
| `end_card_cta(template, episode)` | Fills in `{episode}` and `{next}`. |
| `length_warning(seconds, target)` | A note when the episode is under or over the target length. |

#### `review.py`: the review sheet

| Name | What it does |
|---|---|
| `Review` | The result: path, contact sheet, total USD, and the number of calls not priced in USD. |
| `_read_json`, `_cell` | Read a JSON file leniently; escape text for a Markdown table cell. |
| `contact_sheet(ep, script, motions, font)` | All stills at a fifth of full size, 5 per row, each labeled with shot number and rendered motion. Missing images show as an outlined box. |
| `write_review(series, ep, script, mode, status, shot_seconds, font)` | Writes `review.md`: header facts (length vs target, words, shots), video link with music and ambience, the before-posting checklist, narration, the shot table (planned → rendered motion), claims (flagging files not in `sources/`), video fallbacks, contact sheet and cost per step. |

#### `cost.py`: the cost log

| Name | What it does |
|---|---|
| `read_costs(ep)` | Entries in `cost.json` (empty if absent), with clear errors if the file is malformed. |
| `log_cost(ep, step, item, model, units, unit, amount_usd)` | Appends one paid call with a UTC timestamp and writes it immediately. |
| `StepCost`, `summarize(entries)` | Totals per step: calls, USD, units by kind, and unpriced calls. |
| `format_step(s)`, `_num(v)` | One summary line, e.g. `images: $0.84 · 12 calls · 12 image`. |

### `templates/series/`

The starter `series.yaml` (every setting, commented), `bible.md` (sections for premise, main character, narrator, tone, series rules, recurring characters, season arc and an example episode) and `style_line.txt`. `__TITLE__` and `__NAME__` are filled in by `new_series.py`, and every `TODO:` line must be replaced before the pipeline will run.

## Tests

```sh
pytest
```

The suite makes no network calls and never touches your real `series/` folder. `tests/conftest.py` builds a throwaway project with a small test series in a temp directory, and every API is replaced by the fake clients. Tests that render audio or video need ffmpeg installed.

| File | Covers |
|---|---|
| `test_series.py` | Loading and validating series folders and `.env` settings |
| `test_script.py` | Script writing, validation, retries, revise/redo/approve and the `make_episode.py` flow |
| `test_research.py` | Research prompts, citation handling and the source file |
| `test_images.py` | Refs, image planning, fingerprints, budgets and moved-shot reuse |
| `test_voice.py` | Narration planning, word timings, pauses and the ElevenLabs client wrapper |
| `test_video.py` | Motion rendering, captions, music/ambience, levels and assembly |
| `test_review.py` | The review sheet, contact sheet, cost log and `new_series.py` |

## Safety and privacy

- **Keys** live only in `.env` (git-ignored). They're passed explicitly to each SDK, so the SDKs never fall back to shell variables, and they're hidden from every `repr`.
- **Series content is private.** The whole `series/` folder is git-ignored, so bibles, sources, refs and episodes stay off GitHub while the code is public. Back it up yourself, for example by keeping it in a synced Drive folder.
- **Nothing is posted.** The only uploads are the inputs sent to the generation APIs themselves (prompts, and reference images to the image model).
- **Spending is guarded.** Nothing paid runs after the script until you approve it. `--dry-run` previews every paid call. Image runs stop at `IMAGE_COST_LIMIT`. Every paid call is logged in `cost.json`.
