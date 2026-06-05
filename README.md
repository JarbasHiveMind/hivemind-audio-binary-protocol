# hivemind-audio-binary-protocol

Binary audio plugin for [hivemind-core](https://github.com/JarbasHiveMind/HiveMind-core).

Adds server-side WakeWord detection, VAD, STT, and TTS to a hivemind-core hub so that
lightweight satellites (e.g. [hivemind-mic-satellite](https://github.com/JarbasHiveMind/hivemind-mic-satellite))
can stream raw audio and receive transcriptions or synthesised speech without running
those models locally.

This plugin is the direct replacement for the old "HiveMind-listener" proof-of-concept.

## Where it fits

```
hivemind-core
  └── hivemind-plugin-manager  (BinaryDataHandlerFactory loads plugins by entry-point)
        └── hivemind-audio-binary-protocol  ← this repo
              ├── ovos-simple-listener  (WakeWord + VAD + STT pipeline)
              └── OVOSTTSFactory / OVOSSTTFactory / OVOSVADFactory / OVOSWakeWordFactory
```

The plugin registers under the `hivemind.binary.protocol` entry-point group as
`hivemind-audio-binary-protocol-plugin`.

## Install

```bash
pip install hivemind-audio-binary-protocol
```

You also need OVOS STT, TTS, VAD, and WakeWord plugins. Install them as you would in a
standard OVOS setup:

```bash
pip install ovos-stt-plugin-server ovos-tts-plugin-piper ovos-vad-plugin-silero \
            ovos-ww-plugin-precise-lite
```

## Quickstart

Add the `binary_protocol` block to `~/.config/hivemind-core/server.json`:

```json
{
  "binary_protocol": {
    "module": "hivemind-audio-binary-protocol-plugin",
    "hivemind-audio-binary-protocol-plugin": {
      "stt": {
        "module": "ovos-stt-plugin-server",
        "ovos-stt-plugin-server": {"url": "https://stt.openvoiceos.org"}
      },
      "tts": {
        "module": "ovos-tts-plugin-piper",
        "ovos-tts-plugin-piper": {"voice": "en_US-lessac-medium"}
      },
      "vad": {
        "module": "ovos-vad-plugin-silero"
      },
      "wake_word": "hey_mycroft",
      "hotwords": {
        "hey_mycroft": {
          "module": "ovos-ww-plugin-precise-lite",
          "model": "https://github.com/OpenVoiceOS/precise-lite-models/raw/master/wakewords/en/hey_mycroft.tflite"
        }
      }
    }
  }
}
```

Then start hivemind-core with the `listen` subcommand:

```bash
hivemind-core listen
```

## Audio streaming modes

This plugin handles three distinct binary audio flows:

| Mode | Client sends | Hub returns | Use case |
|---|---|---|---|
| Microphone stream | Raw PCM audio chunks | Bus messages (wakeword/utterance events) | Mic satellite; hub does all pipeline processing |
| STT transcription | Raw PCM audio | `recognizer_loop:transcribe.response` | Client wants transcription without triggering skills |
| STT handle | Raw PCM audio | Triggers `recognizer_loop:utterance` on the bus | Client wants the hub to handle the utterance |

TTS is triggered by the bus (`speak:synth` or `speak:b64_audio`) and returns binary
WAV audio or a Base64-encoded string back to the client.

## Configuration reference

The plugin's config block mirrors the OVOS plugin config convention. Each sub-plugin
(`stt`, `tts`, `vad`) takes its standard OVOS config:

| Key | Description |
|---|---|
| `stt` | STT plugin config. `module` selects the OVOS STT plugin. |
| `tts` | TTS plugin config. `module` selects the OVOS TTS plugin. |
| `vad` | VAD plugin config. `module` selects the OVOS VAD plugin. |
| `wake_word` | WakeWord name (key into `hotwords`). |
| `hotwords` | Dict of wakeword configurations, keyed by wakeword name. |
| `utterance_transformers` | List of OVOS utterance transformer plugin names. |
| `dialog_transformers` | List of OVOS dialog transformer plugin names. |
| `metadata_transformers` | List of OVOS metadata transformer plugin names. |

If the config block is omitted, the plugin falls back to reading `mycroft.conf`
(the standard OVOS configuration file) to select plugins.

## Access control

This plugin respects hivemind-core's per-client `allowed_types` whitelist. Clients must
be provisioned with appropriate access to send binary audio or receive TTS output.

## Docs

- [docs/audio_flow.md](docs/audio_flow.md) — detailed STT/TTS flow, FakeMicrophone, per-client listeners
- [docs/configuration.md](docs/configuration.md) — full configuration reference
- [docs/operations.md](docs/operations.md) — plugin selection, satellite setup, authoring a binary plugin
