import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sourceDir = __dirname;
const targetDir = path.resolve(__dirname, "dist");

const files = [
  { name: "index.html", banner: "<!-- Generated from apps/web source files. -->\n" },
  { name: "styles.css", banner: "/* Generated from apps/web source files. */\n" },
  { name: "app.js", banner: "// Generated from apps/web source files.\n" },
];

await mkdir(targetDir, { recursive: true });

for (const file of files) {
  const sourcePath = path.join(sourceDir, file.name);
  const targetPath = path.join(targetDir, file.name);
  const content = await readFile(sourcePath, "utf8");
  await writeFile(targetPath, `${file.banner}${content}`, "utf8");
  console.log(`synced ${file.name}`);
}
