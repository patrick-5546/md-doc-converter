// Render math with KaTeX: a Pandoc JSON filter, which Pandoc runs with Node.js, that
// replaces each Math element with the HTML KaTeX renders for it. Invalid TeX is shown in
// KaTeX's error color instead of failing the render. A document with math gets the
// stylesheet that MD_DOC_CONVERTER_KATEX_CSS names, with its fonts, as a data URI: Pandoc
// renders in its sandbox, which reads no file that the document names.
//
// katex is loaded from MD_DOC_CONVERTER_NODE_MODULES, the node_modules directory that
// `md-doc-converter setup` installs. The filter uses only import(), so Node.js can run it
// as CommonJS or as an ES module, whatever package.json files enclose it.

// Render the math in node; return whether there was any.
function render(katex, node) {
	let found = false;
	if (Array.isArray(node)) {
		node.forEach((item, index) => {
			if (item?.t === "Math") {
				const [type, tex] = item.c;
				const options = { displayMode: type.t === "DisplayMath", throwOnError: false };
				node[index] = { t: "RawInline", c: ["html", katex.renderToString(tex, options)] };
				found = true;
			} else {
				found = render(katex, item) || found;
			}
		});
	} else if (node && typeof node === "object") {
		for (const value of Object.values(node)) {
			found = render(katex, value) || found;
		}
	}
	return found;
}

// Add a stylesheet to the document's css metadata, which Pandoc's template links.
function addStylesheet(document, stylesheet) {
	const entry = { t: "MetaString", c: stylesheet };
	const css = document.meta.css;
	const entries = !css ? [] : css.t === "MetaList" ? css.c : [css];
	document.meta.css = { t: "MetaList", c: [...entries, entry] };
}

async function main() {
	const { createRequire } = await import("node:module");
	const { join } = await import("node:path");
	const { readFile } = await import("node:fs/promises");
	const { fileURLToPath } = await import("node:url");
	const nodeModules = process.env.MD_DOC_CONVERTER_NODE_MODULES;
	if (!nodeModules) {
		throw new Error("MD_DOC_CONVERTER_NODE_MODULES is not set");
	}
	const katex = createRequire(join(nodeModules, "_"))("katex");
	const chunks = [];
	for await (const chunk of process.stdin) {
		chunks.push(chunk);
	}
	const document = JSON.parse(Buffer.concat(chunks).toString("utf8"));
	if (render(katex, document)) {
		const stylesheet = process.env.MD_DOC_CONVERTER_KATEX_CSS;
		if (!stylesheet) {
			throw new Error("MD_DOC_CONVERTER_KATEX_CSS is not set");
		}
		const css = await readFile(fileURLToPath(stylesheet));
		addStylesheet(document, `data:text/css;base64,${css.toString("base64")}`);
	}
	process.stdout.write(JSON.stringify(document));
}

main().catch((error) => {
	console.error(`katex.js: ${error.message} (run md-doc-converter setup)`);
	process.exit(1);
});
