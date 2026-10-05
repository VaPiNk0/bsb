// Poskladá statický web pre Cloudflare Pages z ../frontend (rovnaké cesty, aké servíruje Flask).
import { cpSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "frontend");
const out = join(here, "dist");

rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, "static"), { recursive: true });
for (const file of readdirSync(src)) {
  cpSync(join(src, file), join(out, file.endsWith(".html") ? file : join("static", file)));
}

const csp = [
  "default-src 'self'", "script-src 'self' https://challenges.cloudflare.com", "style-src 'self'",
  "connect-src 'self'", "frame-src https://challenges.cloudflare.com", "img-src 'self' data:",
  "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
].join("; ");
writeFileSync(join(out, "_headers"), [
  "/*",
  "  X-Content-Type-Options: nosniff",
  "  X-Frame-Options: DENY",
  "  Referrer-Policy: no-referrer",
  `  Content-Security-Policy: ${csp}`,
  "",
].join("\n"));
console.log(`Hotovo: ${out}`);
