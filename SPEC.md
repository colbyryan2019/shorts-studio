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
4. **Motion**: each shot becomes a clip. Per series `visual_mode`: `stills` animates every still with cheap ffmpeg motion (rocking, drifting, push-in); `video` turns every still into a generated video clip (image-to-video, still as first frame); `mixed` generates video for the most dramatic few shots and uses ffmpeg motion for the rest.
5. **Assembly**: ffmpeg joins clips, narration, music, sound effects, captions and the end card into the episode's `final.mp4`.
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
  prices.yaml           # editable per-model prices used by the cost guard
  assets/
    music/              # shared royalty-free tracks (.mp3)
    sfx/                # shared ambience loops (.mp3), e.g. waves, wind
    fonts/              # caption fonts (.ttf)
  series/
    stranded-at-sea/
      series.yaml       # per-series settings (see below)
      bible.md          # premise, characters, tone, season arc
      style_line.txt    # the image style line
      sources/          # factual source text (.txt / .md), optional
      refs/             # reference images (e.g. survivor.png, dinghy.png)
      music/            # optional series-specific tracks; falls back to assets/music
      sfx/              # optional series-specific ambience; falls back to assets/sfx
      episodes/
        ep01/
          script.json
          images/       # 01.png ...
          audio/        # 01.mp3 ...
          video/        # 01.mp4 ... raw generated clips, as returned by the video model
          clips/        # 01.mp4 ... per mode, fitted to the shot's duration
          final.mp4     # the series' visual_mode; --visual-mode X writes final_X.mp4
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
VIDEO_PROVIDER=fal       # fal (a Google Gemini API provider may come later)
VIDEO_MODEL=             # image-to-video model id, e.g. fal-ai/veo3.1/fast/image-to-video
```

Model names change; keep every model id in config, never hard-coded.

### Prices (prices.yaml)

Prices live in an editable `prices.yaml` at the project root, never in code. Values are placeholders for the user to confirm from each provider's pricing page.

```yaml
video:
  fal-ai/veo3.1/fast/image-to-video:
    per_second: 0.10            # 720p/1080p, no audio
    per_second_with_audio: 0.15
    durations: [4, 6, 8]        # clip lengths the model accepts, in seconds
    vertical: true              # accepts aspect_ratio 9:16
  fal-ai/veo3.1/image-to-video:
    per_second: 0.20
    per_second_with_audio: 0.40
    durations: [4, 6, 8]
    vertical: true
```

A model missing from `prices.yaml` can't be used for video (the cost guard has nothing to estimate with).

## Per-series configuration (series.yaml)

```yaml
title: Stranded at Sea
voice_id: ""                 # ElevenLabs voice for this series' narrator
words: [118, 135]            # narration length range (about 65 s)
seconds: [60, 70]            # optional: target episode length (default 60-70); the review and final build flag anything outside
shots: [12, 16]
end_card_format: "DAY {day} · WATER: {water}"
end_card_cta: "Follow for part {next}"   # optional: on-screen follow line under the end card; {episode}, {next}
default_motion: rock         # pushin | rock | drift (also the fallback for failed video shots)
hook_motion: punchin         # optional: shot 1's motion whatever the script says (punchin | pushin | rock | drift)
visual_mode: mixed           # stills | video | mixed
video_model: ""              # optional: overrides VIDEO_MODEL in .env for this series
max_video_shots: 3           # mixed mode: at most this many shots are generated video
clip_seconds: 6              # longest clip a script may plan a line for; must be a length the model supports
video_audio: false           # true = keep the model's own audio under the narration
max_video_cost_per_episode_usd: 3.00   # video spend only; images and voice are not counted
caption_style: highlight_word   # highlight_word | bold_white_outline | plain_white
music_volume_db: -20         # dB under the narration (more negative = quieter)
sfx_volume_db: -26           # optional: ambience bed, dB under the narration; no sfx/ files = no bed
music_fade_in_seconds: 0     # optional: fade-in of music and ambience (default 1); 0 = full level on the first frame
line_pause_seconds: 0.3      # optional: silence after each line (pacing)
voice_settings:              # optional: passed to ElevenLabs (tone and pace of the narrator)
  stability: 0.5
  similarity_boost: 0.75
  style: 0.0
  speed: 1.0
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
- The main character is an everyperson the viewer can imagine being: simplified, generic features. Recurring companions share the simplified look.
- Short, fast lines. Every episode runs within its series' `seconds` target (default 60–70, aim for about 65).
- Open on a hook: shot 1 shows the crisis as it happens, close up and mid-action, never a calm wide view or the aftermath.
- End on a cliffhanger. The narration never asks anything of the viewer (no vote, question, "comment below" or "follow"); an optional follow line is shown on the end card by the pipeline.
- When a series teaches facts, every factual claim comes from that series' sources and is listed in `claims`.
- Every story shot is a literal, physically possible scene: one sun in the sky, no symbolic counters or icons standing in for numbers or time. Simple and uncluttered applies to video shots too.
- Educational lines may cut away to a short explainer shot of the fact itself, in the series style (a see-through body with the kidneys glowing, salt crystals forming in seawater): one idea per shot, plain dark background, no text, labels or arrows, no main character.

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
     "motion": "video",
     "motion_prompt": "the mast slides under, bubbles rise, slow push-in",
     "refs": ["dinghy"]}
  ],
  "claims": [{"claim": "Seawater is saltier than kidneys can handle", "source_file": "fm21-76.txt"}]
}
```

Enforce the series.yaml limits (word count, shot count, `max_video_shots`). `motion` is one of `pushin`, `rock`, `drift`, `video`. Every shot has a `motion_prompt` (what moves and what the camera does), so one approved script works in all three visual modes. In `mixed` the script marks the most dramatic shots `video`, up to `max_video_shots`, putting the hook (shot 1) and the cliffhanger (last shot) first in line; in `video` mode every shot is rendered as video regardless of `motion`; in `stills` mode `video` shots use `default_motion`. Each line must be short enough to fit in `clip_seconds` (estimated from word count, checked against real durations after narration). `refs` names must match files in the series `refs/`. On validation failure, retry once with the errors fed back, then fail.

Older scripts using `genvideo` are converted to `video` once, with a generated `motion_prompt` added on the next `--revise`; until then they fail validation with a note.

Review: a new script is a draft. `--revise "notes"` sends the current script and notes back to the LLM; `--redo-script` starts over from the saved `outline.txt`. Both keep the previous version as `script.vN.json`. Hand edits to `script.json` are re-validated on every run. `--approve` records the hash of `script.json` in `approved.json`; any later change returns it to draft. Every paid step after the script requires an approved script.

### 2. Reference images (pipeline/refs.py, make_refs.py)

`refs/refs.yaml` lists each recurring character or object with a description and keywords; `make_refs.py` proposes it from the bible with Claude, and the user can edit it. `--generate` draws `ref_candidates` options per ref into `refs/candidates/` (logged in `candidates.json`); `--pick NAME N` copies one to `refs/NAME.png`. Placeholder candidates from `--fake-images` can't be picked. Once a ref has an image, any shot whose `image_prompt` contains one of its keywords (whole words) must list it in `refs`, or the script fails validation; the script prompt includes each ref's description and keywords.

### 3. Images (pipeline/images.py)

Only for an approved script that still passes validation. For each shot, prompt = series `style_line.txt` + shot `image_prompt` + an instruction to match the reference images, sending the listed reference images (OpenAI `images.edit`; `images.generate` when a shot has no refs). Requested at `image_size` (9:16), then scaled (and padded if needed) to 1080x1920. `images/manifest.json` records a fingerprint of each image's inputs (prompt, ref image contents, model, quality, size): a re-run redraws only shots whose fingerprint changed, plus any `--redo N`. Cost per image is computed from the returned token usage. `--approve` only approves; drawing happens on the next run.

### 4. Narration (pipeline/voice.py)

ElevenLabs text-to-speech with the series `voice_id`, `VOICE_MODEL` and optional `voice_settings`, one request per line (with the neighbouring lines sent as context so the delivery flows), so each line's duration is known exactly. Each request asks for word timings (the with-timestamps endpoint), saved in the manifest for word-by-word captions. Save `audio/NN.mp3`; durations are measured with ffprobe. Shot duration = line duration + `line_pause_seconds` (default 0.3 s; a shot's optional `pause_after` overrides it for a held beat; minimum 1.5 s total). `audio/manifest.json` records each line's text, voice, model, settings, characters, word timings and durations: a re-run re-reads only lines whose text, voice, model or settings changed, plus any `--redo-voice N`. Until the key, model and voice are set the step is skipped with a note; `--fake-voice` makes silent placeholder lines of a realistic length for free.

### 5. Motion (pipeline/motion.py)

Turn each still into a clip of the shot's duration at 1080x1920, 30 fps:
- `pushin`: slow zoom 100% to 110% (zoompan).
- `punchin`: the hook: a fast zoom 100% to 120% that eases out, so most of it lands in the first moments. Used for shot 1 when the series sets `hook_motion`; not a script motion.
- `rock`: scale to about 115%, rotate with a small sine wave (about ±2°, period about 3 s) plus a small vertical bob, then crop back to 1080x1920 so no corners show. Each shot starts at a different point in the swell.
- `drift`: slow, steady horizontal pan across a frame about 112% wide; alternate shots pan opposite ways.
- `rock` and `drift` are rendered at 2x and scaled down, so slow movement doesn't step in whole pixels.
- `video`: see the video step below.

Clip length is a whole number of frames, and the narration is padded to the same length, so sound and picture stay in sync. `clips/manifest.json` fingerprints each clip (image, length, motion, and for video the raw clip), so only clips whose inputs changed are re-rendered, plus any `--redo N`.

### 5b. Video (pipeline/video.py, pipeline/videogen.py)

Runs after narration, so each shot's real duration is known. Only for an approved script that still passes validation.

- **Consistency:** the still (made from the series refs) is always generated first and sent as the first frame for image-to-video. All visual style comes from `style_line.txt` and the bible: the video prompt is the shot's `motion_prompt` plus an instruction to keep the still's art style, characters and framing. Nothing style-specific in `pipeline/`.
- **Provider:** a small `VideoProvider` interface (`generate(still, prompt, seconds, audio) -> mp4`, plus the durations and vertical support it reads from `prices.yaml`). One implementation to start: fal.ai via `fal-client` (Veo 3.1 Fast and Veo 3.1). Model from the series `video_model`, else `VIDEO_MODEL`.
- **Format:** request 9:16 at 1080p for models with `vertical: true` (both Veo 3.1 models), then scale to exactly 1080x1920, 30 fps. For a model without vertical support, the run stops and asks: crop or pad is a decision for the user, not a default.
- **Clip length:** the shortest length the model supports that covers the shot's duration, up to `clip_seconds`.
- **Audio:** `generate_audio` off unless `video_audio: true`; the ElevenLabs narration is always the main audio.
- **Fitting to narration:** trim if longer. If shorter by 1 s or less, hold the last frame; if shorter by more, continue with the series `default_motion` on the clip's last frame for the remainder.
- **Failures:** retry with backoff (3 attempts), then fall back to `default_motion` on the still. Every fallback is recorded in the clip manifest and listed in `review.md`.
- **Caching:** raw clips are saved to `video/NN.mp4` and fingerprinted (still contents, motion prompt, model, length, audio flag), so they're reused by every mode and every re-run. `--redo N` regenerates only shot N.

### 5c. Cost guard (pipeline/cost.py)

- Before any video call, print the estimated video cost for the episode: each video shot at its planned clip length × the model's price from `prices.yaml`, one attempt each, plus a note that retries can add up to 2 more attempts per shot. Already-cached clips cost nothing.
- If the estimate is over `max_video_cost_per_episode_usd`, stop before spending anything unless `--allow-over-budget` is passed.
- Every actual paid call (images, voice, video) is appended to the episode's `cost.json` with model, units, price and amount.

### 5d. Visual mode override

`--visual-mode stills|video|mixed` overrides the series setting for one run and writes `final_stills.mp4`, `final_mixed.mp4` or `final_video.mp4`, reusing the same approved script, stills, narration and video cache. Fitted clips are kept per mode (`clips/<mode>/`) so switching modes doesn't re-render the others.

### 6. Assembly (pipeline/assemble.py)

- Concatenate the shot clips with hard cuts.
- Narration track: line audio placed at each shot's start.
- Levels: the narration is measured and brought to -16 LUFS; music and ambience are measured too and set `music_volume_db` / `sfx_volume_db` under it, whatever their own mastering; a limiter keeps peaks under -1 dBFS.
- Music: a track from the series `music/` (or `assets/music/`), chosen at random (or with `--music NAME`, saved in the episode's `music.txt`; `--sfx NAME` picks the ambience loop the same way, saved in `sfx.txt`) but the same one on every run of an episode, starting at a random point in the track (also fixed per episode) so long tracks give varied beds and skip quiet intros. Faded in over `music_fade_in_seconds` (default 1 s; 0 starts at full level so the hook lands hard), looped if short, faded out over the last 2 s. With no tracks the video has narration only, with a note.
- Sound effects: an ambience loop (waves, wind) from the series `sfx/` (or `assets/sfx/`), picked and offset the same way, at `sfx_volume_db` under everything, with the same fades. No files, no bed.
- Captions: drawn with Pillow as transparent PNGs and overlaid by ffmpeg (so any ffmpeg build works, no libass needed), in the font from `assets/fonts/` (first .ttf/.otf; Pillow's built-in font if none). `highlight_word` shows a short phrase of a few words with the word being spoken in a highlight colour, timed from ElevenLabs' word timings (no transcription); lines without word timings (e.g. `--fake-voice`) fall back to `bold_white_outline`. `bold_white_outline` and `plain_white` show one short phrase at a time, timed from the line durations.
- End card: `end_card` text in plain white capitals over the last shot for its final 2 s, with the series' `end_card_cta` (if set) as a smaller highlighted line under it. The narration itself never asks the viewer for anything.
- `final.json` fingerprints every input, so an unchanged episode isn't re-encoded.
- Output `final.mp4`, H.264, AAC, 1080x1920, 30 fps.

### 7. Review and cost (pipeline/review.py)

Write `review.md`: title, full narration, the claims with their source files, the motion per shot (and the mode it was rendered in), any video fallbacks with their errors, and a contact sheet image of all stills. Summarise `cost.json` (per step and episode total); print the total at the end.

## Requirements

- Python 3.11+, ffmpeg on PATH. Libraries: anthropic, openai, elevenlabs, fal-client, pydantic, python-dotenv, pillow, pyyaml.
- No series-specific logic in `pipeline/`; everything series-specific comes from the series folder.
- Re-running is safe: existing outputs are reused; `--redo 5` regenerates shot 5's image and clip; `--redo-script` starts over.
- `--dry-run` prints the script and planned API calls (with the video cost estimate) without spending money on images, voice or video.
- Clear errors when a key or series file is missing; retries with backoff on API errors.
- Load API keys only from `.env` inside the project (python-dotenv), never from the shell environment setup.
- Never post or publish anywhere. The only uploads are inputs sent to the generation APIs themselves (reference images to the image model, stills to the video model).

## Milestones

1. Project skeleton, series loading (series.yaml, bible, house rules), and the script step with validation and `--dry-run`. Test with Stranded at Sea episode 1. *(done)*
1b. Research step (`research.py`) that fills `sources/` with cited quotes. *(done)*
2. `make-refs` and images with references and caching. *(done)*
3. Narration with per-line durations. *(done)*
4. Assembly with `pushin`, captions, music and end card: first full video. *(done)*
5. `rock` and `drift` motion. *(done)*
6. Polish (free): word timings and `highlight_word` captions, the `sfx/` ambience bed, music start offset and fade-in, `line_pause_seconds` and `voice_settings`, the literal-scene and cutaway house rules. Then re-script Stranded at Sea episode 1 at about 65 s and render it in stills.
7. Video mode: `VideoProvider` (fal + Veo 3.1 Fast / Veo 3.1), image-to-video from the stills, fitting to narration, fallbacks, `prices.yaml`, the cost guard and `cost.json`.
8. Mixed mode and comparison: `visual_mode` and the video settings in series.yaml (replacing `max_genvideo_shots`), `motion_prompt` in the script, the `--visual-mode` override. Render Stranded at Sea episode 1 in stills and mixed to compare.
9. Review sheet, `--redo` flags, `new_series.py`, polish.

## Acceptance

`python make_episode.py --series stranded-at-sea --episode 1 --outline "..."` produces a vertical video within the series' `seconds` target (60–70 s for Stranded at Sea, aim for 65) with word-timed captions, steady narration, every shot in motion, the end card, a review sheet and a cost log. In `stills` mode it costs under about $1 in API spend; in `mixed` mode the video spend stays under `max_video_cost_per_episode_usd` unless `--allow-over-budget` is passed. Creating a second series with `new_series.py` requires no code changes.
