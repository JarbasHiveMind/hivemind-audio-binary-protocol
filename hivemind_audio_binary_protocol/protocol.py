import os
import queue
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from queue import Queue
from typing import Dict, Any, List, Tuple, Optional, Union

import pybase64
from hivemind_bus_client.message import HiveMessage, HiveMessageType, HiveMindBinaryPayloadType
from hivemind_core.protocol import HiveMindClientConnection
from hivemind_plugin_manager.protocols import BinaryDataHandlerProtocol, ClientCallbacks
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session
from ovos_bus_client.util import get_message_lang
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.templates.microphone import Microphone
from ovos_plugin_manager.templates.stt import STT
from ovos_plugin_manager.templates.transformers import AudioLanguageDetector
from ovos_plugin_manager.templates.tts import TTS
from ovos_plugin_manager.templates.vad import VADEngine
from ovos_plugin_manager.tts import OVOSTTSFactory
from ovos_plugin_manager.utils.audio import AudioData
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory
from ovos_simple_listener import SimpleListener, ListenerCallbacks
from ovos_utils.fakebus import FakeBus
from ovos_utils.log import LOG

from hivemind_audio_binary_protocol.transformers import (AudioTransformersService,
                                                         DialogTransformersService,
                                                         MetadataTransformersService,
                                                         TTSTransformersService,
                                                         UtteranceTransformersService)


# The only audio format this node can process: the HIVEMIND-AUDIO-1 §2
# default, signed 16-bit PCM at 16 kHz. There is no resampling step here, so
# a payload that states anything else is rejected rather than misread (§2).
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


def _is_supported_audio_format(sample_rate: int, sample_width: int) -> bool:
    return sample_rate == SAMPLE_RATE and sample_width == SAMPLE_WIDTH


class AudioCallbacks(ListenerCallbacks):
    """
    Callbacks for handling various stages of audio recognition
    """

    def __init__(self, bus: Optional[Union[MessageBusClient, FakeBus]] = None) -> None:
        """
        Initialize AudioCallbacks with an optional message bus.
        
        Parameters:
            bus: Message bus client used to emit and receive messages. If omitted, a FakeBus bound to a new Session is created for testing or isolated use.
        """
        self.bus = bus or FakeBus(session=Session())

    def listen_callback(cls):
        """
        Signal the start of a listening session and emit bus events to begin recording.
        
        Emits a "mycroft.audio.play_sound" message to play the start-listening sound, a "recognizer_loop:wakeword" event, and a "recognizer_loop:record_begin" event; also logs the state transition to IN_COMMAND.
        """
        LOG.info("New loop state: IN_COMMAND")
        cls.bus.emit(Message("mycroft.audio.play_sound",
                             {"uri": "snd/start_listening.wav"}))
        cls.bus.emit(Message("recognizer_loop:wakeword"))
        cls.bus.emit(Message("recognizer_loop:record_begin"))

    def end_listen_callback(cls):
        """
        Signal that listening has ended and notify the message bus.
        
        Emits a "recognizer_loop:record_end" event on the instance bus and logs the transition to the WAITING_WAKEWORD state.
        """
        LOG.info("New loop state: WAITING_WAKEWORD")
        cls.bus.emit(Message("recognizer_loop:record_end"))

    def error_callback(cls, audio: AudioData):
        """
        Handle an STT error by emitting a speech recognition "unknown" event on the bus.
        
        Parameters:
            audio (AudioData): The audio that failed transcription; provided for context but not returned.
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
    sample_rate: int = SAMPLE_RATE
    sample_width: int = SAMPLE_WIDTH
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
    lang_detector: Optional[AudioLanguageDetector] = None  # legacy; AudioLanguageDetector plugins now run in the audio_transformers chain
    utterance_transformers: List[str] = field(default_factory=list)
    metadata_transformers: List[str] = field(default_factory=list)
    dialog_transformers: List[str] = field(default_factory=list)
    audio_transformers: List[str] = field(default_factory=list)
    tts_transformers: List[str] = field(default_factory=list)


@dataclass
class AudioBinaryProtocol(BinaryDataHandlerProtocol):
    """wrapper for encapsulating logic for handling incoming binary data"""
    plugins: Optional[PluginOptions] = None
    utterance_transformers: Optional[UtteranceTransformersService] = None
    metadata_transformers: Optional[MetadataTransformersService] = None
    dialog_transformers: Optional[DialogTransformersService] = None
    audio_transformers: Optional[AudioTransformersService] = None
    tts_transformers: Optional[TTSTransformersService] = None
    config: Dict[str, Any] = field(default_factory=dict)
    hm_protocol: Optional['AudioReceiverProtocol'] = None
    callbacks: Optional[ClientCallbacks] = None
    listeners = {}
    # peers currently streaming RAW_AUDIO in a format we can not process; they
    # are told once, not once per chunk (AUDIO-1 §2 — a raw stream is
    # continuous, so a per-chunk refusal would flood the peer)
    refused_streams = set()

    def __post_init__(self):
        # Configure wakeword, TTS, STT, and VAD plugins
        if self.plugins is None:

            if not self.config:
                LOG.warning("No config passed to AudioBinaryProtocol, "
                            "reading mycroft.conf to select plugins")
                # use regular mycroft.conf
                from ovos_config import Configuration
                config = Configuration()
                self.config["stt"] = config["stt"]
                self.config["tts"] = config["tts"]
                self.config["vad"] = config["listener"]["VAD"]
                self.config["wakeword"] = config["listener"]["wake_word"]
                self.config["hotwords"] = config["hotwords"]
                self.config["utterance_transformers"] = list(config.get("utterance_transformers", {}))
                self.config["dialog_transformers"] = list(config.get("dialog_transformers", {}))
                self.config["metadata_transformers"] = list(config.get("metadata_transformers", {}))
                self.config["audio_transformers"] = list(config.get("audio_transformers", {}))
                self.config["tts_transformers"] = list(config.get("tts_transformers", {}))

            LOG.debug(f"Loading STT '{self.config['stt']['module']}': {self.config['stt']}")
            stt = OVOSSTTFactory.create(self.config["stt"])
            LOG.debug(f"Loading TTS '{self.config['tts']['module']}': {self.config['tts']}")
            tts = OVOSTTSFactory.create(self.config["tts"])
            LOG.debug(f"Loading VAD '{self.config['vad']['module']}': {self.config['vad']}")
            vad = OVOSVADFactory.create(self.config["vad"])
            ww = self.config.get("wakeword") or self.config.get("wake_word") # backwards compat
          
            self.plugins = PluginOptions(
                wakeword=ww,  # TODO - allow per client
                stt=stt,
                tts=tts,
                vad=vad,
                dialog_transformers=self.config.get("dialog_transformers", []),
                utterance_transformers=self.config.get("utterance_transformers", []),
                metadata_transformers=self.config.get("metadata_transformers", []),
                audio_transformers=self.config.get("audio_transformers", []),
                tts_transformers=self.config.get("tts_transformers", [])
            )

        if self.utterance_transformers is None:
            self.utterance_transformers = UtteranceTransformersService(
                self.agent_protocol.bus, self.plugins.utterance_transformers)
        if self.dialog_transformers is None:
            self.dialog_transformers = DialogTransformersService(
                self.agent_protocol.bus, self.plugins.dialog_transformers)
        if self.metadata_transformers is None:
            self.metadata_transformers = MetadataTransformersService(
                self.agent_protocol.bus, self.plugins.metadata_transformers)
        if self.audio_transformers is None:
            self.audio_transformers = AudioTransformersService(
                self.agent_protocol.bus, self.plugins.audio_transformers)
        if self.tts_transformers is None:
            self.tts_transformers = TTSTransformersService(
                self.agent_protocol.bus, self.plugins.tts_transformers)

        # ensure client audio listener is closed when client disconnects
        if not self.callbacks:
            self.callbacks = ClientCallbacks(on_disconnect=AudioBinaryProtocol.stop_listener)
        else:
            original = self.callbacks.on_disconnect

            def wrapper(c):
                try:
                    original(c)
                except:
                    raise
                finally:
                    AudioBinaryProtocol.stop_listener(c)

            self.callbacks.on_disconnect = wrapper

        # agent protocol payloads with binary audio results
        self.agent_protocol.bus.on("recognizer_loop:b64_audio", self.handle_audio_b64)
        self.agent_protocol.bus.on("recognizer_loop:b64_transcribe", self.handle_transcribe_b64)
        self.agent_protocol.bus.on("speak:b64_audio", self.handle_speak_b64)
        self.agent_protocol.bus.on("speak:synth", self.handle_speak_synth)

    def add_listener(self, client: HiveMindClientConnection) -> None:
        """
        Register and start a per-client audio listener and message forwarder.
        
        Sets up a FakeBus bound to the client's session, attaches a message handler that forwards listener events to the client and injects
        "recognizer_loop:utterance" messages into the agent protocol, creates a SimpleListener using the configured plugins (mic, VAD,
        wakeword, STT, callbacks), stores the listener in AudioBinaryProtocol.listeners under the client's peer, and starts it.
        
        Parameters:
            client (HiveMindClientConnection): The HiveMind client connection to attach the listener to.
        """
        LOG.info(f"Creating listener for peer: {client.peer}")
        bus = FakeBus(session=client.sess)
        bus.connected_event = threading.Event()  # TODO missing in FakeBus
        bus.connected_event.set()

        def on_msg(m: str):
            m: Message = Message.deserialize(m)
            hm: HiveMessage = HiveMessage(HiveMessageType.BUS, payload=m)
            client.send(hm)  # forward listener messages to the client
            if m.msg_type == "recognizer_loop:utterance":
                self.hm_protocol.handle_message(hm, client)  # process it as if it came from the client

        bus.on("message", on_msg)

        # TODO allow different per client
        ww_cfg = self.config["hotwords"][self.plugins.wakeword]
        LOG.debug(f"Loading client Wake Word '{self.plugins.wakeword}': {ww_cfg}")

        AudioBinaryProtocol.listeners[client.peer] = SimpleListener(
            mic=FakeMicrophone(),
            vad=self.plugins.vad,
            wakeword=OVOSWakeWordFactory.create_hotword(self.plugins.wakeword, self.config["hotwords"]),
            stt=self.plugins.stt,
            callbacks=AudioCallbacks(bus)
        )
        AudioBinaryProtocol.listeners[client.peer].start()

    @classmethod
    def stop_listener(cls, client: HiveMindClientConnection) -> None:
        """
        Stop and remove a listener for a disconnected client.

        Args:
            client: The HiveMind client connection.
        """
        cls.refused_streams.discard(client.peer)
        if client.peer in cls.listeners:
            LOG.info(f"Stopping listener for key: {client.peer}")
            cls.listeners[client.peer].stop()
            cls.listeners.pop(client.peer)

    # helpers
    def transform_utterances(self, utterances: List[str], lang: str) -> Tuple[str, Dict]:
        """
        Pipe utterance through transformer plugins to get more metadata.
        Utterances may be modified by any parser and context overwritten
        """
        original = list(utterances)
        context = {}
        if utterances:
            utterances, context = self.utterance_transformers.transform(utterances, dict(lang=lang))
            if original != utterances:
                LOG.debug(f"utterances transformed: {original} -> {utterances}")
        return utterances, context

    def transform_dialogs(self, utterance: str, lang: str) -> Tuple[str, Dict]:
        """
        Pipe utterance through transformer plugins to get more metadata.
        Utterances may be modified by any parser and context overwritten
        """
        original = utterance
        context = {}
        if utterance:
            utterance, context = self.dialog_transformers.transform(utterance, dict(lang=lang))
            if original != utterance:
                LOG.debug(f"speak transformed: {original} -> {utterance}")
        return utterance, context

    def transform_audio(self, audio: AudioData) -> Tuple[AudioData, Dict]:
        """
        Pipe audio through the audio transformer chain before the STT stage.
        The returned context may carry e.g. stt_lang from an
        AudioLanguageDetector plugin in the chain.
        """
        if not self.audio_transformers.plugins:
            return audio, {}
        chunk, context = self.audio_transformers.transform(audio.frame_data)
        return AudioData(chunk, audio.sample_rate, audio.sample_width), context

    def transform_tts_audio(self, wav: str) -> str:
        """
        Pipe a synthesized file through the tts transformer chain, operating
        on a temp copy so the TTS plugin's audio cache is never mutated.
        """
        if not self.tts_transformers.plugins:
            return wav
        ext = os.path.splitext(wav)[1] or ".wav"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            tmp_path = f.name
        shutil.copy(wav, tmp_path)
        transformed, _ = self.tts_transformers.transform(tmp_path)
        return transformed

    def get_tts(self, message: Optional[Message] = None) -> str:
        """
        Generate TTS audio for the given utterance.

        Args:
            message (Message, optional): A Mycroft Message object containing the 'utterance' key.

        Returns:
            str: Path to the generated audio file.
        """
        utterance = message.data['utterance']
        ctxt = self.plugins.tts._get_ctxt({"message": message})
        wav, _ = self.plugins.tts.synth(utterance, ctxt)
        return self.transform_tts_audio(str(wav))

    def get_b64_tts(self, message: Optional[Message] = None) -> str:
        """
        Generate TTS audio and return it as a Base64-encoded string.

        Args:
            message (Message, optional): A Mycroft Message object containing the 'utterance' key.

        Returns:
            str: Base64-encoded TTS audio data.
        """
        wav = self.get_tts(message)
        # cast to str() to get a path, as it is a AudioFile object from tts cache
        with open(wav, "rb") as f:
            audio = f.read()

        s = time.monotonic()
        encoded = pybase64.b64encode(audio).decode("utf-8")
        LOG.debug(f"b64 encoding took: {time.monotonic() - s} seconds")

        return encoded

    def transcribe_b64_audio(self, message: Optional[Message] = None) -> List[Tuple[str, float]]:
        """
        Transcribe Base64-encoded audio data.

        Args:
            message (Message, optional): A Mycroft Message object containing 'audio' (Base64) and optional 'lang'.

        Returns:
            List[Tuple[str, float]]: List of transcribed utterances with confidence scores.
        """
        b64audio = message.data["audio"]
        lang = message.data.get("lang", self.plugins.stt.lang)
        sample_rate = message.data.get("sample_rate", 16000)
        sample_width = message.data.get("sample_width", 2)

        s = time.monotonic()
        wav_data = pybase64.b64decode(b64audio)
        LOG.debug(f"b64 decoding took: {time.monotonic() - s} seconds")

        audio = AudioData(wav_data, sample_rate, sample_width)
        audio, context = self.transform_audio(audio)
        if context.get("stt_lang") and not message.data.get("lang"):
            lang = context["stt_lang"]
        return self.plugins.stt.transcribe(audio, lang)

    ###############
    def handle_microphone_input(self, bin_data: bytes, sample_rate: int, sample_width: int,
                                client: HiveMindClientConnection) -> None:
        """
        Handle binary audio data input from the microphone.

        Args:
            bin_data (bytes): Raw audio data.
            sample_rate (int): Sample rate of the audio.
            sample_width (int): Sample width of the audio.
            client (HiveMindClientConnection): Connection object for the client sending the data.
        """
        if not _is_supported_audio_format(sample_rate, sample_width):
            # AUDIO-1 §2: refuse the stream instead of dropping it in silence.
            # There is no in-band format negotiation in a raw stream, so a peer
            # that is never told keeps sending into a void.
            if client.peer not in self.refused_streams:
                self.refused_streams.add(client.peer)
                self._reject_audio_format(sample_rate, sample_width, client)
            return

        self.refused_streams.discard(client.peer)
        if client.peer not in self.listeners:
            self.add_listener(client)
        m: FakeMicrophone = self.listeners[client.peer].mic
        m.queue.put(bin_data)

    def _reject_audio_format(self, sample_rate: int, sample_width: int,
                             client: HiveMindClientConnection) -> None:
        """Refuse an audio payload whose stated format this node can not process.

        HIVEMIND-AUDIO-1 §2: reject rather than misread the bytes. Transcribing
        them anyway gives the peer a plausible but wrong transcript, which is
        worse than an explicit failure.
        """
        LOG.error(f"Rejecting audio from {client.peer}: unsupported format "
                  f"({sample_rate}, {sample_width}), "
                  f"expected: ({SAMPLE_RATE}, {SAMPLE_WIDTH})")
        m = Message("recognizer_loop:speech.recognition.unknown",
                    {"error": "unsupported_audio_format",
                     "sample_rate": SAMPLE_RATE, "sample_width": SAMPLE_WIDTH})
        client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    def handle_stt_transcribe_request(self, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
                                      client: HiveMindClientConnection) -> None:
        """
        Transcribe binary audio and send a transcription response to the client.
        
        Construct an AudioData object from the provided bytes, run the configured STT engine,
        and emit a "recognizer_loop:transcribe.response" message containing the transcriptions
        and language to the specified client.
        
        Parameters:
            bin_data (bytes): Raw PCM/WAV audio bytes to transcribe.
            sample_rate (int): Sample rate used to interpret the audio bytes.
            sample_width (int): Sample width (bytes per sample) used to interpret the audio bytes.
            lang (str): Language code to pass to the STT engine.
            client (HiveMindClientConnection): Connection to send the transcription response to.
        """
        LOG.debug(f"Received binary STT input: {len(bin_data)} bytes")
        if not _is_supported_audio_format(sample_rate, sample_width):
            self._reject_audio_format(sample_rate, sample_width, client)
            return
        audio = AudioData(bin_data, sample_rate, sample_width)
        audio, context = self.transform_audio(audio)
        if context.get("stt_lang") and not lang:
            lang = context["stt_lang"]
        tx = self.plugins.stt.transcribe(audio, lang)
        m = Message("recognizer_loop:transcribe.response", {"transcriptions": tx, "lang": lang})
        client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    def handle_stt_handle_request(self, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
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
        if not _is_supported_audio_format(sample_rate, sample_width):
            self._reject_audio_format(sample_rate, sample_width, client)
            return
        audio = AudioData(bin_data, sample_rate, sample_width)
        audio, audio_context = self.transform_audio(audio)
        if audio_context.get("stt_lang") and not lang:
            lang = audio_context["stt_lang"]
        tx = self.plugins.stt.transcribe(audio, lang)
        if tx:
            utts = [t[0].rstrip(" '\"").lstrip(" '\"") for t in tx]
            utts, context = self.transform_utterances(utts, lang)
            context = {**audio_context, **context}
            context = self.metadata_transformers.transform(context)
            m = Message("recognizer_loop:utterance",
                        {"utterances": utts, "lang": lang},
                        context=context)
            self.hm_protocol.handle_inject_agent_msg(m, client)
        else:
            LOG.info(f"STT transcription error for client: {client.peer}")
            m = Message("recognizer_loop:speech.recognition.unknown")
            client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    def handle_audio_b64(self, message: Message):
        lang = get_message_lang(message)
        transcriptions = self.transcribe_b64_audio(message)
        transcriptions, context = self.transform_utterances([u[0] for u in transcriptions], lang=lang)
        context = self.metadata_transformers.transform(context)
        msg: Message = message.forward("recognizer_loop:utterance",
                                       {"utterances": transcriptions, "lang": lang})
        msg.context.update(context)
        self.hm_protocol.agent_protocol.bus.emit(msg)

    def handle_transcribe_b64(self, message: Message):
        lang = get_message_lang(message)
        client = self.hm_protocol.clients[message.context["source"]]
        msg: Message = message.reply("recognizer_loop:b64_transcribe.response",
                                     {"lang": lang})
        msg.data["transcriptions"] = self.transcribe_b64_audio(message)
        if msg.context.get("destination") is None:
            msg.context["destination"] = "skills"  # ensure not treated as a broadcast
        payload = HiveMessage(HiveMessageType.BUS, msg)

        client.send(payload)

    def handle_speak_b64(self, message: Message):
        client = self.hm_protocol.clients[message.context["source"]]

        msg: Message = message.reply("speak:b64_audio.response", message.data)
        msg.data["audio"] = self.get_b64_tts(message)
        if msg.context.get("destination") is None:
            msg.context["destination"] = "audio"  # ensure not treated as a broadcast
        payload = HiveMessage(HiveMessageType.BUS, msg)
        client.send(payload)

    def handle_speak_synth(self, message: Message):
        client = self.hm_protocol.clients[message.context["source"]]
        lang = get_message_lang(message)

        message.data["utterance"], context = self.transform_dialogs(message.data["utterance"], lang)
        wav = self.get_tts(message)
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
