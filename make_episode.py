"""Turn an episode outline into a vertical video. So far: the script (with review and approval), images, narration and the
assembled video (push-in, rock and drift motion, captions, music, end card).

    python make_episode.py --series stranded-at-sea --episode 1 --outline "..." [--fake-llm]
    python make_episode.py --series stranded-at-sea --episode 1 --revise "make line 4 punchier"
    python make_episode.py --series stranded-at-sea --episode 1 --approve
    python make_episode.py --series stranded-at-sea --episode 1 [--dry-run] [--redo 5] [--redo-voice 5]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.assemble import assemble, length_warning
from pipeline.config import PROJECT_ROOT, load_settings
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.imagegen import FAKE_MODEL, ImageClient, make_budget, make_image_client
from pipeline.images import describe_plan, make_images, plan_images
from pipeline.llm import LLMClient, make_client
from pipeline.motion import make_clips, plan_clips
from pipeline.schema import validate_script
from pipeline.script import format_summary, planned_calls, write_script
from pipeline.series import Series, load_series
from pipeline.voice import FAKE_MODEL as FAKE_VOICE_MODEL
from pipeline.voice import (VoiceClient, describe_voice_plan, make_voice, make_voice_client, narration_timings,
                            plan_voice, voice_setup_problem)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Make one episode of a series.")
    p.add_argument("--series", required=True, help="folder name under series/")
    p.add_argument("--episode", required=True, type=int, help="episode number, e.g. 1")
    p.add_argument("--outline", help="short outline of the episode (needed the first time)")
    p.add_argument("--dry-run", action="store_true", help="write the script, list planned paid calls, spend nothing else")
    p.add_argument("--redo-script", action="store_true", help="write a new script from the outline (the old one is kept as script.vN.json)")
    p.add_argument("--revise", metavar="NOTES", help="ask the LLM to revise the current script per these notes")
    p.add_argument("--approve", action="store_true", help="approve the current script so later steps may run")
    p.add_argument("--fake-llm", action="store_true", help="use a canned script instead of calling the Claude API")
    p.add_argument("--fake-images", action="store_true", help="draw placeholder images instead of calling the image API")
    p.add_argument("--redo", type=int, action="append", default=[], metavar="N",
                   help="redraw shot N's image even if it is up to date (repeatable)")
    p.add_argument("--fake-voice", action="store_true", help="make silent placeholder narration instead of calling ElevenLabs")
    p.add_argument("--redo-voice", type=int, action="append", default=[], metavar="N",
                   help="re-read shot N's narration even if it is up to date (repeatable)")
    args = p.parse_args(argv)

    if args.episode < 1:
        p.error("--episode must be 1 or more")
    if args.revise is not None and args.redo_script:
        p.error("use either --revise or --redo-script, not both")
    if args.approve and (args.revise is not None or args.redo_script):
        p.error("--approve can't be combined with --revise or --redo-script: review the new script first")
    if args.revise is not None and not args.revise.strip():
        p.error("--revise needs notes, e.g. --revise \"rewrite in second person\"")
    return args


def make_script_client(args: argparse.Namespace, series: Series, root: Path) -> LLMClient:
    if series.config.sources_required and not series.sources:
        if not args.fake_llm:
            raise PipelineError(
                f"No source text in {series.path / 'sources'} and sources_required is true. "
                f"Run: python research.py --series {series.name} --episode {args.episode}  "
                "(or add .txt / .md files yourself), then run again."
            )
        print(f"Warning: {series.path / 'sources'} is empty; continuing because of --fake-llm.\n")
    client, _ = make_client(args.fake_llm, root)
    return client


def run(argv: list[str] | None = None, root: Path = PROJECT_ROOT,
        client: LLMClient | None = None, image_client: ImageClient | None = None,
        voice_client: VoiceClient | None = None) -> int:
    args = parse_args(argv)
    series = load_series(args.series, root, args.episode)
    ep = Episode.of(series, args.episode)
    missing_sources_ok = args.fake_llm and not series.sources
    revising = args.revise is not None

    if args.outline:
        if ep.has_script() and not (args.redo_script or revising) and args.outline.strip() != ep.load_outline():
            print("Note: a script already exists, so --outline was not used. Add --redo-script to write a new one.\n")
        else:
            ep.save_outline(args.outline)

    if revising and not ep.has_script():
        raise PipelineError(f"Nothing to revise: {ep.script_path} doesn't exist yet. Run with --outline first.")

    if args.redo_script or revising or not ep.has_script():
        outline = ep.load_outline()
        if not outline:
            raise PipelineError("No outline for this episode. Pass --outline \"...\".")
        current = None
        if revising:
            current = ep.load_script_data()
            if not isinstance(current, dict):
                raise PipelineError(f"{ep.script_path} must contain a JSON object to revise.")
        llm = client or make_script_client(args, series, root)
        print("Revising the script..." if revising else "Writing the script...")
        data, _ = write_script(
            llm, series, args.episode, outline,
            current=current, notes=args.revise, missing_sources_ok=missing_sources_ok,
        )
        backup = ep.save_script(data)
        print(f"Saved {ep.script_path}" + (f" (previous version kept as {backup.name})" if backup else ""))
    else:
        print(f"Using existing {ep.script_path} (edit it by hand, or use --revise / --redo-script)")

    # Always re-check what is on disk: it may have been edited by hand.
    result = validate_script(ep.load_script_data(), series, missing_sources_ok=missing_sources_ok)
    if result.script is None:
        print(f"\n{ep.script_path} does not match the script format:")
        print("\n".join(f"  x {e}" for e in result.errors))
        return 1

    if args.approve:
        if result.errors:
            print(format_summary(result.script, series, ep.approval_status(), result.warnings, result.errors))
            print("\nNot approved: fix the problems above first.")
            return 1
        ep.approve()
        print()
        print(format_summary(result.script, series, ep.approval_status(), result.warnings))
        print("\nApproved. Run again without --approve to make the images (add --dry-run to preview the cost first).")
        return 0

    status = ep.approval_status()
    print()
    print(format_summary(result.script, series, status, result.warnings, result.errors))
    print()

    if status != "approved":
        print("Next: review the script, then either edit script.json, run with --revise \"notes\", or --approve.")
        if args.dry_run:
            print()
            print(planned_calls(result.script))
            print("(These would wait until the script is approved.)")
        return 1 if result.errors else 0

    if result.errors:
        print("The approved script no longer passes the checks above (for example, refs picked since). "
              "Fix it with --revise or by hand, then --approve again.")
        return 1

    if args.fake_images:
        model = FAKE_MODEL
    else:
        model = image_client.model if image_client else load_settings(root).image_model
    plans = plan_images(series, ep, result.script, model, set(args.redo))
    print(describe_plan(plans, series.config.image_quality))

    # Narration runs only once it's set up (key, model, voice), or with --fake-voice.
    if voice_client:
        voice_ready, voice_id, voice_model = True, voice_client.voice_id, voice_client.model
    elif args.fake_voice:
        voice_ready, voice_id, voice_model = True, "fake", FAKE_VOICE_MODEL
    else:
        problem = voice_setup_problem(series, root)
        voice_ready = problem is None
        voice_id, voice_model = series.config.voice_id, load_settings(root).voice_model
    voice_plans = plan_voice(ep, result.script, voice_id, voice_model, set(args.redo_voice))
    print()
    if voice_ready:
        print(describe_voice_plan(voice_plans))
    else:
        print(f"Narration: skipped until set up ({problem}). --fake-voice makes placeholder audio.")

    if args.dry_run:
        print("Video: rendered for free with ffmpeg once the images and narration exist.")
        print("\n(--dry-run: nothing drawn, read or rendered.)")
        return 0

    if any(p.reason for p in plans):
        images = image_client or make_image_client(args.fake_images, root, series.config.image_quality)
        print(f"\nDrawing with {images.model}...")
        spent = make_images(images, series, ep, plans, make_budget(images.model, root))
        print(f"Image cost this run: ${spent:.2f}")
    print(f"\nImages are in {ep.path / 'images'}. Look them over; --redo N redraws one shot, and editing a "
          "shot's image_prompt redraws just that shot next run.")

    if voice_ready:
        if any(p.reason for p in voice_plans):
            voice = voice_client or make_voice_client(args.fake_voice, series, root)
            print(f"\nReading narration with {voice.model}...")
            durations = make_voice(voice, ep, voice_plans)
        else:
            durations = make_voice(None, ep, voice_plans)  # nothing to read; just collects durations
        total = sum(seconds for _, seconds in durations)
        print(f"\nNarration is in {ep.path / 'audio'}: {total:.1f}s of video at {len(durations)} shots. "
              "Listen through; --redo-voice N re-reads one line, and editing a line's narration re-reads just it.")
    else:
        print("\nVideo: waits for narration (set up ElevenLabs, or use --fake-voice for silent placeholder audio).")
        return 0

    timings = narration_timings(ep, result.script)
    clips = plan_clips(ep, result.script, {n: shot for n, (_, shot) in timings.items()}, set(args.redo),
                       series.config.default_motion)
    if any(c.reason for c in clips):
        print("\nRendering clips...")
        make_clips(ep, clips)
    print("\nAssembling final.mp4...")
    built = assemble(series, ep, result.script, clips, timings, root)
    if not built.rebuilt:
        print("  nothing changed since the last build")
    if built.music is None:
        print(f"  Note: no music found in {series.path / 'music'} or {root / 'assets' / 'music'}; narration only.")
    if built.font is None:
        print(f"  Note: no font in {root / 'assets' / 'fonts'}; captions use Pillow's built-in font.")
    if warning := length_warning(built.seconds):
        print(f"  {warning}")
    print(f"\nDone: {built.final} ({built.seconds:.1f}s"
          + (f", music: {built.music.name}" if built.music else "") + ")")
    return 0


def main() -> int:
    try:
        return run()
    except PipelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
