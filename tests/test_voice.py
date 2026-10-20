import json
import shutil
from dataclasses import dataclass, field

import pytest

import make_episode
from pipeline.config import load_settings
from pipeline.episode import Episode
from pipeline.errors import PipelineError
from pipeline.schema import validate_script
from pipeline.series import load_series
from pipeline.voice import (FakeVoiceClient, Speech, _explain, audio_seconds, make_voice, make_voice_client,
                            plan_voice, shot_durations, shot_seconds, word_timings, words_from_alignment)

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


@dataclass
class StubVoice:
    """Returns fake bytes; paired with a stub probe so these tests need no ffmpeg."""

    model: str = "stub-model"
    voice_id: str = "stub-voice"
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def speak(self, text, previous_text, next_text):
        self.calls.append((text, previous_text, next_text))
        return Speech(b"mp3:" + text.encode(), [(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())])


def probe(path):
    return len(path.read_bytes()) / 20  # deterministic "duration" from the stub bytes


@pytest.fixture
def episode(project, good_script):
    series = load_series("test-series", project, 1)
    ep = Episode.of(series, 1)
    ep.save_script(good_script)
    return series, ep


def _script(series, ep):
    return validate_script(ep.load_script_data(), series).script


def _read(ep, script, client, redo=(), settings=None):
    plans = plan_voice(ep, script, client.voice_id, client.model, set(redo), settings)
    make_voice(client, ep, plans, probe=probe)
    return plans


def test_shot_seconds_adds_pause_with_a_minimum():
    assert shot_seconds(2.0) == 2.3
    assert shot_seconds(0.4) == 1.5
    assert shot_seconds(2.0, pause=0.6) == 2.6


def test_words_are_grouped_from_character_timings():
    text = "Don't  drink."
    starts = [i * 0.1 for i in range(len(text))]
    ends = [s + 0.1 for s in starts]
    assert words_from_alignment(list(text), starts, ends) == [("Don't", 0.0, 0.5), ("drink.", 0.7, 1.3)]


def test_voice_settings_reread_only_when_set(episode):
    series, ep = episode
    script = _script(series, ep)
    client = StubVoice()
    _read(ep, script, client)
    client.calls.clear()
    _read(ep, script, client, settings=None)
    assert client.calls == []  # no settings: earlier lines stay valid
    _read(ep, script, client, settings={"stability": 0.4})
    assert len(client.calls) == len(script.shots)


def test_lines_without_word_timings_are_reread(episode):
    series, ep = episode
    script = _script(series, ep)
    _read(ep, script, StubVoice())
    path = ep.path / "audio" / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest["02.mp3"]["words"]
    path.write_text(json.dumps(manifest))
    plans = plan_voice(ep, script, "stub-voice", "stub-model", set())
    assert [p.reason for p in plans if p.reason] == ["no word timings yet"]


def test_lines_are_cached_and_reread_only_when_they_change(episode):
    series, ep = episode
    script = _script(series, ep)
    client = StubVoice()
    _read(ep, script, client)
    assert len(client.calls) == len(script.shots)
    assert (ep.path / "audio" / "01.mp3").read_bytes() == b"mp3:" + script.shots[0].narration.encode()

    client.calls.clear()
    plans = _read(ep, script, client)
    assert client.calls == [] and not any(p.reason for p in plans)

    data = ep.load_script_data()
    data["shots"][3]["narration"] = "The sun climbs. Your lips crack."
    ep.save_script(data)
    script = _script(series, ep)
    _read(ep, script, client)
    assert [c[0] for c in client.calls] == ["The sun climbs. Your lips crack."]

    client.calls.clear()
    _read(ep, script, client, redo=[2])
    assert [c[0] for c in client.calls] == [script.shots[1].narration]


def test_changing_voice_rereads_everything(episode):
    series, ep = episode
    script = _script(series, ep)
    _read(ep, script, StubVoice())
    other = StubVoice(voice_id="another-voice")
    _read(ep, script, other)
    assert len(other.calls) == len(script.shots)


def test_neighbouring_lines_are_sent_for_flow(episode):
    series, ep = episode
    script = _script(series, ep)
    client = StubVoice()
    _read(ep, script, client)
    lines = [s.narration for s in script.shots]
    assert client.calls[0] == (lines[0], "", lines[1])
    assert client.calls[1] == (lines[1], lines[0], lines[2])
    assert client.calls[-1] == (lines[-1], lines[-2], "")


def test_manifest_records_durations_for_later_steps(episode):
    series, ep = episode
    script = _script(series, ep)
    _read(ep, script, StubVoice())
    manifest = json.loads((ep.path / "audio" / "manifest.json").read_text())
    first = manifest["01.mp3"]
    assert first["text"] == script.shots[0].narration
    assert first["characters"] == len(script.shots[0].narration)
    assert first["words"][0][0] == script.shots[0].narration.split()[0]
    durations = shot_durations(ep, script)
    assert list(durations) == [s.n for s in script.shots]
    assert durations[1] == shot_seconds(first["line_seconds"])
    assert shot_durations(ep, script, pause=1.0)[1] == shot_seconds(first["line_seconds"], 1.0)
    script.shots[0].pause_after = 0.8  # a shot's own pause wins over the series default
    assert shot_durations(ep, script, pause=1.0)[1] == shot_seconds(first["line_seconds"], 0.8)
    assert shot_durations(ep, script, pause=1.0)[2] == shot_seconds(manifest["02.mp3"]["line_seconds"], 1.0)
    script.shots[0].pause_after = None
    assert word_timings(ep, script)[1][0][0] == script.shots[0].narration.split()[0]

    data = ep.load_script_data()
    data["shots"][0]["narration"] = "A different first line."
    ep.save_script(data)
    with pytest.raises(PipelineError, match="shot 1 is missing or out of date"):
        shot_durations(ep, _script(series, ep))


def test_redo_voice_unknown_shot(episode):
    series, ep = episode
    with pytest.raises(PipelineError, match="no shot 99"):
        plan_voice(ep, _script(series, ep), "v", "m", {99})


def test_voice_settings_come_from_env_file(project):
    assert load_settings(project).voice_model == ""
    (project / ".env").write_text("ELEVENLABS_API_KEY=el-test\nVOICE_MODEL=eleven_multilingual_v2\n")
    settings = load_settings(project)
    assert settings.elevenlabs_api_key == "el-test" and settings.voice_model == "eleven_multilingual_v2"
    assert "el-test" not in repr(settings)


def test_real_client_lists_missing_setup(project):
    series = load_series("test-series", project, 1)
    with pytest.raises(PipelineError) as e:
        make_voice_client(False, series, project)
    assert "ELEVENLABS_API_KEY" in str(e.value) and "VOICE_MODEL" in str(e.value) and "voice_id" in str(e.value)


def test_api_errors_are_explained():
    class Err:
        def __init__(self, status, body):
            self.status_code, self.body = status, body
    assert "Check ELEVENLABS_API_KEY" in _explain(Err(401, {"detail": {"message": "bad key"}}), "v", "m")
    assert "voice 'v'" in _explain(Err(404, None), "v", "m")
    assert "error 500" in _explain(Err(500, {"detail": "boom"}), "v", "m")


# --- make_episode.py --------------------------------------------------------------

def test_cli_voice_needs_approval_and_skips_until_set_up(episode, project, capsys):
    series, ep = episode
    base = ["--series", "test-series", "--episode", "1", "--fake-images"]
    client = StubVoice()
    make_episode.run(base, root=project, voice_client=client)
    assert client.calls == []  # draft: nothing read

    make_episode.run(base + ["--approve"], root=project)
    capsys.readouterr()
    assert make_episode.run(base, root=project) == 0  # no voice set up: images still run
    out = capsys.readouterr().out
    assert "Narration: skipped until set up" in out and "ELEVENLABS_API_KEY" in out
    assert not (ep.path / "audio").exists()


def test_cli_dry_run_reads_nothing(episode, project, capsys):
    series, ep = episode
    ep.approve()
    client = StubVoice()
    assert make_episode.run(["--series", "test-series", "--episode", "1", "--dry-run"],
                            root=project, voice_client=client) == 0
    assert client.calls == []
    assert f"{len(_script(series, ep).shots)} lines to read" in capsys.readouterr().out


@pytest.fixture
def no_video(monkeypatch):
    """Skip clip rendering and assembly; tests/test_video.py covers them."""
    from pipeline.assemble import Assembly
    monkeypatch.setattr(make_episode, "make_clips", lambda ep, plans: None)
    monkeypatch.setattr(make_episode, "assemble",
                        lambda series, ep, *a: Assembly(ep.path / "final.mp4", 30.0, None, None, rebuilt=False))


@needs_ffmpeg
def test_cli_fake_voice_end_to_end(episode, project, capsys, no_video):
    series, ep = episode
    ep.approve()
    base = ["--series", "test-series", "--episode", "1", "--fake-images", "--fake-voice"]
    assert make_episode.run(base, root=project) == 0
    script = _script(series, ep)
    durations = shot_durations(ep, script)
    assert len(durations) == len(script.shots) and all(d >= 1.5 for d in durations.values())
    words = len(script.shots[0].narration.split())
    assert audio_seconds(ep.path / "audio" / "01.mp3") == pytest.approx(words / 2.7, abs=0.15)
    assert "s of video at" in capsys.readouterr().out

    assert make_episode.run(base + ["--redo-voice", "3"], root=project) == 0
    assert "03.mp3  (--redo-voice)" in capsys.readouterr().out


@needs_ffmpeg
def test_fake_voice_makes_real_mp3(tmp_path):
    speech = FakeVoiceClient().speak("one two three four five six", "", "")
    path = tmp_path / "a.mp3"
    path.write_bytes(speech.audio)
    assert audio_seconds(path) == pytest.approx(6 / 2.7, abs=0.15)
    assert [w for w, _, _ in speech.words] == "one two three four five six".split()
    assert speech.words[-1][2] == pytest.approx(6 / 2.7, abs=0.01)
