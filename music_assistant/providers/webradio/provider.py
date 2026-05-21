"""Web Radio Broadcast player provider implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiohttp import web
from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import (
    ConfigEntryType,
    PlaybackState,
    PlayerFeature,
    ProviderFeature,
)
from music_assistant_models.errors import SetupFailedError
from music_assistant_models.player import DeviceInfo, PlayerMedia

from music_assistant.constants import (
    CONF_ENTRY_ENABLE_ICY_METADATA,
    CONF_ENTRY_OUTPUT_CODEC_DEFAULT_MP3,
    CONF_OUTPUT_CODEC,
    DEFAULT_STREAM_HEADERS,
    DLNA_CONTENT_FEATURES_REALTIME,
    ICY_HEADERS,
)
from music_assistant.helpers.audio import (
    format_icy_metadata_frame,
    get_mime_type,
)
from music_assistant.helpers.ffmpeg import get_ffmpeg_stream
from music_assistant.models.player import Player
from music_assistant.models.player_provider import PlayerProvider

from .constants import (
    CONF_STATIONS,
    CONF_STREAM_URL_LABEL,
    PLAYER_ID_PREFIX,
    URL_PATH_PREFIX,
)
from .helpers import slugify_station_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.player_queue import PlayerQueue
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant


_ICY_FULL_INTERVAL: int = 256_000
_ICY_STANDARD_INTERVAL: int = 16_384


@dataclass
class _Station:
    """Runtime registration data for a single web radio station."""

    slug: str
    player_id: str
    player: WebRadioPlayer
    url_path: str
    unregister_route: Callable[[], None]
    listener_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WebRadioProvider(PlayerProvider):
    """
    Provider that publishes one HTTP audio stream per configured "station".

    Each station appears in Music Assistant as a virtual player with its own
    queue. Dumb internet-radio devices (or VLC, a browser, etc.) tune in by
    pointing at the station's HTTP URL; whatever the station's queue is
    currently playing becomes the audio broadcast on that URL.

    A station serves one HTTP listener at a time. To stream the same content
    to multiple devices, create one station per device.
    """

    def __init__(
        self,
        mass: MusicAssistant,
        manifest: ProviderManifest,
        config: ProviderConfig,
        supported_features: set[ProviderFeature] | None = None,
    ) -> None:
        """
        Initialize the provider.

        :param mass: The owning Music Assistant instance.
        :param manifest: Loaded provider manifest.
        :param config: This provider instance's config.
        :param supported_features: Optional feature set; falls back to the
            features declared in the module-level setup helper.
        """
        super().__init__(mass, manifest, config, supported_features)
        self._stations: dict[str, _Station] = {}

    async def loaded_in_mass(self) -> None:
        """Register players and HTTP routes for all configured stations."""
        await self._sync_stations()

    async def unload(self, is_removed: bool = False) -> None:
        """
        Unregister all stations on provider unload.

        :param is_removed: True when the provider instance is being removed.
        """
        del is_removed
        for station in list(self._stations.values()):
            station.unregister_route()
            await self.mass.players.unregister(station.player_id)
        self._stations.clear()

    async def remove_player(self, player_id: str) -> None:
        """
        Remove a station and persist its removal in the provider config.

        :param player_id: MA player_id of the station to remove.
        """
        station = next(
            (s for s in self._stations.values() if s.player_id == player_id),
            None,
        )
        if station is None:
            await self.mass.players.unregister(player_id, True)
            return

        raw_names = self._raw_station_names()
        new_names = [name for name in raw_names if slugify_station_name(name) != station.slug]
        if new_names != raw_names:
            self._update_config_value(CONF_STATIONS, new_names)

        station.unregister_route()
        del self._stations[station.slug]
        await self.mass.players.unregister(player_id, True)

    # ---------------------------------------------------------------
    # Station / route lifecycle
    # ---------------------------------------------------------------

    def _raw_station_names(self) -> list[str]:
        """Return the raw list of station names from provider config."""
        value = self.config.get_value(CONF_STATIONS)
        if not isinstance(value, list):
            return []
        return [str(name) for name in value if str(name).strip()]

    async def _sync_stations(self) -> None:
        """
        Reconcile registered stations with the provider config.

        Stations whose slug disappears from config are unregistered; new
        stations are created. Existing stations are left untouched.
        """
        configured: dict[str, str] = {}
        for name in self._raw_station_names():
            slug = slugify_station_name(name)
            if not slug:
                self.logger.warning(
                    "Ignoring station %r: name has no alphanumeric characters", name
                )
                continue
            if slug in configured:
                raise SetupFailedError(
                    f"Duplicate station slug {slug!r} from name {name!r}; "
                    "rename one of the stations so each has a unique URL."
                )
            configured[slug] = name

        for slug in list(self._stations):
            if slug not in configured:
                station = self._stations.pop(slug)
                station.unregister_route()
                await self.mass.players.unregister(station.player_id)

        for slug, name in configured.items():
            if slug in self._stations:
                continue
            await self._register_station(slug, name)

    async def _register_station(self, slug: str, display_name: str) -> None:
        """
        Register a virtual player and HTTP route for a single station.

        :param slug: URL-safe slug derived from the display name.
        :param display_name: Human-readable station name shown in the UI.
        """
        player_id = f"{PLAYER_ID_PREFIX}{slug}"
        player = WebRadioPlayer(
            provider=self,
            player_id=player_id,
            station_slug=slug,
            display_name=display_name,
        )
        await self.mass.players.register(player)

        codec = self._station_codec(player_id)
        url_path = f"{URL_PATH_PREFIX}{slug}.{codec}"
        unregister = self.mass.streams.register_dynamic_route(
            url_path, self._handle_stream_request, "*"
        )
        self._stations[slug] = _Station(
            slug=slug,
            player_id=player_id,
            player=player,
            url_path=url_path,
            unregister_route=unregister,
        )
        player.set_stream_url(self._public_url(url_path))

    def refresh_station_route(self, slug: str) -> None:
        """
        Re-register a station's HTTP route after its codec config changed.

        :param slug: Slug of the station whose route should be refreshed.
        """
        station = self._stations.get(slug)
        if station is None:
            return
        codec = self._station_codec(station.player_id)
        new_path = f"{URL_PATH_PREFIX}{slug}.{codec}"
        if new_path == station.url_path:
            return
        station.unregister_route()
        station.url_path = new_path
        station.unregister_route = self.mass.streams.register_dynamic_route(
            new_path, self._handle_stream_request, "*"
        )
        station.player.set_stream_url(self._public_url(new_path))

    def _station_codec(self, player_id: str) -> str:
        """
        Return the configured output codec extension for a player.

        :param player_id: MA player_id of the virtual station player.
        :return: Codec value, e.g. ``"mp3"`` or ``"flac"``.
        """
        codec = self.mass.config.get_raw_player_config_value(
            player_id, CONF_OUTPUT_CODEC, CONF_ENTRY_OUTPUT_CODEC_DEFAULT_MP3.default_value
        )
        return str(codec or "mp3")

    def _public_url(self, url_path: str) -> str:
        """
        Build the externally reachable HTTP URL for a station path.

        :param url_path: Internal stream route path, e.g. ``"/webradio/rock.mp3"``.
        """
        return f"{self.mass.streams.base_url}{url_path}"

    def _station_for_request(self, request: web.Request) -> _Station:
        """
        Resolve the station whose route matches the incoming request.

        :param request: Inbound aiohttp request.
        :return: The matching ``_Station``.
        :raises web.HTTPNotFound: When no station matches the request path.
        """
        for station in self._stations.values():
            if station.url_path == request.path:
                return station
        raise web.HTTPNotFound(reason=f"Unknown web radio station: {request.path}")

    # ---------------------------------------------------------------
    # HTTP stream handler
    # ---------------------------------------------------------------

    async def _handle_stream_request(self, request: web.Request) -> web.StreamResponse:
        """
        Serve the queue flow audio for a station to a single HTTP listener.

        :param request: Inbound aiohttp request.
        """
        station = self._station_for_request(request)

        if station.listener_lock.locked():
            raise web.HTTPConflict(
                reason=(
                    f"Station {station.slug!r} is already serving a listener. "
                    "Create a second station for an additional concurrent listener."
                )
            )

        async with station.listener_lock:
            return await self._serve_stream(request, station)

    async def _serve_stream(self, request: web.Request, station: _Station) -> web.StreamResponse:
        """
        Stream the active queue contents as a single continuous response.

        :param request: Inbound aiohttp request.
        :param station: Resolved station record.
        """
        player = station.player
        queue = self.mass.player_queues.get(station.player_id)
        start_item = queue.current_item if queue else None
        if queue is None or start_item is None:
            raise web.HTTPServiceUnavailable(reason=f"Station {station.slug!r} has nothing playing")

        flow_pcm_format = await self.mass.streams.audio.select_flow_format(player)
        output_format = await self.mass.streams.audio.get_output_format(
            output_format_str=station.url_path.rsplit(".", 1)[-1],
            player=player,
            content_sample_rate=flow_pcm_format.sample_rate,
            content_bit_depth=flow_pcm_format.bit_depth,
            media_type=start_item.media_type,
        )

        icy_preference = self.mass.config.get_raw_player_config_value(
            station.player_id,
            CONF_ENTRY_ENABLE_ICY_METADATA.key,
            CONF_ENTRY_ENABLE_ICY_METADATA.default_value,
        )
        client_wants_icy = request.headers.get("Icy-MetaData", "") == "1"
        enable_icy = client_wants_icy and icy_preference != "disabled"
        icy_interval = _ICY_FULL_INTERVAL if icy_preference == "full" else _ICY_STANDARD_INTERVAL

        headers = {
            **DEFAULT_STREAM_HEADERS,
            **ICY_HEADERS,
            "contentFeatures.dlna.org": DLNA_CONTENT_FEATURES_REALTIME,
            "Content-Type": get_mime_type(output_format.output_format_str),
        }
        if enable_icy:
            headers["icy-metaint"] = str(icy_interval)

        resp = web.StreamResponse(status=200, reason="OK", headers=headers)
        resp.enable_chunked_encoding()
        await resp.prepare(request)

        if request.method != "GET":
            return resp

        self.logger.debug(
            "Web radio listener connected: station=%s output=%s icy=%s",
            station.slug,
            output_format.output_format_str,
            enable_icy,
        )

        ffmpeg_chunk_size = icy_interval if enable_icy else None
        try:
            async for chunk in get_ffmpeg_stream(
                audio_input=self.mass.streams.audio.get_queue_flow_stream(
                    queue=queue,
                    start_queue_item=start_item,
                    pcm_format=flow_pcm_format,
                ),
                input_format=flow_pcm_format,
                output_format=output_format,
                filter_params=self.mass.streams.audio.get_player_filter_params(
                    player.player_id, flow_pcm_format, output_format
                ),
                # near-realtime feed: small initial burst, then natural pacing
                extra_input_args=["-readrate", "1.1", "-readrate_initial_burst", "5"],
                chunk_size=ffmpeg_chunk_size,
            ):
                try:
                    await resp.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionError):
                    break

                if not enable_icy:
                    continue

                title, image_url = _icy_metadata_for_queue(queue, icy_preference)
                try:
                    await resp.write(format_icy_metadata_frame(title, image_url))
                except (BrokenPipeError, ConnectionResetError, ConnectionError):
                    break
        finally:
            self.logger.debug("Web radio listener disconnected: station=%s", station.slug)

        return resp


def _icy_metadata_for_queue(queue: PlayerQueue, icy_preference: object) -> tuple[str, str | None]:
    """
    Pick the title (and optional image URL) advertised via ICY metadata.

    :param queue: The active queue for the station.
    :param icy_preference: Per-player ICY preference value; ``"full"`` enables
        StreamURL in addition to StreamTitle.
    :return: Tuple of (title, image_url-or-None).
    """
    current_item = queue.current_item
    title = "Music Assistant"
    image_url: str | None = None
    if current_item is None:
        return title, image_url
    if current_item.streamdetails and current_item.streamdetails.stream_title:
        title = current_item.streamdetails.stream_title
    elif current_item.name:
        title = current_item.name
    if icy_preference == "full" and current_item.image is not None:
        image_url = current_item.image.path
    return title, image_url


class WebRadioPlayer(Player):
    """A single web radio "station" exposed as a Music Assistant player."""

    def __init__(
        self,
        provider: WebRadioProvider,
        player_id: str,
        station_slug: str,
        display_name: str,
    ) -> None:
        """
        Initialize the station player.

        :param provider: Owning ``WebRadioProvider`` instance.
        :param player_id: Unique MA player_id (``webradio_<slug>``).
        :param station_slug: URL-safe slug for this station.
        :param display_name: Human-readable station name.
        """
        super().__init__(provider, player_id)
        self._station_slug = station_slug
        self._stream_url: str | None = None
        self._attr_name = display_name
        self._attr_supported_features = {PlayerFeature.PLAY_MEDIA}
        self._attr_device_info = DeviceInfo(
            model="Web Radio Station",
            manufacturer="Music Assistant",
        )

    @property
    def station_slug(self) -> str:
        """Return the URL-safe slug of this station."""
        return self._station_slug

    def set_stream_url(self, url: str) -> None:
        """
        Cache the public URL so it can be shown in the player's settings.

        :param url: Externally reachable HTTP URL for this station.
        """
        self._stream_url = url

    async def get_config_entries(
        self,
        action: str | None = None,
        values: dict[str, ConfigValueType] | None = None,
    ) -> list[ConfigEntry]:
        """
        Return player-level config entries.

        :param action: Optional action key from the config UI.
        :param values: Optional intermediate config values from the UI.
        :return: List of ConfigEntry objects for this player.
        """
        del action, values
        return [
            CONF_ENTRY_OUTPUT_CODEC_DEFAULT_MP3,
            ConfigEntry(
                key=CONF_STREAM_URL_LABEL,
                type=ConfigEntryType.LABEL,
                label=(
                    f"Stream URL: {self._stream_url}"
                    if self._stream_url
                    else "Stream URL: (unavailable)"
                ),
                description=(
                    "Point your dumb radio, VLC, or browser at this URL to "
                    "tune into the station. Only one listener at a time is "
                    "supported per station."
                ),
            ),
        ]

    async def on_config_updated(self) -> None:
        """Refresh the published URL when the codec config changes."""
        provider = self.provider
        if isinstance(provider, WebRadioProvider):
            provider.refresh_station_route(self._station_slug)

    async def play_media(self, media: PlayerMedia) -> None:
        """
        Mark the station as playing.

        :param media: Details of the media item to play.
        """
        # A web radio station has no external sink: audio only flows when an
        # HTTP listener connects to the station URL. We just track state here
        # so the MA queue controller progresses correctly.
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def play(self) -> None:
        """Resume playback (state-only)."""
        self._attr_playback_state = PlaybackState.PLAYING
        self.update_state()

    async def stop(self) -> None:
        """Stop playback and clear current media (state-only)."""
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self.update_state()
