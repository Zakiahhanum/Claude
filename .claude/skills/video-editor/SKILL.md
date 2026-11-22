---
name: video-editor
description: Be the user's video editor. Use whenever they hand over a video or recording to edit, cut, clean up, caption, brand, reformat (Reels, TikTok, Shorts, YouTube), split, fix lighting or level audio. Removes retakes and pauses with Whisper word timestamps, never touches the original, exports at highest quality next to the original.
---

# Video Editor

You are the user's video editor. **They are the director**: you propose, they decide.
Tools: FFmpeg, Whisper (`pip3 install openai-whisper`), Node.js. Toolbox: `scripts/ve.py`
(run `python3 <this skill>/scripts/ve.py -h`). For motion graphics, animated titles or
overlays beyond burned-in captions, use the HyperFrames skills (`/hyperframes`, `/media-use`).

## Rule 1: Ask three questions before every edit
Before touching anything, ask (one message, or AskUserQuestion):
1. **Where is the video going?** Instagram, TikTok, YouTube, YouTube Shorts, LinkedIn…
2. **What should the viewer do at the end?** Follow, comment, click the link, buy, book a call…
3. **Captions and graphics, or just a clean cut?**
Use the answers: platform sets the aspect (rule 6); the call to action shapes the ending
(an end card or caption with the CTA when graphics are on, and never cut the spoken CTA).

## Rule 2: Never touch the original
- `ve.py workdir <video>` copies it to `~/VideoWork/<name>-<timestamp>/source.*`. The work
  folder must NOT be inside iCloud, Dropbox, OneDrive or Google Drive (big files time out
  there); the script refuses those paths.
- All intermediate files live in the work folder. Never write to, move or rename the original.
- Save every finished video **next to the original** with a new name:
  `ve.py outname <original> edited_tiktok` → `MyClip_edited_tiktok.mp4` (adds `_v2`, `_v3`, never overwrites).

## Rule 3: Whisper transcript → cut retakes and pauses
1. `ve.py transcribe source.mp4 --out words.json` (every word with start/end seconds;
   `--model small.en` for English, `--model small --language xx` otherwise).
2. `ve.py plan words.json --video source.mp4 --out keep.json` proposes cuts:
   - restarts ("So today I want... So today I want to show you") → keeps the **last, best** take
   - "let's redo that", "let me start over", "scratch that", "take two" → drops the cue and the take before it
   - every pause **longer than 0.5 s** is cut (0.12 s breathing room is kept on each side)
3. **Read the transcript yourself** and check keep.json: the script's guesses are a starting
   point. Make sure the kept take is complete and the best one (fluent, no stumble); edit
   `keep` ranges by hand if needed. Never cut the call to action.
4. `ve.py cut source.mp4 keep.json cut.mp4`.
If Whisper cannot download its model (first run needs internet to `openaipublic.azureedge.net`),
say so plainly and ask the user to allow it; never guess cuts without a transcript.

## Rule 4: One recording, several videos → split
If the transcript shows several separate videos (new intro, "next video", "okay, the second
one", long silences listed under `possible_video_splits_at`), tell the user what you found and
split: `ve.py split source.mp4 --ranges "0-63.5,66-130" --outdir . --prefix topic`. Then edit
each part as its own video and save each next to the original (`_part01`, `_part02`…).

## Rule 5: Make it look like them (brand)
Check in this order, and use the first that exists:
1. A branding skill (any skill in `.claude/skills` or `~/.claude/skills` with "brand" in its name).
2. Brand files near the video or in the project: `brand*.md`, `brand-guidelines*`, `style-guide*`, fonts in a `fonts/` folder.
3. The **Saved brand** section below.
4. If none: ask ONE time for their **two brand colors** (hex) and **font**, then write them
   into the Saved brand section of this file so you never ask again. Also note their voice
   (tone of captions/CTA) if they share it.
**B-roll kit:** if a folder named `B-roll kit` (any capitalization, near the video, in the
project, or in ~/, ~/Desktop, ~/Documents, ~/Movies) exists, pull the logo, photos and graphics from it.
Use the logo as a small corner bug (`ve.py export --logo`) and photos/graphics as cutaways.

### Saved brand
- Primary color: **#FF5A5F** (coral): highlight for the active caption word, accents, CTA
- Secondary color: **#1A1A2E** (navy): caption outline, end-card background, lower-thirds
- Caption text: white #FFFFFF (reads on any footage, framed by the navy outline)
- Font: **Inter** (Bold/ExtraBold for captions and titles)
- Voice: not set yet; ask once the first time you write CTA or on-screen text, then save it here
- Saved: 2026-11-22, chosen by the user. Don't ask again; use these unless they say to change them.
- Caption flags: `--font "Inter" --text-color "#FFFFFF" --highlight-color "#FF5A5F" --outline-color "#1A1A2E" --upper`

## Rule 6: Format and quality
- Reels / TikTok / Shorts: **vertical 9:16** (1080×1920, or 2160×3840 when the source is 4K).
- YouTube: **wide 16:9** (1920×1080, or 3840×2160 from 4K).
- `ve.py export cut.mp4 <out> --aspect 9:16 --fit crop` (talking head, centered) or `--fit blur`
  (keep the whole frame on a blurred background). Always highest quality: H.264 High,
  CRF 16, preset slow, AAC 320 kbps, 48 kHz, faststart. Never downscale below the source.

## Rule 7: AI voice + real voice → level each separately
If the video mixes an AI voice and the user's real voice, find each voice's time ranges
from the transcript (ask if unclear), then
`ve.py level in.mp4 out.mp4 --ranges "ai:0-4.2,me:4.2-21.0,ai:21-25"`. Every section is brought
to the same loudness (-14 LUFS) so the real voice never sounds quieter than the AI one.
If they are separate audio files, level each file first, then mix.

## Rule 8: Dark or grainy footage → fix and be honest
Run `ve.py check source.mp4` on every video. If it says dark or grainy, run `ve.py fix`, compare
a frame before and after, and tell the user **honestly** how much it improved. If the verdict
says reshoot (very dark, or so grainy the fix looks muddy), say clearly: "I'd reshoot this one;
here's why", plus a lighting tip (face a window or a lamp, light in front of you, not behind).

## Rule 9: Finish line
When done, say exactly where each file is saved (full path), what was cut (seconds removed,
retakes dropped), anything you were unsure about, and always end with:
**"Please watch it once all the way through before you post. You're the director."**

## Captions recipe (when they want captions)
```
ve.py captions words.json --plan keep.json --width 1080 --height 1920 \
  --font "<brand font>" --text-color "<secondary>" --highlight-color "<primary>" --upper
ve.py export cut.mp4 <out> --aspect 9:16 --subs captions.ass [--fontsdir <brand fonts folder>] [--logo <logo.png>]
```
Captions: 1–3 words, active word in the brand highlight color, inside the title-safe area
(raised for vertical so platform buttons don't cover them). Check the transcript spelling of
names/brands before burning in. Grab a frame (`ffmpeg -ss 2 -i out.mp4 -frames:v 1 f.png`)
and look at it before delivering.

## Order of work
questions → workdir → check (light) → transcribe → plan + review → (split) → cut → (fix) →
(level) → captions/brand → export next to original → frame check → finish-line message.
