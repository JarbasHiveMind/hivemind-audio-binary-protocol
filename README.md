# HiveMind Audio Binary Protocol Plugin

Extends [hivemind-core](https://github.com/JarbasHiveMind/hivemind-core) and integrates with [ovos-simple-listener](https://github.com/TigreGotico/ovos-simple-listener), enabling audio-based communication with advanced features for **secure, distributed voice assistant functionality**.

---

## 🌟 Key Features

- **Audio Stream Handling**:  
  Accepts encrypted binary audio streams, performing **WakeWord detection**, **Voice Activity Detection (VAD)**, **Speech-to-Text (STT)**, and **Text-to-Speech (TTS)** directly on the `hivemind-listener` instance.  
  *(Lightweight clients like [hivemind-mic-satellite](https://github.com/JarbasHiveMind/hivemind-mic-satellite) only run a microphone and VAD plugin.)*

- **STT Service**:  
  Provides **STT** via the [hivemind-websocket-client](https://github.com/JarbasHiveMind/hivemind-websocket-client), accepting Base64-encoded audio inputs.

- **TTS Service**:  
  Provides **TTS** via the [hivemind-websocket-client](https://github.com/JarbasHiveMind/hivemind-websocket-client), returning Base64-encoded audio outputs.

- **Secure Plugin Access**:  
  Running **TTS/STT via HiveMind Listener** requires an access key, offering fine-grained **access control** compared to non-authenticated server plugins.


> 💡 **Tip**: `hivemind-audio-binary-protocol` is a plugin for `hivemind-core` and is compatible with all existing HiveMind clients.

---

## 🚀 Getting Started

### Installation

```bash
pip install hivemind-audio-binary-protocol
```

## Configuration

In your hivemind `server.json` set `"binary_protocol"` module to `"hivemind-audio-binary-protocol-plugin"` and configure the plugins to be used like you would in OVOS

```json
  "binary_protocol": {"module": "hivemind-audio-binary-protocol-plugin",
                      "hivemind-audio-binary-protocol-plugin": {
                          "stt": {"module": "XXX-plugin", "XXX-plugin":{}},
                          "tts": {"module": "XXX-plugin", "XXX-plugin":{}},
                          "vad": {"module": "XXX-plugin", "XXX-plugin":{}},
                          "wake_word": "hey_mycroft",
                          "hotwords": {
                              "hey_mycroft": {
                                  "module": "ovos-ww-plugin-precise-lite",
                                  "model": "https://github.com/OpenVoiceOS/precise-lite-models/raw/master/wakewords/en/hey_mycroft.tflite"
                              }
                          }
                      }},
```

If installed and configured correctly the audio binary protocol will be used when you run `hivemind-core listen`


---

## 🌐 Example Use Cases

1. **Microphone Satellite**: Use [hivemind-mic-satellite](https://github.com/JarbasHiveMind/hivemind-mic-satellite) to stream raw audio to the `hivemind-listener`.  
   > Microphones handle audio capture and VAD, while the Listener manages WakeWord, STT, and TTS processing.

2. **Authenticated STT/TTS Services**: Connect clients securely using access keys for transcribing or synthesizing audio via the HiveMind Listener, ensuring robust access control.

---

## 🤝 Contributing

We welcome contributions!

---

## ⚖️ License

HiveMind Listener is open-source software, licensed under the [Apache 2.0 License](LICENSE).
