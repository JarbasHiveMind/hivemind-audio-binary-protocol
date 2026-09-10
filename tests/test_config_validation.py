"""A deployer config block that sets stt/tts/vad but omits hotwords (or a
wake_word) is a partial block. __post_init__'s mycroft.conf fallback only
fires under `if not self.config`, so a partial block skips it entirely: the
plugin constructs, the hub starts, CI is green, and the missing key is only
touched later, in add_listener, when a satellite streams its first RAW_AUDIO
frame (KeyError, connection dropped with no close code).

These construct the real AudioBinaryProtocol through its real __post_init__,
stubbing only the OVOS plugin factories (as the audit's repro did), and
assert construction itself refuses a config it cannot serve.

See knowledge/wiki/audits/security/hivemind-raw-audio-2026-09-09.md, section 1.
"""
from unittest.mock import MagicMock, patch

import pytest

from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol


def _base_config():
    return {
        "stt": {"module": "fake-stt"},
        "tts": {"module": "fake-tts"},
        "vad": {"module": "fake-vad"},
    }


def _make_agent_protocol():
    agent_protocol = MagicMock()
    agent_protocol.bus = MagicMock()
    return agent_protocol


def _construct(config):
    with patch("hivemind_audio_binary_protocol.protocol.OVOSSTTFactory.create"), \
         patch("hivemind_audio_binary_protocol.protocol.OVOSTTSFactory.create"), \
         patch("hivemind_audio_binary_protocol.protocol.OVOSVADFactory.create"):
        return AudioBinaryProtocol(config=config, agent_protocol=_make_agent_protocol())


def test_missing_hotwords_key_raises_at_construction():
    config = _base_config()
    config["wake_word"] = "hey_jarbas"

    with pytest.raises((ValueError, KeyError)) as exc:
        _construct(config)

    assert "hotwords" in str(exc.value) or "hey_jarbas" in str(exc.value)


def test_hotwords_present_but_no_wake_word_raises_at_construction():
    config = _base_config()
    config["hotwords"] = {"hey_jarbas": {"module": "fake-ww"}}
    # no wake_word/wakeword key at all

    with pytest.raises((ValueError, KeyError, TypeError)):
        _construct(config)


def test_wake_word_names_missing_hotword_entry_raises_at_construction():
    config = _base_config()
    config["wake_word"] = "hey_jarbas"
    config["hotwords"] = {"hey_mycroft": {"module": "fake-ww"}}  # hey_jarbas absent

    with pytest.raises((ValueError, KeyError)) as exc:
        _construct(config)

    assert "hey_jarbas" in str(exc.value)
