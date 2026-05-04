# Classical music support — design spec

**Status:** Draft for discussion
**Scope:** Full design across all stages — model, schema, parsing, providers, enrichment, frontend.
**Stage 1 (model changes) PR doc:** `CLASSICAL_MUSIC_STAGE_1_MODELS.md`
**Community proposal:** `CLASSICAL_MUSIC_PROPOSAL.md`

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

- A "period" (Baroque/Classical/Romantic) field — there is no standard tag or MusicBrainz field for this, and it can be derived from genre tags or composer dates. Out of scope.
- Rewriting users' existing tags. Local tags are authoritative; we never overwrite or strip a tagged value.
- Replacing the existing flat `Track.artists` / `Album.artists` fields. New role-typed credits sit alongside.

## Implementation stages

The work splits across two repos and several PRs. Each stage is independently deployable; later stages depend on earlier ones for data shape but not for behaviour.

| # | Stage | Repo | Depends on | Summary |
|---|---|---|---|---|
| 1 | **Model changes** | `music-assistant-models` | — | New `Work` MediaItem, `ArtistRole` enum, `Credit` type, additive fields on `Track`/`Album`. Fully non-breaking. *(See `CLASSICAL_MUSIC_STAGE_1_MODELS.md`.)* |
| 2 | **Database schema & migrations** | `music-assistant/server` | 1 | New `works` table, `work_arrangements` junction, `work_id`/`movement_*` columns on `tracks`, `role`/`instrument`/`position` columns on `track_artists` and `album_artists`. Migration backfills existing rows with `role=MAIN_ARTIST`. |
| 3 | **Server controllers & API** | `music-assistant/server` | 2 | New `WorksController`. `TracksController` and `AlbumsController` extended for role-typed credits and work linkage. WebSocket commands for work browse, role-filtered track queries. Comparison/dedup updated to use Work MBID. **Search is unchanged at this stage** — it's deferred to Stages 8 & 9 to keep PRs reviewable. |
| 4 | **Local file tag parsing** | `music-assistant/server` | 3 | `helpers/tags.py` reads `COMPOSER` / `CONDUCTOR` / `PERFORMER` / `WORK` / `MOVEMENT*` / role-suffixed `PERFORMER:instrument` / `TMCL` pairs across Vorbis / ID3 / MP4 / APEv2. Plus a small fallback set for Classical Extras tags (`groupheading`, `top_work`, `is_classical`, `movement`). Local-file users get the Classical view's browse functionality at this point (search comes in Stage 8). |
| 5 | **Streaming provider mapping** | `music-assistant/server` | 3 | Per-provider PRs: Qobuz (resolves existing TODO), Apple Music (composer mapping fix), Subsonic-compatible (structured contributors), and audit of Tidal/Spotify/Deezer/YouTube Music. Independent and parallelisable per provider. |
| 6 | **MusicBrainz enrichment** | `music-assistant/server` | 3 | Extends the MB provider beyond ID-only fetching to pull Recording-Artist relationships (composer/conductor/performer with instruments), Recording-Work links, Work entity metadata (type, catalog numbers, parent work), and Work-Work arrangement relationships. Fills gaps where local tags or streaming providers fall short. **Strict rule: enrich-don't-override** — never overwrite a value the source already supplied. |
| 7 | **Frontend Classical view** | `music-assistant/frontend` | 3 | New top-level "Classical" entry with three internal tabs: Composers / Works / Performers (Performers carries role-filter chips). Composer detail page (works listed, not albums), Work detail page (recordings collapsed under one composition), extended Track credits panel, OTHER VERSIONS reused for unmatched-Work suggestions. **No search inside the Classical view** — search lives in the global search bar (Stages 8 & 9). |
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
- `position` is **per-role**: each role group has its own ordering starting at 0. So a track with two SOLOIST entries and three PERFORMER entries has positions 0–1 within SOLOIST and 0–2 within PERFORMER, not a global 0–4 sequence. Simpler to reason about, easier to render, and avoids conflating ordering across heterogeneous roles.
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
    arrangement_of: list[ItemMapping] = field(default_factory=list)   # source work(s) this is an arrangement of
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
- `WorkType` covers the most common 12 types plus `OTHER`. MusicBrainz has ~25 types; the proposed enum covers the ones that matter for browsing/grouping. `OTHER` catches the long tail. Adding new variants later is non-breaking — consumers should fall back to `OTHER` for unknown values.
- `parent_work` is optional and self-referential. Movements *can* be modelled as separate Works with a parent link (mirroring MusicBrainz), but the **default rule is parent Work only, with `movement_*` fields on Track**. A movement-Work row is created only when the source supplies a distinct MBID for it (i.e. the file's `MUSICBRAINZ_WORKID` points to the movement, or MB enrichment surfaces a movement-level Work entity). This avoids a row-count explosion — a Bach library with 8000 tracks would otherwise produce 8000+ Work rows for movement entities alone.
- `arrangement_of` captures transcriptions, orchestrations, and reductions where one Work is derived from another (Mussorgsky's *Pictures at an Exhibition* piano original ↔ Ravel's orchestration; Bach organ works transcribed for piano; opera scenes transcribed for solo instrument). MusicBrainz models these as distinct Works connected by an "arrangement of" relationship. The list form handles medleys and works arranged from multiple sources. The reverse direction ("which works are arrangements of *this* one") is derived by querying — not stored.
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

For compilation albums, `Album.composers` and `Album.conductors` may return long lists — a "100 Greatest Classical Hits" compilation could have 50+ distinct composers. This is intentional: the data is honest about what's there, and display logic in the frontend can collapse to a placeholder like "Various composers" above some threshold. This is *not* the same as the existing `Album.artists = [Various Artists]` pattern (which uses a single placeholder Artist entity); the new credit-based properties always carry the actual list.

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

## Matching policy

How instances of these entities are matched and grouped at runtime. This belongs in the spec because it constrains what the data shape needs to support; the actual matching code lives in the server.

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

### User overrides (future polish)

A per-track or per-album "treat as classical" / "exclude from classical" override is the cleanest fix for users whose libraries don't match these defaults — e.g. someone who *does* want their Williams scores in the Classical view, or who *doesn't* want a particular contemporary album. Same mechanism works for hiding individual tracks from the view without retagging. Out of scope for Phase 1; additive when added.

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

- **Composers** — index of artists who appear with `role=COMPOSER` in any track's credits. Click → composer detail page (works listed underneath, not albums).
- **Works** — index of `Work` entities. Click → Work detail page (multiple recordings of the same composition collapsed under one entry).
- **Performers** — index of artists who appear in any non-composer role. **Filter chips at the top of the tab let the user narrow by role:** *All / Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers*. The chip pattern reuses the existing filter convention in MA (e.g. the album-type filter that lets users narrow to "live", "soundtrack", etc.). Click an entry → performer detail page (works performed, conductors collaborated with, recordings in library).

  The **Other performers** chip catches credits with `role=PERFORMER` — our catch-all role for credits that couldn't be more specifically classified (instrument missing from the tag, generic "performer" MB relationship without attributes, backing/session musicians, etc.). Including this chip explicitly makes sure no credit is invisible to a chip-narrowed view; without it those performers would only show under "All" and users would silently miss them.

**Why one Performers tab instead of separate Conductors / Ensembles tabs:** classical listeners think in terms of "who's playing this" — and the answer might be a person (conductor, soloist) or a group (orchestra, ensemble, choir). Splitting into two tabs duplicates the navigation; combining with role chips gives the same browsing power with less surface area. Chamber music and a-cappella choral music — which have no conductor — are also covered naturally by this single-tab approach, where a separate Conductors tab would miss them.

### View structure (low-fidelity sketches)

These sketches are **structural, not visual** — they exist to anchor the navigation discussion and make element placement concrete for review. Final visual design (typography, spacing, density, mobile layout, light/dark theming) is out of scope here and will be done in the frontend PR (Stage 7).

what you described maps cleanly: Composers → Composer detail → Work detail; Performers (Conductors chip) → Conductor detail → Work detail. Same destination from different starting points.

What each view looks like (rough sketches)
Composers tab — index of all composers in the library:

┌─ Classical ── [Composers] Works  Performers ────────┐
│  Sort: name ▼      Search: [               ]       │
│                                                     │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐          │
│  │ JSB │ │ LvB │ │ WAM │ │ FC  │ │ AP  │  ...     │
│  └─────┘ └─────┘ └─────┘ └─────┘ └─────┘          │
│  Bach    Beethoven Mozart  Chopin  Pärt           │
│  212 wks 157 wks  89 wks  43 wks  62 wks          │
└─────────────────────────────────────────────────────┘
Composer detail — the composer's works listed:

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
Works tab — all works across all composers, browseable directly:

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
Work detail — multiple recordings of one composition (this is the new page type):

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
Click ▶ on the recording header → queue all movements gapless. Click an individual movement → play that one.

Performers tab with the Conductors chip selected:

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
Conductor detail — works performed by this conductor:

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
Answering your question
"is the Works view from a composer or conductor the same as the Works tab, just filtered?"

Effectively yes. The Composer detail page is conceptually "the Works tab filtered to this composer, with a composer header at the top." Same goes for the Conductor detail — "Works tab filtered to works conducted by this person." Implementation-wise the frontend can reuse the same Works-list component with different filter parameters, just with the surrounding context (composer/conductor header) differing.

The destination is always the same: clicking a Work from anywhere — Works tab, Composer detail, Conductor detail — opens the same Work detail page with all its recordings grouped underneath.

Sort options
Reasonable defaults per view:

Works tab: composer name (default), work title, year of composition, recording count.
Composer detail: catalog number (default — Op. / BWV / K. order is canonical), work title, work type, recording count. Filter by work type useful here too.
Conductor detail: composer (default — group all Beethoven together, then all Brahms), work title, recording count.
Are there sort options you'd want that aren't there? The "by performer" sort you mentioned is interesting — could be useful inside the Work detail page to order recordings by conductor name or orchestra name.

So looking at the conductor detail. If a user clicks on the first row does it expand to just show the 8 recordings? (version I guess is the word I poorly used!) And then you click again to expand a recording to show the movements? Or should we actually go from conductor to work detail with it filtered as we just discussed?

Good question — and I think Option B is the cleaner answer, but with a contextual twist.

Why Option B (navigate to Work detail) is cleaner
Inline-expand to three levels (works → recordings → movements) gets visually heavy fast. You'd have multiple expand/collapse states to track on one page, and the same Work would render slightly differently here than on its own Work detail page — meaning two places to maintain the recording-with-movements display logic.

Navigating to the Work detail page keeps that page as the single canonical home for "everything about this work" — which is also where users land from Composer detail, Works tab, search, and the OTHER VERSIONS section. Same destination from every direction. Easier to maintain, more predictable for users.

The contextual twist
Without anything else, navigating from Karajan → Beethoven 5 lands on a Work detail page showing all recordings of Beethoven 5 (Karajan, Bernstein, Solti, etc.) — and the user has to scan to find the eight they came for. That's annoying.

The fix: Work detail page knows its arrival context and applies an implicit filter. When you arrive from a Conductor detail page, the recordings list defaults to that conductor's recordings only, with an obvious escape hatch to see all:

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
Click Show all → the page expands to include Bernstein's, Solti's, etc. recordings of the same work. Best of both: came-here-to-see-Karajan intent honoured by default, full picture available with one click.

The Composer detail flow doesn't need this filter — every recording on a Bach work IS by Bach, so there's nothing to narrow.

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

The implementation parameterises the Work detail query with an optional `filter_by_artist_id` (or similar). Server-side it's a credit-join filter on the recordings; frontend just renders the filtered list with the escape hatch.

### Recordings link back to source albums

Each recording on the Work detail page is rendered with a link to its source album (the album it was originally released on). This preserves the path from "browsing classical → found a recording" to "playing the original album as released" — covering the use case where a user discovers a recording via the Classical view and wants to know which album in their library it came from.

## Decisions log

Records of the substantive design questions that came up during drafting and their resolutions, so reviewers don't have to re-litigate them.

1. **Duplication between `artists` and `credits[role=MAIN_ARTIST]`.** *Resolved:* `artists` canonical for headline; `credits` canonical for non-headline roles; server keeps `MAIN_ARTIST` entries in `credits` mirroring `artists`. (See "Synchronisation rule" under Backwards compatibility.)
2. **Movements as Works vs. just movement fields.** *Resolved:* parent Work only with `movement_*` fields on Track is the default. Movement-Works only created when the source supplies a distinct MBID for them. (See `Work` notes.)
3. **`WorkType` granularity.** *Resolved:* 12 common types + `OTHER`. Easy to extend later; consumers should fall back to `OTHER` for unknown values.
4. **`Credit.position` semantics.** *Resolved:* per-role ordering, each role group starts at 0.
5. **Period / era field.** *Resolved:* out of scope. No canonical source (no standard tag, no MB field). Genre tags cover this for users who want it.
6. **Promoting classical sub-views to main nav vs. internal tabs.** *Resolved:* single top-level "Classical" entry with internal tabs. Main nav approaching capacity; classical sub-views only useful to users with classical content. (See "Frontend integration approach".)
7. **Whether to recommend Classical Extras (Picard plugin) to users.** *Resolved:* no blanket recommendation. Plugin produces wrong data when MB lacks Work info (the Vivaldi/Kennedy case), can destructively rewrite `ARTIST`, and configuration variance is enormous. We support its common output tag names as parser fallbacks but do not endorse it. (See parser policy in the Stage 4 doc when written.)
8. **Tab layout inside the Classical view.** *Resolved:* three tabs — Composers / Works / Performers — with role-filter chips inside Performers (*All / Conductors / Orchestras / Chamber groups / Choirs / Soloists / Other performers*). Considered five tabs (separate Conductors and Ensembles) and four tabs (with a Search tab), both rejected. Five tabs duplicate the navigation for symphonic repertoire and miss chamber/a-cappella music; the fourth Search tab is redundant once the global search has a Classical chip. Combined Performers tab with chips reuses MA's existing filter pattern (album-type filter precedent). (See "Tab layout inside the Classical view".)
9. **"Other performers" chip in the Performers tab.** *Resolved:* include it. Catches `role=PERFORMER` (the catch-all role for credits that couldn't be more specifically classified — missing instrument, generic MB relationship, session musicians, etc.). Without this chip those credits would be invisible to any role-narrowed view, which classical users would notice. Considered tightening the parser to drive `PERFORMER` toward zero, rejected as it conflates "we don't know" with guessing.
10. **Where Classical search lives.** *Resolved:* in the existing global search bar via a *Classical* master chip, not as a tab inside the Classical view. A Search tab inside Classical would duplicate the same UI with the same data behind a different entry point. Auto-activate the Classical chip when search is invoked from the Classical view to give context-aware default scope. (See "Search integration".)
11. **Staging Classical search across two PRs.** *Resolved:* Stage 8 ships the basic Classical chip returning a flat list of up to 50 mixed results (single-term substring match, no nested chips). Stage 9 adds the nested chip hierarchy (Composers / Works / Performers as second level; performer-role chips as third level inside Performers). Splitting keeps PR review tractable and gives an early demoable milestone. (See stages table.)
12. **Search backend upgrade (FTS5, multi-term token-AND, ranked results).** *Resolved:* out of scope for the classical project entirely. These would be MA-wide infrastructure changes affecting every entity type's search behaviour and need their own RFC. Classical search uses the current substring-match backend with extended fields. When MA-wide search is later upgraded as its own initiative, classical inherits the improvement.
13. **Support for Roon and Classical Extras tag conventions.** *Resolved:* the parser reads a small set of well-known fallback tag names from each (notably `PART`, `ENSEMBLE`, `SOLOIST`, `PERSONNEL`, `SECTION` from Roon; `groupheading`, `top_work`, `is_classical`, `movement` from Classical Extras), with inline code comments identifying the source. We don't *recommend* either tagger to users (each has its own failure modes), but Picard remains the canonical reference; alternative tag names are read as fallbacks so users coming from those tools work without retagging.
14. **Classical as an album-type filter.** *Rejected.* The existing album-type filter (Live / Soundtrack / Compilation / etc.) draws from MusicBrainz's release-type taxonomy and describes the production context of a release. Classical is a genre/classification that cuts *across* release types — a classical album can also be Live, Compilation, or Soundtrack. Adding "Classical" alongside Live/Soundtrack would be a category error and create false either/or choices. Users who want to filter the regular Albums view to classical-only can use the genre filter (if their albums are tagged with classical genres) or browse via the Classical view. We don't put "Rock" or "Jazz" in the album-type filter for the same reason.
15. **Navigation: contextual filter on Work detail.** *Resolved.* All "list of works" views navigate to the same Work detail page. When arrival happens from a performer-filtered context (Conductor / Soloist / Orchestra / Ensemble / Choir detail), the Work detail page applies an implicit recording filter to that performer with a "Show all" escape hatch. From Composer detail, Works tab, Search, or OTHER VERSIONS, no filter is applied. (See "Navigation pattern" under Frontend integration approach.)

## Open questions

These are deferred to follow-up additions when concrete demand arises. All are additive and non-breaking when added.

1. **`Track.section` (Roon `SECTION` equivalent).** Roon supports a three-level hierarchy `WORK → SECTION → PART` for operas (e.g. "Le nozze di Figaro" → "Act 1" → "Cinque... dieci..."). Our model handles two levels (parent Work + movement on Track). For Roon-style opera tagging, an additive `Track.section: str | None` field would capture the intermediate level cheaply. Alternative: model Acts as proper movement-Works via parent_work nesting (more faithful to MB but creates more Work rows). Defer until a concrete consumer needs it.
2. **`Track.performance_id` (Roon `WORKID` equivalent).** Would disambiguate multiple recordings of the same Work on a single album when the heuristic (Work + conductor + ensemble grouping) can't tell them apart — e.g. same conductor + same ensemble recording the same Work twice on one album. **No verified real-world example identified** of this case; deferred until a user reports an album where the heuristic fails. If added, the parser would read Roon's `WORKID` tag (no Picard equivalent — users would need to add it manually with a tag editor).

## Out of scope (future work)

- Period / era field (no canonical source; derive from genres or composer dates if needed).
- `Track.is_partial_recording` flag for tracks that are only an excerpt of their linked Work. Additive when added.
- Lyricist / librettist relationships beyond the basic `LYRICIST` role.
- Recording-level metadata (recording date, venue, producer credits beyond the basic role).
- Multi-disc opera structure beyond what `parent_work` already supports.
- Per-track / per-album "treat as classical" / "exclude from classical" override (Classical view inclusion).
- "Composer as primary artist for classical" toggle (iTunes-style headline rewriting in non-Classical browse views).
