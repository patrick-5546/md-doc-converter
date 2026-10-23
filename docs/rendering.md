# Rendering Markdown to HTML and PDF

```sh
md-doc-converter md-to-html INPUT [-o FILE.html] [options]
md-doc-converter md-to-pdf  INPUT [-o FILE.pdf]  [options]
```

`INPUT` is one Markdown file or a folder of Markdown files. Rendering needs
Node.js and the toolchain that `md-doc-converter setup` installs, including
Pandoc; `md-doc-converter doctor` checks all of them, and a render that is
missing one fails before it writes anything.

## Output

Each render writes one file:

| Input | Default output |
| --- | --- |
| `docs/spec.md` | `docs/spec.html` or `docs/spec.pdf` |
| `docs/` (a folder) | `docs/docs.html` or `docs/docs.pdf`, inside the folder |
| `.` | `<current folder name>.html` or `.pdf`, in the current folder |

A file input is named without its final extension; a folder input keeps its
complete name, including dots. Spaces and Unicode are preserved. `-o` selects
another file and creates its folder if needed; it must end in `.html` or
`.htm` for HTML and `.pdf` for PDF:

```sh
md-doc-converter md-to-pdf docs/spec.md -o "reviews/spec review.pdf"
```

An existing output is replaced in one step, and only after rendering succeeds:
a failed render leaves the previous file untouched. Rendering refuses an output
that is the input, the extra stylesheet, a symlink or a directory.

The source Markdown and its images are never changed, and no public
intermediate files are written. Prepared Markdown, generated styles and, for a
PDF, the staged HTML stay in a private temporary directory that is removed
after the render. To inspect them, keep them in a new or empty directory; they
are kept even when rendering fails:

```sh
md-doc-converter md-to-pdf docs/spec.md --keep-intermediates /tmp/spec-render
```

The HTML is self-contained: it embeds its images, the GitHub Markdown
stylesheet and, for a document with math, KaTeX's stylesheet and fonts. It
opens anywhere without a network, and printing it to PDF needs none either.
Links to other local files point to their absolute paths on the machine that
rendered them.

Rendering is safe for documents from elsewhere. It embeds only image files
inside the input's Git repository or, outside Git, inside the folder that holds
the input: the file's folder, or the parent of a folder, where the folder's
title page and its Word images are. It reads no other file and downloads
nothing that a document names, so a document cannot copy your files, such as
SSH keys, into its HTML or PDF, nor tell its author when and where it was
rendered. Any other image, including one referenced by URL, stays a reference,
with a warning: the HTML loads it when it is opened, and the PDF is printed
without it. Rendered HTML runs no script: its
content security policy allows none. The PDF is printed with JavaScript off,
and the browser loads nothing but the HTML.

## Pipeline

1. Preprocess the source Markdown into a private temporary file, embedding
   its images.
2. Convert it to self-contained HTML with Pandoc, in its sandbox, with Mermaid
   diagrams and KaTeX math, embedding the GitHub Markdown stylesheet, then the
   shared styles, the page header and footer and any extra stylesheet.
3. For `md-to-pdf`, print the HTML with headless Chrome, which lays out the
   pages from the stylesheet's page rules, to a tagged PDF with heading
   bookmarks and clickable links, limited by [`--pdf-timeout`](#pdf-time-limit).
4. Publish the requested file and remove the private files.

## Folder input

A folder is collected recursively into one document. As in Azure DevOps Wikis,
a `.order` file lists a folder's entries one per line, as Markdown filenames
without `.md` or as subfolder names; unlisted entries follow, sorted by name.
A `topic.md` beside a `topic/` folder is the title page for that folder's pages
and is placed with them. Without `.order`, entries are sorted by name, except
that the first Markdown file leads when the folder has no title page. Entries
whose names begin with `.` are skipped. Azure DevOps Wiki macro lines,
`[[_TOC_]]` and `[[_TOSP_]]`, are removed from the prepared Markdown; source
files keep them.

When the folder is inside a Git work tree, Git-ignored files are skipped.
`--no-respect-gitignore` includes them; `--respect-gitignore` requests
filtering explicitly and warns when it has no effect.

## Links, headings and references

Single-file and folder rendering share GFM-compatible heading IDs with Word
conversion. Folder collection remaps file and section links to the final
combined document IDs, including duplicate headings. Unresolved old-style
fragments produce diagnostics rather than guessed destinations.

Inline, full-reference, collapsed-reference, and shortcut links and images use
the same source-relative path and heading handling. Each file's references and
footnotes are resolved independently before collection, so repeated labels
cannot take their definitions from another file. Link titles and encoded asset
paths are preserved; literal code is not treated as navigation. Bit-field
notation that would otherwise define a link reference, such as `[1:0]: Mode` at
the start of a line, a list item, or an HTML table cell, renders as text.

Private preprocessing may normalize equivalent Markdown syntax, but it never
reformats source files. Column widths, which Pandoc derives from a pipe table
line longer than 72 characters, are dropped, so a table keeps its alignment
however long its lines are. Cross-file Markdown links with query parameters
remain unchanged and produce a warning rather than discarding the query.

A block-level HTML tag, such as `<ul>`, `<p>`, or `<div>`, in a Markdown table
cell produces a warning with its line: Pandoc's Markdown reader ends the cell at
that tag and drops the rest, although GFM renderers show it. Write such a cell's
lines with `<br>`, or make the table HTML. An escaped pipe in a table cell's code
span, `` `a\|b` ``, renders as the pipe GFM shows, not with the backslash
Pandoc's Markdown reader keeps.

## Mermaid diagrams

Each `mermaid` code block becomes an image of its diagram, drawn by
[mermaid-cli](https://github.com/mermaid-js/mermaid-cli) 12 in one run for the
whole render and embedded in the HTML. Diagrams use Mermaid's defaults, such as
the ELK layout for flowcharts and state, class and ER diagrams. A diagram can
choose others in its front matter, for example the dagre layout of earlier
Mermaid versions:

````markdown
```mermaid
---
config:
  layout: dagre
---
flowchart LR
  Request --> Response
```
````

Diagrams are shown at 75% of their natural size, and no wider than the page. A
diagram that Mermaid cannot draw stops the render with Mermaid's message and
the diagram's position, such as `Mermaid diagram 2 of 3 (flowchart LR)`.

## Math

TeX math between `$` signs, or between `$$` for display math, is rendered with
[KaTeX](https://katex.org/). Math that KaTeX cannot parse is shown as its TeX
source in red, with KaTeX's message as its tooltip, rather than stopping the
render. Only a document with math embeds KaTeX's stylesheet and its WOFF2
fonts, about 340 KB.

## Page header and footer

PDF pages show page numbers, centered at the bottom as `Page N of M`. Header and
footer text is optional and empty by default:

```sh
md-doc-converter md-to-pdf docs/spec.md --header "Design review" --footer "Draft"
```

The header appears at the top right and the footer at the bottom left, in
8-point gray text. `MD_DOC_CONVERTER_HEADER` and `MD_DOC_CONVERTER_FOOTER` set
defaults; the options override them, and an empty option clears a default.

The text is literal, never CSS or HTML, and is escaped safely. Quote it for the
shell, using single quotes when it contains `$` or other shell-special
characters. Headers, footers, and page numbers use the body's sans-serif font
stack; code keeps its monospace stack.

Page-margin rules affect only PDF output. The header uses the `@top-right`
margin box, the footer `@bottom-left`, and the page number `@bottom-center`.
See [margin at-rules](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@page#margin_at-rules)
for the other boxes, and the `counter(page)` and `counter(pages)` values, which
an extra stylesheet can use.

## Extra stylesheet

`--extra-css FILE` applies a stylesheet after the shared styles and the header
and footer, for additional branding or layout. It is inlined into the HTML, so
it must not contain `</style`. Files it references with `url()`, such as fonts
or a logo, are embedded: paths, absolute or relative to the stylesheet, and
URLs, which are downloaded. Keep content
within the page width: when anything is wider, Chrome shrinks every page of the
PDF to fit it.

The shared styles provide:

- US Letter pages with one-inch margins;
- page numbering and the optional header and footer text;
- headings kept on the page of the text they introduce;
- Linux-compatible text and monospace font stacks;
- image, table, code-block, and page-width overflow protection;
- Mermaid diagram scaling;
- reusable compact and fixed-width table classes.

## PDF time limit

Each browser step of a PDF render (loading the page, then printing it) may take
up to 600 seconds by default. Large folder renders on busy hosts can need more.
Set `--pdf-timeout` to a larger whole number of seconds, or to `0` to remove the
limit; `MD_DOC_CONVERTER_PDF_TIMEOUT` sets a default:

```sh
md-doc-converter md-to-pdf docs --pdf-timeout 1800
md-doc-converter md-to-pdf docs --pdf-timeout 0
```

A step that exceeds the limit fails the render with a message naming the
setting, and any previous output is left unchanged. HTML rendering does not use
the limit.

## Table styling

Wrap a Markdown table in a classed `div` when it needs tighter PDF layout.
Blank lines before and after the table are required.

| Class | Effect |
| --- | --- |
| `table-sm` | 85% font size |
| `table-xs` | 75% font size |
| `table-compact` | Reduced cell padding |
| `table-fixed` | Equal-width columns |
| `table-cols` | Widths controlled by `--col1` through `--col6` |

Classes can be combined. For wide tables, start with `table-sm table-compact`
for about five or six columns and `table-xs table-compact` for seven or more.
Add `table-fixed` for equal-width columns, or use `table-cols` to size
individual columns.

Example:

```markdown
<div class="table-sm table-compact">

| Signal | Bits | Description |
| --- | --- | --- |
| example | [7:0] | Example value |

</div>
```

Explicit widths:

```markdown
<div class="table-cols" style="--col1: 20%; --col3: 60%">

| Signal | Bits | Description |
| --- | --- | --- |
| example | [7:0] | Example value |

</div>
```

Markdown tables cannot span columns. Use a raw HTML table inside the wrapper
when cells must span columns; the classes apply to it in the same way:

```markdown
<div class="table-xs table-compact">

<table>
<tr>
  <th>Manager</th>
  <th>Bit 3</th><th>Bit 2</th><th>Bit 1</th><th>Bit 0</th>
</tr>
<tr>
  <td>FOO</td>
  <td>0</td>
  <td colspan="3">some_signal[2:0]</td>
</tr>
</table>

</div>
```

## Reviewing a render

Review the generated PDF for wide tables, diagram sizing, links, page breaks,
the header and footer, and page numbering. The table classes above can tighten
layout without changing the source data.
