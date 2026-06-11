import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

const sourceRoot = resolve(import.meta.dirname, "..");

test("one-click CVE is exposed as a visible navigation route", () => {
  const routesSource = readFileSync(resolve(sourceRoot, "app/routes.tsx"), "utf8");
  const sidebarSource = readFileSync(resolve(sourceRoot, "components/layout/Sidebar.tsx"), "utf8");

  assert.match(routesSource, /OneClickCVE/);
  assert.match(routesSource, /name: '一键CVE'/);
  assert.match(routesSource, /path: '\/one-click-cve'/);
  assert.match(sidebarSource, /'\/one-click-cve'/);
});
