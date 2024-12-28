import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from queue import Queue
from shutil import which
from tempfile import NamedTemporaryFile
from typing import Dict
from typing import List, Tuple, Optional, Union

import pybase64
import speech_recognition as sr
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.util import get_message_lang
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_plugin_manager.templates.stt import STT
from ovos_plugin_manager.templates.transformers import AudioLanguageDetector
from ovos_plugin_manager.templates.tts import TTS
from ovos_plugin_manager.templates.vad import VADEngine
from ovos_plugin_manager.tts import OVOSTTSFactory
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory
from ovos_simple_listener import SimpleListener, ListenerCallbacks
from ovos_utils.fakebus import FakeBus
from ovos_utils.log import LOG

from hivemind_bus_client.message import HiveMessage, HiveMessageType, HiveMindBinaryPayloadType
from hivemind_core.agents import OVOSProtocol
from hivemind_core.protocol import HiveMindListenerProtocol, HiveMindClientConnection
from hivemind_listener.transformers import (DialogTransformersService,
                                            MetadataTransformersService,
                                            UtteranceTransformersService)


def bytes2audiodata(data: bytes) -> sr.AudioData:
    """
    Convert raw audio bytes into `speech_recognition.AudioData`.

    Args:
        data: Raw audio bytes.

    Returns:
        An AudioData object representing the audio data.
    """
    recognizer = sr.Recognizer()
    with NamedTemporaryFile() as fp:
        fp.write(data)
        ffmpeg = which("ffmpeg")
        if ffmpeg:
            p = fp.name + "converted.wav"
            # ensure file format
            cmd = [ffmpeg, "-i", fp.name, "-acodec", "pcm_s16le", "-ar",
                   "16000", "-ac", "1", "-f", "wav", p, "-y"]
            subprocess.call(cmd)
        else:
            LOG.warning("ffmpeg not found, please ensure audio is in a valid format")
            p = fp.name

        with sr.AudioFile(p) as source:
            audio = recognizer.record(source)
    return audio


class HMCallbacks(ListenerCallbacks):
    """
    Callbacks for handling various stages of audio recognition
    """

    def __init__(self, bus: Optional[Union[MessageBusClient, FakeBus]] = None) -> None:
        """
        Initialize the HiveMind Callbacks.

        Args:
            bus: The message bus client or a FakeBus for testing.
        """
        self.bus = bus or FakeBus()

    def listen_callback(cls):
        """
        Callback triggered when listening starts.
        """
        LOG.info("New loop state: IN_COMMAND")
        cls.bus.emit(Message("mycroft.audio.play_sound",
                             {"uri": "snd/start_listening.wav"}))
        cls.bus.emit(Message("recognizer_loop:wakeword"))
        cls.bus.emit(Message("recognizer_loop:record_begin"))

    def end_listen_callback(cls):
        """
        Callback triggered when listening ends.
        """
        LOG.info("New loop state: WAITING_WAKEWORD")
        cls.bus.emit(Message("recognizer_loop:record_end"))

    def error_callback(cls, audio: sr.AudioData):
        """
        Callback triggered when an error occurs during STT processing.

        Args:
            audio: The audio data that caused the error.
        """
        LOG.error("STT Failure")
        cls.bus.emit(Message("recognizer_loop:speech.recognition.unknown"))

    def text_callback(cls, utterance: str, lang: str):
        """
        Callback triggered when text is successfully transcribed.

        Args:
            utterance: The transcribed text.
            lang: The language of the transcription.
        """
        LOG.info(f"STT: {utterance}")
        cls.bus.emit(Message("recognizer_loop:utterance",
                             {"utterances": [utterance], "lang": lang}))


@dataclass
class FakeMicrophone(Microphone):
    """
    A async implementation of a Microphone from a client connection.
    """
    queue: "Queue[Optional[bytes]]" = field(default_factory=Queue)
    _is_running: bool = False
    sample_rate: int = 16000
    sample_width: int = 2
    sample_channels: int = 1
    chunk_size: int = 4096

    def start(self) -> None:
        """
        Start the microphone
        """
        self._is_running = True

    def read_chunk(self) -> Optional[bytes]:
        """
        Read a chunk of audio data from the queue.

        Returns:
            A chunk of audio data or None if the queue is empty.
        """
        try:
            return self.queue.get(timeout=0.5)
        except queue.Empty:
            return None
        except Exception as e:
            LOG.exception(e)
            return None

    def stop(self) -> None:
        """
        Stop the microphone
        """
        self._is_running = False
        while not self.queue.empty():
            self.queue.get()
        self.queue.put_nowait(None)


@dataclass
class PluginOptions:
    """
    Configuration for plugins used in the listener.
    """
    wakeword: str = "hey_mycroft"
    tts: TTS = field(default_factory=OVOSTTSFactory.create)
    stt: STT = field(default_factory=OVOSSTTFactory.create)
    vad: VADEngine = field(default_factory=OVOSVADFactory.create)
    lang_detector: Optional[AudioLanguageDetector] = None  # TODO: Implement language detection.
    utterance_transformers: List[str] = field(default_factory=list)
    metadata_transformers: List[str] = field(default_factory=list)
    dialog_transformers: List[str] = field(default_factory=list)


class AudioBinaryProtocol:
    """wrapper for encapsulating logic for handling incoming binary data"""
    plugins: Optional[PluginOptions] = None
    utterance_transformers: Optional[UtteranceTransformersService] = None
    metadata_transformers: Optional[MetadataTransformersService] = None
    dialog_transformers: Optional[DialogTransformersService] = None

    hm_protocol: Optional['AudioReceiverProtocol'] = None
    listeners = {}

    @classmethod
    def add_listener(cls, client: HiveMindClientConnection) -> None:
        """
        Create and start a new listener for a connected client.

        Args:
            client: The HiveMind client connection.
        """
        LOG.info(f"Creating listener for peer: {client.peer}")
        bus = FakeBus()
        bus.connected_event = threading.Event()  # TODO missing in FakeBus
        bus.connected_event.set()

        def on_msg(m: str):
            m: Message = Message.deserialize(m)
            hm: HiveMessage = HiveMessage(HiveMessageType.BUS, payload=m)
            client.send(hm)  # forward listener messages to the client
            if m.msg_type == "recognizer_loop:utterance":
                cls.hm_protocol.handle_message(hm, client)  # process it as if it came from the client

        bus.on("message", on_msg)

        cls.listeners[client.peer] = SimpleListener(
            mic=FakeMicrophone(),
            vad=cls.plugins.vad,
            wakeword=OVOSWakeWordFactory.create_hotword(cls.plugins.wakeword),  # TODO allow different per client
            stt=cls.plugins.stt,
            callbacks=HMCallbacks(bus)
        )
        cls.listeners[client.peer].start()

    @classmethod
    def stop_listener(cls, client: HiveMindClientConnection) -> None:
        """
        Stop and remove a listener for a disconnected client.

        Args:
            client: The HiveMind client connection.
        """
        if client.peer in cls.listeners:
            LOG.info(f"Stopping listener for key: {client.peer}")
            cls.listeners[client.peer].stop()
            cls.listeners.pop(client.peer)

    @classmethod
    # helpers
    def transform_utterances(cls, utterances: List[str], lang: str) -> Tuple[str, Dict]:
        """
        Pipe utterance through transformer plugins to get more metadata.
        Utterances may be modified by any parser and context overwritten
        """
        original = list(utterances)
        context = {}
        if utterances:
            utterances, context = cls.utterance_transformers.transform(utterances, dict(lang=lang))
            if original != utterances:
                LOG.debug(f"utterances transformed: {original} -> {utterances}")
        return utterances, context

    @classmethod
    def transform_dialogs(cls, utterance: str, lang: str) -> Tuple[str, Dict]:
        """
        Pipe utterance through transformer plugins to get more metadata.
        Utterances may be modified by any parser and context overwritten
        """
        original = utterance
        context = {}
        if utterance:
            utterance, context = cls.dialog_transformers.transform(utterance, dict(lang=lang))
            if original != utterance:
                LOG.debug(f"speak transformed: {original} -> {utterance}")
        return utterance, context

    @staticmethod
    def get_tts(message: Optional[Message] = None) -> str:
        """
        Generate TTS audio for the given utterance.

        Args:
            message (Message, optional): A Mycroft Message object containing the 'utterance' key.

        Returns:
            str: Path to the generated audio file.
        """
        utterance = message.data['utterance']
        ctxt = AudioBinaryProtocol.plugins.tts._get_ctxt({"message": message})
        wav, _ = AudioBinaryProtocol.plugins.tts.synth(utterance, ctxt)
        return str(wav)

    @classmethod
    def get_b64_tts(cls, message: Optional[Message] = None) -> str:
        """
        Generate TTS audio and return it as a Base64-encoded string.

        Args:
            message (Message, optional): A Mycroft Message object containing the 'utterance' key.

        Returns:
            str: Base64-encoded TTS audio data.
        """
        wav = cls.get_tts(message)
        # cast to str() to get a path, as it is a AudioFile object from tts cache
        with open(wav, "rb") as f:
            audio = f.read()

        s = time.monotonic()
        encoded = pybase64.b64encode(audio).decode("utf-8")
        LOG.debug(f"b64 encoding took: {time.monotonic() - s} seconds")

        return encoded

    @staticmethod
    def transcribe_b64_audio(message: Optional[Message] = None) -> List[Tuple[str, float]]:
        """
        Transcribe Base64-encoded audio data.

        Args:
            message (Message, optional): A Mycroft Message object containing 'audio' (Base64) and optional 'lang'.

        Returns:
            List[Tuple[str, float]]: List of transcribed utterances with confidence scores.
        """
        b64audio = message.data["audio"]
        lang = message.data.get("lang", AudioBinaryProtocol.plugins.stt.lang)

        s = time.monotonic()
        wav_data = pybase64.b64decode(b64audio)
        LOG.debug(f"b64 decoding took: {time.monotonic() - s} seconds")

        audio = bytes2audiodata(wav_data)
        return AudioBinaryProtocol.plugins.stt.transcribe(audio, lang)

    ###############
    @classmethod
    def handle_microphone_input(cls, bin_data: bytes, sample_rate: int, sample_width: int,
                                client: HiveMindClientConnection) -> None:
        """
        Handle binary audio data input from the microphone.

        Args:
            bin_data (bytes): Raw audio data.
            sample_rate (int): Sample rate of the audio.
            sample_width (int): Sample width of the audio.
            client (HiveMindClientConnection): Connection object for the client sending the data.
        """
        if client.peer not in cls.listeners:
            cls.add_listener(client)
        m: FakeMicrophone = cls.listeners[client.peer].mic
        if m.sample_rate != sample_rate or m.sample_width != sample_width:
            LOG.debug(f"Got {len(bin_data)} bytes of audio data from {client.peer}")
            LOG.error(f"Sample rate/width mismatch! Got: ({sample_rate}, {sample_width}), "
                      f"expected: ({m.sample_rate}, {m.sample_width})")
            # TODO - convert sample_rate if needed
        else:
            m.queue.put(bin_data)

    @classmethod
    def handle_stt_transcribe_request(cls, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
                                      client: HiveMindClientConnection) -> None:
        """
        Handle STT transcription request from binary audio data.

        Args:
            bin_data (bytes): Raw audio data.
            sample_rate (int): Sample rate of the audio.
            sample_width (int): Sample width of the audio.
            lang (str): Language of the audio.
            client (HiveMindClientConnection): Connection object for the client sending the data.
        """
        LOG.debug(f"Received binary STT input: {len(bin_data)} bytes")
        audio = sr.AudioData(bin_data, sample_rate, sample_width)
        tx = cls.plugins.stt.transcribe(audio, lang)
        m = Message("recognizer_loop:transcribe.response", {"transcriptions": tx, "lang": lang})
        client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    @classmethod
    def handle_stt_handle_request(cls, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
                                  client: HiveMindClientConnection) -> None:
        """
        Handle STT utterance transcription and injection into the message bus.

        Args:
            bin_data (bytes): Raw audio data.
            sample_rate (int): Sample rate of the audio.
            sample_width (int): Sample width of the audio.
            lang (str): Language of the audio.
            client (HiveMindClientConnection): Connection object for the client sending the data.
        """
        LOG.debug(f"Received binary STT input: {len(bin_data)} bytes")
        audio = sr.AudioData(bin_data, sample_rate, sample_width)
        tx = cls.plugins.stt.transcribe(audio, lang)
        if tx:
            utts = [t[0].rstrip(" '\"").lstrip(" '\"") for t in tx]
            utts, context = cls.transform_utterances(utts, lang)
            context = cls.metadata_transformers.transform(context)
            m = Message("recognizer_loop:utterance",
                        {"utterances": utts, "lang": lang},
                        context=context)
            cls.hm_protocol.handle_inject_agent_msg(m, client)
        else:
            LOG.info(f"STT transcription error for client: {client.peer}")
            m = Message("recognizer_loop:speech.recognition.unknown")
            client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    @classmethod
    def handle_audio_b64(cls, message: Message):
        lang = get_message_lang(message)
        transcriptions = AudioBinaryProtocol.transcribe_b64_audio(message)
        transcriptions, context = AudioBinaryProtocol.transform_utterances([u[0] for u in transcriptions], lang=lang)
        context = AudioBinaryProtocol.metadata_transformers.transform(context)
        msg: Message = message.forward("recognizer_loop:utterance",
                                       {"utterances": transcriptions, "lang": lang})
        msg.context.update(context)
        cls.hm_protocol.agent_protocol.bus.emit(msg)

    @classmethod
    def handle_transcribe_b64(cls, message: Message):
        lang = get_message_lang(message)
        client = cls.hm_protocol.clients[message.context["source"]]
        msg: Message = message.reply("recognizer_loop:b64_transcribe.response",
                                     {"lang": lang})
        msg.data["transcriptions"] = AudioBinaryProtocol.transcribe_b64_audio(message)
        if msg.context.get("destination") is None:
            msg.context["destination"] = "skills"  # ensure not treated as a broadcast
        payload = HiveMessage(HiveMessageType.BUS, msg)

        client.send(payload)

    @classmethod
    def handle_speak_b64(cls, message: Message):
        client = cls.hm_protocol.clients[message.context["source"]]

        msg: Message = message.reply("speak:b64_audio.response", message.data)
        msg.data["audio"] = AudioBinaryProtocol.get_b64_tts(message)
        if msg.context.get("destination") is None:
            msg.context["destination"] = "audio"  # ensure not treated as a broadcast
        payload = HiveMessage(HiveMessageType.BUS, msg)
        client.send(payload)

    @classmethod
    def handle_speak_synth(cls, message: Message):
        client = cls.hm_protocol.clients[message.context["source"]]
        lang = get_message_lang(message)

        message.data["utterance"], context = AudioBinaryProtocol.transform_dialogs(message.data["utterance"], lang)
        wav = AudioBinaryProtocol.get_tts(message)
        with open(wav, "rb") as f:
            bin_data = f.read()
        metadata = {"lang": lang,
                    "file_name": wav.split("/")[-1],
                    "utterance": message.data["utterance"]}
        metadata.update(context)
        payload = HiveMessage(HiveMessageType.BINARY,
                              payload=bin_data,
                              metadata=metadata,
                              bin_type=HiveMindBinaryPayloadType.TTS_AUDIO)
        client.send(payload)


class AudioReceiverProtocol(HiveMindListenerProtocol):
    def __post_init__(self):
        AudioBinaryProtocol.hm_protocol = self
        if AudioBinaryProtocol.plugins is None:
            AudioBinaryProtocol.plugins = PluginOptions()
        if AudioBinaryProtocol.utterance_transformers is None:
            AudioBinaryProtocol.utterance_transformers = UtteranceTransformersService(
                self.agent_protocol.bus, AudioBinaryProtocol.plugins.utterance_transformers)
        if AudioBinaryProtocol.dialog_transformers is None:
            AudioBinaryProtocol.dialog_transformers = DialogTransformersService(
                self.agent_protocol.bus, AudioBinaryProtocol.plugins.dialog_transformers)
        if AudioBinaryProtocol.metadata_transformers is None:
            AudioBinaryProtocol.metadata_transformers = MetadataTransformersService(
                self.agent_protocol.bus, AudioBinaryProtocol.plugins.metadata_transformers)
        # agent protocol payloads with binary audio results
        self.agent_protocol.bus.on("recognizer_loop:b64_audio", AudioBinaryProtocol.handle_audio_b64)
        self.agent_protocol.bus.on("recognizer_loop:b64_transcribe", AudioBinaryProtocol.handle_transcribe_b64)
        self.agent_protocol.bus.on("speak:b64_audio", AudioBinaryProtocol.handle_speak_b64)
        self.agent_protocol.bus.on("speak:synth", AudioBinaryProtocol.handle_speak_synth)

    ########
    # binary data protocol handlers
    def handle_client_disconnected(self, client: HiveMindClientConnection) -> None:
        super().handle_client_disconnected(client)
        AudioBinaryProtocol.stop_listener(client)

    def handle_microphone_input(self, bin_data: bytes, sample_rate: int, sample_width: int,
                                client: HiveMindClientConnection) -> None:
        AudioBinaryProtocol.handle_microphone_input(bin_data, sample_rate, sample_width, client)

    def handle_stt_transcribe_request(self, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
                                      client: HiveMindClientConnection) -> None:
        AudioBinaryProtocol.handle_stt_transcribe_request(bin_data, sample_rate, sample_width, lang, client)

    def handle_stt_handle_request(self, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
                                  client: HiveMindClientConnection) -> None:
        AudioBinaryProtocol.handle_stt_handle_request(bin_data, sample_rate, sample_width, lang, client)
