# HiveMind Listener

Built on top of [hivemind-core](https://github.com/JarbasHiveMind/hivemind-core), it extends it with additional features:
- Accepts audio streams
  - Binary data is also encrypted 
  - WakeWord, VAD and STT run on `hivemind-listener`
  - [hivemind-mic-satellite](https://github.com/JarbasHiveMind/hivemind-mic-satellite) only runs a microphone and a VAD plugin
- provides a STT service via messagebus (accepts b64 encoded audio)
- provides a TTS service via messagebus (returns b64 encoded audio)

running TTS/STT via hivemind has the advantage of access control, ie, requires an access key to use the plugins vs the non-authenticated server plugins

> NOTE: this should be run **instead** of `hivemind-core`, all clients are compatible 