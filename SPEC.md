# Shorts Studio: Episode Pipeline Spec

Build a reusable Python command-line tool that turns a short episode outline into a finished vertical video (MP4) for any series. Each series lives in its own folder with its own bible, style, voice and sources; the engine code is shared. The first series is "Stranded at Sea." Build it milestone by milestone (see the end of this file). Ask before adding dependencies beyond those listed.

## What it does

```
python make_episode.py --series stranded-at-sea --episode 1 --outline "Your boat sinks. No water. You must not drink seawater. End: storm on the horizon."
python research.py --series stranded-at-sea --episode 1   # finds sources for the episode, saves quotes to sources/
python new_series.py --name next-idea          # scaffolds a new series folder from templates
```

0. **Research**: Claude searches the web and saves verbatim quotes with links to the series `sources/`, for a human to check.
1. **Script**: an LLM writes the narration and a shot list from the outline, the house rules, the series bible and the series sources. The script stays a draft until a human approves it; nothing paid runs after the script until then.
2. **Images**: an image API generates one still per shot in the series style, using the series reference images.
3. **Narration**: ElevenLabs reads each line in the series narrator voice.
4. **Motion**: each still becomes a short clip with cheap ffmpeg motion (rocking, drifting, push-in). At most a few shots per episode (set per series) use a real image-to-video model.
5. **Assembly**: ffmpeg joins clips, narration, music, captions and the end card into the episode's `final.mp4`.
6. **Review**: it writes a review sheet so a human can check the script and claims before posting. It never posts anything.

## Folder layout

```
shorts-studio/
  SPEC.md
  house_rules.md        # rules every series follows (see below)
  make_episode.py
  research.py
  make_refs.py
  new_series.py
  pipeline/             # one module per step, no series-specific code
  templates/
    series/             # starter files copied by new_series.py
  assets/
    music/              # shared royalty-free tracks (.mp3)
    fonts/              # caption fonts (.ttf)
  series/
    stranded-at-sea/
      series.yaml       # per-series settings (see below)
      bible.md          # premise, characters, tone, season arc
      style_line.txt    # the image style line
      sources/          # factual source text (.txt / .md), optional
      refs/             # reference images (e.g. survivor.png, dinghy.png)
      music/            # optional series-specific tracks; falls back to assets/music
      episodes/
        ep01/
          script.json
          images/       # 01.png ...
          audio/        # 01.mp3 ...
          clips/        # 01.mp4 ...
          final.mp4
          review.md
          cost.json
  .env                  # API keys only, never committed
```

The whole `series/` folder is git-ignored: the code is public, series content is not. Put `series/` inside a Google Drive or OneDrive synced folder, or symlink it there, so it is backed up and finished videos appear on every device.

## Global configuration (.env)

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=          # image generation (or use FAL for images too)
ELEVENLABS_API_KEY=
FAL_KEY=                 # image-to-video clips
LLM_MODEL=               # current Claude Sonnet-class model string
IMAGE_PROVIDER=openai    # openai | fal
IMAGE_MODEL=             # image model id, e.g. gpt-image-2.5-sunburst
VOICE_MODEL=             # ElevenLabs model id, e.g. eleven_multilingual_v2
VIDEO_MODEL=             # fal model id for image-to-video (Veo Lite or Kling class)
```

Model names change; keep every model id in config, never hard-coded.

## Per-series configuration (series.yaml)

```yaml
title: Stranded at Sea
voice_id: ""                 # ElevenLabs voice for this series' narrator
words: [90, 110]             # narration length range
shots: [8, 12]
max_genvideo_shots: 2
end_card_format: "DAY {day} · WATER: {water}"
default_motion: rock         # pushin | rock | drift
caption_style: bold_white_outline
music_volume_db: -20
sources_required: true       # every factual claim must cite a file in sources/
research_max_searches: 5     # optional: web searches per research run
research_max_fetches: 3      # optional: pages read per research run
research_guidance: "..."     # optional: which sources to prefer
image_quality: medium        # optional: passed to the image model
image_size: "1152x2048"      # optional: requested size, scaled to 1080x1920
ref_candidates: 3            # optional: candidates per ref in make_refs.py
```

`new_series.py` copies `templates/series/` into `series/<name>/` with a blank bible, style line and series.yaml.

## House rules (house_rules.md, applied to every series)

- Second-person narration ("you"), serious and dramatic. No jokes, no comic relief, no mascot.
- Visual style is set per series (`style_line.txt`); keep it simple and readable on a phone. The narration carries the drama.
- The main character is an everyperson the viewer can imagine being: simplified, generic features.
- Short, fast lines.
- End on a cliffhanger. Never a vote, a question to the audience, or "comment below."
- When a series teaches facts, every factual claim comes from that series' sources and is listed in `claims`.

The series bible can add rules but not override these.

## Step details

### 0. Research (pipeline/research.py, research.py)

One Claude call with the web search and web fetch tools (basic versions, citations on). Claude searches, reads the 2–3 most authoritative pages (per `research_guidance`), and writes a brief quoting them. Only the quoted passages are saved, verbatim, grouped by page with title and URL, to `sources/epNN-research.md` (or a `--topic` name). Each quote is matched against the fetched page text; unmatched quotes are flagged. Claude's own summary is printed, never saved as a source. Existing files are not overwritten without `--force`; `--fake-llm` previews without saving.

### 1. Script (pipeline/script.py)

Call the Claude API with: `house_rules.md`, the series `bible.md`, relevant text from the series `sources/`, the outline, and the last 3 episodes' `script.json` (for continuity). Return strict JSON, validated with pydantic:

```json
{
  "title": "Day One",
  "end_card": "DAY 1 · WATER: NONE",
  "shots": [
    {"n": 1, "narration": "Your boat is gone.",
     "image_prompt": "A sailboat mast vanishing under dark water at dawn",
     "motion": "drift", "refs": ["dinghy"]}
  ],
  "claims": [{"claim": "Seawater is saltier than kidneys can handle", "source_file": "fm21-76.txt"}]
}
```

Enforce the series.yaml limits (word count, shot count, genvideo cap). `motion` is one of `pushin`, `rock`, `drift`, `genvideo`. `refs` names must match files in the series `refs/`. On validation failure, retry once with the errors fed back, then fail.

Review: a new script is a draft. `--revise "notes"` sends the current script and notes back to the LLM; `--redo-script` starts over from the saved `outline.txt`. Both keep the previous version as `script.vN.json`. Hand edits to `script.json` are re-validated on every run. `--approve` records the hash of `script.json` in `approved.json`; any later change returns it to draft. Every paid step after the script requires an approved script.

### 2. Reference images (pipeline/refs.py, make_refs.py)

`refs/refs.yaml` lists each recurring character or object with a description and keywords; `make_refs.py` proposes it from the bible with Claude, and the user can edit it. `--generate` draws `ref_candidates` options per ref into `refs/candidates/` (logged in `candidates.json`); `--pick NAME N` copies one to `refs/NAME.png`. Placeholder candidates from `--fake-images` can't be picked. Once a ref has an image, any shot whose `image_prompt` contains one of its keywords (whole words) must list it in `refs`, or the script fails validation; the script prompt includes each ref's description and keywords.

### 3. Images (pipeline/images.py)

Only for an approved script that still passes validation. For each shot, prompt = series `style_line.txt` + shot `image_prompt` + an instruction to match the reference images, sending the listed reference images (OpenAI `images.edit`; `images.generate` when a shot has no refs). Requested at `image_size` (9:16), then scaled (and padded if needed) to 1080x1920. `images/manifest.json` records a fingerprint of each image's inputs (prompt, ref image contents, model, quality, size): a re-run redraws only shots whose fingerprint changed, plus any `--redo N`. Cost per image is computed from the returned token usage. `--approve` only approves; drawing happens on the next run.

### 4. Narration (pipeline/voice.py)

ElevenLabs text-to-speech with the series `voice_id` and `VOICE_MODEL`, one request per line (with the neighbouring lines sent as context so the delivery flows), so each line's duration is known exactly. Save `audio/NN.mp3`; durations are measured with ffprobe. Shot duration = line duration + 0.3 s pause (minimum 1.5 s). `audio/manifest.json` records each line's text, voice, model, characters and durations: a re-run re-reads only lines whose text, voice or model changed, plus any `--redo-voice N`. Until the key, model and voice are set the step is skipped with a note; `--fake-voice` makes silent placeholder lines of a realistic length for free.

### 5. Motion (pipeline/motion.py)

Turn each still into a clip of the shot's duration at 1080x1920, 30 fps:
- `pushin`: slow zoom 100% to 110% (zoompan).
- `rock`: scale to about 115%, rotate with a small sine wave (about ±2°, period about 3 s) plus a small vertical bob, then crop back to 1080x1920 so no corners show.
- `drift`: slow horizontal pan across a slightly oversized frame.
- `genvideo`: send the still to the image-to-video model on fal.ai with a short motion prompt that preserves the art style, trim or loop to duration. If the call fails, fall back to the series `default_motion`.

### 6. Assembly (pipeline/assemble.py)

- Concatenate the shot clips with hard cuts.
- Narration track: line audio placed at each shot's start.
- Music: random track from the series `music/` (or `assets/music/`), at `music_volume_db` under narration, fade out at the end.
- Captions: build an ASS subtitle file from the script lines and known timings (no transcription needed), styled per `caption_style`, one short phrase at a time (split long lines).
- End card: `end_card` text in plain white capitals over the last shot for its final 2 s.
- Output `final.mp4`, H.264, AAC, 1080x1920, 30 fps.

### 7. Review and cost (pipeline/review.py)

Write `review.md`: title, full narration, the claims with their source files, the motion per shot, and a contact sheet image of all stills. Write `cost.json` with an estimated cost per API call and the episode total; print the total at the end.

## Requirements

- Python 3.11+, ffmpeg on PATH. Libraries: anthropic, openai, elevenlabs, fal-client, pydantic, python-dotenv, pillow, pyyaml.
- No series-specific logic in `pipeline/`; everything series-specific comes from the series folder.
- Re-running is safe: existing outputs are reused; `--redo 5` regenerates shot 5's image and clip; `--redo-script` starts over.
- `--dry-run` prints the script and planned API calls without spending money on images, voice or video.
- Clear errors when a key or series file is missing; retries with backoff on API errors.
- Load API keys only from `.env` inside the project (python-dotenv), never from the shell environment setup.
- Never upload or post anywhere.

## Milestones

1. Project skeleton, series loading (series.yaml, bible, house rules), and the script step with validation and `--dry-run`. Test with Stranded at Sea episode 1.
1b. Research step (`research.py`) that fills `sources/` with cited quotes.
2. `make-refs`: generate candidate reference images for the characters and objects in the bible, in the series style; the user picks one of each for `refs/`. Scripts must list the ref for every shot that shows them. Then images with references and caching.
3. Narration with per-line durations.
4. Assembly with `pushin` only, captions, music and end card: first full video.
5. `rock` and `drift` motion.
6. `genvideo` clips with fallback and the per-series cap.
7. Review sheet, cost log, `--redo` flags, `new_series.py`, polish.

## Acceptance

`python make_episode.py --series stranded-at-sea --episode 1 --outline "..."` produces a 25–45 second vertical video with matching captions, steady narration, at least half the shots in motion, the end card, a review sheet and a cost log, for under about $2 in API spend. Creating a second series with `new_series.py` requires no code changes.
