# Recording the demo GIF

Goal: a 20–30 second GIF at the top of the README that shows one question being answered and
the "How I got this" panel. Keep it under 10 MB, GitHub's limit for images in a README.

## 1. Set up

```bash
uv run mc app        # http://localhost:8501
```

- Use a browser window about 1280 × 800, with dark mode and browser zoom at 90–100%.
- Hide bookmarks and extensions so only the app is visible.
- Click **Clear conversation** so the chat starts empty.

## 2. Record (macOS)

Use [Kap](https://getkap.co) (free, `brew install --cask kap`), which exports GIFs directly.
QuickTime (**File → New Screen Recording**) also works, with the conversion in step 3.

Script, about 30 seconds:

1. Start on the empty app (1 s).
2. Click the sidebar example **"Engine 42's sensors are drifting…"**.
3. Let the live progress lines appear: sensor data, work orders, manuals.
4. When the answer appears, scroll slowly through it (3–4 s).
5. Click **How I got this**. Show the CRITICAL status, the sensor drift chart, and the
   "all citations grounded ✅" footer (5–6 s).

The agent takes 15–20 seconds to answer. Trim or speed up that wait when editing: in Kap,
use the trim handles; in QuickTime, use **Edit → Trim**.

## 3. Export

- **Kap:** export as GIF at 12–15 fps, width 1200 px.
- **QuickTime .mov:** convert with ffmpeg (`brew install ffmpeg`):

```bash
ffmpeg -i demo.mov -vf "fps=12,scale=1200:-1:flags=lanczos,palettegen" palette.png
ffmpeg -i demo.mov -i palette.png \
  -lavfi "fps=12,scale=1200:-1:flags=lanczos[x];[x][1:v]paletteuse" docs/demo.gif
```

If the file is over 10 MB, lower the width to 1000 px or the fps to 10.

## 4. Add it to the README

Save the GIF as `docs/demo.gif`. In `README.md`, replace the HTML comment under the links with:

```markdown
![Maintenance Copilot demo](docs/demo.gif)
```

Then commit and push:

```bash
git add docs/demo.gif README.md
git commit -m "Add demo GIF to README"
git push
```
