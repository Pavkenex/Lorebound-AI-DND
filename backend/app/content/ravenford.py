"""Ravenford world content (GDD §110, §112).

Seven locations, twelve NPCs, three factions, three intersecting story
threads, a heavy-rain opening arrival, and three Lantern Inn rumours that
look unrelated but intersect as the mystery is investigated.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    description: str
    connects_to: tuple[str, ...] = ()
    points_of_interest: tuple[str, ...] = ()
    secret: str = ""


LOCATIONS: tuple[Location, ...] = (
    Location(
        id="ravenford",
        name="Ravenford",
        description=(
            "A rain-slick market town where the Northern Road meets the old "
            "monastery path. Timber roofs steam, gutters roar, and strangers "
            "are noticed."
        ),
        connects_to=("lantern-inn", "market", "northern-road", "watchtower"),
        points_of_interest=("Town well", "Notice board", "Rain shrine"),
        secret="The town council quietly pays the Merchant Guild for 'road safety'.",
    ),
    Location(
        id="lantern-inn",
        name="Lantern Inn",
        description=(
            "A low-beamed roadhouse on Ravenford's edge. Peat fire, sharp-eyed "
            "innkeeper Marla, and every rumour in town passing through the door."
        ),
        connects_to=("ravenford", "market", "northern-road"),
        points_of_interest=("Common room", "Notice board", "Storeroom strongbox"),
        secret="Marla's ledger records every guest — including two who never returned.",
    ),
    Location(
        id="market",
        name="Ravenford Market",
        description=(
            "Canvas awnings sagging with rain. Guild stalls buy silver at "
            "suspiciously high prices; peddlers whisper about the road."
        ),
        connects_to=("ravenford", "lantern-inn"),
        points_of_interest=("Guild silver stall", "Peddler row", "Rain barrels"),
        secret="The guild pays over market rate for raw silver, no questions asked.",
    ),
    Location(
        id="northern-road",
        name="Northern Road",
        description=(
            "A muddy track climbing out of Ravenford toward the forest and the "
            "monastery. Cart ruts vanish into pools; ambush country in this rain."
        ),
        connects_to=("ravenford", "lantern-inn", "forest", "watchtower"),
        points_of_interest=(" Broken cart", "Mudflat ruts", "Wayside cairn"),
        secret="Fresh cart tracks turn off toward the monastery drainage tunnels.",
    ),
    Location(
        id="forest",
        name="Greywood Forest",
        description=(
            "Dripping pines closing over the road. Lantern lights drift between "
            "the trunks at night — too steady for will-o'-wisps."
        ),
        connects_to=("northern-road", "old-monastery"),
        points_of_interest=("Charcoal burners' camp", "Drainage tunnel mouth", "Old cairns"),
        secret="The 'monastery lights' are lanterns carried through the old drainage tunnels.",
    ),
    Location(
        id="old-monastery",
        name="Old Monastery",
        description=(
            "A half-ruined house of the Quiet Order above the treeline. Bells "
            "without ringers; cellars deeper than the brothers admit."
        ),
        connects_to=("forest", "northern-road"),
        points_of_interest=("Bell tower", "Scriptorium", "Sealed cellar"),
        secret="The sealed cellar opens onto smugglers' tunnels — and a locked pen.",
    ),
    Location(
        id="watchtower",
        name="Ravenford Watchtower",
        description=(
            "The town watch's rain-lashed tower overlooking the road. Undermanned, "
            "underpaid, and tired of vanishing-traveler reports going nowhere."
        ),
        connects_to=("ravenford", "northern-road"),
        points_of_interest=("Signal brazier", "Report desk", "Lockup"),
        secret="The watch sergeant suspects the guild but lacks proof — and funds.",
    ),
)

LOCATION_IDS: tuple[str, ...] = tuple(loc.id for loc in LOCATIONS)

# ---------------------------------------------------------------------------
# NPCs (12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Npc:
    id: str
    name: str
    role: str
    location: str
    faction: str
    disposition: str
    knows: tuple[str, ...] = ()


NPCS: tuple[Npc, ...] = (
    Npc("marla", "Marla", "Innkeeper of the Lantern Inn", "lantern-inn",
        "unaffiliated", "warm to guests, iron to thieves",
        ("missing-travelers last seen", "ledger of guests")),
    Npc("borin", "Borin", "Drunk mercenary", "lantern-inn",
        "unaffiliated", "belligerent until beaten, then loyal",
        ("saw cart tracks turn toward the forest",)),
    Npc("sella", "Sella Voss", "Guild silver factor", "market",
        "merchant-guild", "polite, evasive, rich",
        ("guild silver prices", "paid a 'carrier' on the Northern Road")),
    Npc("tomm", "Tomm Ash", "Market peddler", "market",
        "unaffiliated", "chatty, nervous",
        ("guild buying silver rumor", "travelers bought cloaks before leaving")),
    Npc("sergeant-dain", "Sergeant Dain", "Watch sergeant", "watchtower",
        "town-watch", "gruff, honest, stretched thin",
        ("three missing-traveler reports", "suspects the guild, lacks proof")),
    Npc("wren", "Wren", "Watch runner", "watchtower",
        "town-watch", "eager, young",
        ("timed the monastery lights",)),
    Npc("brother-anselm", "Brother Anselm", "Monastery porter", "old-monastery",
        "quiet-order", "pious, frightened",
        ("cellar is forbidden", "heard carts below at night")),
    Npc("mother-ilde", "Mother Ilde", "Abbess of the Quiet Order", "old-monastery",
        "quiet-order", "serene, hiding something",
        ("knows the tunnels exist", "the order's debts to the guild")),
    Npc("corb", "Corb", "Charcoal burner", "forest",
        "unaffiliated", "wary of strangers",
        ("lanterns moving through the trees", "tunnel mouth location")),
    Npc("fenn", "Fenn", "Smuggler carrier", "northern-road",
        "merchant-guild", "desperate, bribable",
        ("ambush point", "where the travelers are held")),
    Npc("ossia", "Ossia", "Guild caravan guard", "northern-road",
        "merchant-guild", "professional, tired of secrets",
        ("escorted 'sealed cargo' at night",)),
    Npc("elder-bran", "Elder Bran", "Town councillor", "ravenford",
        "town-watch", "affable, compromised",
        ("council pays the guild for 'road safety'",)),
)

# ---------------------------------------------------------------------------
# Factions (3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Faction:
    id: str
    name: str
    goal: str
    methods: str
    headquarters: str


FACTIONS: tuple[Faction, ...] = (
    Faction(
        id="merchant-guild",
        name="Ashen Merchant Guild",
        goal="Control the flow of silver through Ravenford at any cost.",
        methods="Overpay for raw silver, hire carriers off-ledger, silence witnesses.",
        headquarters="market",
    ),
    Faction(
        id="quiet-order",
        name="Order of the Quiet Bell",
        goal="Keep the monastery standing and its debts hidden.",
        methods="Look away from the tunnels in exchange for guild coin.",
        headquarters="old-monastery",
    ),
    Faction(
        id="town-watch",
        name="Ravenford Watch",
        goal="Keep the road safe and find the missing travelers.",
        methods="Patrols, reports, and one honest sergeant with no budget.",
        headquarters="watchtower",
    ),
)

# ---------------------------------------------------------------------------
# Story threads (3, intersecting)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryThread:
    id: str
    title: str
    hook: str
    truth: str
    clues: tuple[str, ...] = ()
    intersects_with: tuple[str, ...] = ()


THREADS: tuple[StoryThread, ...] = (
    StoryThread(
        id="missing-travellers",
        title="The Missing Travellers",
        hook="Two travelers bound for the monastery never arrived.",
        truth=(
            "Guild carriers ambushed them on the Northern Road for the raw "
            "silver they carried; they are held in the monastery cellar pen."
        ),
        clues=(
            "Marla's ledger: last guests, northbound in rain.",
            "Mud on a dropped cloak matches the Northern Road mudflats.",
            "Fenn knows the pen location.",
        ),
        intersects_with=("monastery-lights", "guild-silver"),
    ),
    StoryThread(
        id="monastery-lights",
        title="Lights near the Monastery",
        hook="Steady lantern lights move through the forest at night.",
        truth=(
            "Smugglers carry guild silver through the old drainage tunnels; "
            "the lights are their lanterns, not spirits."
        ),
        clues=(
            "Wren timed the lights: a walking pace, nightly.",
            "Corb can point to the tunnel mouth.",
            "Tunnel mud matches the monastery cellar floor.",
        ),
        intersects_with=("missing-travellers", "guild-silver"),
    ),
    StoryThread(
        id="guild-silver",
        title="Guild Silver",
        hook="The guild buys raw silver far above market rate, no questions asked.",
        truth=(
            "The guild smuggles untaxed silver through the monastery tunnels, "
            "and pays the order's debts to keep the cellar door shut. The "
            "travelers were taken for the silver they carried."
        ),
        clues=(
            "Sella's payments ledger names night carriers.",
            "Mother Ilde knows about the order's debts.",
            "Elder Bran signed the 'road safety' payments.",
        ),
        intersects_with=("missing-travellers", "monastery-lights"),
    ),
)

#: How the threads join: every pair intersects (the mystery is one knot).
THREAD_INTERSECTIONS: tuple[tuple[str, str, str], ...] = (
    ("missing-travellers", "monastery-lights",
     "The ambushers used the smugglers' tunnels to move unseen."),
    ("missing-travellers", "guild-silver",
     "The travelers were taken for the raw silver they carried."),
    ("monastery-lights", "guild-silver",
     "The lights are guild silver moving through the tunnels."),
)

# ---------------------------------------------------------------------------
# Opening + rumours
# ---------------------------------------------------------------------------

OPENING_SCENE: str = (
    "Heavy rain hammers the Northern Road as you crest the last rise. Below, "
    "Ravenford steams in the downpour — lamplight bleeding through shutters, "
    "gutters roaring, the bell of the Old Monastery lost in the weather. Your "
    "cloak is soaked through, your purse is light, and the only warm windows "
    "belong to the Lantern Inn. Whatever brought you here, the rain has decided: "
    "you are staying the night."
)

RUMOURS: tuple[dict[str, str], ...] = (
    {
        "id": "rumor-missing-travellers",
        "title": "Missing travellers",
        "text": (
            "Two travelers bound for the monastery walked into the rain three "
            "nights back and never came down again. Marla keeps their names "
            "in her ledger."
        ),
        "thread": "missing-travellers",
    },
    {
        "id": "rumor-monastery-lights",
        "title": "Lights near the monastery",
        "text": (
            "Steady lights drift through Greywood Forest after dark — too even "
            "for spirits, says the watch runner who timed them."
        ),
        "thread": "monastery-lights",
    },
    {
        "id": "rumor-guild-silver",
        "title": "Guild buying silver",
        "text": (
            "The Ashen Guild pays over market rate for raw silver, no questions "
            "asked. Even the councillor looks the other way."
        ),
        "thread": "guild-silver",
    },
)


def get_location(location_id: str) -> Location:
    for loc in LOCATIONS:
        if loc.id == location_id:
            return loc
    raise KeyError(f"unknown location: {location_id!r}")


def get_npc(npc_id: str) -> Npc:
    for npc in NPCS:
        if npc.id == npc_id:
            return npc
    raise KeyError(f"unknown npc: {npc_id!r}")


def get_thread(thread_id: str) -> StoryThread:
    for thread in THREADS:
        if thread.id == thread_id:
            return thread
    raise KeyError(f"unknown thread: {thread_id!r}")
