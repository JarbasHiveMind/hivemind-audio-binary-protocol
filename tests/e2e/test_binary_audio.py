"""
E2E tests: a satellite streams binary / base64 audio to a master running this
package's real :class:`AudioBinaryProtocol`, and we assert the frames are
received and decoded by the protocol's handlers.

Topology (from ``conftest.audio_topology``): 1 master `M0` whose binary data
handler is the real ``AudioBinaryProtocol`` (with stub STT/TTS/VAD/wakeword),
1 satellite `S0`. All wiring is in-process via hivescope — no sockets, no
models, no real audio hardware.

Message shapes mirror the Voice PE satellite contract:
  - ``HiveMessageType.BINARY`` with ``RAW_AUDIO`` / ``STT_AUDIO_TRANSCRIBE`` /
    ``STT_AUDIO_HANDLE`` bin types,
  - OVOS ``recognizer_loop:b64_transcribe`` / ``speak:b64_audio`` bus messages.
"""
import base64
import threading
import time

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType
from ovos_bus_client.message import Message

from .conftest import make_wav_bytes


def _wait_satellite_bus(satellite, msg_type, timeout=5.0):
    """Register a one-shot handler BEFORE sending, then return (event, result)."""
    event = threading.Event()
    result = []

    def handler(msg):
        result.append(msg)
        event.set()

    satellite.internal_bus.once(msg_type, handler)
    return event, result


# ════════════════════════════════════════════════════════════════════
#  Binary streaming — RAW_AUDIO (microphone input)
# ════════════════════════════════════════════════════════════════════

class TestRawAudioStreaming:

    def test_raw_audio_frame_reaches_protocol(self, audio_topology):
        """A single RAW_AUDIO frame is decoded and routed to the handler."""
        _, master, satellite, protocol, _ = audio_topology

        received = []
        orig = protocol.handle_microphone_input

        def spy(bin_data, sample_rate, sample_width, client):
            received.append((bin_data, sample_rate, sample_width))
            return orig(bin_data, sample_rate, sample_width, client)

        protocol.handle_microphone_input = spy

        frame = b"\x02\x03" * 480
        satellite.send(HiveMessage(
            HiveMessageType.BINARY, payload=frame,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2},
        ))
        time.sleep(0.2)

        assert received, "RAW_AUDIO frame never reached handle_microphone_input"
        got_bytes, sr, sw = received[0]
        assert got_bytes == frame, "audio payload corrupted in transit"
        assert (sr, sw) == (16000, 2)

    def test_raw_audio_creates_per_client_listener(self, audio_topology):
        """First RAW_AUDIO frame spins up a SimpleListener for the peer."""
        _, master, satellite, protocol, _ = audio_topology

        satellite.send(HiveMessage(
            HiveMessageType.BINARY, payload=b"\x00\x01" * 480,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2},
        ))
        time.sleep(0.2)

        assert satellite.peer in protocol.listeners, (
            "no listener created for the streaming peer"
        )

    def test_multiple_raw_audio_frames_all_received(self, audio_topology):
        """Every chunk in a stream reaches the handler intact and in order."""
        _, master, satellite, protocol, _ = audio_topology

        received = []
        orig = protocol.handle_microphone_input

        def spy(bin_data, sample_rate, sample_width, client):
            received.append(bin_data)
            return orig(bin_data, sample_rate, sample_width, client)

        protocol.handle_microphone_input = spy

        frames = [bytes([i & 0xFF]) * 960 for i in range(5)]
        for frame in frames:
            satellite.send(HiveMessage(
                HiveMessageType.BINARY, payload=frame,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2},
            ))
        time.sleep(0.3)

        assert received == frames, f"expected {len(frames)} frames intact, got {received!r}"


# ════════════════════════════════════════════════════════════════════
#  Binary STT — transcribe request / handle request
# ════════════════════════════════════════════════════════════════════

class TestBinarySTT:

    def test_stt_transcribe_decodes_payload(self, audio_topology):
        """STT_AUDIO_TRANSCRIBE: the WAV bytes are handed to STT verbatim."""
        _, master, satellite, protocol, stt = audio_topology

        wav = make_wav_bytes(2000)
        satellite.send(HiveMessage(
            HiveMessageType.BINARY, payload=wav,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_TRANSCRIBE,
            metadata={"sample_rate": 16000, "sample_width": 2, "lang": "en-us"},
        ))
        time.sleep(0.2)

        assert wav in stt.transcribed, (
            "decoded binary STT payload did not reach the STT engine intact"
        )

    def test_stt_transcribe_response_returned_to_satellite(self, audio_topology):
        """STT_AUDIO_TRANSCRIBE produces a transcribe.response back to the satellite."""
        _, master, satellite, protocol, stt = audio_topology

        event, result = _wait_satellite_bus(satellite, "recognizer_loop:transcribe.response")

        wav = make_wav_bytes(2000)
        satellite.send(HiveMessage(
            HiveMessageType.BINARY, payload=wav,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_TRANSCRIBE,
            metadata={"sample_rate": 16000, "sample_width": 2, "lang": "en-us"},
        ))

        assert event.wait(timeout=5.0), "no transcribe.response delivered to satellite"
        assert result[0].data["transcriptions"][0][0] == "hello world"

    def test_stt_handle_injects_utterance(self, audio_topology):
        """STT_AUDIO_HANDLE: decoded audio is transcribed and injected as an utterance."""
        _, master, satellite, protocol, stt = audio_topology

        wav = make_wav_bytes(2000)
        satellite.send(HiveMessage(
            HiveMessageType.BINARY, payload=wav,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_HANDLE,
            metadata={"sample_rate": 16000, "sample_width": 2, "lang": "en-us"},
        ))
        time.sleep(0.2)

        assert wav in stt.transcribed, "STT_AUDIO_HANDLE payload not decoded to STT"
        injected = master.agent_protocol.last_injected("recognizer_loop:utterance")
        assert injected is not None, "no utterance injected on the agent bus"
        assert injected.data["utterances"] == ["hello world"]


# ════════════════════════════════════════════════════════════════════
#  Base64 STT / TTS over the OVOS bus
# ════════════════════════════════════════════════════════════════════

class TestBase64Audio:

    def test_b64_transcribe_reaches_agent_bus(self, audio_topology):
        """recognizer_loop:b64_transcribe is injected onto the master agent bus."""
        _, master, satellite, protocol, stt = audio_topology

        wav = make_wav_bytes(2000)
        b64_audio = base64.b64encode(wav).decode("utf-8")
        satellite.send(Message("recognizer_loop:b64_transcribe", {
            "audio": b64_audio, "lang": "en-us",
        }))
        time.sleep(0.2)

        master.agent_protocol.assert_injected("recognizer_loop:b64_transcribe")
        # the handler base64-decodes and runs STT on the exact bytes
        assert wav in stt.transcribed, "b64 STT payload not decoded before transcribe"

    def test_b64_transcribe_response_returned_to_satellite(self, audio_topology):
        """The b64 transcribe handler replies with transcriptions to the satellite."""
        _, master, satellite, protocol, stt = audio_topology

        event, result = _wait_satellite_bus(satellite, "recognizer_loop:b64_transcribe.response")

        wav = make_wav_bytes(2000)
        b64_audio = base64.b64encode(wav).decode("utf-8")
        satellite.send(Message("recognizer_loop:b64_transcribe", {
            "audio": b64_audio, "lang": "en-us",
        }))

        assert event.wait(timeout=5.0), "no b64_transcribe.response delivered to satellite"
        assert result[0].data["transcriptions"][0][0] == "hello world"

    def test_speak_b64_audio_response_returned_to_satellite(self, audio_topology):
        """speak:b64_audio synthesizes TTS and returns base64 WAV to the satellite."""
        _, master, satellite, protocol, stt = audio_topology

        event, result = _wait_satellite_bus(satellite, "speak:b64_audio.response")

        satellite.send(Message("speak:b64_audio", {
            "utterance": "hello world", "lang": "en-us",
        }))

        assert event.wait(timeout=5.0), "no speak:b64_audio.response delivered to satellite"
        audio_field = result[0].data.get("audio")
        assert audio_field, "response carried no audio payload"
        decoded = base64.b64decode(audio_field)
        assert decoded[:4] == b"RIFF", "returned TTS audio is not a WAV"


# ════════════════════════════════════════════════════════════════════
#  Full streaming flow — record_begin → chunks → record_end → speak
# ════════════════════════════════════════════════════════════════════

class TestFullStreamingFlow:

    def test_vad_stream_then_speak_round_trip(self, audio_topology):
        """Lifecycle: record_begin, audio chunks, record_end, then a TTS reply."""
        _, master, satellite, protocol, stt = audio_topology

        received = []
        orig = protocol.handle_microphone_input

        def spy(bin_data, sample_rate, sample_width, client):
            received.append(bin_data)
            return orig(bin_data, sample_rate, sample_width, client)

        protocol.handle_microphone_input = spy

        satellite.send(Message("recognizer_loop:record_begin"))
        chunks = [b"\x04\x05" * 480 for _ in range(3)]
        for chunk in chunks:
            satellite.send(HiveMessage(
                HiveMessageType.BINARY, payload=chunk,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2},
            ))
        satellite.send(Message("recognizer_loop:record_end"))
        time.sleep(0.3)

        assert received == chunks, "not all streamed audio chunks reached the protocol"

        master.agent_protocol.assert_injected("recognizer_loop:record_begin")
        master.agent_protocol.assert_injected("recognizer_loop:record_end")

        # master synthesizes a spoken reply back down to the satellite
        event, result = _wait_satellite_bus(satellite, "speak")
        master.send_to_satellite(
            satellite.peer,
            HiveMessage(HiveMessageType.BUS,
                        payload=Message("speak", {"utterance": "it is 3 PM"})),
        )
        assert event.wait(timeout=5.0), "satellite never received the spoken reply"
        assert result[0].data["utterance"] == "it is 3 PM"
