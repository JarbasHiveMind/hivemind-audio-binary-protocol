# Configuration Reference

The plugin is configured under the `binary_protocol` key in
`~/.config/hivemind-core/server.json`.

```json
{
  "binary_protocol": {
    "module": "hivemind-audio-binary-protocol-plugin",
    "hivemind-audio-binary-protocol-plugin": {
      "stt":       { "module": "<ovos-stt-plugin-name>",  "<plugin-name>": {} },
      "tts":       { "module": "<ovos-tts-plugin-name>",  "<plugin-name>": {} },
      "vad":       { "module": "<ovos-vad-plugin-name>",  "<plugin-name>": {} },
      "wake_word": "<hotword-name>",
      "hotwords": {
        "<hotword-name>": {
          "module": "<ovos-ww-plugin-name>",
          "model": "<path-or-url>"
        }
      },
      "utterance_transformers": [],
      "dialog_transformers":    [],
      "metadata_transformers":  [],
      "audio_transformers":     [],
      "tts_transformers":       []
    }
  }
}
```

## Top-level keys

| Key | Description |
|---|---|
| `stt` | OVOS STT plugin config block. `module` selects the plugin. |
| `tts` | OVOS TTS plugin config block. `module` selects the plugin. |
| `vad` | OVOS VAD plugin config block. `module` selects the plugin. |
| `wake_word` | Name of the active hotword (key into `hotwords`). |
| `hotwords` | Dict of hotword configurations. Each entry has at least `module` and optionally a `model` path or URL. |
| `utterance_transformers` | List of OVOS utterance transformer plugin names to apply after STT. |
| `dialog_transformers` | List of OVOS dialog transformer plugin names to apply before TTS. |
| `metadata_transformers` | List of OVOS metadata transformer plugin names. |
| `audio_transformers` | List of OVOS audio transformer plugin names applied before STT (an `AudioLanguageDetector` in the chain resolves a missing language). |
| `tts_transformers` | List of OVOS tts transformer plugin names applied to synthesized audio (on a temp copy — the TTS cache is never mutated). |

Per-plugin settings come from the deployer configuration (mycroft.conf)
section of the same name; the lists here select which plugins are enabled.
Chains run in ascending priority order (OVOS-TRANSFORM §4). See the
[ovos-plugin-manager transformer docs](https://github.com/OpenVoiceOS/ovos-plugin-manager/blob/dev/docs/transformers.md)
for the full contract.

**When to use — and the surprise factor.** Transformers here run
server-side for *every* thin satellite: an utterance transformer means the
agent receives a different transcript than what the satellite's user said;
a tts transformer means every satellite plays back post-processed audio.
That is the tool for mesh-wide corrections, language auto-detection and a
uniform audio character — with zero client changes. Per-device effects
(denoise for one bad mic, a voice effect on one speaker) belong on the
satellite instead. Never enable the same plugin on both the satellite and
this server, or it is applied twice.

## Fallback to mycroft.conf

If the config block is omitted or the `stt` / `tts` / `vad` keys are absent,
the plugin reads the standard OVOS configuration (`mycroft.conf`) to select
plugins. This mirrors the OVOS device configuration exactly.

## Example: Piper TTS + FasterWhisper STT + Silero VAD

```json
{
  "binary_protocol": {
    "module": "hivemind-audio-binary-protocol-plugin",
    "hivemind-audio-binary-protocol-plugin": {
      "stt": {
        "module": "ovos-stt-plugin-fasterwhisper",
        "ovos-stt-plugin-fasterwhisper": {"model": "base.en"}
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

## Example: remote STT server

```json
{
  "binary_protocol": {
    "module": "hivemind-audio-binary-protocol-plugin",
    "hivemind-audio-binary-protocol-plugin": {
      "stt": {
        "module": "ovos-stt-plugin-server",
        "ovos-stt-plugin-server": {
          "url": "https://stt.openvoiceos.org"
        }
      },
      "tts": {
        "module": "ovos-tts-plugin-piper",
        "ovos-tts-plugin-piper": {"voice": "en_US-lessac-medium"}
      },
      "vad": {"module": "ovos-vad-plugin-silero"},
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

---
[← Audio flow](audio_flow.md) · [Home](../README.md) · [Operations →](operations.md)
