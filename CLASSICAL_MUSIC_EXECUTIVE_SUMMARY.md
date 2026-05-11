# Classical Music Support — Executive Summary

**For:** MA head devs and reviewers
**Status:** Proposal for endorsement
**Full design:** [`CLASSICAL_MUSIC_MODEL_SPEC.md`](CLASSICAL_MUSIC_MODEL_SPEC.md) (~900 lines, all decisions captured)
**Per-stage PR docs:** `CLASSICAL_MUSIC_STAGE_1_MODELS.md`, `CLASSICAL_MUSIC_STAGE_2_SCHEMA.md`, etc.

---

## TL;DR

Classical music doesn't work well in MA today, and there's clear evidence — Apple's $-investment launching *Apple Music Classical* as a dedicated app in 2023, Roon's classical features driving $700+ subscriptions, Idagio's standalone classical streaming business — that classical listeners are a real, addressable, currently-underserved segment.

This proposal adds first-class classical metadata support to MA across 10 staged, independently-deployable changes. **Stages 1 and 2 (model package, database schema) are already shipped**; the remaining stages are backend controllers, tag parsing, provider mapping, MusicBrainz enrichment, the new Classical view, and search. The work is **strictly additive and non-breaking** — pop / rock / electronic / jazz users see no change unless they want to.

MA's unique advantage over Roon, Apple Music Classical, and Idagio: **we work with the user's existing music library across all their sources** (local files, Spotify, Tidal, Qobuz, Apple Music, etc.) rather than locking them into one ecosystem. Designed from the ground up using canonical MusicBrainz data and shaped by user feedback from the MA Discord, Roon forums, and classical-listener community discussions.

---

## The opportunity

### Market validation is unambiguous

| Service | Signal | What it tells us |
|---|---|---|
| **Apple Music Classical** (launched March 2023) | Apple acquired Primephonic in 2021, spent 18+ months building a *dedicated app* for classical, launched it free to existing Apple Music subscribers | Apple believes classical is a meaningful enough segment to justify a separate product surface |
| **Roon** | Classical handling is consistently cited as a top reason audiophiles pay $150/year or $700 lifetime | Hardcore audio users will pay a premium for classical-aware browsing |
| **Idagio** | Standalone classical-only streaming service, ~10 years in market | Classical listeners willing to pay for a dedicated tool |
| **Classical Extras Picard plugin** | Active community plugin maintained for years to work around standard tagging gaps | Users are doing manual workarounds because tools don't serve them natively |

The demand is real, established, and willing to pay. The MA Discord's "Better Classical Music Support" thread shows the same user base wanting MA to serve them.

### Cost of not doing this

Audiophile users (a major MA segment overlap) churn to Roon for classical. New users with classical libraries bounce off MA. Home Assistant integration users wanting classical features hit dead ends. None of these losses show up in obvious metrics — they're silent departures or non-adoptions.

### Cost of doing this is bounded

Strictly additive across all stages. Schema/model already shipped. No existing functionality changes. The Classical view is opt-in by MediaType (lives alongside the standard Artists/Albums/Tracks views — those are unchanged). The work is staged so each PR is reviewable in isolation.

---

## The problem

MA's current artist model is **flat**. A track has an `artists` list — period. There's no distinction between composer, conductor, orchestra, soloist, or accompanist. Classical music depends on those distinctions:

> *Karajan / Berlin Philharmonic conduct Beethoven's Symphony No. 5: II. Andante con moto*
>
> Today this track gets squashed into something like `artists = ["Berlin Philharmonic", "Karajan"]` or `artists = ["Beethoven"]` depending on tagging. The composer-vs-conductor-vs-orchestra distinction is lost. The fact that this is movement 2 of 4 of a single composition (the Symphony) is lost. The relationship to other recordings of the same composition (Bernstein/VPO's, Solti/Chicago's) is lost.

What classical listeners actually want (synthesised from Roon CMI forums discussion, Apple Music Classical's launch coverage, Classical Extras plugin's mere existence as a community workaround, and the MA "Better Classical Music Support" Discord thread):

1. **Browse by composer as the primary axis** — "show me all my Bach" is the single most-cited ask.
2. **Work as a first-class browseable entity** — multiple recordings of the same composition grouped under one entry; movements playable as a unit gapless.
3. **Distinct conductor / orchestra / soloist credits** — filterable to "all Karajan recordings", "all Berlin Philharmonic recordings", "all violin recordings" without fuzzy text matching.
4. **Catalog numbers (BWV, K., Op., HWV) parsed and searchable** — often the canonical handle for a work.
5. **Roll-up across granularity** — the same recording can appear on multiple albums; the same Work has multiple recordings; arrangements are distinct from sources. All needs to roll up cleanly into searchable / playable units.

---

## The solution (high level)

Three additions at the model layer; one new view in the frontend; preserved compatibility everywhere else.

### Model layer

- **`Work` as a first-class MediaItem** — the *composition* (e.g. "Symphony No. 5 in C minor, Op. 67"), distinct from any specific recording. Multiple recordings of the same Work share one Work entity, matched by MusicBrainz Work MBID where available.
- **Role-typed credits via a `Credit` type** — `(artist, role, instrument, position)` where role is one of `MAIN_ARTIST` / `COMPOSER` / `CONDUCTOR` / `ORCHESTRA` / `ENSEMBLE` / `CHOIR` / `SOLOIST` / `PERFORMER` / `LYRICIST` / `ARRANGER`. Sits alongside the existing flat `Track.artists` list (which remains canonical for the headline credit).
- **Movement linkage on `Track`** — `work`, `movement_number`, `movement_total`, `movement_name`. Multi-movement playback and Work-grouped browse become possible.

All strictly additive. No existing field changes type or is removed.

### Frontend layer

A new top-level **"Classical" navigation entry** with three internal tabs:

- **Composers** — index of composers in the library. Click → composer detail (works listed underneath).
- **Works** — index of compositions. Click → Work detail (multiple recordings grouped under one composition; movements visible per recording; "Show all" navigation contextual filters).
- **Performers** — index of conductors / orchestras / chamber groups / choirs / soloists with role-filter chips.

The standard Artists / Albums / Tracks views are **unchanged**. The Classical view is a *parallel lens* over the same data — not a replacement. Users who don't have classical content see the Classical entry greyed out (same pattern as Audiobooks / Podcasts).

UI mockups (low-fidelity structural sketches): see `CLASSICAL_MUSIC_MODEL_SPEC.md` → "View structure (low-fidelity sketches)" section. The Work detail page is the genuinely new page type — collapsing multiple recordings of one composition into one browseable entry with movements expandable per recording.

### Backend layer

Server-side `WorksController` mirrors the existing per-MediaType controllers. `TracksController` and `AlbumsController` gain role-typed-credit awareness. MusicBrainz enrichment extended to pull Recording-Work links and Work entity metadata. Per-entity `Track.is_classical` / `Album.is_classical` / `Artist.is_classical` boolean fields exposed for clients to render classical-aware UI without replicating classification logic.

---

## Design principles

Each elaborated in the master spec; condensed here:

- **Strictly additive, non-breaking.** No existing field changes type or is removed. Old consumers keep working unchanged.
- **MBID is authoritative.** When a tag carries both an entity name and a MusicBrainz ID, the MBID determines the canonical entity. Resolves "Béla Bartók" vs "Bela Bartok" via canonical data, not fuzzy text matching.
- **Comprehensive tagging produces the optimal outcome.** Thin tags get a thin experience by design. We deliberately do **not** infer composer credits from track titles or artist fields — false-positive risk is high (pop tracks where the artist *is* the composer would pollute the Classical view).
- **Opt-in by MediaType.** The Classical view sources only from Track / Album / Artist / Work. Radio / Podcast / Audiobook etc. are explicitly excluded regardless of genre tags.
- **The parser is permissive; the UI is opinionated.** All standard tags are read and stored as structured credits. The Classical view's browse axes filter to performing roles; track detail surfaces shows the full credit list.

---

## Competitive positioning

| | Roon | Apple Music Classical | Idagio | **Music Assistant** |
|---|---|---|---|---|
| **Classical browse** | Excellent (CMI/TLS) | Excellent | Excellent | Proposed: equivalent quality |
| **Cost** | $150/yr or $700 lifetime + Roon-compatible hardware | Apple Music subscription (~$10/mo, Apple-only) | $10–15/mo (standalone) | **Free, open source** |
| **Library sources** | Roon Core + supported streaming | Apple Music catalog only | Idagio catalog only | **Local files + Spotify + Tidal + Apple Music + Qobuz + Deezer + YouTube Music + others** |
| **Lock-in** | Hardware ecosystem | Apple ecosystem | Single service | **None — works with whatever the user already has** |
| **Open source** | No | No | No | **Yes** |
| **Home Assistant integration** | No | No | No | **Native (MA's primary integration)** |
| **Customisable / extensible** | No | No | No | **Yes (community-driven)** |

**MA's unique value proposition:** classical-aware browsing across the user's existing diverse library, without forcing them into one ecosystem and without an additional subscription. Open source, customisable, integrated with the smart-home stack they're already using.

Roon has the best classical UX but costs a fortune and locks users to its hardware ecosystem. Apple Music Classical works only with Apple's own catalog. Idagio is standalone classical-only — useful if classical is your *only* listening, useless if you also have a pop library. MA is the only solution that respects the reality of how users actually listen: a mix of streaming providers, local files, and home-automation integration.

---

## Implementation plan

The work splits into 10 stages, each independently deployable. Each stage produces a reviewable PR; later stages depend on earlier ones for data shape but not for shipping behaviour.

| # | Stage | Repo | Status |
|---|---|---|---|
| 1 | Model package additions (Work, Credit, ArtistRole, WorkType) | `music-assistant-models` | **Shipped** |
| 2 | Database schema & migrations | `music-assistant/server` | **Shipped** |
| 3 | Server controllers & API (WorksController, role-typed queries) | `music-assistant/server` | In design |
| 4 | Local file tag parsing | `music-assistant/server` | In design |
| 5 | Streaming provider mapping (per-provider) | `music-assistant/server` | In design |
| 6 | MusicBrainz enrichment | `music-assistant/server` | In design |
| 7 | Frontend Classical view | `music-assistant/frontend` | In active development |
| 8 | Basic Classical search (chip + flat 50 results) | both | Deferred |
| 9 | Refined classical search (nested chip hierarchy) | both | Deferred |
| 10 | Playback / queue behaviour (gapless within Work, no shuffle) | both | Deferred |

**Suggested delivery order:** Stages 1–2 are done. Stage 3 unblocks Stages 4–6 (which can land in parallel). Stage 7 can run in parallel with the backend stages (currently doing so against mock data). Stages 8–10 are independent polish.

### User-visible activation timeline

Worth being honest with users about what they'll see when:

- **Stages 1–3 alone:** no visible change. Foundation only.
- **Stage 4 (local-file tag parsing):** users with well-tagged local libraries (Picard) get the full Classical view experience.
- **Stage 5 (streaming provider mapping):** per-provider gains — Apple Music gives composers, Qobuz gives structured contributors, Spotify barely moves (Spotify exposes near-zero structured classical data).
- **Stage 6 (MusicBrainz enrichment):** streaming-only users finally get useful classical data via ISRC → MB Recording → Work lookup. This is the biggest unlock for non-local-library users.
- **Stage 7 (frontend Classical view):** users actually see the Classical view in the UI.

So users with proper Picard-tagged local libraries see value after Stages 4 and 7. Streaming-only users see meaningful value once Stage 6 (MB enrichment) lands. Worth communicating this expectation honestly when this ships.

---

## Risk and scope management

### What this is **NOT**

- **Not a streaming service.** MA remains agnostic to where music comes from.
- **Not classical-only.** The Classical view is a parallel lens; pop/rock/jazz/electronic browsing is unchanged.
- **Not breaking.** Every model change is additive with safe defaults. Old consumers continue working unchanged.
- **Not gating MA-wide search infrastructure.** Classical search at Stage 8 uses the existing single-term substring backend with extended fields — no FTS5 migration, no token-AND search backend changes. An MA-wide search upgrade is its own RFC, separate from this work.

### What's deliberately deferred or out of scope

- Period / era as a browse axis (no canonical source — no standard tag, no MB field; derivable from genre tags or composer dates if needed).
- Fuzzy matching across spelling variants without an MBID (resolved by the MBID-canonical rule).
- Curated classical playlists.
- Commissioned composer artwork.
- Three-level opera hierarchy (`WORK → SECTION → PART`) — additive when user demand surfaces.

### Compatibility considerations

The MA model package release pattern means:

- The server pins a `music-assistant-models` version. Updating to the version with Stage 1 additions is a deliberate dependency bump, not automatic.
- New fields with safe defaults round-trip through mashumaro serialisation; old clients (HA integration, third-party model consumers) ignore unknown fields gracefully.
- The new `MediaType.WORK` value is unknown to old clients, which fall through to their `_missing_ → UNKNOWN` handler — same behaviour they'd exhibit for any future media type.

No coordinated multi-component release required. Each stage's PR ships when ready.

---

## How this was designed

This proposal isn't speculative — it's grounded in concrete user feedback and external precedent:

- **MA "Better Classical Music Support" Discord thread** — direct user signal from MA's own community.
- **Roon Classical Music Initiative (CMI) and Three Line Solution (TLS)** — Roon's documented design (community forums) for handling classical metadata; informed the role-typed credit shape (composer, conductor, orchestra, soloist, ensemble, choir, performer-with-instrument).
- **Apple Music Classical launch (March 2023)** — validated the three-axis browse structure (composer / work / performer), the recording-year display on Work detail, and the search-aware-of-opus-numbers approach.
- **Classical Extras Picard plugin** — community workaround for standard tagging gaps; informed which fallback tag names the parser supports.
- **Real-world tag investigation** — multiple CDs across genres analysed (Wedding Classics, Highlights from Swan Lake, Best Classical Album, Stars of Opera) to verify the design handles realistic tagging variance.

Detailed user-needs synthesis: `CLASSICAL_MUSIC_MODEL_SPEC.md` → "What classical listeners actually want" section.

Full references: `CLASSICAL_MUSIC_MODEL_SPEC.md` → "References" section, including Picard tag mapping, MusicBrainz Work / Recording entities, ID3v2.4 / Vorbis / MP4 tag specs, iTunes movement tag conventions, and links to each community discussion.

---

## What I'm asking for

**Endorsement to continue with the staged delivery.** Stages 1–2 are already in. Stage 3 (server controllers) is the next blocking step; once it lands, Stages 4–6 (parsing + provider mapping + MB enrichment) can run in parallel. Stage 7 (frontend) is already in active development against mock data.

**Review of the master spec at your convenience** — particularly the Decisions log (32 entries, each capturing a substantive design question and its resolution) so that future reviewers don't re-litigate questions that have already been worked through.

**No new infrastructure dependencies.** This works on top of MA's existing model, database, controllers, and frontend stack. No FTS5, no new providers, no breaking changes.

---

## Where to dig deeper

| Document | What it covers |
|---|---|
| `CLASSICAL_MUSIC_MODEL_SPEC.md` | The full design across all 10 stages. ~900 lines. Includes all decisions, UI mockups, edge cases, and the References section. **Start here for anything substantive.** |
| `CLASSICAL_MUSIC_PROPOSAL.md` | Community-facing proposal (shorter than the master spec; targets MA users rather than implementers). |
| `CLASSICAL_MUSIC_STAGE_1_MODELS.md` | Stage 1 PR doc (the model package additions that have already shipped). |
| `CLASSICAL_MUSIC_STAGE_2_SCHEMA.md` | Stage 2 PR doc (the database schema migrations that have already shipped). |
| Future per-stage PR docs | Stages 3–7 will each have a dedicated PR doc when implementation begins, mirroring the Stage 1 / 2 pattern. |

---

## Questions, decisions, and call to action

Specific decisions worth your input:

1. **Endorsement to proceed.** The work has momentum; head-dev endorsement helps move it from "in-flight project" to "official MA roadmap item".
2. **Review of Decisions log** — particularly any entries you'd want re-litigated before too much further work commits.
3. **Any concerns about the staging plan** — especially Stage 3 scope (the biggest server-side stage by code volume).

Happy to walk through any section live, do a deeper dive on specific stages, or modify the plan based on reviewer input. The goal is a classical experience MA users genuinely prefer to Roon and Apple Music Classical — built openly, from the ground up, on the back of MA's existing strengths.
