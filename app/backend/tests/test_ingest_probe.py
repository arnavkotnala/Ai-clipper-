"""ai-clipper: link preview (probe), quality choice, duplicate detection, cancel cleanup."""

from __future__ import annotations

import types

import pytest

from sharkyclips import governor, ingest
from sharkyclips.errors import SharkyClipsError

INFO = {
    "id": "abc123",
    "extractor_key": "Youtube",
    "title": "Ranked clutch session",
    "uploader": "Somebody",
    "duration": 600,
    "thumbnail": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
    "webpage_url": "https://www.youtube.com/watch?v=abc123",
    "formats": [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "filesize": 10_000_000},
        {"format_id": "134", "vcodec": "avc1", "acodec": "none", "height": 360, "tbr": 300, "filesize": 20_000_000},
        {"format_id": "136", "vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 1500, "filesize": 90_000_000},
        {"format_id": "137", "vcodec": "avc1", "acodec": "none", "height": 1080, "tbr": 3000},  # size from tbr
        {"format_id": "298", "vcodec": "avc1", "acodec": "none", "height": 720, "tbr": 2500, "fps": 60,
         "filesize": 150_000_000},
        {"format_id": "sb0", "vcodec": "none", "acodec": "none"},  # storyboard: ignored
    ],
}


class DownloadCancelled(Exception):
    pass


def fake_ytdl(info: dict, on_download=None):
    class YDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return dict(info)

        def download(self, urls):
            if on_download:
                on_download(self.opts)

    return types.SimpleNamespace(YoutubeDL=YDL, utils=types.SimpleNamespace(DownloadCancelled=DownloadCancelled))


@pytest.fixture
def ydl(monkeypatch):
    holder = {"mod": fake_ytdl(INFO)}
    monkeypatch.setattr(ingest, "require_module", lambda *a: holder["mod"])
    monkeypatch.setattr(ingest, "require_tool", lambda name: "/usr/bin/ffmpeg")
    return holder


def test_probe_lists_qualities_with_sizes(ydl):
    out = ingest.probe("https://www.youtube.com/watch?v=abc123")
    assert out["title"] == "Ranked clutch session"
    assert out["thumbnail"].startswith("https://")
    assert [q["height"] for q in out["qualities"]] == [1080, 720, 360]
    q = {q["height"]: q for q in out["qualities"]}
    assert q[720]["size_bytes"] == 160_000_000  # best 720p (highest bitrate) + audio
    assert q[1080]["size_bytes"] == int(3000 * 1000 / 8 * 600 + 10_000_000)  # estimated from bitrate
    assert out["default_height"] == 1080
    assert out["existing_project"] is None


def test_probe_rejects_non_links(ydl):
    with pytest.raises(SharkyClipsError):
        ingest.probe("not a link")


def test_probe_refuses_live_streams(ydl):
    ydl["mod"] = fake_ytdl({**INFO, "is_live": True})
    with pytest.raises(SharkyClipsError, match="live"):
        ingest.probe("https://www.youtube.com/watch?v=abc123")


def test_probe_reports_an_existing_project(ydl):
    project = ingest._project_for_info(INFO, INFO["webpage_url"])
    project.dir.mkdir(parents=True, exist_ok=True)
    (project.dir / "source.mp4").write_bytes(b"x")
    project.save_meta({"id": project.id})
    try:
        assert ingest.probe("https://www.youtube.com/watch?v=abc123")["existing_project"] == project.id
    finally:
        import shutil

        shutil.rmtree(project.dir)


def test_chosen_quality_reaches_yt_dlp(ydl):
    seen = {}

    def download(opts):
        seen["format"] = opts["format"]
        raise RuntimeError("stop here")  # the format string is all this test needs

    ydl["mod"] = fake_ytdl(INFO, download)
    with pytest.raises(SharkyClipsError):
        ingest._ingest_url("https://www.youtube.com/watch?v=abc123", max_height=720)
    assert "height<=720" in seen["format"]


def test_cancel_stops_the_download_and_removes_partial_files(ydl):
    project = ingest._project_for_info(INFO, INFO["webpage_url"])
    job = types.SimpleNamespace(cancel_requested=False)

    def download(opts):
        (project.dir / "source.f136.mp4.part").write_bytes(b"partial")
        hook = opts["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
        job.cancel_requested = True  # the user presses Cancel mid-download
        hook({"status": "downloading", "downloaded_bytes": 20, "total_bytes": 100})
        pytest.fail("the hook must stop the download")

    ydl["mod"] = fake_ytdl(INFO, download)
    governor.set_current_job(job)
    try:
        with pytest.raises(governor.JobCancelled):
            ingest._ingest_url("https://www.youtube.com/watch?v=abc123")
    finally:
        governor.set_current_job(None)
    assert not project.dir.exists()
