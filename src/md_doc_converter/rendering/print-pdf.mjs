// Print one staged HTML file to PDF with Chrome's own paged media support.
//
// Chrome lays out the stylesheet's @page rules itself: page size, margins, and
// margin boxes with page counters. The PDF is tagged and has bookmarks for the
// headings. It runs the chrome-headless-shell that `md-doc-converter setup`
// installs, which Mermaid also uses, from the node_modules directory given on
// the command line. Each browser step, loading the page and printing it, may
// take at most --timeout-seconds (0 disables the limit).
//
// Usage: node print-pdf.mjs --node-modules DIR --timeout-seconds N -o output.pdf input.html
import { writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

// Node timers overflow above 2**31 - 1 milliseconds.
const MAX_TIMEOUT_SECONDS = 2147483;
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
