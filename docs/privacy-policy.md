# Privacy Policy for Cadent

**Last updated: 11 September 2026**

Cadent is push-to-talk dictation that runs entirely on your own computer.
This policy describes what it does with your data. The short version: your
voice and your text never leave your device, and there is no analytics,
telemetry, tracking, or account of any kind.

## What Cadent does not do

- **No account.** Cadent has no sign-in, no user profile, and no backend of
  its own — there is no Cadent server for your data to be sent to.
- **No telemetry or analytics.** Cadent contains no analytics SDK, crash
  reporter, usage tracker, or error-reporting service.
- **No advertising**, and no sharing or selling of data to anyone.
- **No audio recordings are saved.** Microphone audio is held in memory only
  for as long as it takes to transcribe, then discarded. Cadent never writes
  an audio file to disk.
- **No transmission of your speech or text.** Transcription and optional
  cleanup run locally, on your machine, using models stored on your machine.

## What is stored, and where

Everything Cadent keeps is a plain file in your own user profile, under
`%LOCALAPPDATA%\Cadent` on Windows and `~/Library/Application Support/Cadent`
on macOS. Nothing is uploaded.

| File | What it holds |
| --- | --- |
| `history.db` | Your dictation history: the transcribed text, an optional cleaned-up version, the timestamp, how long the dictation lasted, the name of the application you dictated into, which mode was used, and the outcome. |
| `config.json` | Your settings, including your chosen hotkey, model, and any per-application rules. |
| `vocabulary.json` | Words and names you have added so they transcribe correctly. |
| `snippets.json` | Text shortcuts you have defined. |
| `models/` | The speech and cleanup models you chose to download. |
| `cuda/` | The optional GPU support pack, if you installed it. |

You can delete any of this at any time by deleting the folder. Uninstalling
Cadent offers to remove it for you, and keeps it if you say no, so that a
reinstall picks up where you left off.

## Network connections

Cadent makes no network connection at all during normal dictation. It connects
to the internet in exactly two situations, both of which you start:

1. **Downloading a speech or cleanup model**, from Hugging Face
   (`huggingface.co`). Cadent tells you the size before it starts, shows
   progress while it runs, and lets you cancel. Until you choose a model, no
   download happens.
2. **Downloading the optional GPU support pack**, from the Python Package Index
   (`pypi.org`). This is offered only if your computer has an NVIDIA graphics
   card that could speed up transcription, it is never installed
   automatically, and you can undo it by deleting the `cuda` folder.

Both transfers only download files to your computer. Neither uploads your
audio, your transcripts, your settings, your history, or any account or
installation identifier.

Like any download, these requests do reveal your IP address and a user-agent
string to the server you are downloading from — that is how the internet works,
and it is the same information your browser sends when you visit a web page.
Cadent adds nothing to it: no account, no device fingerprint, and no identifier
that would let anyone connect one download to another.

## Microphone access

Cadent records audio only while you are holding your dictation hotkey, or
between a tap and the next tap if you use tap-to-latch. It does not listen in
the background, and it does not record when the hotkey is not engaged. The
audio exists only in memory and is discarded after transcription.

## Accessibility and input

To insert text where you are typing, Cadent types into or pastes into the
application you have in focus. On macOS this requires the Accessibility
permission, which you grant in System Settings and can revoke at any time.
Cadent records the *name* of the focused application alongside a dictation in
your local history, so that you can recognise entries later and so that
per-application rules work. It does not read the contents of other
applications.

## Children

Cadent is a general-purpose utility and is not directed at children.

Cadent does save what you dictate — but only to your own computer, in the
files listed above. It is never transmitted to us or to anyone else: Cadent
has no backend of its own, and the only servers it ever contacts are the two
download hosts named above, which it fetches files from and sends nothing to.
That is true of every user, children included:
no personal information is collected from anyone, in the sense of being
received by us, and deleting the data folder removes what is stored locally.

## Changes to this policy

If this policy changes, the updated version will be published at this address
and the date at the top will change.

## Contact

Questions about this policy: <dj.mikeallison@gmail.com>

Cadent is open source. If you would prefer to verify any of the above rather
than take it on trust, the entire source is at
<https://github.com/mikeallisonJS/cadent>.
