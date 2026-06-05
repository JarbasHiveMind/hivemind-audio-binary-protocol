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
      "metadata_transformers":  []
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
