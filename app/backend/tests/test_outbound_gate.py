"""ai-clipper: with publishing off (the app's only mode), every upload/post/update path refuses."""

from __future__ import annotations

import pytest

from sharkyclips import outbound, phone, platforms, tiktok_browser, updater, youtube
from sharkyclips.errors import SharkyClipsError


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.delenv("AICLIPPER_PUBLISHING", raising=False)


@pytest.mark.parametrize("call", [
    youtube._http,  # OAuth, upload, analytics
    platforms._http,  # TikTok API, Instagram/Facebook Graph
    phone._http,  # Telegram
    lambda: tiktok_browser._Session().__enter__(),  # TikTok Studio browser posting
    tiktok_browser.login_start,
    updater.apply,  # git pull + pip install
])
def test_every_outbound_path_refuses(offline, call):
    with pytest.raises(SharkyClipsError, match="turned off in ai-clipper"):
        call()


def test_update_check_does_not_touch_the_network(offline, monkeypatch):
    monkeypatch.setattr(updater, "git", lambda *a, **k: pytest.fail("git must not run"))
    assert updater.status()["reason"] == "disabled"


def test_only_the_explicit_variable_enables_it(monkeypatch):
    for v in ("", "0", "true", "yes"):
        monkeypatch.setenv("AICLIPPER_PUBLISHING", v)
        assert not outbound.publishing_enabled()
    monkeypatch.setenv("AICLIPPER_PUBLISHING", "1")
    assert outbound.publishing_enabled()


def test_the_upstream_oauth_client_is_not_shipped():
    assert not youtube.SHARED_FILE.exists()
