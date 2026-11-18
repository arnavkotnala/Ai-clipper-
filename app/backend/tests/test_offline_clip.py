"""ai-clipper: offline clipper (truth parsing, accuracy scoring, network watch, refusals)."""

from __future__ import annotations

import socket
import time

import pytest

from sharkyclips import offline_clip as oc
from sharkyclips.errors import SharkyClipsError


def test_parse_truth_formats_and_comments():
    ev = oc.parse_truth("""# my labels
    01:12-01:25, multi_kill, triple kill
    1:03:40-1:03:52, funny
    00:05.5-00:09
    """)
    assert [(e.start, e.end, e.category) for e in ev] == [(72, 85, "multi_kill"), (3820, 3832, "funny"), (5.5, 9, "")]
    assert ev[0].note == "triple kill"


@pytest.mark.parametrize("bad", ["01:20-01:10, x", "hello", "1:2:3:4-1:2:3:5"])
def test_parse_truth_rejects_bad_lines(bad):
    with pytest.raises((SharkyClipsError, ValueError)):
        oc.parse_truth(bad)


def det(start, end, peak=None, i="c"):
    return {"id": i, "start": start, "end": end, "peak": peak if peak is not None else (start + end) / 2}


def test_evaluate_counts_and_one_to_one_matching():
    truth = [oc.TrueEvent(20, 26), oc.TrueEvent(70, 76), oc.TrueEvent(200, 206)]
    dets = [
        det(15, 40, peak=22),   # contains event 1 (mid 23), error 1
        det(18, 45, peak=25),   # also contains event 1, which is already matched -> false positive
        det(60, 90, peak=80),   # event 2 (mid 73), error 7
        det(300, 330),          # nothing
    ]
    e = oc.evaluate(dets, truth)
    assert (e["correct"], e["false_positives"], e["missed"]) == (2, 2, 1)
    assert e["precision"] == 0.5 and e["recall"] == round(2 / 3, 3)
    assert e["mean_timing_error_s"] == 4.0 and e["max_timing_error_s"] == 7.0
    assert e["whole_event_inside"] == 2
    assert e["missed_events"] == ["3:20-3:26"]


def test_evaluate_with_nothing_detected():
    e = oc.evaluate([], [oc.TrueEvent(1, 2)])
    assert e["precision"] == 0 and e["recall"] == 0 and e["missed"] == 1


def test_monitor_reports_non_local_connections_only():
    """A loopback connection is fine; the watcher must not list it (no real internet needed)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    mon = oc.Monitor(interval=0.1)
    mon.start()
    try:
        with mon.stage("x"):
            time.sleep(0.5)
    finally:
        mon.stop()
        cli.close()
        srv.close()
    assert mon.connections == {}
    s = mon.stages[0].to_dict()
    assert s["stage"] == "x" and s["peak_ram_mib"] > 0


def test_monitor_flags_a_remote_address(monkeypatch):
    """Fake one remote connection in the process table: it must be reported with its stage."""
    import psutil

    class Conn:
        raddr = type("A", (), {"ip": "34.36.133.15", "port": 443})()
        status = "ESTABLISHED"

    real = psutil.Process.net_connections
    monkeypatch.setattr(psutil.Process, "net_connections", lambda self, kind="inet": [Conn()] if self.pid == mon.root.pid else real(self, kind))
    mon = oc.Monitor(interval=0.1)
    mon.start()
    try:
        with mon.stage("vision_check"):
            time.sleep(0.4)
    finally:
        mon.stop()
    assert mon.connections["34.36.133.15:443"]["stage"] == "vision_check"


def test_links_are_refused():
    with pytest.raises(SharkyClipsError, match="FILE"):
        oc.run("https://www.youtube.com/watch?v=x")


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(SharkyClipsError, match="not found"):
        oc.run(str(tmp_path / "nope.mp4"))


def test_baseline_weights_switch_motion_off_and_restore():
    from sharkyclips.highlights import pipeline

    original = pipeline.get_mode
    with oc._baseline_weights():
        assert pipeline.get_mode("gaming").weights["motion"] == 0.0
    assert pipeline.get_mode is original
    assert original("gaming").weights["motion"] > 0


def test_markdown_report_renders():
    r = {"video": "x.mp4", "started": "now", "seconds_total": 1.0,
         "media": {"duration": 60, "resolution": "1280x720", "fps": 30}, "offline_ok": True,
         "network_connections": [], "stages": [oc.StageStats("render", 1.0).to_dict()], "clips": [],
         "evaluation": {"ai_clipper": oc.evaluate([det(0, 30)], [oc.TrueEvent(10, 12)])}}
    md = oc.to_markdown(r)
    assert "no connection to any non-local address" in md and "| ai_clipper | 1 | 1 | 0 | 0 |" in md


