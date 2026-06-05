import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const sourceRoot = resolve(import.meta.dirname, "..");

test("checkmarx page restores persisted scan jobs on load", () => {
  const pageSource = readFileSync(resolve(sourceRoot, "pages/CheckmarxScan.tsx"), "utf8");
  const serviceSource = readFileSync(resolve(sourceRoot, "features/checkmarx/services/checkmarxScan.ts"), "utf8");

  assert.match(serviceSource, /listCheckmarxScans/);
  assert.match(pageSource, /loadScanJobs/);
  assert.match(pageSource, /listCheckmarxScans\(/);
  assert.match(pageSource, /扫描任务记录/);
  assert.match(pageSource, /selectJob/);
});
