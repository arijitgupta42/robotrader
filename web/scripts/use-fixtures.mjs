// Local development: serve the fixture weeks (web/fixtures/) as the site's data (web/public/data/).
// The GitHub Pages workflow fills public/data/ from S3 instead, so this never runs there.
// Without --force, existing data (for example a real copy pulled from S3) is left alone.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const target = join(web, "public", "data");
if (existsSync(target) && !process.argv.includes("--force")) {
  console.log("public/data already exists; leaving it (npm run fixtures to replace it with the fixtures)");
} else {
  rmSync(target, { recursive: true, force: true });
  cpSync(join(web, "fixtures"), target, { recursive: true });
  console.log("public/data <- fixtures");
}
