# Operations

## Choosing OVOS plugins

The hub's STT/TTS/VAD/WakeWord plugins are standard OVOS plugins. Choose them
based on your hardware and latency requirements:

| Use case | STT | TTS | VAD | WakeWord |
|---|---|---|---|---|
| Cloud-backed | `ovos-stt-plugin-server` | `ovos-tts-plugin-piper` | `ovos-vad-plugin-silero` | `ovos-ww-plugin-precise-lite` |
| On-device (x86) | `ovos-stt-plugin-fasterwhisper` | `ovos-tts-plugin-piper` | `ovos-vad-plugin-silero` | `ovos-ww-plugin-precise-lite` |
| On-device (RPi) | `ovos-stt-plugin-fasterwhisper` (tiny.en) | `ovos-tts-plugin-piper` | `ovos-vad-plugin-silero` | `ovos-ww-plugin-precise-lite` |

All plugins are discoverable via `pip install` and the OVOS plugin registry.

## Satellite setup: hivemind-mic-satellite

`hivemind-mic-satellite` is the reference lightweight satellite that streams
raw microphone audio to a hub running this plugin:

```bash
pip install hivemind-mic-satellite
hivemind-mic-satellite --host ws://hub-address:5678 \
                       --key your-api-key \
                       --name my-satellite
```

The satellite runs only:
- Microphone capture
- Optionally: local VAD to skip silence chunks before transmitting

All WakeWord, STT, and TTS processing runs on the hub.

## Audio format

The hub's `FakeMicrophone` expects:
- Sample rate: 16 000 Hz
- Sample width: 2 bytes (16-bit PCM)
- Channels: 1 (mono)

Configure the satellite's microphone to match. Audio with a different rate or width is
not converted: the frame is dropped and the client is told, with a
`recognizer_loop:speech.recognition.unknown` message carrying
`{"error": "unsupported_audio_format", "sample_rate": 16000, "sample_width": 2}`.
A continuous microphone stream gets that refusal once per peer, not once per chunk, so
the log shows one error per offending satellite for that path. One-shot STT requests are
refused per request, so a client retrying in a loop does log repeatedly.

## Access key requirements

Provision the satellite's API key via `hivemind-core`:

```bash
hivemind-core add-client --name my-satellite
```

The printed API key is what the satellite passes as `--key`. Ensure the
client's `allowed_types` whitelist includes `recognizer_loop:utterance` (and
any other message types it needs to send or receive).

## Resource considerations

- Each connected satellite that streams audio gets its own `SimpleListener`
  instance in memory, sharing the loaded STT/TTS/VAD/WW model instances.
- Model loading happens once at startup — the first satellite connection triggers
  instantiation if not already loaded.
- On resource-constrained hardware, limit concurrent mic-streaming satellites
  to avoid memory pressure.

## Authoring a binary protocol plugin

Implement `BinaryDataHandlerProtocol` from `hivemind_plugin_manager.protocols`:

```python
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from hivemind_plugin_manager.protocols import BinaryDataHandlerProtocol, ClientCallbacks
from hivemind_core.protocol import HiveMindClientConnection

@dataclass
class MyBinaryProtocol(BinaryDataHandlerProtocol):
    config: Dict[str, Any] = field(default_factory=dict)
    hm_protocol: Optional[object] = None
    callbacks: Optional[ClientCallbacks] = None

    def handle_microphone_input(self, bin_data: bytes, sample_rate: int,
                                sample_width: int,
                                client: HiveMindClientConnection) -> None: ...

    def handle_stt_transcribe_request(self, bin_data: bytes, sample_rate: int,
                                      sample_width: int, lang: str,
                                      client: HiveMindClientConnection) -> None: ...

    def handle_stt_handle_request(self, bin_data: bytes, sample_rate: int,
                                  sample_width: int, lang: str,
                                  client: HiveMindClientConnection) -> None: ...
```

Register under `hivemind.binary.protocol` in `setup.py` or `pyproject.toml`:

```python
# setup.py
entry_points={
    'hivemind.binary.protocol': [
        'my-binary-plugin=my_package:MyBinaryProtocol'
    ]
}
```

```toml
# pyproject.toml
[project.entry-points."hivemind.binary.protocol"]
"my-binary-plugin" = "my_package:MyBinaryProtocol"
```

---
[← Configuration](configuration.md) · [Home](../README.md)
