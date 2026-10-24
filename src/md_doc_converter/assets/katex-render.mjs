// Render TeX with KaTeX, for katex.lua: read a JSON array of formulas, each a [tex, display]
// pair, from standard input, and write a JSON array of the HTML that KaTeX renders for each.
// Invalid TeX is shown in KaTeX's error color instead of failing the render.
//
// katex is loaded from MD_DOC_CONVERTER_NODE_MODULES, the node_modules directory that
// `md-doc-converter setup` installs.
//
// Usage: node katex-render.mjs < formulas.json
import { createRequire } from "node:module";
import { join } from "node:path";

async function main() {
	const nodeModules = process.env.MD_DOC_CONVERTER_NODE_MODULES;
	if (!nodeModules) {
		throw new Error("MD_DOC_CONVERTER_NODE_MODULES is not set");
	}
	const katex = createRequire(join(nodeModules, "_"))("katex");
	const chunks = [];
	for await (const chunk of process.stdin) {
		chunks.push(chunk);
	}
	const formulas = JSON.parse(Buffer.concat(chunks).toString("utf8"));
	const html = formulas.map(([tex, displayMode]) =>
		katex.renderToString(tex, { displayMode, throwOnError: false }),
	);
	process.stdout.write(JSON.stringify(html));
}

main().catch((error) => {
	console.error(`katex-render.mjs: ${error.message} (run md-doc-converter setup)`);
	process.exit(1);
});
