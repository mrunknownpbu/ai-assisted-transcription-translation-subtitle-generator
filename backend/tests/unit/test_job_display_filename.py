import pytest

from app.db.models import AudioStream, Job, MediaFile
from app.db.session import init_db, session_scope


@pytest.mark.unit
def test_display_filename_falls_back_to_the_related_media_files_filename():
    """audio_pipeline jobs never set input_filename (there's no separately-uploaded file --
    the name lives on the MediaFile) -- this is what let a job queue/detail view show
    nothing but a bare job id for every audio_pipeline job."""
    init_db()
    with session_scope() as session:
        media = MediaFile(original_path="/x/My Show S01E01.mkv", filename="My Show S01E01.mkv",
                           container_format="matroska", size_bytes=1, file_hash="h", raw_ffprobe_json={})
        session.add(media)
        session.flush()
        stream = AudioStream(media_file_id=media.id, stream_index=0, codec="aac", channels=2,
                              sample_rate=16000, duration_s=1.0, stream_hash="h2")
        session.add(stream)
        session.flush()
        job = Job(media_file_id=media.id, audio_stream_id=stream.id, target_languages=["es"])
        session.add(job)
        session.commit()
        job_id = job.id

    with session_scope() as session:
        fetched = session.get(Job, job_id)
        assert fetched.display_filename == "My Show S01E01.mkv"


@pytest.mark.unit
def test_display_filename_prefers_input_filename_when_set():
    init_db()
    with session_scope() as session:
        job = Job(job_type="direct_translation", input_srt_path="/x/in.srt", input_filename="in.srt",
                   target_languages=["es"])
        session.add(job)
        session.commit()
        job_id = job.id

    with session_scope() as session:
        assert session.get(Job, job_id).display_filename == "in.srt"


@pytest.mark.unit
def test_display_filename_is_none_when_neither_is_available():
    init_db()
    with session_scope() as session:
        job = Job(job_type="direct_translation", input_srt_path="/x/in.srt", target_languages=["es"])
        session.add(job)
        session.commit()
        job_id = job.id

    with session_scope() as session:
        assert session.get(Job, job_id).display_filename is None
