# Audio Flow

## Overview

The plugin implements `BinaryDataHandlerProtocol` from `hivemind-plugin-manager`.
When a satellite sends a binary frame, `hivemind-core` dispatches it here based on
the `HiveMindBinaryPayloadType` tag in the frame.

Three inbound binary types are handled:

| Type | Handler | Description |
|---|---|---|
| Microphone audio chunks | `handle_microphone_input()` | Continuous raw PCM; feeds a per-client `SimpleListener`. |
| STT transcription request | `handle_stt_transcribe_request()` | One-shot; returns `recognizer_loop:transcribe.response`. |
| STT handle request | `handle_stt_handle_request()` | One-shot; injects `recognizer_loop:utterance` into the bus. |

Two outbound flows are triggered by OVOS bus events:

| Bus event | Handler | Description |
|---|---|---|
| `speak:synth` | `handle_speak_synth()` | Synthesise TTS; send binary WAV to client. |
| `speak:b64_audio` | `handle_speak_b64()` | Synthesise TTS; send Base64-encoded audio as a BUS message. |

## Microphone stream (full pipeline mode)

This is the mode used by `hivemind-mic-satellite`: the satellite streams raw
microphone chunks over an encrypted binary HiveMessage. The hub runs the full
WakeWord → VAD → STT pipeline.

```
satellite mic → binary frames → hub
                                  │
                                  └─ FakeMicrophone.queue
                                        │
                                        └─ SimpleListener (WakeWord + VAD + STT)
                                              │
                                              ├─ recognizer_loop:wakeword
                                              ├─ recognizer_loop:record_begin
                                              ├─ recognizer_loop:record_end
                                              └─ recognizer_loop:utterance
                                                        │
                                                        └─ forwarded back to satellite
                                                           + injected into agent bus
```

### Per-client listeners

Each connected satellite that sends microphone audio gets its own `SimpleListener`
instance (`AudioBinaryProtocol.listeners[client.peer]`). The listener is created on
the first audio frame and torn down when the client disconnects (via the
`ClientCallbacks.on_disconnect` hook).

Each listener has its own `FakeMicrophone` — a thread-safe queue that receives raw
PCM bytes from `handle_microphone_input()` and feeds them into the pipeline.

Shared plugin instances (`stt`, `tts`, `vad`) are reused across all per-client
listeners to avoid loading the same model once per satellite.

### FakeMicrophone

`FakeMicrophone` implements the `ovos_plugin_manager.templates.microphone.Microphone`
ABC. It exposes a `queue: Queue[Optional[bytes]]` — callers put raw PCM chunks in,
`SimpleListener` reads them via `read_chunk()`.

Default audio format expected:
- Sample rate: 16 000 Hz
- Sample width: 2 bytes (16-bit PCM)
- Channels: 1 (mono)

If the satellite sends a different format (different sample rate or width), the frame is
dropped and the client gets a `recognizer_loop:speech.recognition.unknown` BUS message
with `{"error": "unsupported_audio_format", "sample_rate": 16000, "sample_width": 2}`.
For a continuous microphone stream the refusal is sent once per peer, not once per chunk,
and the peer is cleared again as soon as it sends a supported frame. Sample rate
conversion is not implemented, so configure the satellite to match the hub's format.

## STT transcription request (one-shot mode)

The satellite sends a single binary frame tagged as an STT request with a language
code. The hub:

1. Constructs an `AudioData` object from the raw bytes.
2. Runs `stt.transcribe(audio, lang)`.
3. Sends `recognizer_loop:transcribe.response` back to the same client with the
   transcription list and language.

This mode does **not** trigger the WakeWord pipeline and does **not** inject an
utterance into the bus. Use it when the satellite only needs a transcription without
triggering skills.

## STT handle request (one-shot + bus injection)

Like transcription, but after STT the plugin:

1. Runs the transcription through utterance transformers.
2. Runs the result through metadata transformers.
3. Injects `recognizer_loop:utterance` into the agent bus via
   `hm_protocol.handle_inject_agent_msg()`.

OVOS skills and the intent pipeline handle the utterance from there.

## TTS flow

When OVOS emits `speak:synth` or `speak:b64_audio`, the plugin intercepts the
message, synthesises audio via `OVOSTTSFactory`, and sends the result back to
the client that originated the conversation:

```
OVOS skill → speak:synth → dialog transformers → TTS synth
                                                        │
                                                        └─ binary WAV → client
```

`speak:synth` returns a binary `HiveMindBinaryPayloadType.TTS_AUDIO` frame
with metadata: `{"lang": ..., "file_name": ..., "utterance": ...}`.

`speak:b64_audio` returns a BUS message with `{"audio": "<base64>", ...}`.

## Base64 STT (b64 audio)

A client can also send `recognizer_loop:b64_audio` as a regular BUS message
(not a binary frame). The plugin handles this via `handle_audio_b64()`:

1. Decodes the Base64 audio.
2. Transcribes via STT.
3. Runs utterance transformers.
4. Emits `recognizer_loop:utterance` on the agent bus.

This path is used by clients that cannot send binary frames (e.g. HTTP transport
clients or the Web UI).

---
[Home](../README.md) · [Configuration →](configuration.md)
