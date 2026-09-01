#!/usr/bin/env node

"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const zlib = require("node:zlib");

const UDIFF_START = "2024-07-08";
const MONTHS = [
  "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
];
const ARCHIVE_HOSTS = [
  "https://archives.nseindia.com",
  "https://nsearchives.nseindia.com",
];

function isoDateInIndia(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function parseIsoDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) {
    throw new Error(`Invalid date "${value}". Use YYYY-MM-DD.`);
  }
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.valueOf()) || date.toISOString().slice(0, 10) !== value) {
    throw new Error(`Invalid calendar date "${value}".`);
  }
  return date;
}

function compactDate(iso) {
  return iso.replaceAll("-", "");
}

function legacyDate(iso) {
  const [year, month, day] = iso.split("-");
  return `${day}${MONTHS[Number(month) - 1]}${year}`;
}

function legacyTimestamp(iso) {
  const [year, month, day] = iso.split("-");
  return `${day}-${MONTHS[Number(month) - 1]}-${year}`;
}

function formatForDate(iso) {
  return iso >= UDIFF_START ? "udiff" : "legacy";
}

function fileNameForDate(iso) {
  if (formatForDate(iso) === "udiff") {
    return `BhavCopy_NSE_FO_0_0_0_${compactDate(iso)}_F_0000.csv.zip`;
  }
  return `fo${legacyDate(iso)}bhav.csv.zip`;
}

function urlsForDate(iso) {
  const filename = fileNameForDate(iso);
  let pathname;
  if (formatForDate(iso) === "udiff") {
    pathname = `/content/fo/${filename}`;
  } else {
    const [year, month] = iso.split("-");
    pathname = `/content/historical/DERIVATIVES/${year}/${MONTHS[Number(month) - 1]}/${filename}`;
  }
  return ARCHIVE_HOSTS.map((host) => `${host}${pathname}`);
}

function outputPathForDate(root, iso) {
  const [year, month] = iso.split("-");
  return path.join(root, "data", year, month, fileNameForDate(iso));
}

function firstZipEntry(buffer) {
  if (buffer.length < 30 || buffer.readUInt32LE(0) !== 0x04034b50) {
    throw new Error("Response is not a ZIP file (missing PK header).");
  }

  const flags = buffer.readUInt16LE(6);
  const method = buffer.readUInt16LE(8);
  let compressedSize = buffer.readUInt32LE(18);
  let uncompressedSize = buffer.readUInt32LE(22);
  const nameLength = buffer.readUInt16LE(26);
  const extraLength = buffer.readUInt16LE(28);
  const filename = buffer.subarray(30, 30 + nameLength).toString("utf8");
  const dataStart = 30 + nameLength + extraLength;

  if ((flags & 0x08) !== 0 || compressedSize === 0) {
    const centralSignature = Buffer.from([0x50, 0x4b, 0x01, 0x02]);
    const centralOffset = buffer.indexOf(centralSignature, dataStart);
    if (centralOffset === -1) {
      throw new Error("ZIP central directory was not found.");
    }
    compressedSize = buffer.readUInt32LE(centralOffset + 20);
    uncompressedSize = buffer.readUInt32LE(centralOffset + 24);
  }

  const compressed = buffer.subarray(dataStart, dataStart + compressedSize);
  let data;
  if (method === 0) data = compressed;
  else if (method === 8) data = zlib.inflateRawSync(compressed);
  else throw new Error(`Unsupported ZIP compression method ${method}.`);

  if (uncompressedSize && data.length !== uncompressedSize) {
    throw new Error(`ZIP entry size mismatch: expected ${uncompressedSize}, got ${data.length}.`);
  }
  return { filename, data };
}

function validateCsv(csv, iso, format) {
  const lines = csv.replace(/^\uFEFF/, "").trim().split(/\r?\n/);
  if (lines.length < 2) throw new Error("Bhavcopy has no data rows.");

  const header = lines[0].split(",").map((value) => value.trim());
  const required = format === "udiff"
    ? ["TradDt", "Sgmt", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "OpnPric", "ClsPric", "TtlTradgVol", "OpnIntrst"]
    : ["INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "OPEN", "CLOSE", "CONTRACTS", "OPEN_INT", "TIMESTAMP"];
  const missing = required.filter((column) => !header.includes(column));
  if (missing.length) throw new Error(`Bhavcopy is missing columns: ${missing.join(", ")}.`);

  const first = lines[1].split(",").map((value) => value.trim());
  if (format === "udiff") {
    if (first[header.indexOf("TradDt")] !== iso) {
      throw new Error(`UDiFF trade date does not match requested date ${iso}.`);
    }
    if (first[header.indexOf("Sgmt")] !== "FO") {
      throw new Error("UDiFF file is not from the FO segment.");
    }
  } else {
    const expected = legacyTimestamp(iso);
    if (first[header.indexOf("TIMESTAMP")].toUpperCase() !== expected) {
      throw new Error(`Legacy timestamp does not match requested date ${expected}.`);
    }
  }

  return { rows: lines.length - 1, columns: header.length };
}

function validateArchive(buffer, iso) {
  const format = formatForDate(iso);
  const entry = firstZipEntry(buffer);
  if (!entry.filename.toLowerCase().endsWith(".csv")) {
    throw new Error(`Expected a CSV in the ZIP, found "${entry.filename}".`);
  }
  const stats = validateCsv(entry.data.toString("utf8"), iso, format);
  return { ...stats, format, entry: entry.filename, zipBytes: buffer.length };
}

async function fetchWithRetries(url, attempts = 4) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: "application/zip,application/octet-stream;q=0.9,*/*;q=0.8",
          Referer: "https://www.nseindia.com/all-reports-derivatives",
          "User-Agent": "Mozilla/5.0 (compatible; NSE-FNO-Data-bank/1.0; +https://github.com/SantoshSrinivas79/NSE-FNO-Data-bank)",
        },
        redirect: "follow",
        signal: AbortSignal.timeout(45_000),
      });
      if (response.status === 404) return { status: 404 };
      if (!response.ok) throw new Error(`HTTP ${response.status} ${response.statusText}`);
      return { status: response.status, buffer: Buffer.from(await response.arrayBuffer()) };
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, attempt * 2_000));
    }
  }
  throw lastError;
}

async function downloadDate(iso, root = __dirname) {
  const date = parseIsoDate(iso);
  const weekday = date.getUTCDay();
  if (weekday === 0 || weekday === 6) {
    console.log(`Skip ${iso}: weekend.`);
    return { state: "skipped" };
  }

  const destination = outputPathForDate(root, iso);
  try {
    const existing = await fs.readFile(destination);
    const stats = validateArchive(existing, iso);
    console.log(`Already valid: ${path.relative(root, destination)} (${stats.rows.toLocaleString()} rows)`);
    return { state: "existing", destination, stats };
  } catch (error) {
    if (error.code !== "ENOENT") console.warn(`Replacing invalid local file: ${error.message}`);
  }

  const errors = [];
  for (const url of urlsForDate(iso)) {
    try {
      console.log(`Fetch ${url}`);
      const result = await fetchWithRetries(url);
      if (result.status === 404) continue;
      const stats = validateArchive(result.buffer, iso);
      await fs.mkdir(path.dirname(destination), { recursive: true });
      const temporary = `${destination}.part`;
      await fs.writeFile(temporary, result.buffer);
      await fs.rename(temporary, destination);
      console.log(`Saved ${path.relative(root, destination)}: ${stats.rows.toLocaleString()} rows, ${stats.columns} columns`);
      return { state: "downloaded", destination, stats, url };
    } catch (error) {
      errors.push(`${url}: ${error.message}`);
    }
  }

  if (errors.length === 0) {
    console.log(`No NSE F&O bhavcopy published for ${iso}.`);
    return { state: "unavailable" };
  }
  throw new Error(`Could not download ${iso}:\n${errors.join("\n")}`);
}

function parseArgs(argv) {
  const args = { delay: 750 };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--date" || value === "--from" || value === "--to") {
      args[value.slice(2)] = argv[++index];
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(value) && !args.date) {
      args.date = value;
    } else if (value === "--help" || value === "-h") {
      args.help = true;
    } else {
      throw new Error(`Unknown argument "${value}".`);
    }
  }
  return args;
}

function dateRange(from, to) {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  if (start > end) throw new Error("--from must not be later than --to.");
  const dates = [];
  for (let current = start; current <= end; current = new Date(current.valueOf() + 86_400_000)) {
    dates.push(current.toISOString().slice(0, 10));
  }
  return dates;
}

async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  if (args.help) {
    console.log("Usage: node index.js [YYYY-MM-DD] | --date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD");
    return;
  }
  if ((args.from && !args.to) || (!args.from && args.to) || (args.date && args.from)) {
    throw new Error("Use either --date, or both --from and --to.");
  }

  const dates = args.from ? dateRange(args.from, args.to) : [args.date || isoDateInIndia()];
  for (let index = 0; index < dates.length; index += 1) {
    await downloadDate(dates[index]);
    if (index < dates.length - 1) await new Promise((resolve) => setTimeout(resolve, args.delay));
  }
}

module.exports = {
  UDIFF_START,
  dateRange,
  downloadDate,
  fileNameForDate,
  formatForDate,
  legacyDate,
  outputPathForDate,
  parseArgs,
  urlsForDate,
  validateArchive,
  validateCsv,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
