# Classical music model spec (Stage 1)

**Target repo:** `music-assistant-models`
**Status:** Draft for discussion
**Goal:** Add first-class classical-music metadata to the shared models package, in a fully backwards-compatible way, so downstream consumers (server, frontend, HA integration, third-party) can adopt incrementally.

## Background

Music Assistant currently has a flat artist model and no concept of a musical Work. Classical recordings carry credit information that doesn't fit cleanly:

- Composer, conductor, orchestra/ensemble, soloists with instruments — all are first-class credits but are currently squashed into either the `artists` list (no role distinction) or the unstructured `metadata.performers: set[str]` field.
- A Work (e.g. "Beethoven Symphony No. 5 in C minor, Op. 67") has no representation, even though MusicBrainz exposes Works as entities with their own MBID and users routinely want to browse/filter by Work.
- Movements are just sibling tracks with no link back to the Work they belong to, so multi-movement playback and Work-grouped browsing aren't possible.

Standard tags (Picard mapping) and MusicBrainz both already model this richer structure. This spec brings the MA models in line.

## Goals

- Add `Work` as a first-class MediaItem.
- Model artist credits with explicit roles (composer, conductor, orchestra, soloist, performer with instrument, etc.).
- Add Work / movement linkage on Track.
- Keep the change strictly **non-breaking** for existing consumers.

## Non-goals

- Database schema changes (separate PR in the server repo).
- Tag parsing changes (separate PR).
- Streaming provider changes (separate PR per provider).
- Frontend changes (separate PR in the frontend repo).
- A "period" (Baroque/Classical/Romantic) field — there is no standard tag or MusicBrainz field for this, and it can be derived from genre tags or composer dates. Out of scope.

## Backwards compatibility

This change is **additive only**. No existing field changes type or is removed.

| Existing field | What happens | Notes |
|---|---|---|
| `Track.artists: list[Artist \| ItemMapping]` | Unchanged. Continues to mean "headline credit". | New `Track.credits` is added alongside. |
| `Album.artists: list[Artist \| ItemMapping]` | Unchanged. | New `Album.credits` is added alongside. |
| `Track.metadata.performers: set[str]` | Kept; deprecated in docstring. | Server populates it as a derived view from `credits` for back-compat. |
| `Track.metadata.grouping: str \| None` | Kept; deprecated in docstring. | Replaced in semantics by `Track.work` when present. Acts as fallback when no Work tag exists. |

Old consumers continue working unchanged. New consumers opt in by reading the new fields. A future major version may collapse the duplication.

## New types

### `ArtistRole` (enum)

```python
class ArtistRole(StrEnum):
    """Role an artist plays on a track or album credit."""

    MAIN_ARTIST = "main_artist"        # headline credit; equivalent to existing `artists` list
    COMPOSER = "composer"
    LYRICIST = "lyricist"
    ARRANGER = "arranger"
    CONDUCTOR = "conductor"
    ORCHESTRA = "orchestra"            # whole-orchestra credit
    ENSEMBLE = "ensemble"              # chamber group, band, etc.
    CHOIR = "choir"
    SOLOIST = "soloist"                # featured performer (usually with an instrument)
    PERFORMER = "performer"            # any other performing musician
    REMIXER = "remixer"
    PRODUCER = "producer"
```

Notes:

- `MAIN_ARTIST` exists so credits can be a complete list including the headline credit — i.e. the artists in `Track.artists` will appear in `Track.credits` with `role=MAIN_ARTIST`.
- The set is opinionated but not exhaustive. If we hit a role that doesn't fit, we add it. The enum is forward-compatible because consumers can fall back to `PERFORMER` for unknown values.

### `Credit` (dataclass)

```python
@dataclass
class Credit:
    """A single role-tagged artist credit on a track or album."""

    artist: Artist | ItemMapping
    role: ArtistRole
    instrument: str | None = None      # only meaningful for SOLOIST / PERFORMER
    position: int = 0                  # ordering within a role; lower first
```

Notes:

- Free-form `instrument` string is intentional. Picard writes "violin", "piano", "soprano vocals", etc. in the Vorbis `PERFORMER` parens convention; we keep the string as-is rather than enumerate.
- `position` lets the UI render credits in the order they were tagged (first violin before second violin, lead vocal before backing vocals, etc.) without forcing alphabetical.
- Composer Sort Order, MusicBrainz Composer ID, and similar per-role tags do not need separate fields — they are stored on the underlying `Artist` (`sort_name`, `external_ids`).

### `Work` (MediaItem)

```python
class Work(MediaItem):
    """
    A musical composition, distinct from any specific recording of it.

    Multiple recordings of the same work share a Work entity (matched by MusicBrainz Work MBID
    where available). Movements of a multi-part work link to the parent work.
    """

    media_type: MediaType = MediaType.WORK
    composers: list[ItemMapping] = field(default_factory=list)
    catalog_numbers: list[str] = field(default_factory=list)   # ["Op. 67", "BWV 1041", "K. 525"]
    work_type: WorkType | None = None
    parent_work: ItemMapping | None = None                     # for movements / sub-works
    # inherited: item_id, name, sort_name, version, favorite, metadata, external_ids, …
```

```python
class WorkType(StrEnum):
    """High-level work classification, mirrors the MusicBrainz Work `type` field."""

    SYMPHONY = "symphony"
    CONCERTO = "concerto"
    SONATA = "sonata"
    SUITE = "suite"
    OPERA = "opera"
    ORATORIO = "oratorio"
    CANTATA = "cantata"
    MASS = "mass"
    SONG_CYCLE = "song_cycle"
    QUARTET = "quartet"             # string quartet, etc.
    OVERTURE = "overture"
    BALLET = "ballet"
    OTHER = "other"
```

Notes:

- `Work` is a full MediaItem so it gets `external_ids` (for the MusicBrainz Work MBID), images, descriptions, sort_name, search_name, etc. for free.
- `catalog_numbers` is a list because the same work can have multiple catalog references (Op. number plus a thematic catalog like K. or BWV). Stored as strings; parsing/sorting is a presentation concern.
- `parent_work` is optional and self-referential. Movements model as separate Works with a parent link, mirroring MusicBrainz. Whether a Track points at a movement-Work or directly at the parent Work is a tagging choice and the Track stores `movement_number` either way.
- `MediaType.WORK` is a new variant of the existing `MediaType` enum.

### `MediaType.WORK`

Added value to the existing `MediaType` enum. Old consumers that switch over `MediaType` will fall through to their default case, which is the same behaviour as encountering a future unknown type.

## Modified types

### `Track`

Additive fields only:

```python
@dataclass
class Track(MediaItem):
    # ... existing fields unchanged ...

    credits: list[Credit] = field(default_factory=list)
    work: ItemMapping | None = None
    movement_number: int | None = None
    movement_total: int | None = None
    movement_name: str | None = None       # e.g. "I. Allegro con brio"

    @property
    def composers(self) -> list[Artist | ItemMapping]:
        """Artists credited as composers, in tagged order."""
        return [c.artist for c in sorted(self.credits, key=lambda x: x.position)
                if c.role == ArtistRole.COMPOSER]

    @property
    def conductors(self) -> list[Artist | ItemMapping]:
        """Artists credited as conductors, in tagged order."""
        return [c.artist for c in sorted(self.credits, key=lambda x: x.position)
                if c.role == ArtistRole.CONDUCTOR]

    @property
    def performers_with_instruments(self) -> list[tuple[Artist | ItemMapping, str | None]]:
        """Performers and soloists with their instrument, in tagged order."""
        return [(c.artist, c.instrument)
                for c in sorted(self.credits, key=lambda x: x.position)
                if c.role in (ArtistRole.PERFORMER, ArtistRole.SOLOIST)]
```

Notes:

- `movement_name` is separate from `Track.name` because in non-classical contexts the display name is usually the full string ("Symphony No. 5: I. Allegro") while the movement name alone is "I. Allegro" — keeping them split lets the UI choose.
- Convenience properties cover the common access patterns without forcing every consumer to filter `credits` by role manually.

### `Album`

Additive fields only:

```python
@dataclass
class Album(MediaItem):
    # ... existing fields unchanged ...

    credits: list[Credit] = field(default_factory=list)

    @property
    def composers(self) -> list[Artist | ItemMapping]: ...
    @property
    def conductors(self) -> list[Artist | ItemMapping]: ...
```

(Same convenience-property pattern as Track.)

## Field provenance

A reference for the server-side parser/provider work in later stages. Each new model field has a clear source:

| Model field | Vorbis tag | ID3 frame | MP4 atom | MusicBrainz |
|---|---|---|---|---|
| `Credit(role=COMPOSER)` | `COMPOSER` | `TCOM` | `©wrt` | Work-Artist relationship "composer" |
| Composer's `sort_name` | `COMPOSERSORT` | `TSOC` | `soco` | Artist sort name |
| Composer's `Artist.external_ids` (MBID) | `MUSICBRAINZ_COMPOSERID` | `TXXX:MusicBrainz Composer Id` | freeform | Artist MBID |
| `Credit(role=CONDUCTOR)` | `CONDUCTOR` | `TPE3` | `----:com.apple.iTunes:CONDUCTOR` | Recording-Artist relationship "conductor" |
| `Credit(role=PERFORMER, instrument=…)` | `PERFORMER="Name (instrument)"` (multi-valued) | `TMCL` (instrument/name pairs) | freeform `Performer` | Recording-Artist relationship "performer" with instrument attribute |
| `Track.work` (link) | `WORK` | `TIT1` (or `TXXX:WORK`) | `©wrk` | Recording-Work "performance" relationship |
| Work's `external_ids` (MBID) | `MUSICBRAINZ_WORKID` | `TXXX:MusicBrainz Work Id` | freeform | Work MBID |
| `Track.movement_name` | `MOVEMENTNAME` | `MVNM` | `©mvn` | Work name (for the movement Work) |
| `Track.movement_number` | `MOVEMENTNUMBER` | `MVIN` (number part) | `©mvi` | Work part-number attribute |
| `Track.movement_total` | `MOVEMENTTOTAL` | `MVIN` (total part) | `©mvc` | — |
| `Work.catalog_numbers` | (embedded in WORK title by convention) | (same) | (same) | Work catalog-number attributes |
| `Work.work_type` | — | — | — | Work `type` field |

Tags with no MusicBrainz equivalent (e.g. some `TXXX` fields written by certain taggers) and MB fields with no tag equivalent (conductor MBID, performer MBIDs) are accepted gracefully — the parser fills what it can; the enrichment provider fills the rest.

## Examples

A track from "Karajan conducts Beethoven Symphony No. 5", second movement:

```python
Track(
    name="Symphony No. 5 in C minor, Op. 67: II. Andante con moto",
    artists=[                                  # headline credit (unchanged)
        ItemMapping(name="Berlin Philharmonic Orchestra", ...),
        ItemMapping(name="Herbert von Karajan", ...),
    ],
    credits=[                                  # full role-typed credits
        Credit(artist=ItemMapping(name="Ludwig van Beethoven", ...),
               role=ArtistRole.COMPOSER, position=0),
        Credit(artist=ItemMapping(name="Herbert von Karajan", ...),
               role=ArtistRole.CONDUCTOR, position=0),
        Credit(artist=ItemMapping(name="Berlin Philharmonic Orchestra", ...),
               role=ArtistRole.ORCHESTRA, position=0),
        # MAIN_ARTIST entries duplicate the `artists` list, intentionally:
        Credit(artist=ItemMapping(name="Berlin Philharmonic Orchestra", ...),
               role=ArtistRole.MAIN_ARTIST, position=0),
        Credit(artist=ItemMapping(name="Herbert von Karajan", ...),
               role=ArtistRole.MAIN_ARTIST, position=1),
    ],
    work=ItemMapping(
        item_id="...",
        media_type=MediaType.WORK,
        name="Symphony No. 5 in C minor, Op. 67",
    ),
    movement_number=2,
    movement_total=4,
    movement_name="II. Andante con moto",
    metadata=MediaItemMetadata(
        # Derived view for back-compat. Populated by the server, not by consumers.
        performers={"Berlin Philharmonic Orchestra", "Herbert von Karajan",
                    "Ludwig van Beethoven"},
    ),
)
```

A chamber-music track with soloists:

```python
Track(
    name="Piano Trio in B-flat major, Op. 97 'Archduke': I. Allegro moderato",
    credits=[
        Credit(artist=..., role=ArtistRole.COMPOSER),     # Beethoven
        Credit(artist=..., role=ArtistRole.SOLOIST,
               instrument="violin", position=0),          # Heifetz
        Credit(artist=..., role=ArtistRole.SOLOIST,
               instrument="cello", position=1),           # Piatigorsky
        Credit(artist=..., role=ArtistRole.SOLOIST,
               instrument="piano", position=2),           # Rubinstein
    ],
    work=ItemMapping(name="Piano Trio in B-flat major, Op. 97 'Archduke'", ...),
    movement_number=1,
    movement_total=4,
)
```

Corresponding `Work`:

```python
Work(
    name="Symphony No. 5 in C minor, Op. 67",
    sort_name="Symphony No. 005 in C minor, Op. 067",
    composers=[ItemMapping(name="Ludwig van Beethoven", ...)],
    catalog_numbers=["Op. 67"],
    work_type=WorkType.SYMPHONY,
    external_ids={("musicbrainz_workid", "...")},
)
```

## Migration notes for downstream consumers

Old consumers continue to work unchanged. To opt in:

1. **Server (`music-assistant/server`):** schema migration adds `works` table, `track_works` link, and `role`/`instrument`/`position` columns to artist junction tables. Tag parser and providers populate the new fields. The server is responsible for keeping `metadata.performers` populated as a derived view for as long as it is supported.
2. **Frontend (`music-assistant/frontend`):** read `track.credits` for richer credit display, `track.work` / `track.movement_*` for Work-grouped views, and the new `MediaType.WORK` browse pages. No required changes — old views keep working.
3. **HA integration:** no required changes. Optionally surface composer / work in track attributes.
4. **Third-party model consumers:** no required changes; defensively handle the new `MediaType.WORK` value the same way they handle other unknown media types.

The expectation is that the server PR ships first (populating the new fields), then frontend adopts at its own pace.

## Open questions

1. **`ArtistRole` exhaustiveness.** Should we include LYRICIST/ARRANGER/REMIXER/PRODUCER from the start, or wait until they have a concrete consumer? Leaning toward including them — they cost nothing and let the parser preserve information.
2. **Duplication between `artists` and `credits[role=MAIN_ARTIST]`.** Acceptable for non-breaking but ugly. Should we document a rule for how the server keeps them in sync, or treat one as canonical and the other as derived? Leaning toward: `artists` is the canonical headline credit; `credits` is canonical for everyone else; consumers reading `credits` see MAIN_ARTIST entries that mirror `artists`.
3. **Movements as Works vs. just movement fields.** Do we always create a movement-Work and link the Track to it (parent_work pointing at the parent), or do we only create the parent Work and use the Track's `movement_*` fields? MusicBrainz models movements as their own Works, so the former is more faithful and gives every movement an MBID — but it explodes Work row counts. Recommend: parent Work only, `movement_*` on Track, **unless** the source has an MBID for the movement-Work specifically, in which case create it. Worth confirming.
4. **`WorkType` granularity.** Mirror MusicBrainz exactly, or simplify? MusicBrainz has ~25 types; the proposed enum has 12. Open to expanding if there's demand.
5. **`Credit.position` semantics.** Per-role ordering (proposed) or global ordering across roles? Per-role is simpler to reason about, global is closer to how a credits booklet reads. Per-role probably wins.

## Out of scope (future work)

- Period / era field (no canonical source; derive from genres or composer dates if needed).
- Lyricist / librettist relationships beyond the basic `LYRICIST` role.
- Recording-level metadata (recording date, venue, producer credits beyond the basic role).
- Multi-disc opera structure beyond what `parent_work` already supports.
