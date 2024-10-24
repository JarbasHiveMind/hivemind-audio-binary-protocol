import base64
import queue
import threading
from dataclasses import dataclass, field
from queue import Queue
from typing import Dict, List, Tuple, Optional, Union

import speech_recognition as sr
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.util import get_mycroft_bus
from ovos_tts_plugin_piper import PiperTTSPlugin

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType
from hivemind_core.protocol import HiveMindListenerProtocol, HiveMindClientConnection
from hivemind_core.service import HiveMindService
from ovos_dinkum_listener.service import bytes2audiodata
from ovos_dinkum_listener.voice_loop.hotwords import HotwordContainer
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_plugin_manager.templates.stt import STT
from ovos_plugin_manager.templates.transformers import AudioLanguageDetector
from ovos_plugin_manager.templates.tts import TTS
from ovos_plugin_manager.templates.vad import VADEngine
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory
from ovos_simple_listener import SimpleListener, ListenerCallbacks
from ovos_stt_plugin_whisper_turbo import WhisperTurboSTT
from ovos_utils.fakebus import FakeBus
from ovos_utils.log import LOG


class HMCallbacks(ListenerCallbacks):

    def __init__(self, bus: Optional[Union[MessageBusClient, FakeBus]] = None):
        self.bus = bus or FakeBus()

    def listen_callback(cls):
        LOG.info("New loop state: IN_COMMAND")
        cls.bus.emit(Message("mycroft.audio.play_sound",
                             {"uri": "smd/start_listening.wav"}))
        cls.bus.emit(Message("recognizer_loop:wakeword"))
        cls.bus.emit(Message("recognizer_loop:record_begin"))

    def end_listen_callback(cls):
        LOG.info("New loop state: WAITING_WAKEWORD")
        cls.bus.emit(Message("recognizer_loop:record_end"))

    def error_callback(cls, audio: sr.AudioData):
        LOG.error("STT Failure")
        cls.bus.emit(Message("recognizer_loop:speech.recognition.unknown"))

    def text_callback(cls, utterance: str, lang: str):
        LOG.info(f"STT: {utterance}")
        cls.bus.emit(Message("recognizer_loop:utterance",
                             {"utterances": utterance, "lang": lang}))


@dataclass
class FakeMicrophone(Microphone):
    queue: "Queue[Optional[bytes]]" = field(default_factory=Queue)
    _is_running: bool = False
    sample_rate: int = 16000
    sample_width: int = 2
    sample_channels: int = 1
    chunk_size: int = 4096

    def start(self):
        self._is_running = True

    def read_chunk(self) -> Optional[bytes]:
        try:
            return self.queue.get(timeout=0.5)
        except queue.Empty:
            return None
        except Exception as e:
            LOG.exception(e)
            return None

    def stop(self):
        self._is_running = False
        while not self.queue.empty():
            self.queue.get()
        self.queue.put_nowait(None)


class AudioReceiverProtocol(HiveMindListenerProtocol):
    """"""
    wakeword: str = "hey_mycroft"
    tts: TTS = PiperTTSPlugin(config={"voice": "alan-low", "lang": "en-gb"})  # OVOSTTSFactory.create()
    stt: STT = WhisperTurboSTT(config={"use_cuda": True}) # OVOSSTTFactory.create()
    vad: Optional[VADEngine] = OVOSVADFactory.create()
    hotwords: Optional[HotwordContainer] = HotwordContainer(reload_allowed=False, autoload=True)
    lang_detector: Optional[AudioLanguageDetector] = None  # TODO
    listeners: Dict[str, SimpleListener] = {}

    def add_listener(self, client: HiveMindClientConnection):
        LOG.info(f"Creating listener for peer: {client.peer}")
        bus = FakeBus()
        bus.connected_event = threading.Event()  # TODO missing in FakeBus
        bus.connected_event.set()

        def on_msg(m: str):
            m: Message = Message.deserialize(m)
            hm: HiveMessage = HiveMessage(HiveMessageType.BUS, payload=m)
            client.send(hm)
            if m.msg_type == "recognizer_loop:utterance":
                self.handle_message(hm, client)

        bus.on("message", on_msg)

        mic: Microphone = FakeMicrophone()
        AudioReceiverProtocol.listeners[client.peer] = SimpleListener(
            mic=mic,
            vad=self.vad,
            wakeword=OVOSWakeWordFactory.create_hotword(self.wakeword),
            stt=self.stt,
            callbacks=HMCallbacks(bus)
        )
        AudioReceiverProtocol.listeners[client.peer].start()

    @classmethod
    def stop_listener(cls, client: HiveMindClientConnection):
        if client.peer in AudioReceiverProtocol.listeners:
            LOG.info(f"Stopping listener for key: {client.peer}")
            AudioReceiverProtocol.listeners[client.peer].stop()
            AudioReceiverProtocol.listeners.pop(client.peer)

    def handle_handshake_message(self, message: HiveMessage, client: HiveMindClientConnection):
        super().handle_handshake_message(message, client)
        self.add_listener(client)

    def handle_client_disconnected(self, client: HiveMindClientConnection):
        super().handle_client_disconnected(client)
        self.stop_listener(client)

    @classmethod
    def set_stt(cls, stt: STT):
        cls.stt = stt

    @classmethod
    def set_tts(cls, tts: TTS):
        cls.tts = tts

    @classmethod
    def get_b64_tts(cls, message: Message = None) -> str:
        utterance = message.data['utterance']
        ctxt = cls.tts._get_ctxt({"message": message})
        wav, _ = cls.tts.synth(utterance, ctxt)
        # cast to str() to get a path, as it is a AudioFile object from tts cache
        with open(str(wav), "rb") as f:
            audio = f.read()
        return base64.b64encode(audio).decode("utf-8")

    @classmethod
    def transcribe_b64_audio(cls, message: Message = None) -> List[Tuple[str, float]]:
        b64audio = message.data["audio"]
        lang = message.data.get("lang", cls.stt.lang)
        wav_data = base64.b64decode(b64audio)
        audio = bytes2audiodata(wav_data)
        utterances = cls.stt.transcribe(audio, lang)
        return utterances

    def handle_binary_message(
            self, message: HiveMessage, client: HiveMindClientConnection
    ):
        assert message.msg_type == HiveMessageType.BINARY
        if message.bin_type == HiveMindBinaryPayloadType.RAW_AUDIO:
            bin_data = message.payload
            if client.peer in self.listeners:
                # LOG.debug(f"Got {len(bin_data)} bytes of audio data from {client.peer}")
                m: FakeMicrophone = self.listeners[client.peer].mic
                m.queue.put(bin_data)

    def handle_inject_mycroft_msg(self, message: Message, client: HiveMindClientConnection):
        """
        message (Message): mycroft bus message object
        """
        if message.msg_type == "speak:b64_audio":
            msg: Message = message.reply("speak:b64_audio.response", message.data)
            msg.data["audio"] = self.get_b64_tts(message)
            if msg.context.get("destination") is None:
                msg.context["destination"] = "audio"  # ensure not treated as a broadcast
            payload = HiveMessage(HiveMessageType.BUS, msg)
            client.send(payload)
            return
        elif message.msg_type == "recognizer_loop:b64_transcribe":
            msg: Message = message.reply("recognizer_loop:b64_transcribe.response",
                                         {"lang": message.data["lang"]})
            msg.data["transcriptions"] = self.transcribe_b64_audio(message)
            if msg.context.get("destination") is None:
                msg.context["destination"] = "skills"  # ensure not treated as a broadcast
            payload = HiveMessage(HiveMessageType.BUS, msg)
            client.send(payload)
            return
        elif message.msg_type == "recognizer_loop:b64_audio":
            transcriptions = self.transcribe_b64_audio(message)
            msg: Message = message.forward("recognizer_loop:utterance",
                                           {"utterances": [u[0] for u in transcriptions],
                                            "lang": self.stt.lang})
            super().handle_inject_mycroft_msg(msg, client)
        else:
            super().handle_inject_mycroft_msg(message, client)


def run():
    service = HiveMindService(protocol=AudioReceiverProtocol)
    service.run()


if __name__ == "__main__":
    run()
