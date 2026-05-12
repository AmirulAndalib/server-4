# Classical music support — design spec

**Status:** Draft for discussion
**Scope:** Full design across all stages — model, schema, parsing, providers, enrichment, frontend.

## Executive summary

**What ships:** a `Work` entity (the composition, distinct from any specific recording), a role-typed `Credit` type that distinguishes composer / conductor / orchestra / soloist / performer-with-instrument from the headline `artists` list, and movement linkage on `Track`. A new "Classical" frontend view with Composers / Works / Performers tabs surfaces all of it.

**How:** strictly additive across 10 deployable stages. Old consumers keep working unchanged; new consumers opt in. MusicBrainz IDs are authoritative when present; the design deliberately doesn't infer missing data, on the principle that comprehensive tagging produces the optimal outcome and thin tags get a thin experience.

**Why:** classical listeners today can't browse by composer, group movements under a Work, or distinguish a conductor from a soloist — the flat artist model squashes all of these into one undifferentiated list. Schema is the easier half of the problem; data quality (uneven MusicBrainz coverage, sparse streaming-provider classical metadata) is the harder half and is addressed piecemeal across Stages 4–6.

## Background

Music Assistant currently has a flat artist model and no concept of a musical Work. Classical recordings carry credit information that doesn't fit cleanly:

- Composer, conductor, orchestra/ensemble, soloists with instruments — all are first-class credits but are currently squashed into either the `artists` list (no role distinction) or the unstructured `metadata.performers: set[str]` field.
- A Work (e.g. "Beethoven Symphony No. 5 in C minor, Op. 67") has no representation, even though MusicBrainz exposes Works as entities with their own MBID and users routinely want to browse/filter by Work.
- Movements are just sibling tracks with no link back to the Work they belong to, so multi-movement playback and Work-grouped browsing aren't possible.

Standard tags (Picard mapping) and MusicBrainz both already model this richer structure. This spec brings the MA models in line.

## What classical listeners actually want

Synthesised from the Roon CMI/TLS forums discussion, Apple Music Classical's launch, the Classical Extras Picard plugin, the MA "Better Classical Music Support" Discord thread, and direct community feedback. The consistent asks:

1. **Browse by composer as the primary axis** — the most-cited ask.
2. **Work as a first-class browseable entity.** Multiple recordings of one composition (Beethoven 5 by Karajan, Bernstein, Solti…) group under one Work entry; movements play as a unit, gapless, no shuffle by default.
3. **Distinct conductor / orchestra / soloist credits**, filterable to "all Karajan recordings", "all Berlin Philharmonic recordings", "all violin recordings" — without fuzzy text matching.
4. **Catalog numbers (BWV / K. / Op. / HWV) parsed and searchable** — often the canonical handle for a work.
5. **Roll-up across granularity.** The same recording / track / movement appears in many shapes: a single track, a movement of a full work on its source album, the same single track on a compilation, a transcription across instruments (Mussorgsky's *Pictures at an Exhibition* piano original ↔ Ravel's orchestration; Bach organ ↔ piano transcriptions). The data needs to detect each as distinct *and* roll it up to album / work / composer / performer where appropriate. Concrete example from Pärt's catalogue: *Spiegel im Spiegel* appears as three different pieces on its own album plus single tracks on *Alina*, *Fratres*, and *Stabat mater*, all with different performers — a user should reach the same composition from any of those entry points.

**Asks deliberately deferred or out of scope:** fuzzy matching across spelling variants without an MBID (resolved in the Matching policy via the MBID-canonical rule); curated classical playlists and commissioned composer artwork (content / sourcing concerns, not model). Period / era support: model field added (`Artist.period`, Decisions log #5 revised), filter-chip UI deferred to a future polish stage after Stage 7. See the relevant sections for detail.

The mapping from each user need above to the implementing stage(s) appears in the Implementation stages table below.

## Goals

- Add `Work` as a first-class MediaItem.
- Model artist credits with explicit roles (composer, conductor, orchestra, soloist, performer with instrument, etc.).
- Add Work / movement linkage on Track.
- Keep the change strictly **non-breaking** for existing consumers.

## Implementation stages

The work splits across two repos and several PRs. Each stage is independently deployable; later stages depend on earlier ones for data shape but not for behaviour.

| # | Stage | Repo | Depends on | Summary |
|---|---|---|---|---|
| 1 | **Model changes** | `music-assistant-models` | — | New `Work` MediaItem, `ArtistRole` enum, `Credit` type, additive fields on `Track`/`Album`. Fully non-breaking. *(See `CLASSICAL_MUSIC_STAGE_1_MODELS.md`.)* |
| 2 | **Database schema & migrations** | `music-assistant/server` | 1 | New `works` table, `work_arrangements` junction, `work_id`/`movement_*` columns on `tracks`, `role`/`instrument`/`position` columns on `track_artists` and `album_artists`. Migration backfills existing rows with `role=MAIN_ARTIST`. |
| 3 | **Server controllers & API** | `music-assistant/server` | 2 | New `WorksController`. `TracksController` and `AlbumsController` extended for role-typed credits and work linkage. WebSocket commands for work browse, role-filtered track queries. Comparison/dedup updated to use Work MBID. **Search is unchanged at this stage** — it's deferred to Stages 8 & 9 to keep PRs reviewable. |
| 4 | **Local file tag parsing** | `music-assistant/server` | 3 | `helpers/tags.py` reads `COMPOSER` / `CONDUCTOR` / `PERFORMER` / `WORK` / `MOVEMENT*` / role-suffixed `PERFORMER:instrument` / `TMCL` pairs across Vorbis / ID3 / MP4 / APEv2. Plus a small fallback set for Classical Extras tags (`groupheading`, `top_work`, `is_classical`, `movement`). Cue-sheet parsing (per [#3751](https://github.com/music-assistant/server/pull/3751)) needs the same classical-tag awareness — exposing classical fields from cue `REM` lines and/or the underlying audio file's tags so single-file rips with cue sheets aren't second-class in the Classical view. Local-file users get the Classical view's browse functionality at this point (search comes in Stage 8). |
| 5 | **Streaming provider mapping** | `music-assistant/server` | 3 | Per-provider PRs: Qobuz (resolves existing TODO), Apple Music (composer mapping fix), Subsonic-compatible (structured contributors), and audit of Tidal/Spotify/Deezer/YouTube Music. Independent and parallelisable per provider. |
| 6 | **MusicBrainz enrichment** | `music-assistant/server` | 3 | Extends the MB provider beyond ID-only fetching to pull Recording-Artist relationships (composer/conductor/performer with instruments), Recording-Work links, Work entity metadata (type, catalog numbers, parent work), and Work-Work arrangement relationships. Fills gaps where local tags or streaming providers fall short. **Strict rule: enrich-don't-override** — never overwrite a value the source already supplied. **Investigate** adding **Wikipedia as a secondary metadata provider** for Work summary text, implemented as a dedicated `wikipedia` MetadataProvider plugin following the existing TheAudioDB / FanArt.tv pattern (configurable, user-disableable). Resolution chain (validated against MB and Wikidata): (1) primary path — MB Work → `wikidata` URL relationship → Wikidata `wbgetentities` sitelinks API → Wikipedia title for the user's locale → fetch `extract` from Wikipedia's `/page/summary/{title}` endpoint; (2) fallback — MB Work → direct `wikipedia` URL relationship if present (rarer in practice; most MB Works have Wikidata but not direct Wikipedia URLs). Store the resolved `extract` on `Work.metadata.description` for in-app rendering rather than MB's "first two lines + link-out" pattern. Skip title-guessing — graceful degradation when neither MB cross-reference is present. Cache server-side with ETag and a sensible `User-Agent` header, respect Wikipedia and Wikidata rate limits. Plugin runs after MB enrichment so the Wikidata QID is populated on the entity before it queries. |
| 7 | **Frontend Classical view** | `music-assistant/frontend` | 3 | New top-level "Classical" entry with three internal tabs: Composers / Works / Performers (Performers carries role-filter chips). Composer detail page (works listed, not albums), Work detail page (recordings collapsed under one composition), OTHER VERSIONS reused for unmatched-Work suggestions. The **extended Track credits panel** (structured role-typed credits on Track detail) is **deferred to a future polish stage** — see Decisions log #31. **No search inside the Classical view** — search lives in the global search bar (Stages 8 & 9). |
| 8 | **Basic global Classical search** | both | 3 | Server: extend `SearchResults` to include classical entity types (composers, works, performers) and update `search_library` / per-controller search to query against extended `search_name` and role-typed credit fields. Frontend: add a *Classical* master chip to the global search; when selected, returns up to 50 mixed results in a flat list. Single-term substring match — no nested chips, no sub-categorisation. Demoable in isolation. |
| 9 | **Refined classical search** | `music-assistant/frontend` (+ server tweaks) | 8 | Nested chip hierarchy: second level (Composers / Works / Performers) within the Classical chip; third level (role chips: Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers) within Performers. Per-sub-type 50-result drill-downs. Auto-activate the Classical chip when search is invoked from the Classical view for context-aware default scope. |
| 10 | **Playback / queue behaviour** | `music-assistant/server` (+ frontend) | 7 | "Play Work" enqueues all movements in order. No shuffle within a Work by default. Gapless across movements of the same Work. |

Cross-cutting work that rides along: tests at every stage, docs, a "tagging your classical library" guide for users, and a provider-compatibility table showing what each streaming provider exposes.

**Suggested delivery order:** 1 → 2 → 3, then 4 + 5 + 6 in parallel, then 7 and 8 (independent — either order), then 9 builds on 8, with 10 as polish. Stage 4 alone gives well-tagged local-library users browse-level Classical functionality; Stage 8 adds basic search; Stage 9 refines the search UX. Each stage will be socialised with a short summary doc before implementation begins.

**Out of scope across all stages:** any change to MA's underlying search backend (FTS5 migration, multi-term token-AND composition, ranked results). These would be MA-wide infrastructure changes that affect every entity type's search behaviour and need their own RFC outside the classical project. Classical search at Stage 8 uses single-term substring matching against extended fields — same backend MA uses today.

## Backwards compatibility

This change is **additive only**. No existing field changes type or is removed.

| Existing field | What happens | Notes |
|---|---|---|
| `Track.artists: list[Artist \| ItemMapping]` | Unchanged. Continues to mean "headline credit". | New `Track.credits` is added alongside. |
| `Album.artists: list[Artist \| ItemMapping]` | Unchanged. | New `Album.credits` is added alongside. |
| `Track.metadata.performers: set[str]` | Kept; deprecated in docstring. | Server populates it as a derived view from `credits` for back-compat. |
| `Track.metadata.grouping: str \| None` | Kept; deprecated in docstring. | Replaced in semantics by `Track.work` when present. Acts as fallback when no Work tag exists. |

Old consumers continue working unchanged. New consumers opt in by reading the new fields. A future major version may collapse the duplication.

### Synchronisation rule for `artists` vs `credits[role=MAIN_ARTIST]`

Both fields can carry the headline credit, which raises the question of which is canonical when they're populated together. The rule:

- **`artists` is canonical for the headline credit.** It's the field consumers have been reading for years; we don't break that contract.
- **`credits` is canonical for everyone else** (composer, conductor, performers, etc.) — those don't appear in `artists` at all.
- **When `credits` is populated, every artist in `artists` must also appear as a `MAIN_ARTIST` entry in `credits`**, in the same order, with `position` matching the index in `artists`. The server is responsible for keeping the two in sync; consumers can trust either.
- A consumer reading only `credits` and filtering for `role=MAIN_ARTIST` gets the same result as reading `artists`. A consumer reading only `artists` misses non-headline roles but doesn't see anything inconsistent.

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
```

Notes:

- `MAIN_ARTIST` exists so credits can be a complete list including the headline credit — i.e. the artists in `Track.artists` will appear in `Track.credits` with `role=MAIN_ARTIST`.
- The set is scoped to roles that matter for classical music. Pop/electronic roles (`REMIXER`) and general production roles (`PRODUCER`) are intentionally omitted — they can be added in a later stage if a consumer needs them.
- The enum is forward-compatible: consumers should fall back to `PERFORMER` for unknown values added in the future.

### Populating `ORCHESTRA` / `ENSEMBLE` / `CHOIR` / `SOLOIST`

There is **no dedicated tag** for any of these four roles. They are derived from two sources:

**1. Parsing the parenthetical content of `PERFORMER` (Vorbis) / `TMCL` (ID3).** Picard's convention is `PERFORMER="Name (role-or-instrument)"`. The parser in Stage 4 maps the parens content as follows:

| Parens contains (case-insensitive substring) | Mapped role |
|---|---|
| `orchestra`, `philharmonic`, `symphony orchestra` | `ORCHESTRA` |
| `choir`, `chorus`, `chorale`, `schola` | `CHOIR` |
| `ensemble`, `quartet`, `quintet`, `trio`, `consort` | `ENSEMBLE` |
| Specific instrument (`violin`, `piano`, `soprano vocals`, …) | `SOLOIST`, with `instrument` set to the parens content |
| Empty or unrecognised | `PERFORMER`, with `instrument` set to the parens content if present |

This is a heuristic, but mirrors how Picard-tagged libraries are structured in practice. No information is lost when the heuristic falls through — anything unrecognised becomes `PERFORMER` with the original string preserved as `instrument`.

**2. MusicBrainz enrichment (Stage 6).** MusicBrainz models these as distinct Recording-Artist relationship types — `performing orchestra`, `chorus`, `instrument` (with instrument attribute), `performer` — and the enrichment provider maps them directly to the corresponding `ArtistRole`. This is canonical when MB data is available; the tag heuristic only fills the gap when it isn't.

### `Credit` (dataclass)

```python
@dataclass(kw_only=True)
class Credit(DataClassDictMixin):
    """A single role-tagged artist credit on a track or album."""

    artist: Artist | ItemMapping
    role: ArtistRole
    instrument: str | None = None      # only meaningful for SOLOIST / PERFORMER
    position: int = 0                  # ordering within a role; lower first
```

Notes:

- Free-form `instrument` string is intentional. Picard writes "violin", "piano", "soprano vocals", etc. in the Vorbis `PERFORMER` parens convention; we keep the string as-is rather than enumerate.
- `position` is **per-role**: each role group has its own ordering starting at 0. So a track with two SOLOIST entries and three PERFORMER entries has positions 0–1 within SOLOIST and 0–2 within PERFORMER, not a global 0–4 sequence. Simpler to reason about, easier to render, and avoids conflating ordering across heterogeneous roles.
- Composer Sort Order, MusicBrainz Composer ID, and similar per-role tags do not need separate fields — they are stored on the underlying `Artist` (`sort_name`, `external_ids`).
- Inherits `DataClassDictMixin` so nested `Credit` instances inside `Track.credits` / `Album.credits` serialise/deserialise via the same mashumaro pipeline as the surrounding `MediaItem`.

### `Work` (MediaItem)

```python
class Work(MediaItem):
    """
    A musical composition, distinct from any specific recording of it.

    Multiple recordings of the same work share a Work entity (matched by MusicBrainz Work MBID
    where available). Movements of a multi-part work link to the parent work.
    """

    media_type: MediaType = MediaType.WORK
    composers: UniqueList[Artist | ItemMapping] = field(default_factory=UniqueList)
    catalog_numbers: list[str] = field(default_factory=list)   # ["Op. 67", "BWV 1041", "K. 525"]
    work_type: WorkType | None = None
    parent_work: ItemMapping | None = None                     # for movements / sub-works
    arrangement_of: UniqueList[ItemMapping] = field(default_factory=UniqueList)   # source work(s) this is an arrangement of
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

- `Work` is a full MediaItem so it gets `external_ids` (for the MusicBrainz Work MBID), images, descriptions, sort_name, search_name, etc. for free. The `external_ids` set carries the new `ExternalID.MB_WORK` value, and `_MediaItemBase.mbid` is extended to read/write it for `MediaType.WORK` items the same way it does for ARTIST / ALBUM / TRACK.
- `composers` and `arrangement_of` use `UniqueList` (matching the `Album.artists` codebase pattern) — these are reference lists where a duplicate is a bug. `catalog_numbers` stays a plain `list[str]` because string duplicates are low-risk and MB sometimes legitimately returns near-duplicate catalog strings.
- `catalog_numbers` is a list because the same work can have multiple catalog references (Op. number plus a thematic catalog like K. or BWV). Stored as strings; parsing/sorting is a presentation concern.
- `WorkType` covers the most common 12 types plus `OTHER`. MusicBrainz has ~25 types; the proposed enum covers the ones that matter for browsing/grouping. `OTHER` catches the long tail. Adding new variants later is non-breaking — consumers should fall back to `OTHER` for unknown values.
- `parent_work` is optional and self-referential. Movements *can* be modelled as separate Works with a parent link (mirroring MusicBrainz), but the **default rule is parent Work only, with `movement_*` fields on Track**. A movement-Work row is created only when the source supplies a distinct MBID for it (i.e. the file's `MUSICBRAINZ_WORKID` points to the movement, or MB enrichment surfaces a movement-level Work entity). This avoids a row-count explosion — a Bach library with 8000 tracks would otherwise produce 8000+ Work rows for movement entities alone.
- `arrangement_of` captures transcriptions, orchestrations, and reductions where one Work is derived from another (Mussorgsky's *Pictures at an Exhibition* piano original ↔ Ravel's orchestration; Bach organ works transcribed for piano; opera scenes transcribed for solo instrument). MusicBrainz models these as distinct Works connected by an "arrangement of" relationship. The list form handles medleys and works arranged from multiple sources. The reverse direction ("which works are arrangements of *this* one") is derived by querying — not stored.
- `MediaType.WORK` is a new variant of the existing `MediaType` enum.

### `MediaType.WORK`

Added value to the existing `MediaType` enum. Old consumers that switch over `MediaType` will fall through to their default case, which is the same behaviour as encountering a future unknown type.

### `Period` (enum)

```python
class Period(StrEnum):
    """Classical music period / era. Used on Artist (composer) for browse filtering."""

    MEDIEVAL = "medieval"           # c. 500 – 1400
    RENAISSANCE = "renaissance"     # c. 1400 – 1600
    BAROQUE = "baroque"             # c. 1600 – 1750
    CLASSICAL = "classical"         # c. 1750 – 1820
    ROMANTIC = "romantic"           # c. 1820 – 1900
    MODERN = "modern"               # c. 1900 – 1975 (20th century)
    CONTEMPORARY = "contemporary"   # c. 1975 – present (21st century)
```

Date ranges are documentation, not enforced by the enum — they exist to anchor inference rules in `Classification policy`. The seven buckets match Apple Music Classical, Roon, IMSLP, and Wikipedia consensus; edge composers (Beethoven straddles Classical/Romantic; Schoenberg straddles Romantic/Modern) get their closest-fit period. A composer's `period` is the **primary** period of their output, not a list — filter UX is forgiving and a single primary value keeps queries simple.

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

For compilation albums, `Album.composers` and `Album.conductors` may return long lists — a "100 Greatest Classical Hits" compilation could have 50+ distinct composers. This is intentional: the data is honest about what's there, and display logic in the frontend can collapse to a placeholder like "Various composers" above some threshold. This is *not* the same as the existing `Album.artists = [Various Artists]` pattern (which uses a single placeholder Artist entity); the new credit-based properties always carry the actual list.

### `Artist`

Additive field only:

```python
@dataclass
class Artist(MediaItem):
    # ... existing fields unchanged ...

    period: Period | None = None
```

Notes:

- Populated for Artists with a `COMPOSER` role in any of their track credits; null for performer-only artists (a Karajan or Berlin Philharmonic Artist row has no `period` because performers span periods across their repertoire).
- Sourced from: (1) MB enrichment (Stage 6) — composer Artist's birth/death dates map to the bucket their primary output falls in; (2) tag-based fallback (Stage 4) — period name parsed out of a multi-value `GENRE` tag on a track whose composer credit resolves to this Artist; (3) future manual override (out of scope for the initial implementation).
- Used as a filter chip on the Composers tab and (transitively, by joining through `track_artists` where `role=COMPOSER`) the Works tab. Not exposed as a sort axis — periods have no natural sort order users browse by.

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
| `Work.arrangement_of` | — | — | — | Work-Work "arrangement of" relationship |

Tags with no MusicBrainz equivalent (e.g. some `TXXX` fields written by certain taggers) and MB fields with no tag equivalent (conductor MBID, performer MBIDs, arrangement relationships) are accepted gracefully — the parser fills what it can; the enrichment provider fills the rest.

### Tag fallbacks for non-Picard taggers

Some users tag with tools other than Picard — most notably Roon (well-regarded for classical handling) and the Classical Extras Picard plugin. These tools write to different tag names than the MusicBrainz Picard standard mapping. To ensure those libraries work without retagging, the parser reads a small set of fallback tag names per field. Implementation note: **code that reads non-standard tag names should carry an inline comment identifying the source** (e.g. `# Roon convention` or `# Classical Extras plugin`) so future maintainers know where the tag name originates and can find the relevant external documentation.

| Field | Standard | Roon | Classical Extras | Notes |
|---|---|---|---|---|
| Movement name | `MOVEMENTNAME` (`MVNM`) | `PART` | `MOVEMENT` | Picard's iTunes-standard `MOVEMENTNAME` is canonical; Roon and Classical Extras both diverge. |
| Work | `WORK` (`TIT1`) | `WORK` | `WORK` / `groupheading` / `top_work` | Standard tag matches across all three. |
| Section (intermediate level) | — | `SECTION` | — | Roon-only; covers e.g. opera Act 1. See open question. |
| Ensemble credit | `PERFORMER="Name (orchestra)"` etc. | `ENSEMBLE` (dedicated tag) | `soloists` / `involved people` | Roon writes a dedicated tag — cleaner input, no parens parsing. |
| Soloist credit | `PERFORMER:instrument="Name"` | `SOLOIST` (dedicated tag) | `soloists` | Same situation as ensemble. |
| Generic credit with role | `TMCL` (ID3) / `PERFORMER` parens (Vorbis) | `PERSONNEL="Name - Role"` | `soloists` / `involved people` | Roon's `PERSONNEL` parses on ` - ` with surrounding spaces (avoids hitting hyphenated names like "Lloyd-Webber"). |
| Orchestra | (parens-keyword detection in `PERFORMER`) | `PERSONNEL="Name - Orchestra"` | (parens or specific tag) | **Roon does not support an `ORCHESTRA` tag at all** — orchestras come through `PERSONNEL` only. Picard-tagged files use the parens-keyword detection rules documented earlier. |
| Classical-flag | — | — | `is_classical` | Classical Extras only. |

**Classical Extras encodes hierarchy with trailing `::` separators.** Real-world tag values from Classical-Extras-tagged libraries often look like `groupheading: "Orchestral Suite No. 3 in D, BWV 1068 (1730)::"` or `top_work: "Orchestral Suite No. 3 in D, BWV 1068::"` — the trailing `::` is the plugin's hierarchy delimiter, not part of the title. **The parser should strip trailing whitespace and `::` separators** from any Classical Extras fallback tag before using its value. Without this, Work titles end up containing literal `::` in MA's library which is jarring.

## Matching policy

How instances of these entities are matched and grouped at runtime. This belongs in the spec because it constrains what the data shape needs to support; the actual matching code lives in the server.

### Canonical entity resolution via MBID

**MBID is authoritative for any entity it identifies.** When a tag or provider response carries both an entity name and a MusicBrainz ID, the MBID determines the canonical entity; the supplied name is treated as a hint only. This rule applies uniformly across artist credits and works.

| Tag (or provider equivalent) | Resolves | Stored on |
|---|---|---|
| `MUSICBRAINZ_ARTISTID` / `MUSICBRAINZ_ALBUMARTISTID` | Headline artist | `Artist.external_ids` (`MB_ARTIST`) |
| `MUSICBRAINZ_COMPOSERID` | Composer credit | `Credit.artist.external_ids` (`MB_ARTIST`) |
| `MUSICBRAINZ_CONDUCTORID` (where present) | Conductor credit | `Credit.artist.external_ids` (`MB_ARTIST`) |
| `MUSICBRAINZ_PERFORMERID` (where present) | Performer / Soloist credit | `Credit.artist.external_ids` (`MB_ARTIST`) |
| `MUSICBRAINZ_WORKID` | Work entity | `Work.external_ids` (`MB_WORK`) |

**Resolution rule:**

1. If a tag carries an MBID, look up the canonical entity via the MusicBrainz provider. The looked-up name supersedes the tag's text value.
2. Two tracks referencing the same MBID with different text spellings (e.g. "Béla Bartók" vs. "Bela Bartok") resolve to the same canonical entity — solving the diacritic / transliteration / language-variant problem at the data layer rather than via fuzzy string matching.
3. If no MBID is present, the text tag value is used as-is. **Fuzzy cross-track matching is not attempted** — surface as a suggestion via the existing OTHER VERSIONS UI when available, never auto-merge.

The implementation primarily lives in Stage 4 (tag parsing) and Stage 6 (MusicBrainz enrichment); Stage 1's model already supports it via `Artist.external_ids` and the new `Work.external_ids`.

### Work matching

**MusicBrainz Work MBID is the only reliable signal for "same Work".** No fuzzy match on composer + work title can reliably catch:

- Translations: *Spiegel im Spiegel* / *Mirror in the Mirror* / *Miroir dans le miroir* — same MB Work, no string overlap.
- Catalog-number variants: "Op. 67" / "Opus 67" / "5th symphony" / "Symphony No. V" — same Work, varying tag conventions.
- MB editor stylistic differences: original-language vs locale-aliased titles.

The matching rule is therefore:

1. **MBID match → same Work, merge.** Multiple recordings, multi-language tag variants, and box-set vs single-album appearances all collapse correctly when MBIDs are present.
2. **No MBID, exact composer + title match → same Work, merge.** Conservative; only catches the easy cases.
3. **No MBID, fuzzy match → suggestion only.** Surface as a "these might be the same work" link via the existing OTHER VERSIONS UI pattern. Never auto-merge.
4. **No match at all → composer-first browsing is the human fallback.** The Classical view's composer index lets the user navigate Bach → his works → recordings of each, and find related items manually even when matching fails. This is what classical listeners already do; we just make it faster.

### Arrangements

Arrangements/transcriptions are deliberately **separate** Works in MusicBrainz, linked via the "arrangement of" relationship — captured in `Work.arrangement_of`. They must not be auto-merged with their source work, but the Work page should surface the relationship as "related works" (Mussorgsky's piano original ↔ Ravel's orchestration; Bach organ ↔ piano transcriptions). The bidirectional relationship is stored once (on the arrangement) and queried in both directions.

### Multi-value tag handling (semicolon-separated)

**All credit-bearing tags must be evaluated for multiple semicolon-separated values.** Picard's convention for fields that can legitimately carry multiple references is **`value1; value2; value3`** (semicolon + space). Real-world examples:

- `LYRICIST: "Giuseppe Giacosa; Luigi Illica"` — two librettists (Puccini's *La bohème* example).
- `COMPOSER: "Composer A; Composer B"` — collaborative works (e.g. Lennon–McCartney, *Requiem* completions).
- `CONDUCTOR: "..."` — rare but legal for multi-conductor recordings.
- `MUSICBRAINZ_WORKID: "uuid1; uuid2"` — multi-Work tracks (covered in detail below).
- `MUSICBRAINZ_ARTISTID: "uuid1; uuid2"` — when `ARTISTS` or similar carries multiple values.

**Parser rule:** any tag whose semantic is "a list of references" splits on `; ` and produces one `Credit` (or external-ID, or Work link) per value, in order. `position` on `Credit` rows reflects the split order. Tags that are semantically single-valued (e.g. `TITLE`, `ALBUM`, `WORK` when there's no parallel multi-MBID) are taken as-is even if they happen to contain a semicolon.

The general rule applies uniformly; the WORK-specific subsection below covers the additional nuance that multi-value WORKID corresponds to parent+movement or arrangement+source relationships.

### Multi-value `MUSICBRAINZ_WORKID` and `WORK` tags

Picard writes semicolon-separated multi-value `MUSICBRAINZ_WORKID` and `WORK` tags when a Recording is linked to more than one Work in MusicBrainz. Two common cases:

1. **Parent + movement Work** — the Recording is linked to a parent Work (e.g. *Sonata for Piano No. 14 in C-sharp minor, Op. 27 No. 2 "Moonlight"*) and a movement Work (e.g. *"...: I. Adagio sostenuto"*).
2. **Arrangement + source Work** — the Recording is linked to the arrangement Work and the source Work it was arranged from (e.g. Bach's *Air* transcribed for organ, linked to the arrangement plus the original Suite movement).

Picard's convention is **most general → most specific**, semicolon-space separated. Parser policy:

- **Split on `; `** (semicolon + space) to get parallel lists of MBIDs and titles, paired by position.
- **The last entry is the canonical primary** — the Work the Track is most directly performing. For parent+movement that's the movement; for arrangement+source that's the arrangement. `Track.work` points here.
- **Earlier entries are resolved via Stage 6 MusicBrainz enrichment** into either `Work.parent_work` (parent+movement) or `Work.arrangement_of` (arrangement+source). The relationship type cannot be reliably determined at parse time from tag values alone; Stage 6 looks up each Work in MB and reads the actual relationship.
- **Without MBIDs**, a multi-value `WORK` tag is ambiguous (could be parent+child, arrangement+source, or duplicates). Parser falls back to last-value-as-primary and discards earlier values. Users with rich multi-Work tagging should ensure MBIDs are populated.

The same last-is-most-specific convention applies to multi-value `MUSICBRAINZ_RECORDINGID` (rare in practice).

### Within-track artist name resolution

The general rule in "Canonical entity resolution via MBID" above is **MBID is authoritative, name is a hint, fuzzy matching is not attempted**. The parser maintains one deliberate carve-out from that rule, scoped tightly to within-track context.

When a track has multiple credits, and some carry an MBID while others don't, the parser checks for **substring containment** between a text-only credit name and an MBID-canonical credit name *on the same track*. If a text-only credit name is a substring of an MBID-anchored name on the same track, the parser treats them as the same Artist and points the text-only credit at the canonical entity.

Real-world example:

```
Artist: Dame Moura Lympany              (with MUSICBRAINZ_ARTISTID → canonical Artist)
performer:piano: Moura Lympany          (no per-credit MBID)
```

Without the carve-out, MA would create two Artists ("Dame Moura Lympany" and "Moura Lympany"). With the carve-out, the parser recognises *"Moura Lympany"* as a substring of *"Dame Moura Lympany"* within the same track context and merges the credit onto the canonical Artist.

The same logic resolves common honorific / formal-name variations:

- *"Karajan"* ⊂ *"Herbert von Karajan"*
- *"Bach"* ⊂ *"Johann Sebastian Bach"*
- *"Mutter"* ⊂ *"Anne-Sophie Mutter"*

**The carve-out is intentionally narrow:**

- **Within a single track only** — the disambiguation context is tight (only candidates are other artists credited on the same track), so false-positive risk is low.
- **Substring match only** — no Levenshtein, no token-overlap, no phonetic matching. Plain substring of the credit text inside the canonical name.
- **Anchored by an MBID** — at least one credit on the track must carry an MBID for the carve-out to fire. Text-only-to-text-only matching is not attempted even within a track.

**Cross-track** name resolution is still not attempted; fuzzy matches across tracks surface as suggestions via OTHER VERSIONS rather than auto-merging. The within-track rule is an exception precisely because of its tighter context. Worth revisiting if false-positives emerge in practice (e.g. two genuinely distinct artists on the same track whose names happen to share a substring).

### Partial recordings

A track containing only part of a Work (e.g. just *The Great Gate of Kiev* from *Pictures at an Exhibition*) is modelled in MusicBrainz as a Recording-Work relationship with a "partial" attribute. See open questions for whether to surface this as a Track flag.

### Performance grouping within an album

Real-world classical compilations sometimes contain multiple recordings of the *same* Work on a single album — e.g. an album with three different recordings of Beethoven's 5th, each contributing 4 movements (12 tracks total, all linked to the same Work). Without disambiguation, all 12 movements would collapse under one Work entry with confused movement numbering.

**Rule (heuristic, no new tag required):** within a single album, group movements that share **(Work + conductor + ensemble)** as one performance. Three Karajan/Berlin movements + four Bernstein/Vienna movements + four Solti/Chicago movements naturally split into three performance groups based on the differing credit pairs. Picard-tagged files that include proper conductor and ensemble credits get this grouping for free.

This heuristic is sufficient for every concrete example we've identified. If a real-world album turns up where it fails (a hypothetical case would be the same conductor + same ensemble recording the same Work twice on one album, but no verified example), we can revisit. See open questions for a sketch of the deferred field.

### Scale considerations

This is not a model concern but worth flagging since it informs query design downstream: real classical libraries hit the tens of thousands of tracks per composer (8000+ Bach tracks is realistic). The composer-level browse view in the Classical view **must be Work-grouped, not a flat track list** — a composer page is a list of Works first, with recordings nested underneath. The data shape supports this; the server queries and frontend pagination need to deliver it efficiently.

## Classification policy

Two related runtime decisions: (1) when does a track get a `Work` entity attached, and (2) when does a track appear in the Classical view? The rules differ because the cost of getting them wrong differs.

### Classical view scope by MediaType

The Classical view sources exclusively from `Track`, `Album`, `Artist`, and `Work` entities. **`Radio`, `Podcast`, `PodcastEpisode`, `Audiobook`, `Genre`, `Folder`, and other non-music-library MediaTypes are excluded regardless of their genre tags.** A radio station tagged with genre "Classical" remains in the standard Radio browse and does not surface anywhere in the Classical view; same for podcasts, audiobooks, etc.

This is **opt-in by MediaType, not opt-out by exclusion list** — the Classical view's queries explicitly filter to the in-scope types rather than enumerating types to exclude. New MediaTypes added in future versions are excluded by default until the Classical view's query is extended.

The classification rules below ("When to create a Work entity", "When a track appears in the Classical view", "Album-level classical classification") apply only within this scope — they govern Track / Album / Artist / Work population and visibility, not Radio / Podcast / Audiobook.

### When to create a `Work` entity

**Conservative.** A Work should be created only when there is positive evidence that the track is part of a defined composition. In priority order:

1. **`MUSICBRAINZ_WORKID` is present** → match or create the Work; canonical signal.
2. **`WORK` tag is present** (or the plugin fallbacks `groupheading` / `top_work`) → create the Work, deduplicate by composer + title.
3. **Composer is present AND movement info is present** (`MOVEMENTNAME`, `MOVEMENTNUMBER`, or `groupheading`) → infer a Work from the available signal. Multiple movements implies a multi-part composition.
4. **Otherwise: no Work.** A track with only a composer credit and nothing else does **not** become a Work.

The reason for being strict: a permissive rule (any composer credit → Work) pollutes the Works browse with thousands of one-offs from film scores, jazz standards, hip-hop sampling credits, and singer-songwriters who self-credit. The Works browse loses its value if it isn't restricted to actual compositions.

### When a track appears in the Classical view

**More liberal.** False negatives (classical track missing) feel broken; false positives (a soundtrack track appearing) feel mildly annoying. Default toward inclusion. A track appears in the Classical view if **any** of:

1. **`is_classical=1` tag is set** — explicit user signal, definitive.
2. **Track has a `Work` attached** (per the Work-creation rules above) — definitive.
3. **Genre tag matches a classical genre** (Classical, Baroque, Symphony, Concerto, Opera, Sonata, Choral, Chamber music, …).
4. **Track is on an album classified as classical** (see album-level rule below).

### Album-level classical classification

An album is classified as classical if a majority of its tracks satisfy any of the per-track rules above. Once an album is classical, **all** its tracks appear in the Classical view, even ones with thin metadata. This catches the "single Pärt track on a compilation that didn't get tagged with `is_classical`" case — the rest of the album is classical, so the under-tagged track inherits.

### Expected outcomes

| Library content | Outcome |
|---|---|
| Bach box-set with full tags (Work + composer per track) | All tracks in Classical view, grouped under hundreds of Works |
| Pärt compilation with thin tags | All tracks in Classical view via album-level inheritance |
| Hans Zimmer film score (composer credits, "Soundtrack" genre, no Work info) | **Not** in Classical view; no Works created |
| Jazz album with composer credits ("Take Five" — Paul Desmond) | **Not** in Classical view; no Works |
| Singer-songwriter album where artist self-credits as composer | **Not** in Classical view; no Works |
| Classical compilation tagged only with basic fields (`ARTIST` / `TITLE` / `GENRE=Classical`, no composer / conductor / performer / work info) | Appears in the Classical view via the genre rule. Contributes only to the Performers / All chip — absent from the Composers tab, Works tab, and role-specific Performer chips because the structured data isn't there. Best browsed via the standard Albums view. (See decisions log on thin-tag compilations.) |

### Populating `Artist.period`

`Artist.period` is set on composer Artists only. Two tiered sources, applied in **priority order** — first non-null wins; existing values are not overwritten by lower-priority sources:

1. **Genre period from any source (Stage 4) — primary, user-controlled.** The Stage 4 parser inspects genre values from **all sources MA already reads**:

   - Multi-value `GENRE` tag on tracks where this Artist has a `COMPOSER` credit (Vorbis comments, ID3 `TCON`, MP4 `©gen`, etc.).
   - `<genre>` elements in `artist.nfo` for this composer (Kodi convention; see `filesystem_local/__init__.py:1189-1190`).
   - `<genre>` elements in `album.nfo` for albums where this composer has track credits.

   If any period name appears in the combined genre set (case-insensitive match: `Baroque`, `Romantic`, `Medieval`, `Renaissance`, `Classical`, `Modern` / `20th Century`, `Contemporary` / `21st Century`), the corresponding `Period` value is stamped on the composer Artist. Source precedence within this tier when multiple sources name different periods: `artist.nfo` > `album.nfo` > track tags (NFO files are explicitly composer-centric / album-centric metadata, so the user's intent is clearer there than on a per-track tag). The first source-and-track to provide a period wins; subsequent conflicting values are not reconciled.

   **Tag-as-override deliberate inversion.** This sits *above* MB enrichment, not below, because period for boundary composers is genuinely subjective (Beethoven could reasonably be Classical or Romantic depending on which works the user listens to most) — there's no canonical answer to enforce. Giving genre priority makes period **user-overridable today without waiting for a manual-override UI**: a user who disagrees with the MB-inferred placement of a composer just adds the desired period to a `<genre>` element in that composer's `artist.nfo` (easiest, single-file edit) or to a GENRE tag on any of that composer's tracks. Inversion is limited to this one field; the MBID-canonical rule still applies everywhere else.

2. **MusicBrainz enrichment (Stage 6) — secondary, automatic.** When the GENRE-tag path is silent and the Artist has an MBID with birth/death dates available, the period is inferred from the composer's **floruit** (productive peak), approximated as the midpoint of `(birth_year + 25, death_year − 5)` — roughly the composer's prime working years:

   | Floruit midpoint | Period |
   |---|---|
   | before 1400 | `MEDIEVAL` |
   | 1400 – 1600 | `RENAISSANCE` |
   | 1600 – 1750 | `BAROQUE` |
   | 1750 – 1820 | `CLASSICAL` |
   | 1820 – 1900 | `ROMANTIC` |
   | 1900 – 1975 | `MODERN` |
   | after 1975, or still living | `CONTEMPORARY` |

   Floruit-based rather than death-date based: a composer who lived long into the next stylistic period without producing significant new work there should still be bucketed by their primary output. Worked examples:

   | Composer | Birth / death | Floruit midpoint | Bucket | Sanity check |
   |---|---|---|---|---|
   | Handel | 1685 – 1759 | 1720 | `BAROQUE` | ✓ (death-date rule wrongly gave `CLASSICAL`) |
   | Bach | 1685 – 1750 | 1717 | `BAROQUE` | ✓ |
   | Mozart | 1756 – 1791 | 1774 | `CLASSICAL` | ✓ |
   | Haydn | 1732 – 1809 | 1769 | `CLASSICAL` | ✓ |
   | Beethoven | 1770 – 1827 | 1808 | `CLASSICAL` | ✓ (canonical placement; users who want Romantic use the GENRE tag) |
   | Schubert | 1797 – 1828 | 1823 | `ROMANTIC` | ✓ (right at boundary; most placements agree) |
   | Brahms | 1833 – 1897 | 1875 | `ROMANTIC` | ✓ |
   | Mahler | 1860 – 1911 | 1888 | `ROMANTIC` | ✓ |
   | Schoenberg | 1874 – 1951 | 1923 | `MODERN` | ✓ |
   | Pärt | 1935 – | 1985 (assuming current year) | `CONTEMPORARY` | ✓ |

   For living composers, the floruit is computed against the current year as the death-date stand-in. Refreshed when MB data is re-fetched; the field is single-valued so a living composer's bucket may shift over time (usually only matters at the 1975 boundary).

3. **Manual override (future polish).** A dedicated per-Composer override UI is out of scope for the initial implementation; the GENRE-tag path serves as the override mechanism for now. When the dedicated override lands, it sits above both sources.

`Artist.period` is null when neither source resolves — performer-only artists (no `COMPOSER` credit), composers without an MB-linked MBID where no period genre exists on any track tag or NFO file, and composers whose MB record lacks birth/death dates. The Composers tab's period filter chip treats null as "unknown" and excludes those artists from period-specific filters but keeps them in the "All periods" view.

**Edge cases.** Composers spanning two periods (Beethoven, Schubert, Schoenberg, Mahler) get their closest-fit single period via the floruit rule; users disagreeing with the placement use the GENRE-tag path to override. Stylistic pastiches (a 1985 piece written in Baroque style) accept their composer's period today; a future `Work.period` override addresses per-piece pinning if/when real demand emerges.

### User overrides (future polish)

A per-track or per-album "treat as classical" / "exclude from classical" override is the cleanest fix for users whose libraries don't match these defaults — e.g. someone who *does* want their Williams scores in the Classical view, or who *doesn't* want a particular contemporary album. Same mechanism works for hiding individual tracks from the view without retagging. Out of scope for Phase 1; additive when added.

### Computed `is_classical` fields exposed to clients

The classification policy above defines when a track, album, or artist is classical. To enable classical-aware UI rendering and cross-linking *without forcing clients to replicate the classification logic*, three derived boolean fields are exposed on the wire:

| Field | Definition | Use case |
|---|---|---|
| `Track.is_classical: bool` | True if the track satisfies any rule under "When a track appears in the Classical view" (is_classical tag, has Work attached, classical genre, on classical album). | Conditionally render the extended Credits panel above APPEARS ON on Track detail; classical-aware track context menus. |
| `Album.is_classical: bool` | True per "Album-level classical classification" — majority of tracks have `Track.is_classical=True`. | Conditionally render classical-aware album views; surface "view as Classical album" affordance in the standard Albums view. |
| `Artist.is_classical: bool` | True if the artist has any credit on a track where `Track.is_classical=True`. | **Cross-linking** — conditionally render classical sections on the shared Artist detail page; surface "View in Classical view" links / badges; highlight artists in search results that have Classical-view content. |

All three are computed server-side, cached for performance, and recomputed when the underlying credits or classification inputs change. Clients treat them as **read-only flags** and do not replicate the classification logic locally.

**The Classical view's tab indices filter by `Track.is_classical=True`.** The Composers tab is "artists with `role=COMPOSER` on at least one classical track", not "artists with any composer credit anywhere"; the Performers tab and its chip filters use the same scoping. This prevents non-classical composer credits (e.g., a hip-hop sample crediting Beethoven, or a pop songwriter who happens to be credited as `COMPOSER` on a pop track) from polluting the Classical view's browse axes. The same scoping applies to Artist.is_classical above: an artist with a `COMPOSER` credit only on a non-classical track is not classical-flagged.

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

## Frontend integration approach

This spec only defines the data shape, not the UI. But two frontend decisions are worth recording here because reviewers will ask.

### Coexistence with standard browse views

The Classical view is a **parallel lens, not a replacement**. The standard Artists / Albums / Tracks views remain unchanged and continue to be the home for album-as-curated-unit playback — compilation box sets, recital discs, mixed-composer programmes, and any release where the album itself is the meaningful object. Classical-aware users get the additional structure via the Classical view; everyone else sees no change. The two coexist on the same data with no duplication of storage.

### Where it lives in the navigation

**Decided: a single top-level "Classical" entry with internal tabs.** Sub-navigation lives *inside* the Classical view, not as separate top-level menu items.

- **Why a single top-level entry, not several** (i.e. not promoting Composers / Works / Performers to main-nav siblings of Artists / Albums): the main nav is already approaching capacity and reserving space for upcoming shortcuts; classical sub-views are only useful to users with classical content; promoting them clutters the nav for everyone for the benefit of some.
- **Why internal tabs, not a flat single-list view**: classical listeners come into the library from genuinely different entry points (composer-first, performer-first, work-first). No single ordering serves all of them, so the entry point is itself a choice the user makes.
- **Trade-off acknowledged**: sub-tabs inside a top-level view is a new UI pattern for MA. No existing view does this. Worth raising explicitly in the frontend PR so it's a deliberate decision rather than a precedent set by accident.

### Tab layout inside the Classical view

**Decided: three tabs — Composers / Works / Performers.** No Search tab; search lives in the global search bar (see "Search integration" below).

- **Composers** — index of artists who appear with `role=COMPOSER` on at least one track where `Track.is_classical=True` (see "Computed `is_classical` fields" under Classification policy). Click → composer detail page (works listed underneath, not albums).
- **Works** — index of `Work` entities. Click → Work detail page (multiple recordings of the same composition collapsed under one entry).
- **Performers** — index of artists who appear in any **performing role** — `CONDUCTOR` / `ORCHESTRA` / `ENSEMBLE` / `CHOIR` / `SOLOIST` / `PERFORMER` — on at least one track where `Track.is_classical=True`. **Filter chips at the top of the tab let the user narrow by role:** *All / Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers*. The chip pattern reuses the existing filter convention in MA (e.g. the album-type filter that lets users narrow to "live", "soundtrack", etc.). Click an entry → performer detail page (works performed, conductors collaborated with, recordings in library).

  The **Other performers** chip catches credits with `role=PERFORMER` — our catch-all role for credits that couldn't be more specifically classified (instrument missing from the tag, generic "performer" MB relationship without attributes, backing/session musicians, etc.). Including this chip explicitly makes sure no credit is invisible to a chip-narrowed view; without it those performers would only show under "All" and users would silently miss them.

  **Creator roles** (`LYRICIST`, `ARRANGER`) are **not surfaced in the Performers tab** — they're not performers, and major classical streaming services (Apple Music Classical, IDAGIO) don't expose lyricist/librettist as a browse axis either. These credits remain visible in the extended credits panel on Track detail and on Recording rows; users wanting to navigate to a lyricist do so via the credit chip on Track detail. See Decisions log entry on creator-role browse surface.

**Why one Performers tab instead of separate Conductors / Ensembles tabs:** classical listeners think in terms of "who's playing this" — and the answer might be a person (conductor, soloist) or a group (orchestra, ensemble, choir). Splitting into two tabs duplicates the navigation; combining with role chips gives the same browsing power with less surface area. Chamber music and a-cappella choral music — which have no conductor — are also covered naturally by this single-tab approach, where a separate Conductors tab would miss them.

### View structure (low-fidelity sketches)

These sketches are **structural, not visual** — they exist to anchor the navigation discussion and make element placement concrete for review. Final visual design (typography, spacing, density, mobile layout, light/dark theming) is out of scope here and will be done in the frontend PR (Stage 7).

The destination is always the same Work detail page regardless of how the user got there — clicking a Work from the Works tab, Composer detail, Conductor detail, or search all open the same page. The frontend reuses the Works-list component with different filter parameters; only the surrounding context (composer / performer header) differs.

**Composers tab** — index of all composers in the library:

```
┌─ Classical ── [Composers] Works  Performers ────────┐
│  Sort: name ▼      Search: [               ]       │
│                                                     │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐          │
│  │ JSB │ │ LvB │ │ WAM │ │ FC  │ │ AP  │  ...     │
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘          │
│  Bach    Beethoven Mozart  Chopin  Pärt           │
│  212 wks 157 wks  89 wks  43 wks  62 wks          │
└─────────────────────────────────────────────────────┘
```

**Composer detail** — the composer's works listed:

```
┌─ Classical / Composers / Bach ──────────────────────┐
│  ┌────┐  Johann Sebastian Bach                      │
│  │JSB │  1685 – 1750                                │
│  └────┘  212 works · 8,043 recordings               │
│                                                     │
│  Sort: catalog ▼   Filter by type: [All ▼]         │
│                                                     │
│  Brandenburg Concerto No. 1   BWV 1046  14 rec.    │
│  Brandenburg Concerto No. 2   BWV 1047  12 rec.    │
│  Brandenburg Concerto No. 3   BWV 1048  18 rec.    │
│  Brandenburg Concerto No. 5   BWV 1050  21 rec.    │
│  Goldberg Variations          BWV 988    9 rec.    │
│  Mass in B minor              BWV 232    7 rec.    │
│  ...                                                │
└─────────────────────────────────────────────────────┘
```

**Works tab** — all works across all composers, browseable directly:

```
┌─ Classical ── Composers [Works] Performers ────────┐
│  Sort: composer ▼  Type: [All ▼]  [           ]    │
│                                                     │
│  Bach        Brandenburg Concerto 1   BWV 1046     │
│  Bach        Brandenburg Concerto 2   BWV 1047     │
│  ...                                                │
│  Beethoven   Symphony No. 5           Op. 67       │
│  Beethoven   Symphony No. 9           Op. 125      │
│  ...                                                │
│  Pärt        Spiegel im Spiegel                    │
│  ...                                                │
└─────────────────────────────────────────────────────┘
```

**Work detail** — multiple recordings of one composition (the genuinely new page type):

```
┌─ Classical / Works / Brandenburg Concerto No. 5 ────┐
│                                                     │
│  Brandenburg Concerto No. 5 in D major              │
│  J.S. Bach · BWV 1050 · Concerto                   │
│                                                     │
│  RECORDINGS                                         │
│                                                     │
│  ▶ Karajan / Berlin Philharmonic (1973)            │
│      I.   Allegro                          9:54    │
│      II.  Affettuoso                       5:38    │
│      III. Allegro                          5:21    │
│                                                     │
│  ▶ Pinnock / English Concert (1982)                │
│      I.   Allegro                         10:12    │
│      II.  Affettuoso                       5:01    │
│      III. Allegro                          5:33    │
│                                                     │
│  ▶ Marriner / ASMF (1985)                          │
│      I.   Allegro                          9:48    │
│      II.  ...                                       │
│                                                     │
│  RELATED WORKS                                      │
│  (none — not an arrangement of another work)        │
└─────────────────────────────────────────────────────┘
```

Click ▶ on the recording header → queue all movements gapless. Click an individual movement → play that one.

**Performers tab** with the Conductors chip selected:

```
┌─ Classical ── Composers Works [Performers] ────────┐
│  [All] [Conductors] [Orchestras] [Chamber]         │
│  [Choirs] [Soloists] [Other]                       │
│                                                     │
│  Sort: name ▼      Search: [               ]      │
│                                                     │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐         │
│  │ HvK │ │ LB  │ │ GS  │ │ CMD │ │ JEG │  ...    │
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘         │
│  Karajan Bernstein Solti  Davis   Gardiner       │
└─────────────────────────────────────────────────────┘
```

**Conductor detail** — works performed by this conductor:

```
┌─ Classical / Performers / Karajan ──────────────────┐
│  ┌────┐  Herbert von Karajan                        │
│  │ HvK│  1908 – 1989 · Conductor                    │
│  └────┘  312 recordings · 47 works                  │
│                                                     │
│  WORKS PERFORMED                                    │
│  Beethoven, Symphony No. 5             8 rec.      │
│  Beethoven, Symphony No. 9             6 rec.      │
│  Brahms, Symphony No. 1                4 rec.      │
│  Mahler, Symphony No. 5                3 rec.      │
│  ...                                                │
│                                                     │
│  ENSEMBLES                                          │
│  ┌─────┐ ┌─────┐                                   │
│  │ BPO │ │ VPO │                                   │
│  └─────┘ └─────┘                                   │
└─────────────────────────────────────────────────────┘
```

#### Sort defaults per view

- **Works tab:** composer name (default); secondary options for work title, year of composition, recording count.
- **Composer detail:** catalog number (default — Op. / BWV / K. order is canonical); options for work title, work type, recording count. The work-type filter affordance lives here too.
- **Conductor / Soloist / Orchestra detail:** composer name (default — groups all Beethoven together, then all Brahms); options for work title, recording count.
- **Work detail:** chronological by recording year (default); options to sort recordings by conductor or orchestra name.

#### Per-performer scoping of counts on Performer detail pages

All per-row counts and header summary statistics on Performer detail pages (Conductor / Soloist / Orchestra / Ensemble / Choir / Other performer detail) are **scoped to this performer's contributions**, not library-wide totals. Concretely:

- **Header summary** ("312 recordings · 47 works" on Karajan's page) = Karajan's recordings and Karajan's works, not the library's total.
- **Per-Work `recording_count`** in the "Works Performed" section (e.g. "Beethoven, Symphony No. 5 · 8 rec." on Karajan's page) = Karajan's 8 recordings of Beethoven 5, not the library-wide count of Beethoven 5 recordings.
- **Per-collaborator counts** (if rendered, e.g. "Berlin Philharmonic · 124 recordings") = recordings of this performer collaborating with that collaborator, not the collaborator's total.

This is the same principle as Decisions log #15's contextual filter on Work detail: when the URL or query context names a performer, every per-row metric on the page is filtered by that performer at the server query layer, not aggregated globally and rendered post-hoc by the client. Stage 3d API endpoints serving performer-scoped routes (e.g. `music/works/library_items` with `filter_by_artist_id`) must compute counts with the performer filter applied.

#### "Other tracks" sections on Composer and Performer detail pages

A composer or performer may have credits on tracks that lack `Work` linkage — typically when a track has a `COMPOSER` (or non-composer credit) but no `WORK` / `MUSICBRAINZ_WORKID` tag, so no `Work` entity is created (per Classification policy → "When to create a Work entity"). Without a Work, those credits would be invisible on the entity's detail page if it only shows Work-centric content.

To prevent empty / partially-empty detail pages in those cases, both Composer detail and Performer detail render a secondary section after the canonical Works list:

**Composer detail:**

1. **Works** (primary) — canonical compositions sorted by catalog number (default).
2. **Other tracks** (secondary) — tracks credited to this composer where `Track.work IS NULL`. Hidden when empty. Sort options: **Name** (default, alphabetical by track title), **Year** (release year), **Date added**. Album-grouping sort deliberately omitted (would require a new `album.search_name` SORT_KEYS entry; not enough value to justify).

**Performer detail** (Conductor / Soloist / Orchestra / Ensemble / Choir / Other performer):

1. **Works performed** (primary) — canonical compositions where this performer has a credit on at least one linked track. Per-Work `recording_count` scoped to this performer (see "Per-performer scoping of counts" above).
2. **Other tracks** (secondary) — tracks where this performer has any non-composer credit and `Track.work IS NULL`. Hidden when empty. Same sort options as Composer detail's Other tracks: **Name** (default), **Year**, **Date added**.

The "Other tracks" surface is **local to detail pages**, not a global browse axis. The Classical view's Works tab stays Work-centric — synthetic / placeholder Work entities are **not** created for thin-tagged tracks (rejected: pollutes the global Works tab, groups unrelated tracks under fake aggregates, conflicts with the conservative work-creation policy). The fix lives only where the entity-centric context justifies it — on the detail page that knows about the specific entity's credits.

### Search integration

Classical search lives in **the existing global search bar**, not as a tab inside the Classical view. The integration is staged across two PRs to keep review tractable:

**Stage 8 — Basic Classical chip.** Add a *Classical* master chip to the existing global search alongside the current chips (Artists / Albums / Tracks / Playlists / etc.). When selected, the chip returns up to 50 mixed classical results — composers, works, performers, classical-credited tracks — in a flat list. Single-term substring match against extended `search_name` fields and role-typed credits. No nested filtering, no sub-categorisation. Demoable on its own; immediate value.

**Stage 9 — Nested chip hierarchy.** Extends the Classical chip with a second level of chips (Composers / Works / Performers) when activated, plus a third level inside Performers for role narrowing (Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers). This is genuinely new chip-component behaviour for MA — flat chip rows have always been one level deep. Worth flagging explicitly to the head devs because it requires component changes beyond just adding chip values.

When the user invokes search from inside the Classical view, the Classical chip is auto-activated so results are pre-scoped to classical without the user having to click it.

### Future polish: instrument filter

When the Performers tab (or Stage 9's Performers sub-chip in global search) is narrowed to *Soloists*, a further instrument-level filter would let users narrow to "all violinists" or "all piano recordings". The data already supports this (`Credit.instrument` is populated by the parser). Worth adding once the basic structure has shipped and we have feedback. Implementation note: naive substring matching on `"violin"` will accidentally pick up `"viola"` and `"violoncello"`; either curate a small canonical-instrument list the parser normalises to, or live with imperfect matching initially.

### Future polish: work-type filter on the Works tab

The Composer detail page already exposes a *Filter by type* affordance (Symphony / Concerto / Sonata / Opera / etc.) sourced from `Work.work_type`. Promoting the same filter to the top-level Works tab would let users browse "all symphonies in my library across all composers" — a natural classical browsing pattern. The data already supports this; only the frontend filter UI needs adding. Defer until the basic Works tab has shipped and we have feedback on whether the cross-composer view is wanted.

### Detail pages

Detail pages reuse existing patterns where possible — Composer detail mirrors Artist detail (different listing inside), Work detail is shaped like Album detail (different relationships), and the OTHER VERSIONS section already used for cross-provider album linking is the natural home for "these recordings might be the same Work" suggestions when MBID matching fails. The only genuinely new page type is the **Work detail page**, which collapses multiple recordings of one composition into a single browseable entry.

### Navigation pattern: contextual filter on Work detail

Every "list of works" view (Composer detail, Conductor detail, Soloist detail, Orchestra detail, Works tab, Search) navigates to the **same Work detail page** when the user clicks a work. To make this work cleanly when arrival happens from a performer-filtered context, the Work detail page applies a **contextual recording filter** based on the path that got you there:

- **Composer detail → Work detail:** no filter applied — every recording on the page is by that composer anyway. Show all recordings.
- **Conductor / Soloist / Orchestra / Ensemble / Choir detail → Work detail:** filter the recordings list to those involving that performer. Default to filtered view; show a *"Showing N recordings by [performer] — Show all"* affordance to expand to all recordings of the work.
- **Works tab / Search / OTHER VERSIONS → Work detail:** no filter applied.

Example — arriving at Beethoven 5 from Karajan's Conductor detail page:

```
┌─ Classical / Performers / Karajan / Beethoven 5 ────┐
│                                                     │
│  Symphony No. 5 in C minor, Op. 67                  │
│  Ludwig van Beethoven · Op. 67 · Symphony           │
│                                                     │
│  Showing 8 recordings by Karajan        [Show all] │
│                                                     │
│  ▶ Karajan / Berlin Philharmonic (1962)             │
│      I.   Allegro con brio              7:38       │
│      II.  Andante con moto             10:42       │
│      ...                                            │
│                                                     │
│  ▶ Karajan / Berlin Philharmonic (1977)             │
│      ...                                            │
│                                                     │
│  ▶ Karajan / Berlin Philharmonic (1984)             │
│      ...                                            │
└─────────────────────────────────────────────────────┘
```

Clicking *Show all* expands the page to include every recording of the same work (Bernstein, Solti, etc.). Best of both: came-here-to-see-Karajan intent honoured by default, full picture available with one click.

The implementation parameterises the Work detail query with an optional `filter_by_artist_id` (or similar). Server-side it's a credit-join filter on the recordings; frontend just renders the filtered list with the escape hatch.

### Recordings link back to source albums

Each recording on the Work detail page is rendered with a link to its source album (the album it was originally released on). This preserves the path from "browsing classical → found a recording" to "playing the original album as released" — covering the use case where a user discovers a recording via the Classical view and wants to know which album in their library it came from.

### Context menu navigation

Right-click / long-press / 3-dot menu on movement and recording rows in the Classical view follows MA's existing context-menu pattern (Play, Add to queue, More info, Favourite handled by the standard menu logic) plus classical-specific navigation entries.

**On a movement row (an individual track within a recording):**

```
Play
Add to queue
Remove from library
─────────────
Go to album X
Go to composer X
Go to work X                ← navigates to parent Work detail page
Go to performer →           ← submenu listing all non-composer credits
─────────────
Add to playlist
Link to genre
─────────────
More info
Favourite / Unfavourite
```

**On a recording row (the `▶ Karajan / BPO (1962)` collapsible header):**

```
Play recording
Add to queue
                            ← Remove from library deliberately omitted
─────────────
Go to album X               ← the album this recording is grouped within (per the within-album heuristic)
Go to composer X
Go to work X
Go to performer →
─────────────
Add recording to playlist   ← multi-write: queues all member movements in order
Link recording to genre     ← multi-write: applies the genre to all member movements
─────────────
                            ← More info deliberately omitted
Favourite / Unfavourite recording   (per Decisions log #23 — multi-write under one user action)
```

The **"Go to performer" submenu** lists every non-composer credit on the underlying track(s), ordered by role priority (Conductor → Ensemble → Orchestra → Choir → Soloist → Other performer), with soloists' instruments shown in parentheses when present:

```
Go to performer →
    Herbert von Karajan (conductor)
    Berlin Philharmonic Orchestra (orchestra)
    Anne-Sophie Mutter (violin)
```

When the same artist appears in multiple credits on the underlying track — e.g. a multi-instrumentalist credited with two instruments, or a conductor who is also a soloist (Trevor Pinnock often conducts and plays harpsichord on the same recording) — **the submenu deduplicates to a single entry per Artist**, combining their roles/instruments in parens:

```
Go to performer →
    Trevor Pinnock (conductor, harpsichord)
    English Concert (ensemble)
```

This matches the data model: instrument lives on the `Credit`, not on the `Artist`, so the same `Artist.item_id` appears once in the database with multiple Credit rows. All credit entries for the same artist resolve to the same Artist detail page, so listing them separately would be redundant navigation.

**"Go to album X", "Go to composer X", and "Go to work X" remain single entries** because those targets are singular per track / movement / recording (a track has at most one album; a Work has a primary composer; a movement has one parent Work).

**"More info" appears on the movement menu but not on the recording menu.** Movements are first-class `Track` entities and have a canonical detail page (with APPEARS ON / PROVIDER DETAIL sections — see Decisions log #20). Recordings are emergent groupings, not entities, so they have no canonical detail page; every "tell me more about this recording" path (composer / work / album / performers / per-track audio details) is already covered by the other menu entries.

The pop-music pattern of *omitting the navigation entry when multiple artists are credited* deliberately does not apply in the Classical view — see Decisions log entry on Context menu nav entries.

## Decisions log

Records of the substantive design questions that came up during drafting and their resolutions, so reviewers don't have to re-litigate them.

1. **Duplication between `artists` and `credits[role=MAIN_ARTIST]`.** *Resolved:* `artists` canonical for headline; `credits` canonical for non-headline roles; server keeps `MAIN_ARTIST` entries in `credits` mirroring `artists`. (See "Synchronisation rule" under Backwards compatibility.)
2. **Movements as Works vs. just movement fields.** *Resolved:* parent Work only with `movement_*` fields on Track is the default. Movement-Works only created when the source supplies a distinct MBID for them. (See `Work` notes.)
3. **`WorkType` granularity.** *Resolved:* 12 common types + `OTHER`. Easy to extend later; consumers should fall back to `OTHER` for unknown values.
4. **`Credit.position` semantics.** *Resolved:* per-role ordering, each role group starts at 0.
5. **Period / era field.** *Resolved (revised):* in scope as a new optional `Artist.period: Period | None` field with seven enum values (Medieval / Renaissance / Baroque / Classical / Romantic / Modern / Contemporary) — matching Apple Music Classical, Roon, IMSLP, and Wikipedia consensus. Original concern about "no canonical source" addressed by tiered population with a deliberate inversion: **GENRE-tag period is primary (user-controlled override path)**, **MB enrichment is secondary (automatic fallback)**. This inverts the usual MBID-canonical rule for this one field because period for boundary composers is genuinely subjective — no canonical answer exists to enforce, so the tag wins. MB inference uses **floruit midpoint** (approximated as `(birth + 25 + death − 5) / 2`), not death-date — death-date misplaces Handel (d. 1759) into Classical despite being canonical Baroque; floruit handles long-lived composers correctly. Lives on Artist (not Work) — works inherit from composer at query time; works straddling periods or stylistic pastiches are edge cases that can get a `Work.period` override later if real demand emerges. Used as a **filter chip** on Composers and Works tabs, not as a sort axis. Considered putting it directly on Work (rejected — derivable from composer, avoids row-level duplication); considered storing as a list to handle composer-spans-periods cases (rejected — single primary period keeps filter UX simple and queries cheap; edge composers get closest-fit period and GENRE-tag override available); considered death-date inference (rejected — Handel-shaped misplacement). Adding the enum + field now while Stage 1 / Stage 2 are open avoids a later model bump + migration; population is deferred until Stage 4 (GENRE tag) and Stage 6 (MB enrichment).
6. **Promoting classical sub-views to main nav vs. internal tabs.** *Resolved:* single top-level "Classical" entry with internal tabs. Main nav approaching capacity; classical sub-views only useful to users with classical content. (See "Frontend integration approach".)
7. **Whether to recommend Classical Extras (Picard plugin) to users.** *Resolved:* **actively recommend against it for new tagging.** Plugin destructively rewrites `ARTIST` (replacing the MB-canonical composer name with the performer name, depending on configuration), produces wrong data when MB lacks Work info (the Vivaldi/Kennedy case), encodes hierarchy with trailing `::` separators that need stripping (see Tag fallbacks subsection), and configuration variance is enormous so different users get different rewrites. Standard Picard with iTunes-style movement tags enabled gives the parser the same useful signal (`WORK`, `MUSICBRAINZ_WORKID`, `MOVEMENTNAME` / `MOVEMENTNUMBER` / `MOVEMENTTOTAL`, `performer:instrument`) without the destructive ARTIST rewrite. **For existing Classical-Extras-tagged libraries:** MA's Stage 4 parser supports the plugin's common output tag names as fallbacks (`groupheading`, `top_work`, `is_classical`, `movement`, etc.) so users can switch the plugin off going forward without re-tagging their existing files. New tagging should use standard Picard.
8. **Tab layout inside the Classical view.** *Resolved:* three tabs — Composers / Works / Performers — with role-filter chips inside Performers (*All / Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers*). Considered five tabs (separate Conductors and Ensembles) and four tabs (with a Search tab), both rejected. Five tabs duplicate the navigation for symphonic repertoire and miss chamber/a-cappella music; the fourth Search tab is redundant once the global search has a Classical chip. Combined Performers tab with chips reuses MA's existing filter pattern (album-type filter precedent). (See "Tab layout inside the Classical view".)
9. **"Other performers" chip in the Performers tab.** *Resolved:* include it. Catches `role=PERFORMER` (the catch-all role for credits that couldn't be more specifically classified — missing instrument, generic MB relationship, session musicians, etc.). Without this chip those credits would be invisible to any role-narrowed view, which classical users would notice. Considered tightening the parser to drive `PERFORMER` toward zero, rejected as it conflates "we don't know" with guessing.
10. **Where Classical search lives.** *Resolved:* in the existing global search bar via a *Classical* master chip, not as a tab inside the Classical view. A Search tab inside Classical would duplicate the same UI with the same data behind a different entry point. Auto-activate the Classical chip when search is invoked from the Classical view to give context-aware default scope. (See "Search integration".)
11. **Staging Classical search across two PRs.** *Resolved:* Stage 8 ships the basic Classical chip returning a flat list of up to 50 mixed results (single-term substring match, no nested chips). Stage 9 adds the nested chip hierarchy (Composers / Works / Performers as second level; performer-role chips as third level inside Performers). Splitting keeps PR review tractable and gives an early demoable milestone. (See stages table.)
12. **Search backend upgrade (FTS5, multi-term token-AND, ranked results).** *Resolved:* out of scope for the classical project entirely. These would be MA-wide infrastructure changes affecting every entity type's search behaviour and need their own RFC. Classical search uses the current substring-match backend with extended fields. When MA-wide search is later upgraded as its own initiative, classical inherits the improvement.
13. **Support for Roon and Classical Extras tag conventions.** *Resolved:* the parser reads a small set of well-known fallback tag names from each (notably `PART`, `ENSEMBLE`, `SOLOIST`, `PERSONNEL`, `SECTION` from Roon; `groupheading`, `top_work`, `is_classical`, `movement` from Classical Extras), with inline code comments identifying the source. We don't *recommend* either tagger to users (each has its own failure modes), but Picard remains the canonical reference; alternative tag names are read as fallbacks so users coming from those tools work without retagging.
14. **Classical as an album-type filter.** *Rejected.* The existing album-type filter (Live / Soundtrack / Compilation / etc.) draws from MusicBrainz's release-type taxonomy and describes the production context of a release. Classical is a genre/classification that cuts *across* release types — a classical album can also be Live, Compilation, or Soundtrack. Adding "Classical" alongside Live/Soundtrack would be a category error and create false either/or choices. Users who want to filter the regular Albums view to classical-only can use the genre filter (if their albums are tagged with classical genres) or browse via the Classical view. We don't put "Rock" or "Jazz" in the album-type filter for the same reason.
15. **Navigation: contextual filter on Work detail.** *Resolved.* All "list of works" views navigate to the same Work detail page. When arrival happens from a performer-filtered context (Conductor / Soloist / Orchestra / Ensemble / Choir detail), the Work detail page applies an implicit recording filter to that performer with a "Show all" escape hatch. From Composer detail, Works tab, Search, or OTHER VERSIONS, no filter is applied. (See "Navigation pattern" under Frontend integration approach.)
16. **Recording year provenance on Work detail.** *Resolved:* sourced from MusicBrainz Recording's first-release-date when MB enrichment is available. This displays original-recording dates correctly for reissues — a 1962 Karajan recording released in a 2010 box set displays as 1962, not 2010. Falls back to album release date when MB data isn't available.
17. **Composer birth/death dates on Composer detail.** *Resolved:* sourced from MusicBrainz Artist begin/end dates. Rendered only when populated; absent gracefully when not. Whether `Artist` needs additive begin/end fields in the model (vs. deriving from existing metadata) is a Stage 1 / Stage 2 implementation detail to confirm during review.
18. **Sort-name conventions for Composer / Performer indexes.** *Resolved:* use `Artist.sort_name`, populated from MusicBrainz canonical sort name. This handles surname-first ordering ("Beethoven, Ludwig van" sorts under B), particle-prefix conventions ("von Karajan" sorts per the MB editor's normalised choice), and non-Latin scripts (Cyrillic, Han, etc.). Local-file taggers populate via `COMPOSERSORT` / `TSOC` and equivalents per Field provenance.
19. **Classical view enablement trigger.** *Resolved:* the Classical entry appears greyed out in the main navigation when the library contains no classical content, matching the existing pattern for Audiobooks and Podcasts. Users who want it permanently hidden can disable it via settings.
20. **Cross-album recording grouping on Work detail.** *Resolved:* relies on MA's existing track-matching machinery. Identical recordings appearing on multiple albums (original release, anniversary box, complete-works compilations) are already collapsed into a single track entity by the standard match-and-dedup logic, with the multiple album appearances surfaced via the existing APPEARS ON / PROVIDER DETAIL sections of the track detail view. The Work detail page therefore sees one recording, not three, with no grouping logic beyond the within-album heuristic (Work + conductor + ensemble) already specified. Selecting MORE INFO on a movement navigates to the standard track detail view, where every album that recording appears on is visible.
21. **Instrument as a primary browse axis vs. a sub-filter.** *Resolved:* keep instrument as a sub-filter under the Performers / Soloists chip, not as a top-level browse axis (Apple Music Classical promotes instrument to primary navigation — "all violinists" / "all piano recordings"). The reason for the deliberate divergence is data-quality risk: instrument strings sourced from `PERFORMER` parens or `TMCL` pairs are messy and inconsistent ("violin" vs. "violin solo" vs. "viola" matching too eagerly, instrument-with-modifier strings, language variants). Apple resolves this with editorial curation we can't replicate. Surfacing instrument as a primary axis on noisy data would produce a worse experience than not surfacing it; gating it behind the Soloists chip means users who reach it have already opted into a narrower context where the messiness is more tolerable. Revisit if a future canonical-instrument normalisation pass becomes available.
22. **Classical compilations with thin tags (no composer / conductor / performer / work info).** *Resolved:* such albums appear in the Classical view if and only if their genre matches a classical genre — the only classification rule they can satisfy. When they appear, they contribute only to the Performers / All chip; the Composers tab, Works tab, and role-specific Performer chips stay empty because the structured data isn't there. We deliberately do *not* attempt fuzzy extraction from track titles (e.g. parsing "Beethoven: Symphony No. 5..." into composer + work) because false-positive risk is high — pop tracks where the artist is also the composer would pollute the index, and the parse is brittle across languages and tagging conventions. This follows the broader MA principle that **comprehensive tagging produces the optimal outcome**: thin tags get a thin experience by design. The mitigation is the standard Albums view, which remains the natural home for compilation-as-curated-unit playback regardless of tag quality (per "Coexistence with standard browse views").
23. **Recording-level favourite semantics.** *Resolved (pending user-research validation):* favouriting a recording in the UI = favouriting all of its member movement tracks. Recording isn't a first-class entity in the model (per #20), so there is no `Recording.favorite` flag; the frontend wraps the multi-write under one user action and the backend sees N standard track-favorite updates land together. On read, a recording renders as "favourited" when all its member tracks are favourited; partial states (e.g. user un-favourites one movement after favouriting the whole recording) render as unfavourited or as a half-state per the Stage 7 UX preference. The alternative — no favourite affordance at the recording level — was considered and rejected: classical listeners want to express "the Karajan 1962 Beethoven 5 is my favourite recording" without favouriting each of four movements individually, and Work-level favourites mean something different (favouriting the composition itself, not this specific performance). **User research note:** the exact propagation rules (all-vs-any test for "is favourited" read; partial-state rendering; behaviour when a member movement is un-favourited after the recording was marked favourite) should be validated against real classical listener behaviour before Stage 7 ships. The current design uses the all-members rule; alternative interpretations are easy to swap later without model changes.
24. **Context menu nav entries in the Classical view.** *Resolved:* the pop-music pattern of "omit the navigation entry when multiple artists are credited" does not apply in the Classical view — multi-performer is the norm in classical, not the exception, and applying that rule would mean almost no classical track has a "Go to performer" entry. Performer navigation uses a submenu listing every non-composer credit on the underlying track(s), ordered by role priority (Conductor → Ensemble → Orchestra → Choir → Soloist → Other performer), with soloists' instruments shown in parentheses. The submenu deduplicates by Artist when the same person appears in multiple credits (e.g. conductor-plus-harpsichordist, multi-instrumentalist) — combining roles/instruments in parens — because instrument lives on the `Credit`, not on the `Artist`, and all entries resolve to the same Artist detail page. "Go to album", "Go to composer", and "Go to work" remain single entries because those targets are singular (a track has at most one album; a Work has a primary composer; a movement has one parent Work). **More info** appears on the movement menu (movements are first-class `Track` entities with a canonical detail page) but is omitted from the recording menu (recordings are emergent groupings, not entities, with no canonical detail target — every "tell me more" path is already covered by the other entries). See "Context menu navigation" under Frontend integration approach for the full menu shape.
25. **Recording-level menu operations: multi-write to member movements.** *Resolved:* operations on a recording row that map naturally onto per-track operations are implemented as **multi-write under one user action** — the frontend issues N standard track-level operations (one per member movement) and the backend sees them as ordinary per-track writes. This pattern applies to: *Favourite recording* (per #23 — favourites all member movements), *Add recording to playlist* (queues all member movements in playback order, mirroring "Play recording = gapless queue"), and *Link recording to genre* (applies the genre to all member movements; genres are per-track in the model). The exception is *Remove from library*: this is deliberately **not** offered at the recording level, because the cost of a wrong bulk action is high (multiple library removals at once, potentially across albums via APPEARS ON). Users who want to remove a whole recording from library multi-select the member movements and remove explicitly. The general principle: multi-write is acceptable for adds and toggles; deletes stay per-track for safety.
26. **Compilation releases with partial structural metadata.** *Resolved (expected behaviour):* classical compilation albums often carry credit metadata (composer, conductor, performer, sometimes is_classical, often Genre=Classical) but lack structural metadata — no `MUSICBRAINZ_WORKID`, no `MOVEMENTNUMBER` / `MOVEMENTTOTAL`, often a `WORK` tag that conflates work + movement into one string (e.g. `"Orchestral Suite No. 3: II. Air"`). This is because the source release in MusicBrainz typically doesn't link the excerpted track to a parent Work entity — the compilation editor placed it standalone, not as movement N of M. **MA handles these gracefully:** the track appears in the Classical view (via Genre rule), in the Composers tab (via composer credit) and the Performers tab (via conductor / orchestra / soloist credits). It does **not** appear in the Works tab because no Work entity is created without a positive Work signal (per the conservative work-creation policy under Classification policy). This is the intended behaviour, not a bug. Distinct from #22 (which covers the further degraded case of compilation tracks with only the most basic tags and no classical-specific credits at all). Users who want compilation tracks to roll up under their parent Work in browse would need to manually add `WORK` and `MUSICBRAINZ_WORKID` tags via Picard or similar. Dedicated classical releases (complete-Bach-cantatas sets, Pinnock's Orchestral Suites, etc.) typically have full structural metadata from MB and are where the Works tab really shines.
27. **Multi-value `MUSICBRAINZ_WORKID` and `WORK` tags.** *Resolved:* Picard writes semicolon-separated values when a Recording is linked to more than one Work in MusicBrainz — typically parent + movement (Beethoven's *Moonlight* Sonata + its first movement) or arrangement + source (Bach's *Air* arranged for organ + the original Suite movement). Parser splits on `; `; the **last entry is the canonical primary** (Picard's convention is most-general → most-specific). Earlier entries resolve via Stage 6 MB enrichment into `Work.parent_work` (parent+movement) or `Work.arrangement_of` (arrangement+source) — the relationship type cannot be reliably distinguished at parse time, only by looking up the Works' actual MB relationships. Without MBIDs, a multi-value `WORK` tag is ambiguous; parser falls back to last-value-as-primary and discards earlier values. See "Multi-value `MUSICBRAINZ_WORKID` and `WORK` tags" under Matching policy.
28. **Within-track artist name resolution (substring-only carve-out).** *Resolved:* the general rule (Matching policy: "Canonical entity resolution via MBID") is that fuzzy matching across spelling variants is not attempted; MBID is authoritative and text-only names round-trip as-is. One **deliberate carve-out**: within a single track, when one credit is MBID-anchored (e.g. `Artist: "Dame Moura Lympany"` with `MUSICBRAINZ_ARTISTID`) and another credit on the same track has a text-only name that is a **substring** of the MBID-canonical name (e.g. `performer:piano: "Moura Lympany"`), the parser merges the text-only credit onto the canonical Artist entity. Catches the common honorific / formal-name variation case (*"Moura Lympany"* ⊂ *"Dame Moura Lympany"*; *"Karajan"* ⊂ *"Herbert von Karajan"*; *"Bach"* ⊂ *"Johann Sebastian Bach"*) without invoking fuzzy matching. **The carve-out is intentionally narrow:** within a single track only (tight disambiguation context), substring-match only (no Levenshtein / token / phonetic), and anchored by at least one MBID on the track (text-only-to-text-only matching is not attempted even within a track). Cross-track name resolution remains MBID-only as before. Worth revisiting if false-positives emerge from genuinely distinct same-track artists whose names happen to share a substring.
29. **Creator roles (`LYRICIST`, `ARRANGER`) excluded from the Performers tab.** *Resolved:* the Performers tab surfaces **performing roles only** — `CONDUCTOR` / `ORCHESTRA` / `ENSEMBLE` / `CHOIR` / `SOLOIST` / `PERFORMER`. Creator roles (`LYRICIST`, `ARRANGER`) are visible on Track detail's credits panel and on Recording rows but do **not** appear in any Classical-view browse tab. **Parse-vs-display split:** the Stage 4 parser still reads `LYRICIST` and `ARRANGER` tags and stores them as `Credit` rows with the corresponding role — the exclusion is purely a display-layer choice in the Classical view's browse tabs. The credits exist in the data layer (Artist entities exist, `track_artists` rows are written, the standard Artists view lists them like any other artist with credits); they're just not surfaced in the *classical browse axes*. This keeps the parser permissive and uniform (every standard tag mapping is read) while letting the UI be opinionated about what to surface where. Considered putting them under the Performers / "Other performers" chip, rejected because (a) it's a semantic mismatch (lyricists don't perform), (b) no major classical streaming service (Apple Music Classical, IDAGIO) exposes librettist/lyricist as a browse axis — confirmed via web research that user demand is concentrated in academic reference tools (Grove Music Online) not listening apps, and (c) opening the door to creator-role browse surfaces would naturally pull `COMPOSER` out of its dedicated Composers tab and into a generic "Creators" axis, which is a bigger restructuring than this spec aims for. Promotion to a dedicated browse surface (e.g. a *Lyricists* sub-chip or a *Creators* tab) is a future-polish item if real user demand surfaces. Until then: visible on Track detail, not on tabs.
30. **Per-entity `is_classical` computed fields.** *Resolved:* `Track.is_classical`, `Album.is_classical`, and `Artist.is_classical` are exposed as derived boolean fields on the wire, computed server-side per the Classification policy rules. Clients treat them as read-only and do **not** replicate the classification logic locally. `Track.is_classical` follows the existing "When a track appears in the Classical view" rules. `Album.is_classical` follows "Album-level classical classification". `Artist.is_classical` is true if the artist has any credit on a track where `Track.is_classical=True` — enabling cross-linking on the Artist detail page ("View in Classical view" link, conditional classical sections, search-result highlighting) without each consumer recomputing the rules. The Classical view's tab indices (Composers, Performers and its role-chips, Works) all filter by `Track.is_classical=True` at the query layer; this fixes a previous ambiguity in the Composers and Performers tab definitions where "any track's credits" could have leaked non-classical composer / performer credits (e.g., a hip-hop sample crediting Beethoven). The fields are derived/cached, not stored canonical state, so recomputation on credit changes is the implementation responsibility — Stage 3 should compute these per-row and cache appropriately. Without these fields, every client would need to replicate classification, which is fragile and duplicates the truth source.
31. **Extended Track credits panel deferred to future polish.** *Resolved:* the structured role-typed credits panel on Track detail (vertical role-grouped list of composer / conductor / orchestra / soloists with instruments / etc.) is **deferred** to a future polish stage. Stage 7 ships without it. Track detail continues to use the existing `metadata.performers` flat-string display, with `Track.is_classical` gating any classical-specific UI. **Side effect of the deferral:** `LYRICIST` and `ARRANGER` credits become **invisible in the UI** until the panel ships — the data exists in `track_artists`, available via the API, but no Track-detail surface currently renders it (and creator roles are out of the Classical view's browse tabs by design — Decisions log #29). Acceptable trade-off: the use case (browsing by librettist / arranger) is niche, no major classical streaming app surfaces it, and the panel is fully additive when it lands (no data shape changes). Stage 7 closure does not wait for the credits panel; the remaining Stage 7 surfaces (Classical view tabs, detail pages, OTHER VERSIONS reuse) ship without it. Performer and composer credits remain visible via the Composers/Performers tabs and their detail pages; the deferral primarily affects creator-role visibility on Track detail.
32. **Per-performer scoping of counts on Performer detail pages.** *Resolved:* counts on a Performer detail page (Conductor / Soloist / Orchestra / Ensemble / Choir / Other performer detail) — header summary stats, per-Work `recording_count` in the "Works Performed" section, per-collaborator counts — are **scoped to this performer's contributions**, not library-wide totals. Karajan's page showing "Beethoven Symphony No. 5 · 8 rec." means 8 Karajan recordings of Beethoven 5, not the library's total. Header "312 recordings · 47 works" means Karajan's 312 recordings and 47 works, not the library total. Same principle as Decisions log #15 (contextual filter on Work detail): when the route names a performer, every per-row metric on the page is filtered by that performer at the server query layer, not aggregated globally and rendered post-hoc by the client. Stage 3d API endpoints serving performer-scoped routes (e.g. `music/works/library_items` with `filter_by_artist_id`, performer-detail queries) must compute counts with the performer filter applied. Frontend Claude flagged this during Stage 7 prototyping; without explicit specification, backend Claude might serve library-wide counts as the default.
33. **"Other tracks" sections on Composer and Performer detail pages.** *Resolved:* a composer or performer may have credits on tracks that lack `Work` linkage (composer-only credit, no `WORK` / `MUSICBRAINZ_WORKID` tag — see "When to create a Work entity"). To prevent empty / partially-empty detail pages when an entity's library credits are all on Workless tracks, both Composer detail and Performer detail render a secondary **"Other tracks"** section below the canonical Works list. Composer detail's section lists tracks where this composer is credited and `Track.work IS NULL`; Performer detail's section lists tracks where this performer has any non-composer credit and `Track.work IS NULL`. Both sections are hidden when empty. The "Other tracks" surface is **local to detail pages only** — the Classical view's Works tab stays Work-centric and free of synthetic / placeholder Work entities. Considered creating synthetic `UNKNOWN` Work entities (rejected — pollutes the global Works tab, groups unrelated tracks under fake aggregates, conflicts with the conservative work-creation policy from Classification policy). Considered tightening Composers / Performers tab indices to exclude entities whose credits are all Workless (rejected — hides legitimately classical content from the browse axis). The detail-page-local "Other tracks" pattern is the cleanest fix: Works stay canonical globally, thin-tagged content surfaces via the entity-centric view, no schema or model changes required (pure frontend section using existing data).

## Open questions

These are deferred to follow-up additions when concrete demand arises. All are additive and non-breaking when added.

1. **`Track.section` (Roon `SECTION` equivalent).** Roon supports a three-level hierarchy `WORK → SECTION → PART` for operas (e.g. "Le nozze di Figaro" → "Act 1" → "Cinque... dieci..."). Our model handles two levels (parent Work + movement on Track). For Roon-style opera tagging, an additive `Track.section: str | None` field would capture the intermediate level cheaply. Alternative: model Acts as proper movement-Works via parent_work nesting (more faithful to MB but creates more Work rows). Defer until a concrete consumer needs it.
2. **`Track.performance_id` (Roon `WORKID` equivalent).** Would disambiguate multiple recordings of the same Work on a single album when the heuristic (Work + conductor + ensemble grouping) can't tell them apart — e.g. same conductor + same ensemble recording the same Work twice on one album. **No verified real-world example identified** of this case; deferred until a user reports an album where the heuristic fails. If added, the parser would read Roon's `WORKID` tag (no Picard equivalent — users would need to add it manually with a tag editor).
3. **Movements view on Work detail (alternative grouping of recordings).** The current Work detail page organises content by Recording (each recording shows its movements collapsed underneath). A complementary **"group by movement"** option would transpose the same data — each movement gets a header with all library recordings of that movement underneath, enabling cross-recording comparison of the same movement ("how does Furtwängler's Adagio compare to Karajan's?"). **Design (when implemented):** a *Group by* toggle / dropdown at the top of the recordings list — `[Group by: Recording ▼]` with `Recording` (default) and `Movement` options. Same data, same component, different group-by; not a separate section. Hide the `Movement` option (or the toggle entirely) when the Work has only one movement (Pachelbel Canon, *Spiegel im Spiegel*, Albinoni Adagio — degenerate case). Frontend-only addition — no new data fetch, just a different rendering of the recordings the page already loads. **Deferred to post-Stage 10 polish unless user demand surfaces**; the existing Recordings view + OTHER VERSIONS on track detail covers the comparison use case at a different entry point.

## References

### Tag standards and canonical mappings

- MusicBrainz Picard tag mapping — canonical cross-format mapping (Vorbis / ID3 / MP4 / APEv2): https://picard-docs.musicbrainz.org/en/appendices/tag_mapping.html
- MusicBrainz Work entity — Work, catalog numbers, work types, arrangement-of relationships: https://musicbrainz.org/doc/Work
- MusicBrainz Recording entity — Recording matching and Recording-Artist relationships used in Stage 6 enrichment and decision #20: https://musicbrainz.org/doc/Recording
- ID3v2.4 frame specification — TCOM, TPE3, TMCL, TIT1, TSOC, MVNM, MVIN: https://id3.org/id3v2.4.0-frames
- Vorbis comment specification — multi-valued PERFORMER convention with parens-suffix instrument/role: https://xiph.org/vorbis/doc/v-comment.html
- iTunes 12.5 movement tags — Apple-introduced MVNM / MVIN / ©mvn / ©mvi / ©mvc: https://www.macworld.com/article/228807/how-to-better-organize-classical-music-in-itunes.html

### Third-party tagging conventions and tools

- Classical Extras Picard plugin — source: https://github.com/MetaTunes/picard-plugins/tree/metabrainz/2.0/plugins/classical_extras

### Community discussions

- Music Assistant Discord — "Better Classical Music Support" thread
- Roon Classical Music Initiative (CMI) and Three Line Solution (TLS) — design discussion: https://community.roonlabs.com/t/one-suggestion-for-organising-a-classical-music-collection-in-roon/243207/10
- Classical Extras Picard plugin — discussion thread: https://community.metabrainz.org/t/classical-extras-2-0/394627

### UX precedent

- Apple Music Classical (launch announcement) — composer / work / performer browse structure, recording-year display, search across opus numbers and performers: https://www.apple.com/newsroom/2023/03/apple-music-classical-is-here/

## Out of scope (future work)

- Period / era field (no canonical source; derive from genres or composer dates if needed).
- `Track.is_partial_recording` flag for tracks that are only an excerpt of their linked Work. Additive when added.
- Lyricist / librettist relationships beyond the basic `LYRICIST` role.
- Recording-level metadata (recording date, venue, producer credits beyond the basic role).
- Multi-disc opera structure beyond what `parent_work` already supports.
- Per-track / per-album "treat as classical" / "exclude from classical" override (Classical view inclusion).
- "Composer as primary artist for classical" toggle (iTunes-style headline rewriting in non-Classical browse views).
