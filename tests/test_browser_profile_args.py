"""The browsing session's security posture is decided entirely by the kwargs
_run_browser_agent hands to BrowserProfile, so those kwargs - and the Chromium
launch flags they compile down to - are what these tests assert.

This is deliberately a *static* test: no browser is launched, no LLM is called,
nothing touches the network. The bug that made it necessary (Chromium quietly
bypassing the SSRF proxy for loopback and link-local targets, and headless
never being set at all) shipped precisely because the only coverage of "does
the profile actually do what we think" was a live end-to-end test that could
not distinguish "the proxy blocked it" from "nothing answered".
"""
import sys

import pytest

from agent8088 import engine as A

pytest.importorskip("browser_use")


@pytest.fixture(autouse=True)
def _default_ssrf_posture(monkeypatch):
    """Pin the SSRF settings these tests reason about, instead of inheriting
    whatever config.txt the machine running them happens to have.

    _browser_profile_kwargs reads SSRF_ALLOW_HOSTS / SSRF_ALLOW_PRIVATE as
    module globals baked at import, so without this the security assertions
    below silently describe the developer's own config rather than the default
    posture - and six of them failed outright under the config.txt this repo
    ships. A security test whose verdict depends on ambient configuration
    cannot be trusted to catch a regression. Tests that specifically exercise
    an allowlist still monkeypatch these afterwards, which wins over this
    fixture.
    """
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", set(), raising=False)
    monkeypatch.setattr(A, "SSRF_ALLOW_PRIVATE", False, raising=False)
    # Same problem, different setting: browser_headless=0 is exactly what a
    # demo or debugging config sets, and it made the "a real window never
    # opens" assertions fail on the developer's own machine while passing in
    # CI. The tests that are *about* the visible-window opt-in set these
    # themselves after this fixture runs.
    monkeypatch.setattr(A, "BROWSER_HEADLESS", True, raising=False)
    monkeypatch.delenv("AGENT8088_BROWSER_HEADLESS", raising=False)


def _profile(tmp_path, **overrides):
    """Build a real BrowserProfile from production kwargs and return it.

    user_data_dir is supplied because get_args() asserts on it; the path has no
    "chrome" in it, so browser-use's profile-copying step is a no-op and
    nothing outside tmp_path is touched.
    """
    from browser_use import BrowserProfile

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")
    kwargs.update(overrides)
    return BrowserProfile(user_data_dir=str(tmp_path / "profile"), **kwargs)


def test_proxy_is_configured_with_the_loopback_bypass_removed():
    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert kwargs["proxy"].server == "http://127.0.0.1:45671"
    # Chromium bypasses the proxy for loopback/link-local hosts by default, so
    # 169.254.169.254 (cloud metadata) would never reach the SSRF filter.
    # "<-loopback>" subtracts that implicit rule.
    assert kwargs["proxy"].bypass == "<-loopback>"


def test_launch_args_carry_the_proxy_server_and_bypass_list(tmp_path):
    args = _profile(tmp_path).get_args()

    assert "--proxy-server=http://127.0.0.1:45671" in args
    assert "--proxy-bypass-list=<-loopback>" in args


def test_launch_args_are_headless(tmp_path):
    profile = _profile(tmp_path)

    assert profile.headless is True
    # browser-use spells headless as a launch flag, not just a Python kwarg;
    # this is what actually keeps a real window off the user's screen.
    assert any(a.startswith("--headless") for a in profile.get_args())
    assert "--start-maximized" not in profile.get_args()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only Chrome flag")
def test_launch_args_do_not_access_the_macos_login_keychain(tmp_path):
    assert "--use-mock-keychain" in _profile(tmp_path).get_args()


@pytest.mark.parametrize("host", [
    "127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5", "192.168.1.1",
    "172.16.0.1", "172.31.255.254", "100.64.0.1", "100.127.0.1", "0.0.0.0",
    "::1", "fd00::1", "fe80::1", "printer.local", "db.internal",
])
def test_private_and_loopback_hosts_are_prohibited_from_navigation(host):
    """The second, independent layer behind the proxy: browser-use's own
    security watchdog refuses these before Chromium issues any request, which
    is the only thing that covers loopback (Chromium keeps exempting it from
    the proxy even with "<-loopback>", because the proxy itself is on
    127.0.0.1)."""
    import fnmatch

    patterns = A._browser_profile_kwargs("http://127.0.0.1:45671")["prohibited_domains"]

    assert any(fnmatch.fnmatchcase(host, p) or host == p for p in patterns), \
        f"{host} is not covered by any prohibited_domains pattern"


@pytest.mark.parametrize("host", [
    "example.com", "en.wikipedia.org", "fcbarcelona.com", "fda.gov",
    "100.200.30.40", "172.32.0.1", "11.0.0.1", "localhost.example.com",
])
def test_ordinary_public_hosts_are_not_prohibited(host):
    import fnmatch

    patterns = A._browser_profile_kwargs("http://127.0.0.1:45671")["prohibited_domains"]

    assert not any(fnmatch.fnmatchcase(host, p) or host == p for p in patterns), \
        f"{host} is wrongly matched by a prohibited_domains pattern"


def test_prohibited_domains_stays_below_the_pattern_matching_threshold():
    """browser-use converts a list of >= 100 domains to a set and silently
    stops doing pattern matching, which would turn every glob above into a
    no-op (browser_use/browser/profile.py, DOMAIN_OPTIMIZATION_THRESHOLD)."""
    from browser_use.browser.profile import DOMAIN_OPTIMIZATION_THRESHOLD

    patterns = A._browser_profile_kwargs("http://127.0.0.1:45671")["prohibited_domains"]

    assert isinstance(patterns, list)
    assert len(patterns) < DOMAIN_OPTIMIZATION_THRESHOLD


def test_the_watchdog_actually_refuses_a_prohibited_url(tmp_path):
    """Assert against browser-use's real matcher rather than reimplementing
    it, so this test fails if the upstream semantics ever change."""
    import logging

    from browser_use.browser.watchdogs.security_watchdog import SecurityWatchdog

    profile = _profile(tmp_path)
    fake_session = type("_S", (), {
        "browser_profile": profile,
        "logger": logging.getLogger("test_browser_profile_args"),
    })()
    watchdog = SecurityWatchdog.model_construct(browser_session=fake_session)

    assert watchdog._is_url_allowed("https://example.com/") is True
    assert watchdog._is_url_allowed("http://169.254.169.254/latest/meta-data/") is False
    assert watchdog._is_url_allowed("http://127.0.0.1:8123/x") is False
    assert watchdog._is_url_allowed("http://localhost:8123/x") is False
    assert watchdog._is_url_allowed("http://10.0.0.5/x") is False
    # An obfuscated IP literal (2130706433 == 127.0.0.1) that no hostname
    # pattern can express - this is what block_ip_addresses is there for.
    assert watchdog._is_url_allowed("http://2130706433/") is False


def test_ip_literals_are_blocked_outright_when_nothing_is_allowlisted():
    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert kwargs["block_ip_addresses"] is True


def test_an_ssrf_allowlisted_host_stays_reachable(monkeypatch):
    """ssrf_allow_hosts is the documented escape hatch for reaching one
    internal host. The navigation deny-list must not silently override it -
    a deny-list of patterns cannot express an exception, so the covering
    pattern is dropped instead."""
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", {"127.0.0.1:8123"})

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "127.*" not in kwargs["prohibited_domains"]
    # ...but only that host's range opens up; everything else stays shut.
    assert "169.254.*" in kwargs["prohibited_domains"]
    assert "10.*" in kwargs["prohibited_domains"]
    # block_ip_addresses would refuse the allowlisted IP literal too.
    assert kwargs["block_ip_addresses"] is False


@pytest.mark.parametrize("entry,freed,still_blocked", [
    ("127.0.0.1:8123", "127.*", "10.*"),
    ("127.0.0.1", "127.*", "10.*"),
    ("wiki.internal", "*.internal", "10.*"),
    ("[::1]:8080", "::1", "127.*"),
    ("::1", "::1", "127.*"),
])
def test_allowlist_entry_forms_are_all_understood(monkeypatch, entry, freed, still_blocked):
    """ssrf_allow_hosts accepts host and host:port, and a bare IPv6 literal
    ends in ":<digits>" just like a host:port does."""
    monkeypatch.setattr(A, "SSRF_ALLOW_HOSTS", {entry})

    patterns = A._browser_profile_kwargs("http://127.0.0.1:45671")["prohibited_domains"]

    assert freed not in patterns
    assert still_blocked in patterns


def test_ssrf_allow_private_drops_the_navigation_deny_list(monkeypatch):
    """With ssrf_allow_private=1 the proxy's own check is a no-op, so keeping
    a second layer that still blocks would only be confusing."""
    monkeypatch.setattr(A, "SSRF_ALLOW_PRIVATE", True)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "prohibited_domains" not in kwargs
    assert "block_ip_addresses" not in kwargs
    # The proxy and headless are unconditional either way.
    assert kwargs["proxy"].bypass == "<-loopback>"
    assert kwargs["headless"] is True


def test_default_extensions_are_off(tmp_path):
    """browser-use otherwise downloads three CRX extensions from
    clients2.google.com on first launch - traffic that does not go through the
    SSRF proxy and never reaches the audit log."""
    profile = _profile(tmp_path)

    assert profile.enable_default_extensions is False
    assert not any("--load-extension" in a for a in profile.get_args())


# --- browser_headless: the visible-window opt-in -----------------------------
# headless was hard-pinned, which is the right default for an unattended tool
# but leaves no way to *watch* a browsing run (demos, debugging a selector).
# The opt-in must be explicit and must not weaken any other guard.

def test_headless_stays_on_by_default(monkeypatch):
    monkeypatch.delenv("AGENT8088_BROWSER_HEADLESS", raising=False)
    monkeypatch.setattr(A, "BROWSER_HEADLESS", True, raising=False)

    assert A._browser_profile_kwargs("http://127.0.0.1:45671")["headless"] is True


def test_config_can_opt_into_a_visible_window(monkeypatch):
    monkeypatch.delenv("AGENT8088_BROWSER_HEADLESS", raising=False)
    monkeypatch.setattr(A, "BROWSER_HEADLESS", False, raising=False)

    assert A._browser_profile_kwargs("http://127.0.0.1:45671")["headless"] is False


def test_env_var_overrides_config_both_ways(monkeypatch):
    monkeypatch.setattr(A, "BROWSER_HEADLESS", True, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    assert A._browser_profile_kwargs("http://127.0.0.1:45671")["headless"] is False

    monkeypatch.setattr(A, "BROWSER_HEADLESS", False, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "1")
    assert A._browser_profile_kwargs("http://127.0.0.1:45671")["headless"] is True


def test_visible_window_keeps_every_other_guard(monkeypatch):
    """A demo window must not become a hole in the SSRF posture."""
    monkeypatch.setattr(A, "BROWSER_HEADLESS", False, raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert kwargs["proxy"].server == "http://127.0.0.1:45671"
    assert kwargs["proxy"].bypass == "<-loopback>"
    assert kwargs["enable_default_extensions"] is False
    # The deny-list/IP-literal guards are chosen by the SSRF config, never by
    # whether a window is on screen.
    assert "prohibited_domains" in kwargs or A.SSRF_ALLOW_PRIVATE


# --- browser_window: placement for a side-by-side recording ------------------
# A visible window that lands on top of the terminal defeats the purpose of
# watching a run, and the window is re-created per browse_page call, so
# dragging it does not stick. Placement therefore has to be configuration.
# These assert the *compiled Chromium flags*, because browser-use computes its
# own window geometry and an unrecognised kwarg would be silently discarded.

def test_no_window_geometry_when_headless(monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "1")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "1280,900", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "680,0", raising=False)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "window_size" not in kwargs
    assert "window_position" not in kwargs


def test_window_geometry_reaches_the_chromium_command_line(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "1280,900", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "680,0", raising=False)

    args = _profile(tmp_path).get_args()

    assert "--window-size=1280,900" in args
    assert "--window-position=680,0" in args


def test_unset_window_config_leaves_geometry_alone(monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "", raising=False)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "window_size" not in kwargs
    assert "window_position" not in kwargs


def test_malformed_window_config_is_dropped_not_forwarded(tmp_path, monkeypatch):
    """Garbage must not reach Chromium as an extra switch."""
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "wide; --disable-web-security", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "1024x768", raising=False)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")
    assert "window_size" not in kwargs and "window_position" not in kwargs
    assert not any("disable-web-security" in a for a in _profile(tmp_path).get_args())


def test_visible_window_still_keeps_the_mock_keychain_flag(monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "1280,900", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "", raising=False)

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    if sys.platform == "darwin":
        assert kwargs["args"] == ["--use-mock-keychain"]


# --- window geometry from the environment ------------------------------------
# The effective config.txt is ~/.agent8088/config.txt, which outranks the copy
# bundled in a checkout - so a demo run from a worktree needs a route that
# touches no config file at all.

def test_env_supplies_window_geometry_without_any_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "", raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_SIZE", "1280,900")
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_POSITION", "680,0")

    args = _profile(tmp_path).get_args()

    assert "--window-size=1280,900" in args
    assert "--window-position=680,0" in args


def test_env_geometry_beats_config_geometry(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "800,600", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "0,0", raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_SIZE", "1440,980")
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_POSITION", "700,40")

    args = _profile(tmp_path).get_args()

    assert "--window-size=1440,980" in args
    assert "--window-position=700,40" in args
    assert "--window-size=800,600" not in args


def test_env_geometry_is_still_ignored_when_headless(monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_SIZE", "1280,900")
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_POSITION", "680,0")

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "window_size" not in kwargs
    assert "window_position" not in kwargs


def test_malformed_env_geometry_is_dropped(monkeypatch):
    monkeypatch.setenv("AGENT8088_BROWSER_HEADLESS", "0")
    monkeypatch.setattr(A, "BROWSER_WINDOW_SIZE", "", raising=False)
    monkeypatch.setattr(A, "BROWSER_WINDOW_POSITION", "", raising=False)
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_SIZE", "1280x900")
    monkeypatch.setenv("AGENT8088_BROWSER_WINDOW_POSITION", "; rm -rf /")

    kwargs = A._browser_profile_kwargs("http://127.0.0.1:45671")

    assert "window_size" not in kwargs
    assert "window_position" not in kwargs
