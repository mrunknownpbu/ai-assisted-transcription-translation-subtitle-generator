"""Full end-to-end pipeline integration test: real ffmpeg inspection, real ranking, real
faster-whisper ASR, real Silero VAD, real NLLB translation, real QC, real SRT/VTT/WebVTT
output — run through the actual JobRunner exactly as the API/worker would invoke it.
Confirms the whole system produces valid output files from a synthetic multi-stream media
file without ever modifying the source.
"""
import json

import pytest

from app.config import get_settings
from app.db.models import AudioStream, Job, MediaFile
from app.db.session import init_db, session_scope
from app.hardware import detect_hardware
from app.jobs.queue import init_gpu_slots
from app.jobs.worker import JobRunner
from app.pipeline.stage1_inspection.inspector import inspect_media
from tests.fixtures.synthetic_media import build_multi_stream_fixture


@pytest.mark.integration
def test_full_pipeline_produces_valid_multi_format_output_without_touching_source(ffmpeg_fixture_dir, monkeypatch):
    monkeypatch.setenv("SUBTITLE_WHISPER_MODEL_SIZE", "tiny")
    # Keep the smaller NLLB checkpoint for test speed — the 1.3B default is validated
    # separately in test_stage8_nllb_integration.py.
    monkeypatch.setenv("SUBTITLE_NLLB_MODEL_NAME", "facebook/nllb-200-distilled-600M")

    media_path = build_multi_stream_fixture(
        ffmpeg_fixture_dir, speech_text="This is a real end to end test of the subtitle platform.",
    )
    before_bytes = media_path.read_bytes()

    inspection = inspect_media(media_path)
    speech_stream_info = inspection.audio_streams[1]  # constructed order: [0] tone, [1] speech, [2] silence

    init_db()
    with session_scope() as session:
        media = MediaFile(
            original_path=str(media_path), filename=media_path.name, container_format=inspection.container_format,
            size_bytes=inspection.size_bytes, file_hash=inspection.file_hash, raw_ffprobe_json={},
        )
        session.add(media)
        session.flush()

        stream = AudioStream(
            media_file_id=media.id, stream_index=speech_stream_info.stream_index, codec=speech_stream_info.codec,
            channels=speech_stream_info.channels, sample_rate=speech_stream_info.sample_rate,
            duration_s=speech_stream_info.duration_s, stream_hash=speech_stream_info.stream_hash,
            selected_by="auto",
        )
        session.add(stream)
        session.flush()

        job = Job(media_file_id=media.id, audio_stream_id=stream.id, source_language="en",
                   target_languages=["es"], output_formats=["srt", "vtt", "webvtt"])
        session.add(job)
        session.flush()
        job_id = job.id

    settings = get_settings()
    hardware_profile = detect_hardware()
    runner = JobRunner(settings, hardware_profile)

    with session_scope() as session:
        init_gpu_slots(session, max(hardware_profile.gpu_count, 1) * settings.max_concurrent_gpu_jobs)

    with session_scope() as session:
        runner.run(session, job_id)

    with session_scope() as session:
        final_job = session.get(Job, job_id)
        assert final_job.status != "failed", final_job.error_message
        assert final_job.progress_pct == 100.0

        from app.db.models import Output, QcReport, Transcript
        outputs = session.query(Output).filter_by(job_id=job_id).all()
        assert {o.format for o in outputs} == {"srt", "vtt", "webvtt"}

        for output in outputs:
            from pathlib import Path
            path = Path(output.file_path)
            assert path.is_file()
            content = path.read_text()
            assert content.strip() != ""
            if output.format == "srt":
                assert "-->" in content
            else:
                assert content.startswith("WEBVTT")
            sidecar = Path(str(path) + ".provenance.json")
            assert sidecar.is_file()
            provenance = json.loads(sidecar.read_text())
            assert provenance["target_language"] == "es"
            assert provenance["asr_engine"]

        transcript_row = session.query(Transcript).filter_by(job_id=job_id).one()
        assert Path(transcript_row.raw_json_path).is_file()

        qc_rows = session.query(QcReport).filter_by(job_id=job_id).all()
        qc_stages = {row.stage for row in qc_rows}
        # translation_qc + stage11_qc (cue timing/reading-speed) + output_qc (one per
        # written .srt) — entity_qc is absent since this job supplied no glossary.
        assert qc_stages == {"translation_qc", "stage11_qc", "output_qc"}
        assert all(len(row.reasons_json) > 0 for row in qc_rows)

    assert media_path.read_bytes() == before_bytes  # source media never modified
