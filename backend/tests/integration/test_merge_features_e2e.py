"""End-to-end coverage for the three-way-merge additions: glossary-protected translation
flowing through the real audio pipeline, and the new direct-subtitle-translation job type
(real NLLB translation, no ASR/audio involved at all).
"""
import json

import pytest

from app.config import get_settings
from app.db.models import AudioStream, Job, MediaFile, Output, QcReport
from app.db.session import init_db, session_scope
from app.hardware import detect_hardware
from app.jobs.queue import init_gpu_slots
from app.jobs.worker import JobRunner
from app.pipeline.stage1_inspection.inspector import inspect_media
from app.pipeline.stage11_qc_output.formatters.srt import parse_srt
from tests.fixtures.synthetic_media import build_multi_stream_fixture


def _prepare_runner(monkeypatch, whisper_model_size="tiny"):
    monkeypatch.setenv("SUBTITLE_WHISPER_MODEL_SIZE", whisper_model_size)
    monkeypatch.setenv("SUBTITLE_NLLB_MODEL_NAME", "facebook/nllb-200-distilled-600M")
    init_db()
    settings = get_settings()
    hardware_profile = detect_hardware()
    with session_scope() as session:
        init_gpu_slots(session, max(hardware_profile.gpu_count, 1) * settings.max_concurrent_gpu_jobs)
    return JobRunner(settings, hardware_profile)


@pytest.mark.integration
def test_audio_pipeline_with_glossary_protects_a_name(ffmpeg_fixture_dir, monkeypatch):
    runner = _prepare_runner(monkeypatch, whisper_model_size="base")

    # A short invented proper noun spoken by espeak-ng's synthetic voice turned out to be
    # too unreliable for even 'base' Whisper to transcribe consistently during development
    # (dropped entirely, or misspelled) — that's a TTS/ASR-fidelity limitation of this test
    # environment, not something glossary protection can fix (it can only protect a name
    # that was actually transcribed). Protecting an ordinary, clearly-articulated English
    # word instead gives a reliable, deterministic way to prove the same round trip through
    # the real pipeline: it should reach the target-language subtitle file UNTRANSLATED,
    # since it went through translation as an opaque placeholder.
    media_path = build_multi_stream_fixture(
        ffmpeg_fixture_dir, speech_text="This is a wonderful day and everyone is happy.",
    )
    inspection = inspect_media(media_path)
    speech_stream_info = inspection.audio_streams[1]

    with session_scope() as session:
        media = MediaFile(original_path=str(media_path), filename=media_path.name,
                           container_format=inspection.container_format, size_bytes=inspection.size_bytes,
                           file_hash=inspection.file_hash, raw_ffprobe_json={})
        session.add(media)
        session.flush()
        stream = AudioStream(media_file_id=media.id, stream_index=speech_stream_info.stream_index,
                              codec=speech_stream_info.codec, channels=speech_stream_info.channels,
                              sample_rate=speech_stream_info.sample_rate, duration_s=speech_stream_info.duration_s,
                              stream_hash=speech_stream_info.stream_hash, selected_by="auto")
        session.add(stream)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=stream.id, source_language="en",
                   target_languages=["es"], output_formats=["srt"],
                   glossary_entities=[{"canonical": "Wonderful", "aliases": ["wonderful"]}])
        session.add(job)
        session.flush()
        job_id = job.id

    with session_scope() as session:
        runner.run(session, job_id)

    with session_scope() as session:
        final_job = session.get(Job, job_id)
        assert final_job.status != "failed", final_job.error_message

        outputs = session.query(Output).filter_by(job_id=job_id).all()
        assert len(outputs) == 1
        content = open(outputs[0].file_path, encoding="utf-8").read()
        # The protected word travels through translation as an opaque placeholder and is
        # restored verbatim afterward — it should appear in the Spanish output exactly as
        # "Wonderful" (English), not translated to "maravilloso"/"maravillosa".
        assert "wonderful" in content.lower()
        assert "maravillos" not in content.lower()

        qc_stages = {row.stage for row in session.query(QcReport).filter_by(job_id=job_id).all()}
        assert "entity_qc" in qc_stages  # only runs when a job supplied a glossary


@pytest.mark.integration
def test_direct_translation_job_end_to_end(tmp_path, monkeypatch):
    runner = _prepare_runner(monkeypatch)

    srt_path = tmp_path / "input.srt"
    # A real pause (>= the default chunk_pause_gap_s) between the two cues keeps them in
    # separate SourceChunks -- see test_direct_translation_groups_close_cues_for_context
    # below for the case where they're close enough to merge and get re-cut/re-timed.
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello, how are you today?\n\n"
        "2\n00:00:03,000 --> 00:00:05,000\nI am doing well, thank you.\n",
        encoding="utf-8",
    )

    with session_scope() as session:
        job = Job(job_type="direct_translation", input_srt_path=str(srt_path), input_filename="input.srt",
                   source_language="en", target_languages=["es"], output_formats=["srt", "vtt"])
        session.add(job)
        session.flush()
        job_id = job.id

    with session_scope() as session:
        runner.run(session, job_id)

    with session_scope() as session:
        final_job = session.get(Job, job_id)
        assert final_job.status != "failed", final_job.error_message
        assert final_job.progress_pct == 100.0

        outputs = session.query(Output).filter_by(job_id=job_id).all()
        assert {o.format for o in outputs} == {"srt", "vtt"}

        for output in outputs:
            content = open(output.file_path, encoding="utf-8").read()
            assert content.strip() != ""
            sidecar = json.loads(open(output.file_path + ".provenance.json", encoding="utf-8").read())
            assert sidecar["job_type"] == "direct_translation"
            assert "not verified against audio" in sidecar["provenance_note"]

        srt_output = next(o for o in outputs if o.format == "srt")
        content = open(srt_output.file_path, encoding="utf-8").read()
        # A real pause between the two input cues keeps them un-grouped, so each one's
        # timing is still taken verbatim from the input file, unchanged.
        assert "00:00:00,000 --> 00:00:02,000" in content
        assert "00:00:03,000 --> 00:00:05,000" in content


@pytest.mark.integration
def test_direct_translation_groups_close_cues_for_context(tmp_path, monkeypatch):
    """Real motivation: a 5-episode comparison found translating each input cue in
    isolation (the old 1:1 behavior) scored consistently lower chrF/word-F1 than the
    audio pipeline's pause-gap-grouped chunks on the exact same underlying text, in every
    episode -- NLLB translates better with surrounding-sentence context. Two cues with no
    real pause between them (unlike the previous test) must now be grouped into one
    translation call and re-cut/re-timed by Stage 9/10, rather than translated and timed
    independently."""
    runner = _prepare_runner(monkeypatch)

    srt_path = tmp_path / "input.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nHello, how are you today?\n\n"
        "2\n00:00:02,200 --> 00:00:04,000\nI am doing well, thank you.\n",
        encoding="utf-8",
    )

    with session_scope() as session:
        job = Job(job_type="direct_translation", input_srt_path=str(srt_path), input_filename="input.srt",
                   source_language="en", target_languages=["es"], output_formats=["srt"])
        session.add(job)
        session.flush()
        job_id = job.id

    with session_scope() as session:
        runner.run(session, job_id)

    with session_scope() as session:
        final_job = session.get(Job, job_id)
        assert final_job.status != "failed", final_job.error_message

        srt_output = session.query(Output).filter_by(job_id=job_id, format="srt").one()
        cues = parse_srt(open(srt_output.file_path, encoding="utf-8").read())
        assert len(cues) >= 1
        # The union span of the merged group is preserved even though the internal split
        # point is no longer guaranteed to fall exactly at the original 2.0s boundary.
        assert cues[0].start == pytest.approx(0.0, abs=0.01)
        assert cues[-1].end == pytest.approx(4.0, abs=0.01)
