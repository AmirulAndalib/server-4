# Classical Music Support — Overview

## TL;DR

MA's flat artist model doesn't fit classical music well. I'd like to add first-class support for classical metadata across 10 staged changes — all additive, non-breaking. Pop/rock/jazz/electronic users see no change. Classical listeners get composer/work/performer-based browse, role-typed credits, and movement-aware playback — comparable to what Roon and Apple Music Classical offer, but working with MA's existing diverse library and provider stack.

The full design is in [`CLASSICAL_MUSIC_MODEL_SPEC.md`](CLASSICAL_MUSIC_MODEL_SPEC.md) (~900 lines, all decisions captured). This doc is a shorter overview for anyone deciding whether to back the work.

## The problem

MA's current artist model is flat. A track has an `artists` list — period. There's no distinction between composer, conductor, orchestra, soloist, or accompanist. Classical music depends on those distinctions:

> *Karajan / Berlin Philharmonic conduct Beethoven's Symphony No. 5: II. Andante con moto*
>
> Today this track gets squashed into `artists = ["Berlin Philharmonic", "Karajan"]` or `artists = ["Beethoven"]` depending on tagging. The composer-vs-conductor-vs-orchestra distinction is lost. The fact that this is movement 2 of 4 of a single composition is lost. The relationship to other recordings of the same composition (Bernstein/VPO's, Solti/Chicago's) is lost.

What classical listeners actually want, synthesised from the MA "Better Classical Music Support" Discord thread, Roon's classical forums discussion, and Apple Music Classical's design choices:

1. **Browse by composer as the primary axis** — "show me all my Bach" is the single most-cited ask.
2. **Work as a first-class browseable entity** — multiple recordings of the same composition grouped under one entry; movements playable as a unit, gapless.
3. **Distinct conductor / orchestra / soloist credits** — filterable to "all Karajan recordings", "all Berlin Philharmonic recordings", "all violin recordings" without fuzzy text matching.
4. **Catalog numbers (BWV, K., Op., HWV) parsed and searchable** — often the canonical handle for a work.
5. **Roll-up across granularity** — the same recording can appear on multiple albums; the same Work has multiple recordings; arrangements are distinct from sources. All needs to roll up cleanly into searchable / playable units.

## The solution (high level)

Three additions at the model layer, one new view in the frontend, preserved compatibility everywhere else.

### Model layer

- **`Work` as a first-class MediaItem** — the *composition* (e.g. "Symphony No. 5 in C minor, Op. 67"), distinct from any specific recording. Multiple recordings of the same Work share one Work entity, matched by MusicBrainz Work MBID where available.
- **Role-typed credits via a `Credit` type** — `(artist, role, instrument, position)` where role is one of `MAIN_ARTIST` / `COMPOSER` / `CONDUCTOR` / `ORCHESTRA` / `ENSEMBLE` / `CHOIR` / `SOLOIST` / `PERFORMER` / `LYRICIST` / `ARRANGER`. Sits alongside the existing flat `Track.artists` list (which remains canonical for the headline credit).
- **Movement linkage on `Track`** — `work`, `movement_number`, `movement_total`, `movement_name`. Multi-movement playback and Work-grouped browse become possible.

All strictly additive. No existing field changes type or is removed.

### Frontend layer

A new top-level **"Classical" navigation entry** with three internal tabs:

- **Composers** — index of composers in the library. Click → composer detail (works listed underneath).
- **Works** — index of compositions. Click → Work detail (multiple recordings grouped under one composition; movements visible per recording).
- **Performers** — index of conductors / orchestras / chamber groups / choirs / soloists with role-filter chips.

The standard Artists / Albums / Tracks views are **unchanged**. The Classical view is a parallel lens over the same data — not a replacement. Users who don't have classical content see the Classical entry greyed out (same pattern as Audiobooks / Podcasts).

UI mockups have been floated on Discord to gauge feedback, which has been generally positive.

### Backend layer

Server-side `WorksController` mirrors the existing per-MediaType controllers. `TracksController` and `AlbumsController` gain role-typed-credit awareness. MusicBrainz enrichment extended to pull Recording-Work links and Work entity metadata. Per-entity `Track.is_classical` / `Album.is_classical` / `Artist.is_classical` boolean fields exposed so clients can render classical-aware UI without replicating classification logic.

## Design principles

Each one elaborated in the master spec; condensed here:

- **Strictly additive, non-breaking.** No existing field changes type or is removed. Old consumers keep working unchanged.
- **MBID is authoritative.** When a tag carries both an entity name and a MusicBrainz ID, the MBID determines the canonical entity. Resolves "Béla Bartók" vs "Bela Bartok" via canonical data, not fuzzy text matching.
- **Comprehensive tagging produces the optimal outcome.** Thin tags get a thin experience by design. We deliberately do **not** infer composer credits from track titles or artist fields — false-positive risk is high (pop tracks where the artist *is* the composer would pollute the Classical view).
- **Opt-in by MediaType.** The Classical view sources only from Track / Album / Artist / Work. Radio / Podcast / Audiobook etc. are explicitly excluded regardless of genre tags.
- **The parser is permissive; the UI is opinionated.** All standard tags are read and stored as structured credits. The Classical view's browse axes filter to performing roles; track detail surfaces show the full credit list.

## Implementation plan

The work splits into 10 stages, each independently deployable. Each stage produces a reviewable PR; later stages depend on earlier ones for data shape but not for shipping behaviour.

| # | Stage | Repo |
|---|---|---|
| 1 | Model package additions (Work, Credit, ArtistRole, WorkType) | `music-assistant-models` |
| 2 | Database schema & migrations | `music-assistant/server` |
| 3 | Server controllers & API (WorksController, role-typed queries) | `music-assistant/server` |
| 4 | Local file tag parsing | `music-assistant/server` |
| 5 | Streaming provider mapping (per-provider) | `music-assistant/server` |
| 6 | MusicBrainz enrichment | `music-assistant/server` |
| 7 | Frontend Classical view | `music-assistant/frontend` |
| 8 | Basic Classical search (chip + flat 50 results) | both |
| 9 | Refined classical search (nested chip hierarchy) | both |
| 10 | Playback / queue behaviour (gapless within Work, no shuffle) | both |

## How this was designed

This isn't speculative — it's grounded in concrete user feedback and external precedent:

- **MA "Better Classical Music Support" Discord thread** — direct user signal from MA's own community.
- **Roon Classical Music Initiative (CMI) and Three Line Solution (TLS)** — Roon's documented approach to handling classical metadata; informed the role-typed credit shape (composer, conductor, orchestra, soloist, ensemble, choir, performer-with-instrument).
- **Apple Music Classical launch (March 2023)** — validated the three-axis browse structure (composer / work / performer), the recording-year display on Work detail, and the search-aware-of-opus-numbers approach.
- **Classical Extras Picard plugin** — community workaround for standard tagging gaps; informed which fallback tag names the parser supports.
- **Real-world tag investigation** — multiple classical CDs analysed to verify the design handles realistic tagging variance.

Full references (Picard tag mapping, MusicBrainz Work / Recording entities, ID3v2.4 / Vorbis / MP4 tag specs, iTunes movement tag conventions, community discussions): see the References section in `CLASSICAL_MUSIC_MODEL_SPEC.md`.

## Where to dig deeper

| Document | What it covers |
|---|---|
| `CLASSICAL_MUSIC_MODEL_SPEC.md` | The full design across all 10 stages. ~900 lines. Includes all 32 decisions (with rationale), UI mockups, edge cases, and the References section. **Start here for anything substantive.** |
| `CLASSICAL_MUSIC_PROPOSAL.md` | Community-facing proposal (shorter than the master spec; targets MA users rather than implementers). |
| `CLASSICAL_MUSIC_STAGE_1_MODELS.md` | Stage 1 PR doc (the model package additions). |
| `CLASSICAL_MUSIC_STAGE_2_SCHEMA.md` | Stage 2 PR doc (the database schema migrations). |
| Future per-stage PR docs | Stages 3–7 will each get a dedicated PR doc when implementation begins, mirroring the Stage 1 / 2 pattern. |
