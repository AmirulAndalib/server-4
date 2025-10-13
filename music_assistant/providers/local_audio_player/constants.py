"""Constants for the Local Soundcard Player Provider."""

from typing import Final

# Provider domain identifier
DOMAIN: Final[str] = "local_soundcard"

# Configuration keys for provider-level settings
CONF_DEFAULT_DEVICE: Final[str] = "default_device"
CONF_AUTO_START_PLAYERS: Final[str] = "auto_start_players"

# Configuration keys for player-level settings
CONF_DEVICE_ID: Final[str] = "device_id"
CONF_READ_AHEAD_BUFFER: Final[str] = "buffer_size"

# Audio format constants
AUDIO_FORMAT: Final[str] = "float32"  # 32-bit float for high quality
AUDIO_LATENCY: Final[str] = "low"  # Low latency for real-time playback

# Queue and streaming constants
MAX_AUDIO_QUEUE_SIZE: Final[int] = 500  # Maximum chunks in audio queue
CLEANUP_TIMEOUT: Final[float] = 10.0  # Timeout for cleanup operations
POWER_CYCLE_DELAY: Final[float] = 0.5  # Delay between power off/on cycles
