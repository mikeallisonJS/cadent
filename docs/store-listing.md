# Microsoft Store listing copy

Draft content for the Partner Center submission. Paste into the matching
fields; the headings below are Partner Center's own field names.

Everything here is checked against the code, not aspirational. If a claim
stops being true, change it here and in the listing.

---

## Short description

*(Partner Center limit: 1,000 characters, but only the first 270 show in some
Store views — Microsoft recommends staying under 270. This one is 151.)*

> Push-to-talk dictation that runs entirely on your PC. Hold a hotkey, speak,
> release — your words appear at the cursor in any app. No cloud, no account.

---

## Description

*(Partner Center limit: 10,000 characters.)*

> **Dictation that never leaves your computer.**
>
> Hold Ctrl+Win, speak, and let go. Cadent transcribes what you said and types
> it straight into whatever app you were already using — your editor, your
> browser, your chat window, your terminal. No window to switch to, no text to
> copy and paste.
>
> Everything happens on your own machine. Your voice is never uploaded and
> never stored — it never leaves your PC, because Cadent has no backend. It has no
> account to create, no subscription, and no analytics of any kind. After you
> download a speech model once, it works with the network switched off.
>
> **What you get**
>
> - **Push-to-talk, anywhere.** One hotkey works across every application. Hold
>   to dictate, or tap to latch it on for longer passages.
> - **Fully offline speech recognition.** Choose from six speech models, from a
>   fast one that runs comfortably on any laptop to a large one for maximum
>   accuracy. Cadent recommends the right one for your hardware.
> - **Optional AI cleanup.** Turn it on and a local language model removes
>   filler words and fixes punctuation before the text lands. It runs on your
>   machine too, and if it ever fails you get your raw transcript rather than
>   nothing.
> - **Your vocabulary.** Teach it names, jargon, and acronyms it would
>   otherwise mishear.
> - **Snippets.** Say a short phrase, get a long one — email addresses, code
>   blocks, boilerplate.
> - **Searchable history.** Every dictation is saved locally so you can find
>   and reuse something you said last week.
> - **Per-app behaviour.** Some applications reject synthetic typing. Cadent
>   notices, switches that app to clipboard paste, and remembers.
> - **GPU acceleration** where your hardware supports it, on AMD, Intel, and
>   NVIDIA.
>
> **Privacy**
>
> Cadent records audio only while you are holding the hotkey, keeps it in
> memory, and discards it as soon as it has been transcribed. It never writes
> an audio file. Your transcripts, settings, and vocabulary are plain files in
> your own user folder, and deleting that folder deletes all of it.
>
> Cadent connects to the internet in exactly two situations, both of which you
> start yourself. The first is downloading a speech or cleanup model, which is
> disclosed with its size beforehand, shows progress, and can be cancelled. The
> second is the optional GPU support pack described below. Both only fetch
> files — neither uploads your audio, your transcripts, your settings or any
> account identifier. Cadent has no backend of its own to send them to.
>
> **About the optional GPU support pack**
>
> If your PC has an NVIDIA graphics card that could accelerate transcription,
> Cadent offers a one-time download of two NVIDIA support libraries
> (`cublas64_12.dll` and `cublasLt64_12.dll`) from the Python Package Index.
> This is entirely optional, never automatic, and always disclosed before it
> runs. It exists because bundling these libraries with every copy would add
> roughly half a gigabyte for the many people who cannot use them. They are
> stored in a folder named "cuda" inside Cadent's application data folder, and
> deleting that one folder removes the pack and returns Cadent to running on
> the processor. Your settings, history and models are stored separately and
> are not affected.
>
> **Requirements**
>
> Windows 10 or 11, 64-bit. A microphone. Disk space for the app itself, plus
> whichever models you choose to download — Cadent shows each model's exact
> download size before you commit to it.
>
> Cadent is free and open source.

*(End of the Description field — everything above this line, and nothing below
it, goes in the box.)*

**Keep URLs out of the Description.** Microsoft's guidance is explicit: *"Do
not include HTML, code snippets, or URLs in the description field. Instead,
provide support, privacy policy, and website links in their designated
submission fields."* The repository link belongs in **Website**, set below.

---

## Product features

*(Short bullets, 200 characters each.)*

- Hold a hotkey, speak, and your words appear at the cursor in any application
- Fully offline — speech recognition runs on your PC, with no cloud service
- No account, no subscription, no telemetry, no analytics
- Optional local AI cleanup removes filler words and fixes punctuation
- Custom vocabulary for names, jargon, and acronyms
- Text snippets triggered by a spoken phrase
- Searchable local history of everything you have dictated
- GPU acceleration on AMD, Intel, and NVIDIA

---

## Partner Center fields

| Field | Value |
| --- | --- |
| Category | Productivity |
| Subcategory | Pick from whatever Partner Center offers under Productivity — the list changes, so choose at submission time rather than trusting a value written here |
| Privacy policy URL | `https://github.com/mikeallisonJS/cadent/blob/main/docs/privacy-policy.md` |
| Website | `https://github.com/mikeallisonJS/cadent` |
| Support contact | `https://github.com/mikeallisonJS/cadent/issues` |
| Install switches | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` |
| Uninstall switches | `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART` |
| Package URL | `https://downloads.mikeallisonjs.com/vX.Y.Z/Cadent-Setup-X.Y.Z.exe` — printed in the tag build's job summary |

**Not the GitHub Release asset URL.** It answers `302` and redirects to a
signed link that expires within the hour; Partner Center rejects it with "The
package URL redirects to another URL." The R2 copy exists for this reason, and
the tag build checks it returns `200` before going green.

### Product declarations

MSI/EXE submissions have no capability manifest, so there is no microphone
*permission* to declare — that belongs in System requirements below. The
declarations page offers exactly four boxes:

| Declaration | Cadent | Why |
| --- | --- | --- |
| Depends on non-Microsoft drivers or NT services | **No** | Installs no driver and no service. Autostart is an `HKCU\...\Run` value, audio goes through PortAudio in user mode, and GPU work uses whatever driver is already present with CPU fallback. Describe the GPU pack in Notes for certification instead. |
| Tested to meet accessibility guidelines | **No** | This asks whether *Cadent's own UI* is accessible — accessible names, keyboard navigation, 4.5:1 contrast, verified with Narrator, Magnifier and High Contrast, and checked with Inspect or AccChecker. Dictation being assistive technology is not the same claim. Microsoft: "Do not list your app as accessible unless you have specifically engineered and tested it for that purpose." Revisit once that pass is actually done. |
| Supports pen and ink input | **No** | Voice and keyboard only. |
| **Incorporates generative AI features** | **Yes** | Cleanup runs a local LLM that rewrites the transcript, and the declaration explicitly covers models built into the app. The listing already says "optional AI cleanup" and "a local language model", so omitting it would contradict the copy. |

### System requirements

Optional, and softer than it reads: customers whose device lacks a stated
requirement can still download the app — but **they cannot rate or review it
on that device**. Every unnecessary minimum quietly suppresses reviews, which a
new listing cannot afford. Mark what is genuinely needed and nothing else.

| Item | Setting | Why |
| --- | --- | --- |
| Microphone | **Minimum** | The app does nothing without one, and nearly every laptop has one. |
| Keyboard | **Minimum** | Dictation only starts from the global hotkey; the tray click toggles pause, not dictation. |
| Memory | **Recommended, 8 GB** | Not a minimum — `hardware.py` degrades instead of failing, picking the smallest model under 6 GB and flagging "light on memory" under 8. |
| Processor | **Recommended, 4+ cores** | `suggest_model` reaches for the more accurate model at 8 physical cores; nothing requires it. |
| DirectX, Dedicated GPU Memory, Graphics | **Leave blank** | GPU is optional acceleration with CPU fallback throughout. A minimum here would misdescribe the app and silence reviews from the majority who have no dedicated GPU. |
| Touch screen, Mouse, Camera, NFC, Bluetooth LE, Telephony | **No** | None apply. |

### Additional system requirements

Free text, 200 characters per entry, 11 entries maximum:

- `64-bit (x64) processor. Runs on ARM64 PCs under emulation, with reduced performance.`
- `Windows 10 or 11.`
- `Internet connection required once, to download a speech model. Dictation works offline after that.`

The emulation line matters because the installer allows it —
`ArchitecturesAllowed=x64compatible` admits ARM64 machines, where the app runs
under x64 emulation, almost certainly on the CPU and slowly. Nobody has tested
that path; saying so beats a surprised one-star review from a Copilot+ PC.

### Age rating

Complete the questionnaire. Cadent is a utility with no user content, no social
features and no ads, so it should come out at the lowest rating.

### Notes for certification

*(2,000 character limit. The draft below is ~1,430.)*

This field carries more risk than any checkbox on the page. Microsoft warns
that "apps that appear to be incomplete may fail certification", and Cadent is
a tray app with **no main window** that also cannot dictate until a model has
downloaded. A tester who installs it, sees nothing happen and cannot make it
work is a plausible rejection. Fill this in.

> Cadent is a push-to-talk dictation utility. No account or login is required.
>
> IMPORTANT - Cadent runs in the system tray and has no main window. After
> installing, launch it from the Start menu; it then appears as an icon in the
> notification area (you may need to expand the hidden-icons chevron). Click
> that icon for the menu, Settings and dictation history. The app is not
> incomplete - this is the intended design for a utility driven by a global
> hotkey.
>
> FIRST RUN: a setup wizard asks you to choose a speech model and downloads it
> from Hugging Face. The smallest option is fine for testing. The download is
> disclosed with its size before it starts, shows progress, and can be
> cancelled. Dictation cannot work until a model is present, so please let it
> finish before testing.
>
> TO TEST DICTATION: hold Ctrl+Win, speak a sentence, then release. The text is
> typed into whichever application has focus - Notepad is the simplest target.
> A microphone is required.
>
> OPTIONAL GPU SUPPORT PACK: on PCs with an NVIDIA GPU, Cadent may offer a
> one-time download of two NVIDIA cuBLAS libraries from pypi.org to enable GPU
> acceleration. It is never automatic, is always disclosed and confirmed by the
> user first, and is not required - declining leaves Cadent running on the
> processor.
>
> All transcription and optional cleanup run locally on the user's PC. Cadent
> has no backend service and uploads nothing.
>
> Notes entered [TODAY'S DATE].

Fill in the date — Microsoft asks for it explicitly, so testers can judge
whether a problem you mention was temporary.

### Search terms

Listed in priority order. Microsoft's MSI/EXE listing page documents no
keywords field at all, and the familiar seven-term cap belongs to the MSIX
listing — so treat the count as unknown until you see the field. If it caps,
take them from the top.

1. `dictation`
2. `speech to text`
3. `voice typing`
4. `offline`
5. `transcription`
6. `push to talk`
7. `local`
8. `whisper`
9. `accessibility`
10. `productivity`

---

## A note on the GPU support pack

Do not leave this out to keep the listing tidy. It downloads executable code
after installation, and a reviewer who finds that undisclosed is entitled to
read it as dynamically included code, which is a policy problem. Stated plainly
and up front — optional, user-initiated, disclosed in the app, removable — it
reads as a considerate design decision, which is what it is.
