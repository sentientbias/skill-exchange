---
name: "video-qc"
description: "Verify video/audio deliverables before claiming they're done: container, codec, duration, resolution, audio presence, and spot checks."
---

# Video QC

Never claim a video is done without checking the actual file. "The render
finished" is not verification.

## The checklist (use ffprobe)

```bash
ffprobe -v error -show_entries \
  format=duration,size:stream=codec_name,width,height,avg_frame_rate \
  -of default=noprint_wrappers=1 file.mp4
```

Verify each of these against the spec:

1. **Duration** -- nonzero and matches what was asked for (±0.1s tolerance).
2. **Resolution** -- width x height matches the spec.
3. **Video codec** -- usually H.264 (`h264`) for compatibility; note if HDR.
4. **Audio** -- if the deliverable should have audio, confirm an audio stream
   exists AND is non-silent. Check with a loudness scan, not just presence:
   `ffmpeg -i file.mp4 -af astats -f null -` and look at RMS levels.
5. **File size** -- nonzero and plausible (a 10s 1080p H.264 is typically
   several MB, not 40 KB).

## Frame spot checks

Extract 3 frames (start, middle, end) and look at them:

```bash
ffmpeg -y -ss 0 -i file.mp4 -frames:v 1 start.png
ffmpeg -y -ss <mid> -i file.mp4 -frames:v 1 mid.png
```

Confirm: not black, not frozen (frames differ), no render artifacts, text
(if any) is legible.

## Audio-specific checks

- After any retime/splice, scan for runs of digital zero (absolute silence
  mid-word reads as a stutter). Natural room tone sits around -70 dB, not
  -infinity.
- When piping float audio into ffmpeg, cast to float32 first -- piping
  float64 as f32le doubles the duration into garbage.

## Report honestly

State what you checked and the measured values (duration, resolution,
codec). If you didn't check something, say so. "Looks done" is not a QC
result.
