# The project website

A static React site (Vite + TypeScript) served by GitHub Pages at <https://arijitgupta42.github.io/robotrader/>. It shows the Paper Portfolio and replays each week's pipeline run as an animated 3D machine (with a plain list view of the same data). It has no server: at build time the GitHub Pages workflow downloads the JSON the stock-selection Lambda publishes under `site/` into `public/data/`, and the page reads it from its own origin. The data's shape is the contract in [`docs/site-data.md`](../docs/site-data.md).

```bash
cd web
npm ci
npm run dev        # serves the fixture weeks (fixtures/ copied to public/data/) at http://localhost:5173/robotrader/
npm test           # loaders and the stage model, against the fixtures
npm run build      # type-check and build to dist/
```

- `src/data/stages.ts` is the **stage model**: one entry per pipeline stage, in the order the code runs, with its real component, the files and functions behind it, a plain-English description, and how to read that stage's numbers from a week's Run Trace. Every view (the plain list and the 3D machine) reads from it, so a pipeline change is made in one place.
- `src/machine/` is the **3D machine** (three.js through react-three-fiber), loaded lazily so the rest of the page does not wait for it. The pipeline is laid out as a U-shaped factory line: the scout runs left to right along the back row, a pneumatic tube turns the corner, and stock selection runs right to left along the front row to the mail chute. `machineState.ts` turns a week's data into what each part shows (pure and tested), `parts.tsx` draws the twelve stations from simple primitives (no model files, no CDN assets), and `Machine.tsx` handles playback, the camera, hover tips, the details panel and the stage strip (the keyboard route to every part). Parts with no recorded data stay still; the machine never animates numbers it does not have. It drops to lower detail when the frame rate falls, holds still for `prefers-reduced-motion`, follows the playback on narrow screens, and the page falls back to the plain list without WebGL. drei is imported by deep path (`@react-three/drei/core/...`) because its barrel starts blob web workers that the CSP blocks.
- `fixtures/` holds four made-up weeks, generated with the real portfolio code by `python web/scripts/make_fixtures.py`: an opening week, a week with problems (a failed feed, Reddit throttling, the fallback model), a week where the scout never succeeded, and a week whose scout result predates the trace block. Regenerate them when the data's shape changes.
- To look at real data locally: `aws s3 sync s3://sector-scout-results-544611252144/site/ public/data/ --region eu-west-1`, then `npm run dev` (it keeps existing data; `npm run fixtures` puts the fixtures back).
