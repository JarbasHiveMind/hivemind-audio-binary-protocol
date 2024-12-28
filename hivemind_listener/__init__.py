import click
from ovos_config import Configuration
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.tts import OVOSTTSFactory
from ovos_plugin_manager.vad import OVOSVADFactory
from ovos_utils.xdg_utils import xdg_data_home

from hivemind_core.database import ClientDatabase
from hivemind_core.scripts import get_db_kwargs
from hivemind_websocket_protocol import HiveMindWebsocketProtocol
from hivemind_core.service import HiveMindService
from hivemind_core.protocol import HiveMindListenerProtocol
from ovos_bus_client.hpm import OVOSProtocol
from hivemind_listener.protocol import PluginOptions, AudioBinaryProtocol
from hivemind_listener.transformers import (DialogTransformersService,
                                            MetadataTransformersService,
                                            UtteranceTransformersService)


@click.command()
@click.option('--wakeword', default="hey_mycroft", type=str,
              help="Specify the wake word for the listener. Default is 'hey_mycroft'.")
@click.option('--stt-plugin', default=None, type=str, help="Specify the STT plugin to use.")
@click.option('--tts-plugin', default=None, type=str, help="Specify the TTS plugin to use.")
@click.option('--vad-plugin', default=None, type=str, help="Specify the VAD plugin to use.")
@click.option("--dialog-transformers", multiple=True, type=str,
              help=f"dialog transformer plugins to load."
                   f"Installed plugins: {DialogTransformersService.get_available_plugins() or None}")
@click.option("--utterance-transformers", multiple=True, type=str,
              help=f"utterance transformer plugins to load."
                   f"Installed plugins: {UtteranceTransformersService.get_available_plugins() or None}")
@click.option("--metadata-transformers", multiple=True, type=str,
              help=f"metadata transformer plugins to load."
                   f"Installed plugins: {MetadataTransformersService.get_available_plugins() or None}")
@click.option("--ovos_bus_address", help="Open Voice OS bus address", type=str, default="127.0.0.1")
@click.option("--ovos_bus_port", help="Open Voice OS bus port number", type=int, default=8181)
@click.option("--host", help="HiveMind host", type=str, default="0.0.0.0")
@click.option("--port", help="HiveMind port number", type=int, required=False)
@click.option("--ssl", help="use wss://", type=bool, default=False)
@click.option("--cert_dir", help="HiveMind SSL certificate directory", type=str, default=f"{xdg_data_home()}/hivemind")
@click.option("--cert_name", help="HiveMind SSL certificate file name", type=str, default="hivemind")
@click.option("--db-backend", type=click.Choice(['redis', 'json', 'sqlite'], case_sensitive=False), default='json',
              help="Select the database backend to use. Options: redis, sqlite, json.")
@click.option("--db-name", type=str, default="clients",
              help="[json/sqlite] The name for the database file. ~/.cache/hivemind-core/{name}")
@click.option("--db-folder", type=str, default="hivemind-core",
              help="[json/sqlite] The subfolder where database files are stored. ~/.cache/{db_folder}}")
@click.option("--redis-host", default="localhost", help="[redis] Host for Redis. Default is localhost.")
@click.option("--redis-port", default=6379, help="[redis] Port for Redis. Default is 6379.")
@click.option("--redis-password", required=False, help="[redis] Password for Redis. Default None")
def run_hivemind_listener(wakeword, stt_plugin, tts_plugin, vad_plugin,
                          dialog_transformers, utterance_transformers, metadata_transformers,
                          ovos_bus_address: str, ovos_bus_port: int, host: str, port: int,
                          ssl: bool, cert_dir: str, cert_name: str,
                          db_backend, db_name, db_folder,
                          redis_host, redis_port, redis_password
                          ):
    """
    Run the HiveMind Listener with configurable plugins.

    If a plugin is not specified, the defaults from mycroft.conf will be used.

    mycroft.conf will be loaded as usual for plugin settings
    """
    kwargs = get_db_kwargs(db_backend, db_name, db_folder, redis_host, redis_port, redis_password)
    ovos_bus_config = {
        "host": ovos_bus_address or "127.0.0.1",
        "port": ovos_bus_port or 8181,
    }

    websocket_config = {
        "host": host,
        "port": port or 5678,
        "ssl": ssl or False,
        "cert_dir": cert_dir,
        "cert_name": cert_name,
    }

    # Configure wakeword, TTS, STT, and VAD plugins
    config = Configuration()
    if stt_plugin:
        config["stt"]["module"] = stt_plugin
    if tts_plugin:
        config["tts"]["module"] = tts_plugin
    if vad_plugin:
        config["listener"]["VAD"]["module"] = vad_plugin

    AudioBinaryProtocol.plugins = PluginOptions(
        wakeword=wakeword,
        stt=OVOSSTTFactory.create(config),
        tts=OVOSTTSFactory.create(config),
        vad=OVOSVADFactory.create(config),
        dialog_transformers=dialog_transformers,
        utterance_transformers=utterance_transformers,
        metadata_transformers=metadata_transformers
    )

    # Start the service
    click.echo(f"Starting HiveMind Listener with wakeword '{wakeword}'...")
    service = HiveMindService(agent_protocol=OVOSProtocol,
                              agent_config=ovos_bus_config,
                              network_protocol=HiveMindWebsocketProtocol,
                              network_config=websocket_config,
                              hm_protocol=HiveMindListenerProtocol,
                              binary_data_protocol=AudioBinaryProtocol,
                              db=ClientDatabase(**kwargs))

    service.run()


if __name__ == "__main__":
    run_hivemind_listener()
