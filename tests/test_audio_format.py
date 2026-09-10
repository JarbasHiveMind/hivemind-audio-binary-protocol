"""HIVEMIND-AUDIO-1 §2 — stated audio format must be honoured or rejected.

"A receiver that cannot process the stated format MUST reject the payload
rather than misinterpret the bytes — there is no in-band format negotiation
in a raw stream."

This node processes 16 kHz signed 16-bit PCM. The RAW_AUDIO path already
refuses anything else; the two STT paths used to hand whatever the peer
declared straight to the STT plugin, which turns a wrong sample rate into a
garbage transcript instead of a rejection.
"""
from unittest.mock import MagicMock

from hivemind_bus_client.message import HiveMessageType

from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol


def _make_protocol():
    proto = object.__new__(AudioBinaryProtocol)
    proto.listeners = {}
    proto.plugins = MagicMock()
    proto.plugins.stt.transcribe.return_value = [("garbage", 1.0)]
    proto.hm_protocol = MagicMock()
    proto.transform_audio = lambda audio: (audio, {})
    proto.transform_utterances = lambda utts, lang: (utts, {})
    proto.metadata_transformers = MagicMock()
    proto.metadata_transformers.transform = lambda ctx: ctx
    return proto


def _make_client():
    client = MagicMock()
    client.peer = "sat::1"
    client.sent = []
    client.send = client.sent.append
    return client


def _rejections(client):
    return [m for m in client.sent
            if m.msg_type == HiveMessageType.BUS
            and m.payload.msg_type == "recognizer_loop:speech.recognition.unknown"]


# --- the defect ------------------------------------------------------------

def test_transcribe_request_with_unsupported_sample_rate_is_rejected():
    proto = _make_protocol()
    client = _make_client()

    proto.handle_stt_transcribe_request(b"\x00" * 32, 44100, 2, "en-us", client)

    assert not proto.plugins.stt.transcribe.called, (
        "audio in an unsupported format was handed to the STT plugin")
    assert _rejections(client), "the peer was not told its payload was rejected"


def test_transcribe_request_with_unsupported_sample_width_is_rejected():
    proto = _make_protocol()
    client = _make_client()

    proto.handle_stt_transcribe_request(b"\x00" * 32, 16000, 4, "en-us", client)

    assert not proto.plugins.stt.transcribe.called
    assert _rejections(client)


def test_handle_request_with_unsupported_sample_rate_is_rejected():
    proto = _make_protocol()
    client = _make_client()

    proto.handle_stt_handle_request(b"\x00" * 32, 8000, 2, "en-us", client)

    assert not proto.plugins.stt.transcribe.called
    assert not proto.hm_protocol.handle_inject_agent_msg.called, (
        "a transcript of misinterpreted bytes was injected into the agent")
    assert _rejections(client)


# --- positive controls -----------------------------------------------------

def test_transcribe_request_in_the_default_format_still_works():
    proto = _make_protocol()
    client = _make_client()

    proto.handle_stt_transcribe_request(b"\x00" * 32, 16000, 2, "en-us", client)

    assert proto.plugins.stt.transcribe.called
    replies = [m for m in client.sent
               if m.payload.msg_type == "recognizer_loop:transcribe.response"]
    assert len(replies) == 1


def test_handle_request_in_the_default_format_still_reaches_the_agent():
    proto = _make_protocol()
    client = _make_client()

    proto.handle_stt_handle_request(b"\x00" * 32, 16000, 2, "en-us", client)

    assert proto.plugins.stt.transcribe.called
    assert proto.hm_protocol.handle_inject_agent_msg.called
