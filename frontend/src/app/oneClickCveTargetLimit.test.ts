import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { resolve } from "node:path";

test("one-click CVE target count is limited to ten in the UI", () => {
  const sourceRoot = resolve(process.cwd(), "src");
  const source = readFileSync(resolve(sourceRoot, "pages/OneClickCVE.tsx"), "utf8");

  assert.match(source, /const TARGET_COUNT_MAX = 10;/);
  assert.match(source, /1-10/);
});
