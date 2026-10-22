// Render math with KaTeX: a Pandoc JSON filter, which Pandoc runs with Node.js, that
// replaces each Math element with the HTML KaTeX renders for it. Invalid TeX is shown in
// KaTeX's error color instead of failing the render.
//
// katex is loaded from MD_DOC_CONVERTER_NODE_MODULES, the node_modules directory that
// `md-doc-converter setup` installs. The filter uses only import(), so Node.js can run it
// as CommonJS or as an ES module, whatever package.json files enclose it.

function render(katex, node) {
	if (Array.isArray(node)) {
		node.forEach((item, index) => {
			if (item?.t === "Math") {
				const [type, tex] = item.c;
				const options = { displayMode: type.t === "DisplayMath", throwOnError: false };
				node[index] = { t: "RawInline", c: ["html", katex.renderToString(tex, options)] };
			} else {
				render(katex, item);
			}
		});
	} else if (node && typeof node === "object") {
		for (const value of Object.values(node)) {
			render(katex, value);
		}
	}
}

async function main() {
	const { createRequire } = await import("node:module");
	const { join } = await import("node:path");
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
	render(katex, document);
	process.stdout.write(JSON.stringify(document));
}

main().catch((error) => {
	console.error(`katex.js: ${error.message} (run md-doc-converter setup)`);
	process.exit(1);
});
