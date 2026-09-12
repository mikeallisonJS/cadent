# Store screenshots — capture guide

The Microsoft Store requires at least one screenshot and will not accept a
submission without one. This is the shot list, in the order they should appear
in the listing.

You have to take these yourself — they need the app actually running.

## Requirements

- **Format**: PNG.
- **Size**: 1366 × 768 or 1920 × 1080. Use one size for all of them; mixed
  aspect ratios look broken in the Store's carousel.
- **Count**: up to 10. Four good ones beat ten repetitive ones.
- **No chrome you did not mean to ship** — no other windows, no notification
  toasts, no personal data in the frame.

## Before you start

- Set Windows to **light mode** or **dark mode** and stay there. Cadent follows
  the system theme, and a carousel that flips between the two looks accidental.
- Use a clean desktop background, or none.
- Set display scaling to 100% so text renders sharply at native resolution.
- Put something plausible in the dictation history — a few realistic sentences.
  Empty states photograph badly, and real-looking content sells the feature.
- Check every frame for anything you would not publish: file paths with your
  username, email addresses, window titles from real work.

## The shots

### 1. Dictation in progress — the hero image

The whole product in one frame. Have a visible target application (an editor
or a chat window), the overlay showing that Cadent is listening, and text that
has just landed at the cursor.

This is the only screenshot most people will look at. It has to answer "what
does this do" without a caption.

### 2. Settings — General

Shows the app is configurable and looks like a real Windows application.
Frame the hotkey configuration if it fits, since "which key" is the first
question most people have.

### 3. Model picker

Distinctive, and it carries the whole offline story visually: a list of speech
models with their sizes and the "Recommended" chip on the row that suits the
machine. This is the screenshot that says *this runs on your computer*.

### 4. Dictation history

Searchable local history, with realistic entries. Reinforces that your data
stays on your machine.

### 5. Tray menu *(optional)*

Shows how you live with it day to day — it stays out of the way in the tray.
Worth including if you have room.

### 6. Vocabulary or snippets *(optional)*

For people who care about accuracy on names and jargon. Include if you have a
convincing example that is not your own private data.

## Captions

Partner Center allows a caption per screenshot. Write them — they are read
more than the description body. Keep each under about 80 characters:

1. "Hold a hotkey, speak, and your words appear wherever you are typing"
2. "Set your hotkey and pick how Cadent behaves"
3. "Six speech models, all running on your PC — Cadent recommends one for your hardware"
4. "Everything you have dictated, searchable and stored only on your machine"
5. "Lives in the tray and stays out of your way"
6. "Teach it the names and jargon it would otherwise mishear"

## Where to put them

Screenshots do not need to be committed — Partner Center hosts them. If you do
want them in the repo for the README, put them in `docs/images/` rather than
`packaging/icons/`, which `scripts/build.py` collects into the frozen app.
