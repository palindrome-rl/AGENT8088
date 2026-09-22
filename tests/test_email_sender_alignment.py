"""The email gateway authorizes on the From header, so it must authenticate
that header -- not merely some domain in the message.

_verify_sender used to accept `spf=pass` or `dkim=pass` on their own. Neither
says anything about the From header a moment later handed to
Allowlist.is_allowed():

  - SPF authenticates the SMTP envelope sender (Return-Path). Mail sent from
    an attacker's own domain passes SPF for that domain while carrying
    `From: someone@allowlisted.example`.
  - DKIM authenticates the signing domain `d=`, which likewise need not be the
    From domain.

Only DMARC requires alignment with From, so only DMARC -- or an explicitly
aligned SPF/DKIM result -- may authorize.
"""
from __future__ import annotations

import email as email_lib

import pytest

from agent8088.gateway.platforms.email import _verify_sender


def _message(auth_results: str | None, from_addr: str = "boss@allowlisted.example"):
    msg = email_lib.message.Message()
    msg["From"] = from_addr
    if auth_results is not None:
        msg["Authentication-Results"] = auth_results
    return msg


def test_no_authentication_results_header_fails_closed():
    assert _verify_sender(_message(None)) is False


def test_dmarc_pass_is_accepted():
    assert _verify_sender(_message(
        "mx.example.com; dmarc=pass header.from=allowlisted.example")) is True


@pytest.mark.parametrize("auth_results", [
    # SPF passes for the attacker's own envelope domain; From says otherwise.
    "mx.example.com; spf=pass smtp.mailfrom=attacker.example",
    # DKIM signed by the attacker's domain; From says otherwise.
    "mx.example.com; dkim=pass header.d=attacker.example",
    "mx.example.com; spf=pass smtp.mailfrom=attacker.example; "
    "dkim=pass header.d=attacker.example; dmarc=fail header.from=allowlisted.example",
])
def test_unaligned_pass_is_rejected(auth_results):
    assert _verify_sender(_message(auth_results)) is False


@pytest.mark.parametrize("auth_results", [
    "mx.example.com; spf=pass smtp.mailfrom=allowlisted.example",
    "mx.example.com; dkim=pass header.d=allowlisted.example",
])
def test_aligned_spf_or_dkim_is_accepted(auth_results):
    """Alignment is what DMARC actually checks, so an aligned SPF or DKIM pass
    is a genuine authentication of the From domain even when the receiving MTA
    reported no dmarc= result of its own."""
    assert _verify_sender(_message(auth_results)) is True


def test_alignment_is_matched_on_the_whole_domain_label():
    """`notallowlisted.example` is not `allowlisted.example`; a substring test
    would say it is."""
    assert _verify_sender(_message(
        "mx.example.com; spf=pass smtp.mailfrom=notallowlisted.example")) is False


def test_subdomain_of_the_from_domain_is_aligned():
    """Relaxed alignment, which is DMARC's default."""
    assert _verify_sender(_message(
        "mx.example.com; dkim=pass header.d=mail.allowlisted.example")) is True


def test_dmarc_fail_is_rejected_even_next_to_a_pass_word():
    assert _verify_sender(_message(
        "mx.example.com; dmarc=fail header.from=allowlisted.example")) is False
