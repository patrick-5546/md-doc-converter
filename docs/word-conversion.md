# Word conversion

```sh
md-doc-converter docx-to-md docs/spec.docx                     # docs/spec.md, docs/.images/spec/
md-doc-converter docx-to-md docs/spec.docx -o wiki/review.md   # wiki/review.md, wiki/.images/review/
```

`docx-to-md` writes the Markdown beside the Word document, or to the `-o` file,
and the document's images to `.images/<name>/` beside the Markdown. It needs
Pandoc, which `md-doc-converter setup --no-render` installs, and for EMF and
WMF drawings, LibreOffice and Poppler; `md-doc-converter doctor` checks them.

Pandoc reads a prepared copy of the document, transforms rewrite what it reads
into the Markdown described below, and Pandoc writes it as GitHub Flavored
Markdown. An element that a transform cannot handle stays as Pandoc reads it,
and the report says so.

## What the Markdown holds

The Markdown shows the document as Word shows it with every tracked change
accepted.

- **Headings** keep Word's levels 1 to 6; levels 7 to 9 become paragraphs. A
  paragraph in Word's Title style before the first heading becomes the only `#`
  heading, and every other heading moves down one level. Punctuation that ends
  a heading is trimmed and skipped levels are closed, which markdownlint would
  report; a heading with no text is omitted, and a line break in one becomes a
  space.
- **Lists** use `-` for bullets and keep Word's numbers; a lettered or Roman
  level is numbered with digits, the only numbering Markdown has. An empty
  bullet is omitted, and so is an empty numbered item at the start or end of
  its list.
- **Line breaks** are Markdown hard line breaks, a backslash at the end of the
  line; two in a row start a new paragraph. In a table cell they are `<br>`.
- **Tables** are Markdown tables, with Word's first row as the header, or
  minimal HTML where Markdown cannot hold them, such as merged cells, a nested
  table, or a heading or nested list in a cell. In a cell, line breaks,
  paragraphs and a one-level list become lines separated by `<br>`. Rows that
  show nothing are omitted, except the first after the header, such as a
  template's row to fill in. Column widths, colors, borders and cell alignment
  are not kept.
- **Code:** text in a monospace font, such as Consolas, becomes code: whole
  paragraphs of it, or a one-cell table of them, a fenced `text` block, and
  shorter runs inline code. Curly quotes and dashes in code become ASCII.
- **Formatting:** bold, italic and strikethrough stay Markdown, except where
  Markdown would not read them, as in `**Note:**next`, where they become
  `<strong>`, `<em>` and `<del>`. Underline, subscript, superscript and
  highlight become `<u>`, `<sub>`, `<sup>` and `<mark>`, except underline in
  links, headings and code. Paragraphs in Word's quote styles become block
  quotes. Text that Markdown would read as markup, such as `<speeds>`, a
  backtick, a leading `#` or a line of `---`, stays text.
- **Links** keep their targets, and a ScreenTip becomes the link's title. A
  link to a Word bookmark, such as a cross-reference that Word links, leads to
  the heading the bookmark marks, or to the section that holds it. A web or
  e-mail address typed as text becomes a link.
- **Fields** keep the text Word shows, such as caption numbers, and a check box
  shows ☐ or ☒. Page numbers stay text. A table of contents keeps each entry's
  link, number and title, but not its page number.
- **Footnotes and endnotes** become footnotes, `[^1]`, defined at the end of the
  page that refers to them, numbered in order. VS Code's Markdown preview shows
  them only with the [Markdown Footnotes](https://marketplace.visualstudio.com/items?itemName=bierner.markdown-footnotes)
  extension; GitHub, GitLab and `md-to-html` show them.
- **Equations** are TeX: `` $`x`$ `` inline and a ```` ```math ```` block on
  their own line, as GitHub writes them, or with `--math dollars`, `$x$` and
  `$$x$$`.
- **Text boxes:** the text of a text box follows the paragraph that holds it.
- **Images** keep their place, without Word's display size. An image's alt
  text is the title of its figure caption, or else Word's alt text, or else the
  text of a heading right above it, also one kept as a list item; otherwise it
  is empty, and reported. EMF and WMF drawings become SVG, cropped to the
  drawing. An SVG image that contains a script is replaced by the PNG copy that
  Word keeps with it.
- **Spaces:** no-break and other Unicode spaces become plain spaces, except
  spaces that indent a line, which stay no-break spaces so that the indent
  shows. Zero-width spaces are dropped.
- **Symbols:** an arrow in a symbol font, such as Wingdings, becomes the arrow
  that every font has, such as → or ↖, by its direction. Pandoc reads these
  arrows as characters that few fonts have, which PDFs and browsers show as
  boxes.

For example, a short document with a Title paragraph, a cross-reference to a
bookmarked paragraph, text in a monospace font, a captioned figure, and a table
with a numbered list in a cell becomes:

```markdown
# Cache Controller

## Overview

The controller starts after [the reset step](#reset).

## Setup

### Reset

Hold the reset for two cycles.

Then set `ctrl_reg[3:0]` to idle.

![Block diagram](.images/Example/image1.png)

Figure 1: Block diagram

| Field | Values |
| --- | --- |
| MODE | 1. Off<br>2. On<br>Takes effect after a reset. |
```

The Title paragraph is the only `#` heading, and the other headings moved down
a level; `Overview.` lost its final period, and `Reset`, a level 3 heading in a
level 1 section, closes the skipped level. The cross-reference leads to the
section that holds its bookmark, and the figure's alt text is its caption's
title.

## What is left out

- Comments, tracked-change history, hidden text, headers and footers, and the
  page layout.
- Images linked from outside the document, and drawings without a picture, such
  as charts, SmartArt and shapes; the report names each.

## The report

Each item that could not be carried over as Word shows it gets a
`docx-to-md: warning:` line, which names it and where it is; see
[Messages](../README.md#messages) for which are shown. A summary then counts
every kind of warning in two parts: what to check in Word, which the
document's author can fix, and what is expected in Markdown, which shows it
differently. An excerpt:

```text
docx-to-md: converting spec.docx
docx-to-md: warning: images: SmartArt at paragraph 87: no embedded image; omitted
docx-to-md: warning: links: link to missing Word bookmark '_Ref1234'; kept as plain text
docx-to-md: warning: Table 4 remains HTML: merged cells
docx-to-md: warning: Table 9 remains HTML: merged cells
docx-to-md: warning: Table 12 remains HTML: merged cells
docx-to-md: to check in Word:
  1  SmartArt: no embedded image; omitted
  1  link to a missing Word bookmark; kept as plain text
docx-to-md: expected in Markdown:
  5  table kept as HTML: merged cells
docx-to-md: --verbose shows every warning
converted spec.docx -> spec.md: 1 page, 25 tables, 16 image references, 40 Word bookmarks
```

The summary quotes nothing from the document: to report a problem without
sharing the document, share the summary, not the warnings, which can quote it.

`--verbose` also shows the details that worked out, such as a drawing converted
through PDF, and each transform's status: `applied`, `partial`, `not applied`,
`not needed` or `not requested`. The transforms, in the order they apply:

| Transform | What it does |
| --- | --- |
| `fields` | Keeps the text of Word fields and drops table-of-contents page numbers. |
| `hidden-text` | Drops text formatted as Hidden. |
| `spaces` | Writes Unicode spaces as plain spaces, except in indents, and drops zero-width spaces. |
| `symbols` | Writes the arrows of symbol fonts, such as Wingdings, as the arrows that every font has. |
| `underline` | Drops underline from links, headings and monospace text. |
| `images` | Publishes each image once, with alt text from its caption. |
| `image-conversion` | Converts EMF and WMF drawings to cropped SVG. |
| `links` | Keeps hyperlinks and their ScreenTips, leads links to bookmarks, and links typed addresses. |
| `bookmarks` | Points each bookmark at its heading, its section, or the document's top. |
| `headings` | Sets heading levels, trims ending punctuation, closes skipped levels, and omits empty headings. |
| `lists` | Omits empty items and writes lists tight where Markdown means the same. |
| `tables` | Writes tables as Markdown or minimal HTML. |
| `code` | Writes monospace text as code. |
| `cleanup` | Writes one space after list numbers, with each item's other lines indented to its text, trims trailing spaces, and removes escapes that nothing needs. |
| `duplicate-headings` | Distinguishes repeated headings, with `--qualify-duplicate-headings`. |
| `pages` | Settles heading IDs and links, and splits pages for `--split-depth`. |
| `title` | Makes the Title paragraph the only `#` heading. |
| `checks` | Checks the links, images and bookmarks of the result, changing nothing. |

`docx-to-md` exits with status 0 when it converts the document; 1 when it
refuses the document, Pandoc cannot read it, a path is invalid, an earlier
output needs `--replace-generated`, or the output cannot be written; and 2 when
an option is invalid.

## Options

| Option | Effect |
| --- | --- |
| `-o`, `--output FILE.md` | Markdown file to write, with the images in `.images/<name>/` beside it (default: the input's name with `.md`, beside it). |
| `--split-depth 0..6` | Write a page for each heading of levels 1 to this, in a folder named like the output, which lists them (default: 0, one file). |
| `--qualify-duplicate-headings` | Distinguish repeated headings by the headings of the sections that hold them, keeping their levels: an `## Overview` under `# Block B` becomes `## Block B - Overview`. |
| `--dry-run` | Convert and check without writing the Markdown, images or pages. |
| `--replace-generated` | Allow replacing an existing Markdown file at the default output, and nonempty images and pages folders. |
| `--math github\|dollars` | How to write equations: `github`, as `` $`x`$ `` and ```` ```math ```` blocks, which GitHub, GitLab and VS Code show; or `dollars`, as `$x$` and `$$x$$`, for renderers that read only those, such as Azure DevOps Wiki (default: `github`). |
| `--keep-intermediates DIR` | Keep the intermediate files, such as the copy that Pandoc reads, and a `diagnostics.json` report in a new or empty directory apart from the input and outputs, also with `--dry-run`. |
| `-v`, `--verbose` | Show every warning, and the details of each step. |
| `-q`, `--quiet` | Show only errors. |

With `--split-depth 1`, `Example.docx` becomes `Example.md`, which lists the
pages, and a page per section in `Example/`:

```markdown
# Example

## Sections

- [Introduction](Example/01-introduction.md)
- [Setup](Example/02-setup.md)
```

Each page's headings move together so that its first is `#`, and links between
pages lead to the final page and heading.

## Converting again

An earlier conversion's output is replaced only with permission: a Markdown
file at the default output needs `-o` naming it or `--replace-generated`, and a
nonempty images or pages folder always needs `--replace-generated`. A document
without images gets no images folder, and converting it again removes an
earlier one. `--split-depth 0` does not remove an earlier split's pages.

Every output is prepared and checked before any is replaced. If replacing one
fails, the earlier outputs are restored; if that fails too, the message names
the backups to recover them from. Run one conversion at a time for the same
output.

## Refused documents

A document encrypted with a password or by rights management, one in the Word
97-2003 format (`.doc`), and one whose contents expand to more than 1,024 MiB
are refused before conversion; a crafted file can expand to far more than its
own size. Convert an unencrypted `.docx` copy.

## After converting

Read the warnings and the summary, then check the Markdown's tables, code
blocks, links and images. It is usually easiest to correct problems in the Word
document and convert again.
