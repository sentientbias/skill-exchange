---
name: "headless-blender"
description: "Render with Blender on a headless VM: background mode, Cycles CPU, robust frame output, and common gotchas."
---

# Headless Blender

Render on machines with no display. Assumes Blender 4.x+ installed.

## The invocation

```bash
blender -b scene.blend -o //frames/frame-#### -f 1        # single frame
blender -b scene.blend -o //frames/frame-#### -s 1 -e 120 -a  # animation
```

`-b` is background mode (no UI). Always render image sequences (PNG), then
encode video from frames with ffmpeg -- if a render dies at frame 87, you
keep 86 frames instead of nothing.

```bash
ffmpeg -y -framerate 24 -i frames/frame-%04d.png \
  -c:v libx264 -pix_fmt yuv420p out.mp4
```

## Headless rendering engine

- EEVEE needs a GPU/EGL context and usually fails headless. Use **Cycles**
  with CPU:
  ```python
  scene.render.engine = 'CYCLES'
  scene.cycles.device = 'CPU'
  ```
- If the `blender` binary fails to start (missing libEGL etc.), a wrapper
  that forces software GL (Mesa) fixes startup; rendering still goes through
  Cycles CPU.

## Python script gotchas

- **Colors are linear, not sRGB.** Values set via `default_value` on shader
  sockets are interpreted as linear -- sRGB values you paste in will render
  washed out. Convert intended sRGB colors to linear first.
- **Principled BSDF lobes add up** (4.x+). "Transmission Weight" no longer
  replaces diffuse; for pure glass set diffuse "Weight" to 0 or you get a
  milky veil.
- **Mix Shader Fac order**: Fac 0 = first shader input, Fac 1 = second.
  Double-check when wiring Light Path splits.

## Robustness

- Run long renders detached (nohup / backgrounded process) and log to a
  file; check the log for the completion marker, don't assume.
- Set explicit output paths and overwrite behavior; never rely on the .blend
  file's saved paths surviving a move.
- Verify the final MP4 with the video-qc checklist before claiming success.
