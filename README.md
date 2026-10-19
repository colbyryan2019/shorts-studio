# Shorts Studio

Turns a short episode outline into a finished vertical video, for any number of series. The design is in [SPEC.md](SPEC.md).

**Status:** milestones 1–6 and 9: research, the script (with review and approval), reference images, shot images, narration with word timings, the full video (push-in, rock and drift motion, word-by-word captions, music, ambience, end card), a review sheet and cost log per episode, and `new_series.py`. Still to come: video and mixed modes (milestones 7–8).

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

`--redo` and `--redo-voice` take one shot (`5`), a list (`3,7`), a range (`2-4`) or `all`, and can be repeated. They only run on an approved script, on their own (not with `--approve`, `--revise` or `--redo-script`). Redrawing a shot's image also re-renders its clip.

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

- One request per line, so each shot's length is exact: line length + `line_pause_seconds` (default 0.3 s; at least 1.5 s). The total is printed at the end.
- Each line also records when every word is spoken, for word-by-word captions. Lines read before this was added are re-read once.
- Optional `voice_settings` in `series.yaml` (`stability`, `similarity_boost`, `style`, `speed`, `use_speaker_boost`) tune the delivery; changing them re-reads the lines.
- Re-running only re-reads lines whose narration, voice or model changed. Editing one line costs one line.
- ElevenLabs bills characters against your plan's credits; `--dry-run` shows how many a run will use.
- Until the voice is set up, runs still draw images and just skip narration with a note.

## Video

Once the images and narration exist, the same run renders each shot into a clip and assembles `episodes/epNN/final.mp4` (1080x1920, 30 fps). This step is free: it's all ffmpeg on your machine.

```sh
python make_episode.py --series stranded-at-sea --episode 1                              # images, narration, video
python make_episode.py --series stranded-at-sea --episode 1 --fake-images --fake-voice   # whole pipeline, free
```

- **Music:** put royalty-free `.mp3` tracks in `assets/music/` (or `series/<name>/music/` for that series only). Any length works. Each episode picks one track and a starting point in it, keeps both on every run, and fades the music in and out. `--music NAME` picks the track whose file name contains NAME for that episode instead.
- **Levels:** the narration is brought to a standard loudness (-16 LUFS), and music sits `music_volume_db` under it (default -20; more negative is quieter), measured per track, so loud and quiet masters end up at the same level.
- **Ambience:** optional looping beds (waves, wind) in `assets/sfx/` (or `series/<name>/sfx/`), picked and placed the same way, at `sfx_volume_db` (default -26).
- **Captions:** put `.ttf` / `.otf` fonts in `assets/fonts/`; `caption_font` in `series.yaml` picks one by file name (default: the first). Style is `caption_style`: `highlight_word` (a few words at a time, the spoken word in yellow), `bold_white_outline` or `plain_white`.
- Music, ambience and fonts are git-ignored (their licenses may not allow redistribution), so keep your own copies.
- **The hook:** `hook_motion: punchin` in `series.yaml` gives shot 1 a fast zoom that lands in the first moments, whatever the script says (or `pushin`, `rock`, `drift`; leave it out to keep the script's motion). `music_fade_in_seconds: 0` starts the music and ambience at full level on the first frame instead of fading in over 1 s.
- Each shot moves as its `motion` says: `pushin` (slow zoom), `rock` (gentle tilt and bob, like a boat on a swell) or `drift` (slow sideways pan). `genvideo` shots use the series `default_motion` until that milestone is built.
- Re-running re-renders only clips whose image or narration changed, and skips the final build if nothing changed. The target length is 60–70 s (aim for 65); you'll get a note outside that.

## Review sheet and cost

Every run that gets past the images writes `episodes/epNN/review.md`: the title, length, the full narration, each shot with its length and motion (planned, and what was actually rendered), the claims with their source files, any video fallbacks, a `contact_sheet.png` of all the stills, and the cost summary. It has a short checklist; go through it before posting. Nothing is ever posted for you.

Every paid call (each image, each ElevenLabs line) is appended to `episodes/epNN/cost.json` with its model, units and price. Images are priced in USD from their token usage; ElevenLabs lines are logged in characters, since they're billed against your plan's credits. Placeholder runs (`--fake-*`) aren't logged. Calls made before this was added aren't in the log.

## Starting a new series

```sh
python new_series.py --name lost-in-the-desert                          # title from the name: "Lost In The Desert"
python new_series.py --name lost-in-the-desert --title "Lost in the Desert"
```

This copies `templates/series/` into `series/<name>/`: a `series.yaml` with every setting and its default, a `bible.md` outline and a `style_line.txt`, plus empty `sources/`, `refs/` and `episodes/`. Fill in every `TODO:` line in the bible and style line (every command refuses to run until you do), check `series.yaml` (voice, word and shot ranges, end card, `sources_required`), then make refs, research and script as above. No code changes are needed.

## Tests

```sh
pytest
```

The tests make no network calls.
