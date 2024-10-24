# HiveMind Listener

Built on top of [hivemind-core](https://github.com/JarbasHiveMind/hivemind-core) and [ovos-simple-listener](https://github.com/TigreGotico/ovos-simple-listener), it extends it with additional features:
- Accepts audio streams
  - Binary data is also encrypted 
  - WakeWord, VAD, TTS and STT run on `hivemind-listener`
  - [hivemind-mic-satellite](https://github.com/JarbasHiveMind/hivemind-mic-satellite) only runs a microphone and a VAD plugin
- provides a STT service via [hivemind-websocket-client](https://github.com/JarbasHiveMind/hivemind-websocket-client) (accepts b64 encoded audio)
- provides a TTS service via [hivemind-websocket-client](https://github.com/JarbasHiveMind/hivemind-websocket-client) (returns b64 encoded audio)

running TTS/STT via hivemind has the advantage of access control, ie, requires an access key to use the plugins vs the non-authenticated server plugins

> NOTE: this should be run **instead** of `hivemind-core`, all clients are compatible 
