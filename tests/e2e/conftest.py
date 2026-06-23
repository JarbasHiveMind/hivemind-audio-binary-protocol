"""
Shared pytest fixtures for the binary audio protocol e2e tests.

These tests wire a real :class:`AudioBinaryProtocol` (the production
``BinaryDataHandlerProtocol`` shipped by this package) into a hivescope
in-process topology and stream binary / base64 audio from a satellite to the
master. No real STT/TTS/VAD/wakeword models are loaded: lightweight stub
plugin instances stand in for them so the test exercises the protocol's
decode + dispatch logic, not the neural backends.

Node roles (hivescope)
──────────────────────
  Master    — runs hivemind-core (``HiveMindListenerProtocol``) with this
              package's ``AudioBinaryProtocol`` as the binary data handler.
  Satellite — connects via hivemind-bus-client and streams audio upstream.

hivemind-core is deny-by-default / whitelist-only, so the satellite is granted
exactly the audio message types it sends (``_AUDIO_TYPES``).
"""
import struct

import pytest

from ovos_plugin_manager.templates.stt import STT
from ovos_plugin_manager.templates.tts import TTS
from ovos_plugin_manager.templates.vad import VADEngine
from ovos_plugin_manager.templates.hotwords import HotWordEngine

import hivemind_audio_binary_protocol.protocol as protocol_module
from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol, PluginOptions

from hivescope.scenarios import single_satellite


# Satellite → master audio message types. hivemind-core denies everything not
# explicitly whitelisted, so every binary/b64 type the satellite emits must be
# listed here or the master drops it before the protocol ever sees it.
_AUDIO_TYPES = [
    "recognizer_loop:utterance",
    "recognizer_loop:record_begin",
    "recognizer_loop:record_end",
    "recognizer_loop:b64_transcribe",
    "recognizer_loop:b64_audio",
    "speak:b64_audio",
    "speak:synth",
]


def make_wav_bytes(num_samples: int = 16000) -> bytes:
    """Build a minimal 16-bit mono 16 kHz WAV (header + PCM)."""
    pcm = b"\x00\x01" * num_samples
    data_bytes = num_samples * 2
    hdr = bytearray(44)
    struct.pack_into("<4sI4s", hdr, 0, b"RIFF", data_bytes + 36, b"WAVE")
    struct.pack_into("<4sIHHIIHH", hdr, 12, b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    struct.pack_into("<4sI", hdr, 36, b"data", data_bytes)
    return bytes(hdr) + pcm


# ── Stub plugins ──────────────────────────────────────────────────────────
# Concrete, dependency-free subclasses of the OVOS plugin templates. They
# record what audio reached them so tests can assert the protocol decoded the
# payload correctly, without pulling in (and downloading) real models.

class StubSTT(STT):
    """Records the raw bytes handed to transcribe() and returns a fixed result."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transcribed = []  # list[bytes] — frame_data of each AudioData seen

    def transcribe(self, audio, lang=None):
        self.transcribed.append(getattr(audio, "frame_data", None))
        return [("hello world", 0.99)]

    def execute(self, audio, language=None):
        tx = self.transcribe(audio, language)
        return tx[0][0] if tx else ""

    @property
    def available_languages(self):
        return {"en-us"}


class StubVAD(VADEngine):
    """Always reports silence so the listener loop never blocks on speech."""

    def is_silence(self, chunk):
        return True


class StubTTS(TTS):
    """Writes a tiny valid WAV so the b64/synth paths have audio to encode."""

    def get_tts(self, sentence, wav_file, lang=None, voice=None):
        with open(wav_file, "wb") as f:
            f.write(make_wav_bytes(8))
        return wav_file, None


class StubWakeWord(HotWordEngine):
    """Never triggers; the listener stays in record mode for the test."""

    def __init__(self, *args, **kwargs):
        super().__init__("dummy")

    def found_wake_word(self, frame_data):
        return False


@pytest.fixture
def audio_topology(monkeypatch):
    """T1 topology whose master runs the real AudioBinaryProtocol.

    Yields ``(builder, master, satellite, stt)`` where ``stt`` is the StubSTT
    instance wired into the protocol (so tests can inspect decoded audio).
    """
    # The protocol builds a real wakeword via OVOSWakeWordFactory.create_hotword
    # inside add_listener(); no wakeword plugin is installed in CI, so stub it.
    stub_ww = StubWakeWord()
    monkeypatch.setattr(
        protocol_module.OVOSWakeWordFactory,
        "create_hotword",
        staticmethod(lambda *a, **k: stub_ww),
    )

    builder = single_satellite(allowed_types=_AUDIO_TYPES)
    master = builder.get_master("M0")

    stt = StubSTT()
    plugins = PluginOptions(
        wakeword="dummy",
        tts=StubTTS(),
        stt=stt,
        vad=StubVAD(),
    )
    real_protocol = AudioBinaryProtocol(
        agent_protocol=master.agent_protocol,
        plugins=plugins,
        config={"hotwords": {"dummy": {"module": "stub"}}},
    )
    real_protocol.hm_protocol = master.hm_protocol
    master.hm_protocol.binary_data_protocol = real_protocol
    # Listeners is a class attribute shared across instances — reset between tests.
    AudioBinaryProtocol.listeners = {}

    builder.start_all()
    satellite = builder.get_satellite("S0")
    try:
        yield builder, master, satellite, real_protocol, stt
    finally:
        builder.stop_all()
        AudioBinaryProtocol.listeners = {}
