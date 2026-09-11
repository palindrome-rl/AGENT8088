import os
import shutil
from pathlib import Path
import pytest
from agent8088.browser_session import (
    get_session_user_data_dir,
    cleanup_browser_session,
    is_browser_session_active,
    build_session_browser_profile,
    get_or_create_session_browser,
)


def test_session_user_data_dir_is_consistent_and_scoped_to_pid():
    dir1 = get_session_user_data_dir()
    dir2 = get_session_user_data_dir()
    assert dir1 == dir2
    assert f"agent8088-browser-session-{os.getpid()}" in str(dir1)
    assert dir1.exists()


def test_cleanup_browser_session_removes_dir():
    session_dir = get_session_user_data_dir()
    assert session_dir.exists()
    cleanup_browser_session()
    assert not session_dir.exists()
    assert not is_browser_session_active()


def test_build_session_browser_profile_sets_keep_alive_and_user_data_dir():
    profile = build_session_browser_profile(proxy_url="http://127.0.0.1:8888")
    assert profile.keep_alive is True
    session_dir = get_session_user_data_dir().resolve()
    assert Path(profile.user_data_dir).resolve() == session_dir
    assert is_browser_session_active()
    cleanup_browser_session()


def test_get_or_create_session_browser_reuses_same_session():
    profile = build_session_browser_profile(proxy_url="http://127.0.0.1:8888")
    s1 = get_or_create_session_browser(profile)
    s2 = get_or_create_session_browser(profile)
    assert s1 is s2
    cleanup_browser_session()
