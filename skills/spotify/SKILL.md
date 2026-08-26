---
name: spotify
description: Control Spotify on this Mac by voice or by text - what is playing, pause, next and previous track, volume, shuffle, searching for a track or album by name and playing what was found. Use it when the owner asks to put music on, to play a particular track or artist, to make it louder or quieter, to skip a song, to pause, asks what is playing, or asks to find something in Spotify.
---

# spotify

The player lives on this machine, so it is driven by AppleScript - the same way Jarvis mutes the music while he is talking (`~/.claude/jarvis/media.py`). AppleScript cannot search by name, so the public Spotify API is used for that.

## Commands

```bash
S=~/.claude/skills/spotify/spotify.sh

bash $S state                    # what is playing: track, artist, album, volume
bash $S play | pause | toggle
bash $S next | prev
bash $S vol 60                   # set
bash $S vol +10                  # up
bash $S vol -10                  # down
bash $S shuffle on | off
bash $S find "kupla mahogany"    # find a track and play it at once
bash $S find-album "bonobo migration"
bash $S open "chillout"          # show 8 options, play nothing
```

Call it through `bash <path>` rather than directly: the `+x` bit does not always survive being synced or copied between machines.

## What to say out loud

The script prints the state one field per line - retell it in one sentence, never read the fields out:

- on `find` - say nothing, answer in text only
- on `vol` - "set it to eighty"
- on `state` - "playing Ishome, Ken Tavr"
- on `open` - name the first two or three options and ask which one
- on `playlist.py add` - say nothing if the track went in

**Music that started playing is never announced out loud.** The owner just asked for music, and the first thing they would hear is a voice over the top of it. What is playing they can see as text in the session and on the player itself. Only failure is spoken: the track was not found, the player is not running, the wrong thing started.

**A successful playlist addition is not announced out loud either.** They are listening to music, and a voice over it just to say "added" gets in the way more than it helps - the result is there in the session as text. Only failure is spoken: no playlist by that name, the track did not go in, no access.

Track and artist names are said as they are - no transliterating. The voice has a table for latin script, and on an unfamiliar word it spells it out, which is fine.

## Keys for searching

Two values in the Keychain:

```bash
security add-generic-password -U -s spotify-client-id     -a "$USER" -w '<client id>'
security add-generic-password -U -s spotify-client-secret -a "$USER" -w '<client secret>'
```

They come from the Spotify developer dashboard: developer.spotify.com/dashboard, the Create app button. The dashboard is not under the Documentation menu - it hides under your user name, top right.

**A redirect URI is required, and it has to be `http://127.0.0.1:8888/callback`.** Spotify has not accepted the word `localhost` since February 2025 - the form answers "this redirect URI is not secure". The field itself is not needed for search; it only matters once you go as far as personal playlists.

Registration and requests are free.

The script gets the app token itself and caches it in `~/.claude/jarvis/spotify_token` for an hour, mode 600. Without the keys the player commands still work; search does not.

## Your own playlists

```bash
P=~/.claude/skills/spotify/playlist.py

python3 $P list                                  your playlists
python3 $P create "Name" "description"           create a private one
python3 $P add "part of the name" spotify:track:...   add tracks
python3 $P show "part of the name"               what is inside
python3 $P uri "part of the name"                a uri, to play it through spotify.sh
python3 $P like spotify:track:...                add a heart
python3 $P liked                                 the last 20 saved
python3 $P liked spotify:track:...               check whether it has a heart
python3 $P unlike spotify:track:...              remove a heart, one at a time only
```

This runs on access granted as the owner - `auth.py`, a one-time browser flow. Check it with `python3 ~/.claude/skills/spotify/auth.py --check`. The token refreshes itself; there is no need to authorise again.

Playlists are created private: one put together by voice in passing should not quietly appear on a public profile. Making it public is a manual step in the app.

**Hearts.** The right to change the saved library (`user-library-modify`) has to be granted explicitly - without it "add this to my favourites" does not work by voice. The same right can also empty the whole library, which is why `unlike` takes exactly one track per call and there is no bulk removal anywhere in the code. After adding the scope, one re-authorisation is needed: `python3 ~/.claude/skills/spotify/auth.py`.

**API addresses after the February 2026 migration.** Spotify closed the old addresses to apps in development mode, and they answer 403:

- creating: `POST /me/playlists`, not `/users/{id}/playlists`
- tracks: `/playlists/{id}/items`, not `/playlists/{id}/tracks`
- in the response the field is called `item`; older responses had `track`

The error looks like a bare "Forbidden" with no explanation, and is easy to mistake for a missing scope - while the scopes are in fact correct.

## How it is put together

- `spotify.sh` - the player and search: AppleScript plus curl to the API
- `pick.py` - turns a search response into "uri, title, artist" lines
- `auth.py` - the one-time authorisation as the owner, plus token storage and refresh
- `playlist.py` - your own playlists: list, create, add, show
- `SKILL.md` - this file

The parsing is a separate file for a reason, not for tidiness: inside `spotify.sh` it was written as `python3 - <<PY`, and the heredoc took stdin for the code itself - so `json.load` got the already-read script instead of the data. The API request answered correctly all the while, which made it look like search was broken. Do not move the code back into the script.

## Window focus

The skill does not pull the screen over to Spotify. Measured on 22.08: the only command that raises the player window is playing by uri, which means `find` and `find-album`. Reading the state, pausing, volume and skipping a track do not touch the frontmost application at all.

After playing, the skill hands focus back to whatever was active. The hand-back is delayed by 0.7 seconds: immediately does not work, because Spotify activates slightly later and wins. The delay is tunable with `JARVIS_SPOTIFY_FOCUS_DELAY`.

## Limits

- This machine only: AppleScript drives the application here, and does not work remotely.
- The application is not launched automatically. If it is closed, the commands answer that Spotify is not running, and that is right: a quiet Mac should stay quiet.
- Jarvis mutes music while he talks through his own mechanism (`media.py`) and restores it afterwards. This skill is about something else - what is playing, not pausing for speech. They do not collide: `media.py` only touches what it paused itself.
- `find` plays the first result without asking. Use `open` when a choice is wanted.
