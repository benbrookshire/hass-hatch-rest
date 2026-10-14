"""Constants describing Hatch products and their audio tracks."""

from __future__ import annotations

from enum import StrEnum

API_BASE_URL = "https://prod-sleep.hatchbaby.com/"

# Max value for volume/intensity fields in the IoT shadow
MAX_IOT_VALUE = 65535


class Product(StrEnum):
    """Product identifiers as returned by the Hatch API."""

    REST = "rest"
    RIOT = "riot"
    RIOT_PLUS = "riotPlus"
    REST_PLUS = "restPlus"
    REST_MINI = "restMini"
    RESTORE = "restore"
    RESTORE_IOT = "restoreIot"
    RESTORE_V4 = "restoreV4"
    RESTORE_V5 = "restoreV5"
    ALEXA = "alexa"
    GROW = "grow"
    ANSWERED_READER = "answeredReader"
    REST_BABY = "restBaby"


# Products whose shadow schema matches RestIotState (routine-based)
REST_IOT_PRODUCTS: tuple[Product, ...] = (
    Product.RIOT,
    Product.RIOT_PLUS,
    Product.RESTORE_IOT,
    Product.RESTORE_V4,
    Product.RESTORE_V5,
    Product.REST_BABY,
)

KNOWN_PRODUCTS: tuple[Product, ...] = (
    Product.REST_PLUS,
    Product.REST_MINI,
    Product.RESTORE,
    *REST_IOT_PRODUCTS,
)

# Known, but not supported. Bluetooth-only Rest devices are handled by
# Home Assistant's bluetooth stack, not this cloud integration.
IGNORED_PRODUCTS: tuple[str, ...] = (
    Product.REST,
    Product.ALEXA,
    Product.GROW,
    Product.ANSWERED_READER,
)

MODEL_NAMES: dict[str, str] = {
    Product.REST_PLUS: "Rest+",
    Product.REST_MINI: "Rest Mini",
    Product.RESTORE: "Restore",
    Product.RESTORE_IOT: "Restore IoT",
    Product.RIOT_PLUS: "Rest+ 2nd Gen",
    Product.REST_BABY: "Hatch Baby",
    Product.RESTORE_V4: "Restore 2",
    Product.RESTORE_V5: "Restore 3",
    Product.RIOT: "Rest 2nd Gen",
}

# Rest+ audio tracks, in the order the Hatch app lists them
REST_PLUS_AUDIO_TRACKS: dict[int, str] = {
    0: "None",
    2: "Stream",
    3: "Pink Noise",
    4: "Dryer",
    5: "Ocean",
    6: "Wind",
    7: "Rain",
    9: "Bird",
    10: "Crickets",
    11: "Brahms",
    13: "Twinkle",
    14: "Rock-a-Bye",
}
REST_PLUS_TRACK_NONE = 0

REST_MINI_AUDIO_TRACKS: dict[int, str] = {
    0: "None",
    10126: "White Noise",
    10128: "Ocean",
    10130: "Rain",
    10125: "Water",
    10129: "Wind",
    10131: "Birds",
    10127: "Dryer",
    10124: "Heartbeat",
}
REST_MINI_TRACK_NONE = 0
