"""ai-clipper: confidence / engagement scoring and the vision check (fake Ollama transport)."""

import json
import subprocess

import pytest

from sharkyclips import scoring, verify


def cand(**kw):
    c = {"id": "c01", "rank": 1, "start": 10.0, "end": 40.0, "duration": 30.0, "peak": 25.0,
         "hype_score": 80, "reasons": [], "signal_scores": {}}
    c.update(kw)
    return c


def test_more_independent_evidence_means_more_confidence():
    one = scoring.score(cand(signal_scores={"loudness": 0.9}))
    three = scoring.score(cand(signal_scores={"loudness": 0.9, "laughter": 0.8, "motion": 0.7}))
    assert three["confidence"] > one["confidence"]


def test_same_family_signals_do_not_double_count():
    a = scoring.score(cand(signal_scores={"keywords": 0.9}))
    b = scoring.score(cand(signal_scores={"keywords": 0.9, "speech": 0.9}))  # both "speech" family
    assert a["confidence"] == b["confidence"]


def test_hud_event_is_strong_evidence():
    no_hud = scoring.score(cand(signal_scores={"loudness": 0.6}))
    hud = scoring.score(cand(signal_scores={"loudness": 0.6, "game": 0.9},
                             reasons=[{"code": "df_kill", "t": 25, "strength": 0.9}]))
    assert hud["confidence"] - no_hud["confidence"] >= 25


def test_vision_verdict_moves_confidence_both_ways_and_unclear_does_not():
    base = scoring.score(cand(signal_scores={"loudness": 0.9, "motion": 0.8}))["confidence"]
    v = {"status": "ok", "confidence": 0.9, "interest": 0.5}
    yes = scoring.score(cand(signal_scores={"loudness": 0.9, "motion": 0.8}, verification={**v, "happened": "yes"}))
    no = scoring.score(cand(signal_scores={"loudness": 0.9, "motion": 0.8}, verification={**v, "happened": "no"}))
    unclear = scoring.score(cand(signal_scores={"loudness": 0.9, "motion": 0.8},
                                 verification={**v, "happened": "unclear"}))
    assert no["confidence"] < base < yes["confidence"]
    assert unclear["confidence"] == base
    assert no["likely_false_positive"] and not yes["likely_false_positive"]


def test_failed_verification_is_ignored_not_trusted():
    base = scoring.score(cand(signal_scores={"loudness": 0.9}))["confidence"]
    err = scoring.score(cand(signal_scores={"loudness": 0.9}, verification={"status": "error", "happened": "yes",
                                                                            "confidence": 1.0}))
    assert err["confidence"] == base


def test_categories_from_evidence():
    assert scoring.signal_category(cand(reasons=[{"code": "laughter", "t": 1}])) == "funny"
    assert scoring.signal_category(cand(reasons=[{"code": "lol_triple_kill", "t": 1}])) == "multi_kill"
    assert scoring.signal_category(cand(signal_scores={"motion": 0.8, "loudness": 0.7})) == "intense_fight"
    assert scoring.signal_category(cand()) == "other"


def test_phases_extend_end_to_keep_the_payoff():
    c = cand(start=10.0, end=30.0, peak=25.0, reasons=[{"code": "laughter", "t": 29.0, "strength": 0.9}])
    ph = scoring.phases(c, post_roll=3.0, clip_max=60.0, duration=600.0)
    assert ph["climax"] == 25.0 and ph["payoff"] == 29.0
    assert c["end"] == 32.0 and ph["end_extended_for_payoff"]


def test_phases_never_exceed_clip_max():
    c = cand(start=0.0, end=59.0, peak=50.0, reasons=[{"code": "laughter", "t": 59.0, "strength": 0.9}])
    scoring.phases(c, post_roll=3.0, clip_max=60.0, duration=600.0)
    assert c["end"] == 59.0


def test_apply_orders_false_positives_last():
    v_no = {"status": "ok", "happened": "no", "confidence": 0.9, "interest": 0.1}
    res = {"duration": 600, "candidates": [cand(id="c01", hype_score=100, verification=v_no),
                                           cand(id="c02", hype_score=40, start=100, end=130, peak=115)]}
    scoring.apply(res)
    assert res["order"] == ["c02", "c01"]


def test_rank_weights_are_configurable(monkeypatch):
    from sharkyclips import config

    config.update(rank_weights={"confidence": 1, "engagement": 0})
    try:
        c = scoring.score(cand(signal_scores={"loudness": 0.9}))
        assert c["rank_score"] == c["confidence"]
    finally:
        config.update(rank_weights=None)


# ---------------------------------------------------------------- vision check


def test_frame_times_are_symmetric_around_peak():
    t = verify.frame_times(0, 60, 30, 6)
    assert len(t) == 6 and t[0] == 24.0 and t[-1] == 36.0
    # a peak near the clip start stays inside the clip
    t2 = verify.frame_times(10, 40, 11, 4)
    assert t2[0] == 10.0 and max(t2) <= 17.0


class FakeResp:
    def __init__(self, payload, ok=True):
        self.ok, self._p, self.text = ok, payload, json.dumps(payload)

    def json(self):
        return self._p


class FakeHttp:
    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.bodies.append(json)
        return FakeResp({"message": {"content": self.answers.pop(0)}, "eval_count": 50})


GOOD = {"happened": "yes", "event": "multi_kill", "confidence": 0.8, "interest": 0.9,
        "evidence": "Frames 3-5 show three kill-feed entries.", "evidence_frames": [3, 4, 5],
        "action_starts_frame": 2, "action_ends_frame": 5}


def test_ask_sends_images_schema_and_no_detector_label():
    http = FakeHttp([json.dumps(GOOD)])
    out = verify.ask("m", [b"jpg1", b"jpg2", b"jpg3", b"jpg4", b"jpg5", b"jpg6"], "prompt", 6, http=http)
    body = http.bodies[0]
    assert body["format"] == verify.SCHEMA and body["options"]["temperature"] == 0
    assert len(body["messages"][1]["images"]) == 6
    assert out["event"] == "multi_kill"


def test_ask_retries_once_then_fails_loudly():
    http = FakeHttp(["not json", json.dumps({**GOOD, "action_ends_frame": 99})])
    with pytest.raises(verify.SharkyClipsError):
        verify.ask("m", [b"x"] * 6, "p", 6, http=http)
    assert len(http.bodies) == 2


def test_to_verification_maps_frames_to_times():
    times = [24.0, 26.4, 28.8, 31.2, 33.6, 36.0]
    v = verify.to_verification(GOOD, times, "m", 1.0)
    assert v["suggested_start"] == 26.4 and v["suggested_end"] == 33.6
    assert v["evidence_times"] == [28.8, 31.2, 33.6]


def test_prompt_does_not_leak_detector_category():
    p = verify.build_prompt([24.0, 30.0, 36.0], 30.0, "no way", "Valorant")
    assert "multi" not in p.lower() and "laughter" not in p.lower()
    assert "+0.0s" in p and "Valorant" in p


@pytest.mark.requires_ffmpeg
def test_verify_project_end_to_end_with_fake_model(tmp_path, monkeypatch):
    from sharkyclips import llm, memory
    from sharkyclips.project import Project

    pdir = tmp_path / "p1"
    pdir.mkdir()
    src = pdir / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:r=10:d=60", "-pix_fmt", "yuv420p", str(src)], check=True)
    proj = Project.__new__(Project)
    monkeypatch.setattr(Project, "dir", property(lambda self: pdir), raising=False)
    monkeypatch.setattr(Project, "source_path", property(lambda self: src), raising=False)
    monkeypatch.setattr(Project, "transcript_path", property(lambda self: pdir / "none.json"), raising=False)
    monkeypatch.setattr(Project, "load_meta", lambda self: {"game": "Test"}, raising=False)
    res = {"mode": "gaming", "duration": 60, "candidates": [
        cand(id="c01", start=5, end=30, peak=15, signal_scores={"loudness": 0.9}),
        cand(id="c02", start=35, end=58, peak=45, hype_score=30, signal_scores={"loudness": 0.6})]}
    scoring.apply(res)
    (pdir / "highlights.json").write_text(json.dumps(res), encoding="utf-8")
    monkeypatch.setattr(llm, "ensure_server", lambda: "test")
    unloaded = []
    monkeypatch.setattr(llm, "unload", lambda name: unloaded.append(name))
    monkeypatch.setattr(memory, "release_current", lambda: None)
    no = {**GOOD, "happened": "no", "event": "none", "confidence": 0.9, "interest": 0.1}
    http = FakeHttp([json.dumps(GOOD), json.dumps(no)])
    out = verify.verify_project(proj, model="fake-vl", frames=6, http=http)
    saved = json.loads((pdir / "highlights.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in saved["candidates"]}
    assert out["checked"] == 2 and out["errors"] == 0
    assert by_id["c01"]["verification"]["happened"] == "yes" and by_id["c01"]["category"] == "multi_kill"
    assert by_id["c02"]["likely_false_positive"] is True
    assert saved["order"][-1] == "c02"
    assert all(len(b["messages"][1]["images"]) == 6 for b in http.bodies)
    assert unloaded == ["fake-vl"]


def test_minus_one_means_no_action_and_no_boundaries():
    ans = {**GOOD, "happened": "no", "action_starts_frame": -1, "action_ends_frame": -1}
    assert verify.validate(ans, 6) == []
    v = verify.to_verification(ans, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "m", 1.0)
    assert v["suggested_start"] is None and v["suggested_end"] is None


def test_out_of_range_frames_still_rejected():
    assert verify.validate({**GOOD, "action_starts_frame": 0}, 6)
    assert verify.validate({**GOOD, "action_ends_frame": 7}, 6)


def test_answer_in_thinking_field_is_accepted():
    class ThinkHttp(FakeHttp):
        def post(self, url, json=None, timeout=None):  # noqa: A002
            self.bodies.append(json)
            return FakeResp({"message": {"content": "", "thinking": self.answers.pop(0)}})

    out = verify.ask("m", [b"x"] * 6, "p", 6, http=ThinkHttp([__import__("json").dumps(GOOD)]))
    assert out["event"] == "multi_kill"
