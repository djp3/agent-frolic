# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secrets go to the Keychain and come back; nothing about them leaks into errors."""
import pytest

import keychain


def test_a_secret_is_stored_read_and_forgotten(isolated):
    assert keychain.get(keychain.GITHUB_TOKEN) is None and not keychain.has(keychain.GITHUB_TOKEN)
    keychain.put(keychain.GITHUB_TOKEN, "  ghp_abcdefghijklmnopqrstuvwxyz0123456789  ")
    assert keychain.get(keychain.GITHUB_TOKEN) == "ghp_abcdefghijklmnopqrstuvwxyz0123456789"   # trimmed
    assert isolated.items == {("agent-frolic", "github-token"): "ghp_abcdefghijklmnopqrstuvwxyz0123456789"}
    keychain.forget(keychain.GITHUB_TOKEN)
    assert keychain.get(keychain.GITHUB_TOKEN) is None
    keychain.forget(keychain.GITHUB_TOKEN)                      # forgetting twice is fine


def test_only_the_three_known_secrets_are_accepted():
    with pytest.raises(keychain.KeychainError):
        keychain.put("something-else", "value")
    with pytest.raises(keychain.KeychainError):
        keychain.get("something-else")


def test_an_empty_secret_is_refused():
    with pytest.raises(keychain.KeychainError):
        keychain.put(keychain.NGROK_AUTHTOKEN, "   ")


def test_the_webhook_secret_is_made_once_and_kept():
    first = keychain.webhook_secret()
    assert len(first) == 64 and first == keychain.webhook_secret()


def test_a_failing_keychain_raises_without_the_secret_in_the_message():
    class Broken:
        def get_password(self, service, name):
            raise RuntimeError("ghp_secretsecretsecretsecret was in here")
        set_password = delete_password = get_password

    keychain.use(Broken())
    for call in (lambda: keychain.get(keychain.GITHUB_TOKEN),
                 lambda: keychain.put(keychain.GITHUB_TOKEN, "ghp_secretsecretsecretsecret")):
        with pytest.raises(keychain.KeychainError) as caught:
            call()
        assert "ghp_secret" not in str(caught.value)
    assert keychain.has(keychain.GITHUB_TOKEN) is False


@pytest.mark.parametrize("secret, shown", [
    ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_…6789"),
    ("github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz", "github_pat_…wxyz"),
    ("2abcDEFghiJKLmnoPQRstuVWXyz_3abcdefghij", "…ghij"),          # an ngrok authtoken has no prefix
    ("ghp_short", "ghp_…"),
    ("tiny", "…"),
    ("", ""),
    (None, ""),
])
def test_mask_shows_only_the_prefix_and_the_last_four(secret, shown):
    assert keychain.mask(secret) == shown
