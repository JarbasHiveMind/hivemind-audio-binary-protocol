import base64
import queue
import subprocess
import threading
from dataclasses import dataclass, field
from queue import Queue
from shutil import which
from tempfile import NamedTemporaryFile
from typing import Dict, List, Tuple, Optional, Union

import click
import speech_recognition as sr
from ovos_bus_client import MessageBusClient
from ovos_bus_client.message import Message
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
from hivemind_core.protocol import HiveMindListenerProtocol, HiveMindClientConnection
from hivemind_core.service import HiveMindService


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


class AudioReceiverProtocol(HiveMindListenerProtocol):
    """
    Protocol for receiving and processing audio data in HiveMind.
    """
    listeners: Dict[str, SimpleListener] = {}
    plugin_opts: PluginOptions = None

    @property
    def plugins(self) -> PluginOptions:
        """
        Lazily load and return the plugin options.

        Returns:
            The loaded PluginOptions instance.
        """
        if not self.plugin_opts:
            # lazy load
            self.plugin_opts = PluginOptions()
        return self.plugin_opts

    def add_listener(self, client: HiveMindClientConnection) -> None:
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
                self.handle_message(hm, client)  # process it as if it came from the client

        bus.on("message", on_msg)

        AudioReceiverProtocol.listeners[client.peer] = SimpleListener(
            mic=FakeMicrophone(),
            vad=self.plugins.vad,
            wakeword=OVOSWakeWordFactory.create_hotword(self.plugins.wakeword),  # TODO allow different per client
            stt=self.plugins.stt,
            callbacks=HMCallbacks(bus)
        )
        AudioReceiverProtocol.listeners[client.peer].start()

    @classmethod
    def stop_listener(cls, client: HiveMindClientConnection) -> None:
        """
        Stop and remove a listener for a disconnected client.

        Args:
            client: The HiveMind client connection.
        """
        if client.peer in AudioReceiverProtocol.listeners:
            LOG.info(f"Stopping listener for key: {client.peer}")
            AudioReceiverProtocol.listeners[client.peer].stop()
            AudioReceiverProtocol.listeners.pop(client.peer)

    def handle_client_disconnected(self, client: HiveMindClientConnection) -> None:
        """
        Handle a client disconnection event.

        Args:
            client: The HiveMind client connection.
        """
        super().handle_client_disconnected(client)
        self.stop_listener(client)

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
        return str(wav)

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
        return base64.b64encode(audio).decode("utf-8")

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
        wav_data = base64.b64decode(b64audio)
        audio = bytes2audiodata(wav_data)
        utterances = self.plugins.stt.transcribe(audio, lang)
        return utterances

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
        if client.peer not in self.listeners:
            self.add_listener(client)
        m: FakeMicrophone = self.listeners[client.peer].mic
        if m.sample_rate != sample_rate or m.sample_width != sample_width:
            LOG.debug(f"Got {len(bin_data)} bytes of audio data from {client.peer}")
            LOG.error(f"Sample rate/width mismatch! Got: ({sample_rate}, {sample_width}), "
                      f"expected: ({m.sample_rate}, {m.sample_width})")
            # TODO - convert sample_rate if needed
        else:
            m.queue.put(bin_data)

    def handle_stt_transcribe_request(self, bin_data: bytes, sample_rate: int, sample_width: int, lang: str,
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
        audio = sr.AudioData(bin_data, sample_rate, sample_width)
        tx = self.plugins.stt.transcribe(audio, lang)
        if tx:
            utts = [t[0].rstrip(" '\"").lstrip(" '\"") for t in tx]
            m = Message("recognizer_loop:utterance",
                        {"utterances": utts, "lang": lang})
            self.handle_inject_mycroft_msg(m, client)
        else:
            LOG.info(f"STT transcription error for client: {client.peer}")
            m = Message("recognizer_loop:speech.recognition.unknown")
            client.send(HiveMessage(HiveMessageType.BUS, payload=m))

    def handle_inject_mycroft_msg(self, message: Message, client: HiveMindClientConnection) -> None:
        """
        Handle injection of Mycroft bus messages into the HiveMind system.

        Args:
            message (Message): Mycroft bus message object.
            client (HiveMindClientConnection): Connection object for the client receiving the response.
        """
        if message.msg_type == "speak:synth":
            wav = self.get_tts(message)
            with open(wav, "rb") as f:
                bin_data = f.read()
            payload = HiveMessage(HiveMessageType.BINARY,
                                  payload=bin_data,
                                  metadata={"lang": message.data["lang"],
                                            "file_name": wav.split("/")[-1],
                                            "utterance": message.data["utterance"]},
                                  bin_type=HiveMindBinaryPayloadType.TTS_AUDIO)
            client.send(payload)
            return
        elif message.msg_type == "speak:b64_audio":
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


@click.command()
@click.option(
    '--wakeword', default="hey_mycroft",
    help="Specify the wake word for the listener. Default is 'hey_mycroft'. config will be loaded from mycroft.conf."
)
@click.option(
    '--stt-plugin', default=None,
    help="Specify the STT plugin to use. If not provided, the default STT from mycroft.conf will be used."
)
@click.option(
    '--tts-plugin', default=None,
    help="Specify the TTS plugin to use. If not provided, the default TTS from mycroft.conf will be used."
)
@click.option(
    '--vad-plugin', default=None,
    help="Specify the VAD plugin to use. If not provided, the default VAD from mycroft.conf will be used."
)
def run_hivemind_listener(wakeword, stt_plugin, tts_plugin, vad_plugin):
    """
    Run the HiveMind Listener with configurable plugins.
    """
    # Configure wakeword, TTS, STT, and VAD plugins
    AudioReceiverProtocol.plugin_opts = PluginOptions(
        wakeword=wakeword,
        stt=OVOSSTTFactory.create(stt_plugin) if stt_plugin else OVOSSTTFactory.create(),
        tts=OVOSTTSFactory.create(tts_plugin) if tts_plugin else OVOSTTSFactory.create(),
        vad=OVOSVADFactory.create(vad_plugin) if vad_plugin else OVOSVADFactory.create()
    )

    # Start the service
    click.echo(f"Starting HiveMind Listener with wakeword '{wakeword}'...")
    service = HiveMindService(protocol=AudioReceiverProtocol)
    service.run()


if __name__ == "__main__":
    run_hivemind_listener()
