# Better classical music support — proposal

We're looking at how to make Music Assistant work properly for classical-music listeners. Today, classical metadata gets squashed into the same flat "artists" list as everything else, there's no concept of a *Work* (the composition itself, separate from a particular recording), and you can't browse or search by composer. This proposal aims to fix that.

**Please tell us if you need something this proposal doesn't cover.** Easier to hear about it now than after we've shipped half of it.

## What metadata we'll capture

For every track and album, where the source provides it:

- **Composer** — as a proper artist, with their own MusicBrainz ID, sort name, biography, etc. (not just a string)
- **Conductor**
- **Orchestra / ensemble / choir** — as proper artists too
- **Soloists with instruments** — e.g. *Itzhak Perlman (violin)*, *Vladimir Ashkenazy (piano)*
- **Lyricist / librettist** — for opera, oratorio, lieder
- **Arranger** — for transcriptions and orchestrations
- **Work** — the composition itself, e.g. *Symphony No. 5 in C minor, Op. 67* — with catalog numbers (Op., BWV, K., …) and work type (symphony, concerto, sonata, opera, …)
- **Movement number, name, and total** — so the four movements of a symphony are linked into one Work

### Where it comes from

1. **Your local files** — standard tags that MusicBrainz Picard writes by default (`COMPOSER`, `CONDUCTOR`, `PERFORMER`, `WORK`, `MOVEMENTNAME`, etc.).
2. **Streaming providers** — Apple Music, Qobuz, Subsonic-compatible servers, and others where the API exposes the info.
3. **MusicBrainz enrichment** — to fill gaps (e.g. conductor identity for files that only have a name string).

## What this enables in the frontend

- **Browse by composer.** A first-class index, not a search trick.
- **Browse by work.** "All my recordings of Beethoven's 5th" in one place.
- **Filter searches by classical-specific tags.** Type a query, then narrow by composer / conductor / orchestra / instrument / work — the global-search-with-narrowing pattern, extended for classical.
- **Full credits visible on every track.** Composer, conductor, orchestra, every soloist with their instrument — not just a flat artist list.
- **Play a complete work.** All movements, in order, gapless playback, no shuffle within the work by default.
- **Multi-movement tracks grouped visually** so movements appear under their parent Work rather than as four sibling tracks.

## What stays the same

- The existing "Artists" view and main library browse work unchanged.
- Your existing tags are honoured as-is — we don't rewrite your `ARTIST` field.
- No breaking changes for the Home Assistant integration or any third-party tools.

## What's deliberately out of scope

- **"Period" / era filtering** (Baroque, Classical, Romantic, …) — there's no standard tag or MusicBrainz field for it; period would have to be guessed from composer dates. Use genre tags for now.
- **Rewriting or "fixing" your existing tags.** If your tags are wrong, MA won't try to correct them.
- **Recording-level metadata** beyond the credits (recording venue, dates, etc.) — possibly later.

## Tell us

- Is there a way you consume classical music that the above doesn't cover?
- Are there fields you tag in your library today that we haven't listed?
- Is there a streaming service whose classical metadata you'd particularly want supported?
- Is there a browse or playback behaviour you'd want that isn't mentioned?
