// Print one staged HTML file to PDF with Chrome's own paged media support.
//
// Chrome lays out the stylesheet's @page rules itself: page size, margins, and
// margin boxes with page counters. Tables wider than the page are first scaled
// down to fit, so that Chrome need not shrink every page. The PDF is tagged and
// has bookmarks for the headings. It runs the chrome-headless-shell that
// `md-doc-converter setup` installs, which Mermaid also uses, from the
// node_modules directory given on the command line. Each browser step, loading
// the page and printing it, may take at most --timeout-seconds (0 disables the
// limit).
//
// Usage: node print-pdf.mjs --node-modules DIR --timeout-seconds N -o output.pdf input.html
import { writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

// Node timers overflow above 2**31 - 1 milliseconds.
const MAX_TIMEOUT_SECONDS = 2147483;
// Chrome shrinks every page of a PDF to fit the widest element on any of them. A table can be
// wider than the page, because no column is narrower than its longest word, so each such table
// is scaled down to fit instead, to no less than this; one still too wide breaks words anywhere.
const MIN_TABLE_ZOOM = 0.5;
const USAGE =
	"usage: print-pdf.mjs --node-modules DIR --timeout-seconds N -o output.pdf input.html";

function fail(message, status = 1) {
	console.error(`print-pdf: ${message}`);
	// Puppeteer stops the browser when Node exits.
	process.exit(status);
}

let options;
try {
	options = parseArgs({
		options: {
			"node-modules": { type: "string" },
			"timeout-seconds": { type: "string" },
			output: { type: "string", short: "o" },
		},
		allowPositionals: true,
	});
} catch (error) {
	fail(error.message, 2);
}
const { values, positionals } = options;
const timeoutText = values["timeout-seconds"] ?? "";
if (
	positionals.length !== 1 ||
	!values.output ||
	!values["node-modules"] ||
	!/^[0-9]+$/.test(timeoutText)
) {
	fail(USAGE, 2);
}
const timeoutSeconds = Number(timeoutText);
if (timeoutSeconds > MAX_TIMEOUT_SECONDS) {
	fail(`--timeout-seconds must be at most ${MAX_TIMEOUT_SECONDS}`, 2);
}
const input = resolve(positionals[0]);
const output = resolve(values.output);

// In the page: the width between the margins that the stylesheets' @page rules give a page,
// in CSS pixels. The last rule for every page wins, as in the cascade; Chrome prints US Letter
// when no rule sets a size.
function printableWidth() {
	const named = {
		a5: ["148mm", "210mm"],
		a4: ["210mm", "297mm"],
		a3: ["297mm", "420mm"],
		b5: ["176mm", "250mm"],
		b4: ["250mm", "353mm"],
		"jis-b5": ["182mm", "257mm"],
		"jis-b4": ["257mm", "364mm"],
		letter: ["8.5in", "11in"],
		legal: ["8.5in", "14in"],
		ledger: ["11in", "17in"],
	};
	const probe = document.createElement("div");
	probe.style.cssText =
		"position:absolute;visibility:hidden;padding:0;border:0;max-width:none;box-sizing:content-box";
	document.documentElement.append(probe);
	const pixels = (length) => {
		probe.style.width = length;
		return probe.getBoundingClientRect().width;
	};
	let size = "auto";
	let left = "";
	let right = "";
	// @page rules, also inside @media print, @supports and layers; margin boxes are their own.
	const rules = (list) =>
		[...list].flatMap((rule) => {
			if (rule instanceof CSSPageRule) return [rule];
			if (rule instanceof CSSMediaRule && !/\b(?:print|all)\b/.test(rule.media.mediaText)) {
				return [];
			}
			return rule.cssRules ? rules(rule.cssRules) : [];
		});
	for (const sheet of document.styleSheets) {
		for (const rule of rules(sheet.cssRules)) {
			if (rule.selectorText !== "") continue;
			size = rule.style.getPropertyValue("size") || size;
			left = rule.style.getPropertyValue("margin-left") || left;
			right = rule.style.getPropertyValue("margin-right") || right;
		}
	}
	const words = size.trim().toLowerCase().split(/\s+/);
	const name = words.find((word) => word in named);
	const lengths = words.filter((word) => /^[\d.]/.test(word));
	let [width, height] = named.letter;
	if (name) [width, height] = named[name];
	else if (lengths.length) [width, height] = [lengths[0], lengths.at(-1)];
	if (words.includes("landscape")) [width, height] = [height, width];
	const page = pixels(width);
	const margin = (value) => {
		if (value.endsWith("%")) return (page * Number.parseFloat(value)) / 100;
		return /^[\d.]/.test(value) ? pixels(value) : 0;
	};
	const result = page - margin(left) - margin(right);
	probe.remove();
	return result;
}

// In the page: scale each outermost table wider than the page down to fit, to no less than
// *minimum*, then let a table still too wide break its words anywhere.
function fitWideTables(minimum) {
	const limit = document.documentElement.clientWidth;
	const fits = (table) => table.getBoundingClientRect().right <= limit + 0.5;
	for (const table of document.querySelectorAll("table")) {
		if (table.parentElement?.closest("table") || fits(table)) continue;
		for (let step = 0; step < 4 && !fits(table); step += 1) {
			const box = table.getBoundingClientRect();
			const zoom = Number(table.style.zoom || 1) * ((limit - box.left) / box.width);
			table.style.zoom = String(Math.max(minimum, zoom));
			if (zoom <= minimum) break;
		}
		if (!fits(table)) {
			for (const cell of table.querySelectorAll("th, td")) {
				cell.style.overflowWrap = "anywhere";
			}
		}
	}
}

// Resolve Puppeteer as if from a module inside the installed node_modules directory.
const installed = createRequire(join(resolve(values["node-modules"]), "_"));
let puppeteer;
try {
	({ default: puppeteer } = await import(pathToFileURL(installed.resolve("puppeteer")).href));
} catch (error) {
	fail(`cannot find puppeteer (run md-doc-converter setup): ${error.message}`);
}

const timeout = timeoutSeconds * 1000;
try {
	const browser = await puppeteer.launch({
		headless: "shell",
		args: ["--disable-dev-shm-usage"],
		protocolTimeout: timeout,
	});
	const page = await browser.newPage();
	page.setDefaultTimeout(timeout);
	// The HTML embeds everything it shows and needs no script: a document from elsewhere
	// can neither run code nor load a file or URL while it is printed.
	const document = pathToFileURL(input).href;
	await page.setJavaScriptEnabled(false);
	await page.setRequestInterception(true);
	page.on("request", (request) => {
		const url = request.url();
		if (url === document || url.startsWith("data:")) {
			request.continue();
		} else {
			request.abort();
		}
	});
	await page.goto(document, { waitUntil: "load" });
	// Lay the page out as it prints, between the margins, to find tables wider than that.
	// Puppeteer evaluates these functions itself; the page still runs no script.
	const width = await page.evaluate(printableWidth);
	await page.emulateMediaType("print");
	await page.setViewport({ width: Math.max(1, Math.floor(width)), height: 1000 });
	await page.evaluate(fitWideTables, MIN_TABLE_ZOOM);
	const pdf = await page.pdf({
		preferCSSPageSize: true,
		printBackground: true,
		tagged: true,
		outline: true,
	});
	await writeFile(output, pdf);
	await browser.close();
} catch (error) {
	if (error?.name === "TimeoutError" || String(error?.message).includes("protocolTimeout")) {
		console.error(`print-pdf: ${error.message}`);
		fail(
			`a browser step exceeded ${timeoutSeconds} seconds; ` +
				"raise --pdf-timeout or MD_DOC_CONVERTER_PDF_TIMEOUT (seconds, 0 = no limit)",
		);
	}
	console.error(error);
	fail("PDF rendering failed");
}
