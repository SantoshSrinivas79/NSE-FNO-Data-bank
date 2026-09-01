"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {
  dateRange,
  fileNameForDate,
  formatForDate,
  outputPathForDate,
  urlsForDate,
  validateArchive,
  validateCsv,
} = require("../index");

function storedZip(filename, contents) {
  const name = Buffer.from(filename);
  const data = Buffer.from(contents);
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50, 0);
  header.writeUInt16LE(20, 4);
  header.writeUInt16LE(0, 6);
  header.writeUInt16LE(0, 8);
  header.writeUInt32LE(data.length, 18);
  header.writeUInt32LE(data.length, 22);
  header.writeUInt16LE(name.length, 26);
  return Buffer.concat([header, name, data]);
}

test("uses legacy derivatives archive through 5 July 2024", () => {
  assert.equal(formatForDate("2024-07-05"), "legacy");
  assert.equal(fileNameForDate("2024-07-05"), "fo05JUL2024bhav.csv.zip");
  assert.match(urlsForDate("2024-07-05")[0], /historical\/DERIVATIVES\/2024\/JUL/);
});

test("uses UDiFF format from 8 July 2024", () => {
  assert.equal(formatForDate("2024-07-08"), "udiff");
  assert.equal(fileNameForDate("2024-07-08"), "BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip");
  assert.match(urlsForDate("2024-07-08")[0], /content\/fo/);
});

test("organizes archives by year and month", () => {
  assert.equal(
    outputPathForDate("/repo", "2026-08-31"),
    path.join("/repo", "data", "2026", "08", "BhavCopy_NSE_FO_0_0_0_20260831_F_0000.csv.zip"),
  );
});

test("validates a UDiFF FO row and requested trade date", () => {
  const csv = [
    "TradDt,BizDt,Sgmt,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric,ClsPric,TtlTradgVol,OpnIntrst",
    "2026-08-31,2026-08-31,FO,NIFTY,2026-09-24,25000,CE,100,110,42,900",
  ].join("\n");
  assert.deepEqual(validateCsv(csv, "2026-08-31", "udiff"), { rows: 1, columns: 11 });
  assert.throws(() => validateCsv(csv, "2026-08-28", "udiff"), /trade date/);
  const archive = storedZip("BhavCopy_NSE_FO_0_0_0_20260831_F_0000.csv", csv);
  assert.deepEqual(validateArchive(archive, "2026-08-31"), {
    rows: 1,
    columns: 11,
    format: "udiff",
    entry: "BhavCopy_NSE_FO_0_0_0_20260831_F_0000.csv",
    zipBytes: archive.length,
  });
});

test("validates a legacy FO row and requested timestamp", () => {
  const csv = [
    "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP",
    "FUTIDX,NIFTY,25-JUL-2024,0,XX,24000,24100,23900,24050,24050,10,100,200,5,05-JUL-2024",
  ].join("\n");
  assert.deepEqual(validateCsv(csv, "2024-07-05", "legacy"), { rows: 1, columns: 15 });
});

test("builds inclusive date ranges", () => {
  assert.deepEqual(dateRange("2024-07-05", "2024-07-08"), [
    "2024-07-05", "2024-07-06", "2024-07-07", "2024-07-08",
  ]);
});
