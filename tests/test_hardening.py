"""Hardening tests for the audio-binary protocol.

C1 — FakeMicrophone.queue used to be unbounded (Queue() defaults to
maxsize=0); a client streaming RAW_AUDIO faster than the STT/VAD cadence
drains it grows the process memory without limit. The queue must be bounded
and, since a live mic feed cannot be allowed to block or grow, must drop the
OLDEST chunk on overflow rather than the newest.

C2 — transcribe_b64_audio used to hand whatever sample_rate/sample_width the
peer declared straight to the STT plugin, unlike the binary STT paths which
reject an unsupported format via _is_supported_audio_format.

C3 — transcribe_b64_audio used to call pybase64.b64decode with no error
handling: a malformed/truncated blob raised binascii.Error before any reply
was sent, and handle_transcribe_b64 indexed self.hm_protocol.clients by an
unchecked source, raising KeyError for an unknown/missing source. Both used
to leave the round-trip client hanging with no response.
"""
from unittest.mock import MagicMock

import pytest

from hivemind_bus_client.message import HiveMessageType
from ovos_bus_client.message import Message

from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol, FakeMicrophone


def _make_protocol():
    proto = object.__new__(AudioBinaryProtocol)
    proto.listeners = {}
    proto.plugins = MagicMock()
    proto.plugins.stt.transcribe.return_value = [("garbage", 1.0)]
    proto.plugins.stt.lang = "en-us"
    proto.hm_protocol = MagicMock()
    proto.hm_protocol.clients = {}
    proto.transform_audio = lambda audio: (audio, {})
    proto.transform_utterances = lambda utts, lang: (utts, {})
    proto.metadata_transformers = MagicMock()
    proto.metadata_transformers.transform = lambda ctx: ctx
    return proto


def _make_client(peer="sat::1"):
    client = MagicMock()
    client.peer = peer
    client.sent = []
    client.send = client.sent.append
    return client


# --- C1: bounded, drop-oldest queue ----------------------------------------

def test_fake_microphone_queue_is_bounded_and_drops_oldest():
    mic = FakeMicrophone()
    maxsize = mic.queue.maxsize
    assert maxsize > 0, "the mic queue must be bounded, not infinite"

    n = maxsize + 50
    for i in range(n):
        mic.put_chunk(bytes([i % 256]))

    assert mic.queue.qsize() <= maxsize, "queue grew past its bound"

    # drain and confirm the retained chunks are the most recent ones, i.e.
    # the last one enqueued must still be present (oldest were dropped)
    drained = []
    while not mic.queue.empty():
        drained.append(mic.queue.get_nowait())
    assert drained[-1] == bytes([(n - 1) % 256]), "newest chunk was not retained"
    assert drained[0] != bytes([0]), "oldest chunks should have been dropped"


def test_fake_microphone_stop_still_poisons_with_none():
    mic = FakeMicrophone()
    mic.put_chunk(b"\x00" * 8)
    mic.stop()
    assert mic.read_chunk() is None


# --- C2: b64 STT honours the format guard ----------------------------------

def test_transcribe_b64_audio_rejects_unsupported_format():
    proto = _make_protocol()
    message = Message("recognizer_loop:b64_audio",
                       {"audio": "aGVsbG8gd29ybGQ=", "sample_rate": 44100, "sample_width": 2})

    result = proto.transcribe_b64_audio(message)

    assert result == []
    assert not proto.plugins.stt.transcribe.called, (
        "b64 audio in an unsupported format was handed to the STT plugin")


def test_transcribe_b64_audio_in_supported_format_still_works():
    proto = _make_protocol()
    message = Message("recognizer_loop:b64_audio",
                       {"audio": "aGVsbG8gd29ybGQ=", "sample_rate": 16000, "sample_width": 2})

    result = proto.transcribe_b64_audio(message)

    assert proto.plugins.stt.transcribe.called
    assert result == [("garbage", 1.0)]


# --- C3: malformed b64 / unknown source never hang -------------------------

def test_transcribe_b64_audio_malformed_blob_does_not_raise():
    proto = _make_protocol()
    message = Message("recognizer_loop:b64_audio",
                       {"audio": "aGVsbG8", "sample_rate": 16000, "sample_width": 2})

    result = proto.transcribe_b64_audio(message)  # must not raise binascii.Error

    assert result == []
    assert not proto.plugins.stt.transcribe.called


def test_handle_transcribe_b64_unknown_source_does_not_raise():
    proto = _make_protocol()
    message = Message("recognizer_loop:b64_transcribe",
                       {"audio": "aGVsbG8gd29ybGQ="},
                       context={"source": "unknown::peer"})

    proto.handle_transcribe_b64(message)  # must not raise KeyError


def test_handle_transcribe_b64_malformed_blob_still_replies():
    proto = _make_protocol()
    client = _make_client("sat::1")
    proto.hm_protocol.clients["sat::1"] = client

    message = Message("recognizer_loop:b64_transcribe",
                       {"audio": "aGVsbG8", "sample_rate": 16000, "sample_width": 2},
                       context={"source": "sat::1"})

    proto.handle_transcribe_b64(message)  # must not raise, and must reply

    assert client.sent, "the round-trip client was left hanging with no response"
    replies = [m for m in client.sent
               if m.msg_type == HiveMessageType.BUS
               and m.payload.msg_type == "recognizer_loop:b64_transcribe.response"]
    assert replies
    assert replies[0].payload.data["transcriptions"] == []
