# HiveMind Listener

HiveMind Listener extends [hivemind-core](https://github.com/JarbasHiveMind/hivemind-core) and integrates with [ovos-simple-listener](https://github.com/TigreGotico/ovos-simple-listener), enabling audio-based communication with advanced features for **secure, distributed voice assistant functionality**.

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


> 💡 **Tip**: HiveMind Listener replaces `hivemind-core` and is compatible with all existing HiveMind clients.

---

## 🚀 Getting Started

### Installation

```bash
pip install hivemind-listener
```

---

## 🛠️ Commands Overview

```bash
$ hivemind-listener --help
Usage: hivemind-listener [OPTIONS] 

  Run the HiveMind Listener with configurable plugins.

Options:
  --wakeword TEXT    Specify the wake word for the listener. Default is
                     'hey_mycroft'. config will be loaded from mycroft.conf.
  --stt-plugin TEXT  Specify the STT plugin to use. If not provided, the
                     default STT from mycroft.conf will be used.
  --tts-plugin TEXT  Specify the TTS plugin to use. If not provided, the
                     default TTS from mycroft.conf will be used.
  --vad-plugin TEXT  Specify the VAD plugin to use. If not provided, the
                     default VAD from mycroft.conf will be used.
  --help             Show this message and exit.
```

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