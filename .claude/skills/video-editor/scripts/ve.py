#!/usr/bin/env python3
"""ve.py - the video-editor skill's toolbox (FFmpeg + Whisper).

Subcommands (run `python3 ve.py <cmd> -h` for options):
  workdir     copy the original into a safe local work folder (never iCloud/Dropbox)
  probe       print size, length, fps, audio info
  transcribe  Whisper -> words.json (every word with start/end seconds)
  plan        words.json -> keep.json: drops retakes, "let's redo that", pauses > 0.5 s
  cut         keep.json -> one clean video (frame-accurate, high quality)
  split       cut one recording into several videos by time ranges
  level       level different voices (AI voice vs real voice) separately
  check       brightness / grain report with an honest reshoot verdict
  fix         brighten + denoise a dark, grainy video
  captions    words.json -> branded .ass subtitle file
  export      final render: 9:16 or 16:9, optional burned captions, highest quality
  outname     print a safe "next to the original" output path (never overwrites)
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

CLOUD_MARKERS = ("mobile documents", "icloud", "clouddocs", "cloudstorage", "dropbox",
                 "onedrive", "google drive", "googledrive", "box sync")
HQ_VIDEO = ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
            "-profile:v", "high"]
HQ_AUDIO = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"]
FILLER_REDO = [
    r"let'?s redo (that|this|it)", r"let me redo( that| this| it)?", r"redo that",
    r"let me (start|try) (that )?(over|again)", r"let'?s (start|try) (that )?(over|again)",
    r"start(ing)? over", r"take two", r"one more time", r"scratch that", r"from the top",
]


def run(cmd, capture=False):
    print("+", " ".join(str(c) for c in cmd), file=sys.stderr)
    r = subprocess.run([str(c) for c in cmd], capture_output=capture, text=True)
    if r.returncode != 0:
        if capture:
            sys.stderr.write(r.stderr[-3000:])
        sys.exit(f"command failed ({r.returncode})")
    return r


def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
             "-show_streams", path], capture=True)
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(d["format"].get("duration", 0))}
    if v:
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            rot = int(sd.get("rotation", 0) or 0)
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) in (90, 270):
            w, h = h, w
        num, den = (v.get("avg_frame_rate") or "30/1").split("/")
        info.update(width=w, height=h, fps=round(float(num) / float(den or 1), 3),
                    vcodec=v["codec_name"])
    if a:
        info.update(acodec=a["codec_name"], channels=a.get("channels"),
                    sample_rate=a.get("sample_rate"))
    return info


def is_cloud(path):
    p = str(Path(path).resolve()).lower()
    return any(m in p for m in CLOUD_MARKERS)


def safe_out(original, suffix, ext=".mp4"):
    o = Path(original).resolve()
    base = o.with_name(f"{o.stem}_{suffix}{ext}")
    cand, n = base, 2
    while cand.exists():
        cand = o.with_name(f"{o.stem}_{suffix}_v{n}{ext}")
        n += 1
    return cand


# ---------------------------------------------------------------- workdir
def cmd_workdir(a):
    src = Path(a.video).resolve()
    if not src.exists():
        sys.exit(f"not found: {src}")
    root = Path(a.root or os.path.expanduser("~/VideoWork"))
    if is_cloud(root):
        sys.exit(f"work root {root} is inside a cloud-synced folder; pick a local one")
    wd = root / f"{re.sub(r'[^A-Za-z0-9_-]+', '-', src.stem)}-{time.strftime('%Y%m%d-%H%M%S')}"
    wd.mkdir(parents=True, exist_ok=True)
    dst = wd / ("source" + src.suffix.lower())
    shutil.copy2(src, dst)  # copy, never move: the original is never touched
    (wd / "ORIGINAL_PATH.txt").write_text(str(src) + "\n")
    print(json.dumps({"workdir": str(wd), "copy": str(dst), "original": str(src),
                      "original_in_cloud_folder": is_cloud(src)}, indent=2))


def cmd_probe(a):
    print(json.dumps(probe(a.video), indent=2))


def cmd_outname(a):
    print(safe_out(a.original, a.suffix))


# ---------------------------------------------------------------- transcribe
def cmd_transcribe(a):
    out = Path(a.out or Path(a.video).with_suffix(".words.json"))
    wav = out.with_suffix(".16k.wav")
    run(["ffmpeg", "-y", "-v", "error", "-i", a.video, "-vn", "-ac", "1", "-ar", "16000", wav])
    try:
        import whisper
    except ImportError:
        sys.exit("Whisper missing: pip3 install openai-whisper")
    try:
        model = whisper.load_model(a.model)
    except Exception as e:  # usually: model download blocked
        sys.exit(f"could not load Whisper model '{a.model}': {e}\n"
                 "The model downloads once from openaipublic.azureedge.net; allow that host "
                 "or copy the .pt file into ~/.cache/whisper/.")
    res = model.transcribe(str(wav), word_timestamps=True, language=a.language,
                           condition_on_previous_text=False, verbose=False)
    words, segs = [], []
    for si, s in enumerate(res["segments"]):
        segs.append({"id": si, "start": s["start"], "end": s["end"], "text": s["text"].strip()})
        for w in s.get("words", []):
            t = w["word"].strip()
            if t:
                words.append({"id": f"w{len(words)}", "text": t, "start": round(w["start"], 3),
                              "end": round(w["end"], 3), "seg": si})
    out.write_text(json.dumps({"source": str(a.video), "language": res.get("language"),
                               "segments": segs, "words": words}, indent=1))
    wav.unlink(missing_ok=True)
    print(f"{len(words)} words -> {out}")


# ---------------------------------------------------------------- plan
def norm(t):
    return re.sub(r"[^a-z0-9' ]", "", t.lower()).strip()


def sentences(words):
    """Group words into sentences: break on . ? ! or a gap > 0.7 s."""
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and w["start"] - cur[-1]["end"] > 0.7:
            out.append(cur); cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["text"]):
            out.append(cur); cur = []
    if cur:
        out.append(cur)
    return out


def cmd_plan(a):
    d = json.loads(Path(a.words).read_text())
    words = d["words"]
    dur = probe(a.video)["duration"] if a.video else (words[-1]["end"] + 0.5 if words else 0)
    sents = sentences(words)
    drop = set()  # word ids to drop
    notes = []
    # 1) explicit redo phrases: drop the phrase itself and the sentence(s) it refers to
    for si, s in enumerate(sents):
        txt = " ".join(norm(w["text"]) for w in s)
        for pat in FILLER_REDO:
            m = re.search(pat, txt)
            if m:
                drop.update(w["id"] for w in s)
                if si > 0:
                    drop.update(w["id"] for w in sents[si - 1])
                notes.append(f"redo cue '{m.group(0)}' at {s[0]['start']:.2f}s: dropped it "
                             f"and the take before it")
                break
    # 2) restarts: a sentence whose first N words match the next sentence's start
    #    (or that trails off) is an abandoned take; keep the later (usually best) one
    n = a.match_words
    for si in range(len(sents) - 1):
        s, nxt = sents[si], sents[si + 1]
        k = [norm(w["text"]) for w in s[:n]]
        k2 = [norm(w["text"]) for w in nxt[:n]]
        if len(s) >= 2 and len(k) == n and k == k2:
            drop.update(w["id"] for w in s)
            notes.append(f"restart at {s[0]['start']:.2f}s ('{' '.join(k)}...'): kept the "
                         f"later take at {nxt[0]['start']:.2f}s")
    # 2b) mid-sentence restart: same 2+ words repeated back to back ("I want I want to")
    for i in range(len(words) - 3):
        a1 = [norm(words[i]["text"]), norm(words[i + 1]["text"])]
        a2 = [norm(words[i + 2]["text"]), norm(words[i + 3]["text"])]
        if a1 == a2 and all(a1) and words[i]["id"] not in drop:
            drop.update([words[i]["id"], words[i + 1]["id"]])
            notes.append(f"stutter restart '{' '.join(a1)}' at {words[i]['start']:.2f}s")
    kept = [w for w in words if w["id"] not in drop]
    # 3) build keep ranges, cutting every pause longer than max_pause
    pad, segs = a.pad, []
    for w in kept:
        st, en = max(0.0, w["start"] - pad), min(dur, w["end"] + pad)
        if segs and w["start"] - segs[-1]["last_end"] <= a.max_pause and not segs[-1]["broken"]:
            segs[-1]["end"] = en; segs[-1]["last_end"] = w["end"]
        else:
            segs.append({"start": st, "end": en, "last_end": w["end"], "broken": False})
        # a dropped word between this and the next kept word forces a cut
        idx = words.index(w)
        if idx + 1 < len(words) and words[idx + 1]["id"] in drop:
            segs[-1]["broken"] = True
    # merge overlaps created by padding
    merged = []
    for s in segs:
        if merged and s["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append({"start": round(s["start"], 3), "end": round(s["end"], 3)})
    # candidate split points for multi-video recordings: long silences
    splits = [round((words[i]["end"] + words[i + 1]["start"]) / 2, 2)
              for i in range(len(words) - 1)
              if words[i + 1]["start"] - words[i]["end"] >= a.split_gap]
    removed = dur - sum(s["end"] - s["start"] for s in merged)
    plan = {"source": a.video, "keep": merged, "dropped_words": sorted(drop, key=lambda x: int(x[1:])),
            "notes": notes, "possible_video_splits_at": splits,
            "original_seconds": round(dur, 2), "removed_seconds": round(removed, 2)}
    Path(a.out).write_text(json.dumps(plan, indent=1))
    print(f"keep {len(merged)} ranges, remove {removed:.1f}s of {dur:.1f}s -> {a.out}")
    for nline in notes:
        print(" -", nline)
    if splits:
        print(" - long silences (possible separate videos) at:", splits)


# ---------------------------------------------------------------- cut / split
def concat_ranges(src, ranges, out, has_audio=True):
    parts, labels = [], ""
    for i, r in enumerate(ranges):
        s, e = r["start"], r["end"]
        f = 0.012 if e - s > 0.05 else 0
        parts.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d={f},afade=t=out:st={max(0, e - s - f):.3f}:d={f}[a{i}]")
            labels += f"[v{i}][a{i}]"
        else:
            labels += f"[v{i}]"
    n = len(ranges)
    parts.append(f"{labels}concat=n={n}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else ""))
    fc = Path(out).with_suffix(".filter.txt")
    fc.write_text(";\n".join(parts))
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", src, "-filter_complex_script", fc,
           "-map", "[v]"] + (["-map", "[a]"] if has_audio else []) + HQ_VIDEO + \
          (HQ_AUDIO if has_audio else []) + ["-movflags", "+faststart", out]
    run(cmd)
    fc.unlink(missing_ok=True)


def cmd_cut(a):
    plan = json.loads(Path(a.plan).read_text())
    concat_ranges(a.video, plan["keep"], a.out, "acodec" in probe(a.video))
    print(f"cut -> {a.out}")


def cmd_split(a):
    """--ranges '0-63.5,64.2-130' -> one file per range."""
    has_audio = "acodec" in probe(a.video)
    outs = []
    for i, r in enumerate(a.ranges.split(","), 1):
        s, e = (float(x) for x in r.split("-"))
        out = Path(a.outdir) / f"{a.prefix}_part{i:02d}.mp4"
        concat_ranges(a.video, [{"start": s, "end": e}], out, has_audio)
        outs.append(str(out))
    print(json.dumps(outs, indent=1))


# ---------------------------------------------------------------- level voices
def measure_lufs(src, s=None, e=None):
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if s is not None:
        cmd += ["-ss", str(s), "-to", str(e)]
    cmd += ["-i", src, "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    return float(json.loads(m.group(0))["input_i"]) if m else None


def cmd_level(a):
    """--ranges 'ai:0-4.2,me:4.2-20,ai:20-25' : each voice brought to the same loudness."""
    target = a.target
    filters, report = [], []
    for item in a.ranges.split(","):
        label, r = item.split(":")
        s, e = (float(x) for x in r.split("-"))
        lufs = measure_lufs(a.video, s, e)
        if lufs is None or lufs < -70:
            continue
        g = max(-20.0, min(20.0, target - lufs))
        filters.append(f"volume={g:.2f}dB:enable='between(t,{s},{e})'")
        report.append({"voice": label, "from": s, "to": e, "measured_lufs": round(lufs, 1),
                       "gain_db": round(g, 1)})
    filters.append("alimiter=limit=0.89:level=disabled")
    run(["ffmpeg", "-y", "-v", "error", "-i", a.video, "-af", ",".join(filters),
         "-c:v", "copy"] + HQ_AUDIO + [a.out])
    print(json.dumps({"target_lufs": target, "sections": report,
                      "result_lufs": round(measure_lufs(a.out) or 0, 1)}, indent=1))


# ---------------------------------------------------------------- dark / grain
def cmd_check(a):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", a.video, "-vf",
                        "fps=2,scale=640:-2,signalstats,metadata=print:file=-", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    yavg = [float(x) for x in re.findall(r"signalstats\.YAVG=([\d.]+)", r.stdout)]
    ylow = [float(x) for x in re.findall(r"signalstats\.YLOW=([\d.]+)", r.stdout)]
    yhigh = [float(x) for x in re.findall(r"signalstats\.YHIGH=([\d.]+)", r.stdout)]
    # grain: difference between frame and a denoised copy
    r2 = subprocess.run(["ffmpeg", "-hide_banner", "-i", a.video, "-filter_complex",
                         "[0:v]fps=1,scale=640:-2,split[a][b];[b]hqdn3d=6:6:0:0[d];[a][d]psnr=stats_file=-",
                         "-an", "-f", "null", "-"], capture_output=True, text=True)
    ps = [float(x) for x in re.findall(r"psnr_avg:([\d.]+)", r2.stdout + r2.stderr) if x != "inf"]
    if not yavg:
        sys.exit("could not read frames")
    bright = sum(yavg) / len(yavg)
    contrast = sum(h - l for h, l in zip(yhigh, ylow)) / max(1, len(ylow))
    noise_psnr = sum(ps) / len(ps) if ps else 99
    verdict = "fine"
    if bright < 40 or (bright < 60 and noise_psnr < 30):
        verdict = "reshoot recommended: very dark and/or very grainy; fixing will look muddy"
    elif bright < 75 or noise_psnr < 34:
        verdict = "fixable: run `fix`, it will look better but not like well-lit footage"
    print(json.dumps({"avg_brightness_0_255": round(bright, 1), "contrast_range": round(contrast, 1),
                      "grain_psnr_db(higher=cleaner)": round(noise_psnr, 1), "verdict": verdict,
                      "guide": "brightness <75 = dark, <40 = very dark; grain psnr <34 = grainy, <30 = very grainy"},
                     indent=1))


def cmd_fix(a):
    vf = (f"hqdn3d={a.denoise}:{a.denoise}:{a.denoise*1.5}:{a.denoise*1.5},"
          f"eq=gamma={a.gamma}:brightness={a.brightness}:contrast={a.contrast}:saturation=1.08")
    run(["ffmpeg", "-y", "-v", "error", "-stats", "-i", a.video, "-vf", vf] + HQ_VIDEO +
        ["-c:a", "copy", a.out])
    print(f"fixed -> {a.out}")


# ---------------------------------------------------------------- captions
def ass_color(hexc, alpha="00"):
    h = hexc.lstrip("#")
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def ts(t):
    t = max(0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def cmd_captions(a):
    d = json.loads(Path(a.words).read_text())
    words = [w for w in d["words"] if w["text"].strip() and not re.fullmatch(r"[♪�\W]+", w["text"])]
    if a.plan:  # retime onto the cut timeline
        keep = json.loads(Path(a.plan).read_text())["keep"]
        dropped = set(json.loads(Path(a.plan).read_text())["dropped_words"])
        retimed, offset_list, acc = [], [], 0.0
        for r in keep:
            offset_list.append((r["start"], r["end"], acc)); acc += r["end"] - r["start"]
        for w in words:
            if w["id"] in dropped:
                continue
            for s, e, off in offset_list:
                if s <= w["start"] < e:
                    retimed.append({**w, "start": w["start"] - s + off, "end": min(w["end"], e) - s + off})
                    break
        words = retimed
    W, H = a.width, a.height
    vertical = H > W
    size = int((H if not vertical else W) * (0.088 if vertical else 0.062))
    margin_v = int(H * (0.28 if vertical else 0.10))
    text_c, hi_c = ass_color(a.text_color), ass_color(a.highlight_color)
    out_c = ass_color(a.outline_color)
    font = a.font
    groups, cur = [], []
    for w in words:  # 1-3 words per caption, break on pauses and punctuation
        if cur and (len(cur) >= a.max_words or w["start"] - cur[-1]["end"] > 0.35
                    or re.search(r"[.?!,]$", cur[-1]["text"])):
            groups.append(cur); cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    lines = []
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) and groups[gi + 1][0]["start"] - g[-1]["end"] < 0.4 else g[-1]["end"] + 0.15
        for wi, w in enumerate(g):
            st = w["start"] if wi else g[0]["start"]
            en = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
            txt = []
            for wj, x in enumerate(g):
                t = re.sub(r"[{}\\]", "", x["text"]).upper() if a.upper else re.sub(r"[{}\\]", "", x["text"])
                txt.append(f"{{\\c{hi_c}\\fscx108\\fscy108}}{t}{{\\c{text_c}\\fscx100\\fscy100}}" if wj == wi else t)
            pop = "{\\fad(40,0)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{ts(st)},{ts(en)},Brand,,0,0,0,,{pop}{' '.join(txt)}")
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Brand,{font},{size},{text_c},{text_c},{out_c},&H64000000,-1,0,0,0,100,100,1,0,1,{max(4, size // 9)},{max(2, size // 22)},2,{int(W*0.1)},{int(W*0.1)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    Path(a.out).write_text(hdr + "\n".join(lines) + "\n")
    print(f"{len(groups)} caption groups -> {a.out}")


# ---------------------------------------------------------------- export
def cmd_export(a):
    info = probe(a.video)
    w, h = info["width"], info["height"]
    if a.aspect == "9:16":
        tw, th = (2160, 3840) if min(w, h) >= 2160 else (1080, 1920)
    elif a.aspect == "16:9":
        tw, th = (3840, 2160) if min(w, h) >= 2160 else (1920, 1080)
    else:
        tw, th = w - w % 2, h - h % 2
    if a.fit == "crop":
        vf = f"scale={tw}:{th}:force_original_aspect_ratio=increase:flags=lanczos,crop={tw}:{th}"
        chain = f"[0:v]{vf},setsar=1[base]"
    else:  # blurred background, whole frame visible
        chain = (f"[0:v]split[a][b];[a]scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},"
                 f"boxblur=40:2,eq=brightness=-0.08[bg];[b]scale={tw}:{th}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    last = "base"
    if a.subs:
        fontsdir = f":fontsdir='{a.fontsdir}'" if a.fontsdir else ""
        subs = str(Path(a.subs).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        chain += f";[base]subtitles=filename='{subs}'{fontsdir}[subbed]"
        last = "subbed"
    if a.logo:
        lw = int(tw * 0.14)
        chain += (f";[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa=0.9[lg];"
                  f"[{last}][lg]overlay=W-w-{int(tw*0.05)}:{int(th*0.05)}[lgo]")
        last = "lgo"
    cmd = ["ffmpeg", "-y", "-v", "error", "-stats", "-i", a.video]
    if a.logo:
        cmd += ["-i", a.logo]
    cmd += ["-filter_complex", chain, "-map", f"[{last}]", "-map", "0:a?"] + HQ_VIDEO + HQ_AUDIO
    if info.get("fps"):
        cmd += ["-r", str(min(60, info["fps"]))]
    cmd += ["-movflags", "+faststart", a.out]
    run(cmd)
    print(json.dumps({"out": a.out, **probe(a.out)}, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("workdir"); s.add_argument("video"); s.add_argument("--root"); s.set_defaults(f=cmd_workdir)
    s = sp.add_parser("probe"); s.add_argument("video"); s.set_defaults(f=cmd_probe)
    s = sp.add_parser("outname"); s.add_argument("original"); s.add_argument("suffix"); s.set_defaults(f=cmd_outname)
    s = sp.add_parser("transcribe"); s.add_argument("video"); s.add_argument("--out")
    s.add_argument("--model", default="small.en", help="small.en (English), small/medium for other languages")
    s.add_argument("--language", default=None); s.set_defaults(f=cmd_transcribe)
    s = sp.add_parser("plan"); s.add_argument("words"); s.add_argument("--video"); s.add_argument("--out", default="keep.json")
    s.add_argument("--max-pause", type=float, default=0.5); s.add_argument("--pad", type=float, default=0.12)
    s.add_argument("--match-words", type=int, default=3); s.add_argument("--split-gap", type=float, default=3.0)
    s.set_defaults(f=cmd_plan)
    s = sp.add_parser("cut"); s.add_argument("video"); s.add_argument("plan"); s.add_argument("out"); s.set_defaults(f=cmd_cut)
    s = sp.add_parser("split"); s.add_argument("video"); s.add_argument("--ranges", required=True)
    s.add_argument("--outdir", default="."); s.add_argument("--prefix", default="video"); s.set_defaults(f=cmd_split)
    s = sp.add_parser("level"); s.add_argument("video"); s.add_argument("out"); s.add_argument("--ranges", required=True)
    s.add_argument("--target", type=float, default=-14.0); s.set_defaults(f=cmd_level)
    s = sp.add_parser("check"); s.add_argument("video"); s.set_defaults(f=cmd_check)
    s = sp.add_parser("fix"); s.add_argument("video"); s.add_argument("out")
    s.add_argument("--gamma", type=float, default=1.25); s.add_argument("--brightness", type=float, default=0.03)
    s.add_argument("--contrast", type=float, default=1.05); s.add_argument("--denoise", type=float, default=4.0)
    s.set_defaults(f=cmd_fix)
    s = sp.add_parser("captions"); s.add_argument("words"); s.add_argument("--out", default="captions.ass")
    s.add_argument("--plan", help="keep.json, to retime words onto the cut video")
    s.add_argument("--width", type=int, default=1080); s.add_argument("--height", type=int, default=1920)
    s.add_argument("--font", default="Inter"); s.add_argument("--text-color", default="#FFFFFF")
    s.add_argument("--highlight-color", default="#FFD400"); s.add_argument("--outline-color", default="#000000")
    s.add_argument("--max-words", type=int, default=3); s.add_argument("--upper", action="store_true")
    s.set_defaults(f=cmd_captions)
    s = sp.add_parser("export"); s.add_argument("video"); s.add_argument("out")
    s.add_argument("--aspect", choices=["9:16", "16:9", "source"], default="source")
    s.add_argument("--fit", choices=["crop", "blur"], default="crop")
    s.add_argument("--subs"); s.add_argument("--fontsdir"); s.add_argument("--logo"); s.set_defaults(f=cmd_export)
    a = p.parse_args(); a.f(a)


if __name__ == "__main__":
    main()
