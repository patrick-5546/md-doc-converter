// Print one staged HTML file to PDF with Paged.js and a DevTools protocol limit.
//
// pagedjs-cli 0.4 launches its bundled Puppeteer without `protocolTimeout`, so
// each browser step (Paged.js layout, then printing) fails after Puppeteer's
// 180-second default. This keeps pagedjs-cli's Printer and bundled browser but
// launches it with the limit md-doc-converter selects (0 disables the limit).
// pagedjs-cli is loaded from the node_modules directory that
// `md-doc-converter setup` installed, which is given on the command line.
//
// Usage: node pagedjs-print.mjs --node-modules DIR --timeout-seconds N -o output.pdf input.html
import { writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

// Node timers overflow above 2**31 - 1 milliseconds.
const MAX_TIMEOUT_SECONDS = 2147483;
const USAGE =
	"usage: pagedjs-print.mjs --node-modules DIR --timeout-seconds N -o output.pdf input.html";

function fail(message, status = 1) {
	console.error(`pagedjs-print: ${message}`);
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

// Resolve packages as if from a module inside the installed node_modules directory.
let entry;
try {
	entry = createRequire(join(resolve(values["node-modules"]), "_")).resolve("pagedjs-cli");
} catch (error) {
	fail(`cannot find pagedjs-cli (run md-doc-converter setup): ${error.message}`);
}
const { default: Printer } = await import(pathToFileURL(entry).href);

class TimedPrinter extends Printer {
	constructor(timeoutSeconds) {
		super({ allowLocal: true });
		this.protocolTimeout = timeoutSeconds * 1000;
	}

	// Printer.setup() for a local input, plus protocolTimeout.
	async setup() {
		const puppeteer = createRequire(entry)("puppeteer");
		this.browser = await puppeteer.launch({
			headless: this.headless,
			args: [
				"--disable-dev-shm-usage",
				"--export-tagged-pdf",
				"--allow-file-access-from-files",
			],
			ignoreHTTPSErrors: this.ignoreHTTPSErrors,
			protocolTimeout: this.protocolTimeout,
		});
		return this.browser;
	}
}

const printer = new TimedPrinter(timeoutSeconds);
printer.on("page", (page) => {
	if (page.position === 0) {
		console.error("Paged.js: laying out pages");
	}
});
printer.on("rendered", (message) => console.error(`Paged.js: ${message}`));

try {
	const pdf = await printer.pdf(input, { outlineTags: ["h1", "h2", "h3", "h4", "h5", "h6"] });
	await writeFile(output, pdf);
} catch (error) {
	if (String(error?.message).includes("protocolTimeout")) {
		console.error(`pagedjs-print: ${error.message}`);
		fail(
			`a browser step exceeded ${timeoutSeconds} seconds; ` +
				"raise --pdf-timeout or MD_DOC_CONVERTER_PDF_TIMEOUT (seconds, 0 = no limit)",
		);
	}
	console.error(error);
	fail("PDF rendering failed");
}
// Like pagedjs-cli, exit rather than waiting for the browser to close.
process.exit(0);
