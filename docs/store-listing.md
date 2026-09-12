# Microsoft Store listing copy

Draft content for the Partner Center submission. Paste into the matching
fields; the headings below are Partner Center's own field names.

Everything here is checked against the code, not aspirational. If a claim
stops being true, change it here and in the listing.

---

## Short description

*(Partner Center limit: 200 characters. This one is 138.)*

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
> Everything happens on your own machine. Your voice is never uploaded, never
> stored, and never sent to a server, because there is no server. Cadent has no
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
> second is the optional GPU support pack described below. Neither sends
> anything about you anywhere.
>
> **About the optional GPU support pack**
>
> If your PC has an NVIDIA graphics card that could accelerate transcription,
> Cadent offers a one-time download of two NVIDIA support libraries
> (`cublas64_12.dll` and `cublasLt64_12.dll`) from the Python Package Index.
> This is entirely optional, never automatic, and always disclosed before it
> runs. It exists because bundling these libraries with every copy would add
> roughly half a gigabyte for the many people who cannot use them. They are
> stored in your local application data folder, and deleting that folder
> reverts Cadent to running on the processor.
>
> **Requirements**
>
> Windows 10 or 11, 64-bit. A microphone. Disk space for the app itself, plus
> whichever models you choose to download — Cadent shows each model's exact
> download size before you commit to it.
>
> Cadent is open source: https://github.com/mikeallisonJS/cadent

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
| Package URL | The `Cadent-Setup-X.Y.Z.exe` asset on the matching GitHub Release |

### Declarations

- **Microphone**: yes. Cadent records audio while the dictation hotkey is held.
  Declaring this is not optional — an undeclared microphone is a certification
  failure.
- **Age rating**: complete the questionnaire. Cadent is a utility with no user
  content, no social features, and no ads, so it should come out at the lowest
  rating.

### Search terms

`dictation`, `speech to text`, `voice typing`, `transcription`, `offline`,
`local`, `whisper`, `push to talk`, `accessibility`, `productivity`

---

## A note on the GPU support pack

Do not leave this out to keep the listing tidy. It downloads executable code
after installation, and a reviewer who finds that undisclosed is entitled to
read it as dynamically included code, which is a policy problem. Stated plainly
and up front — optional, user-initiated, disclosed in the app, removable — it
reads as a considerate design decision, which is what it is.
