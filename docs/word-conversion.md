# Word conversion

```sh
md-doc-converter docx-to-md docs/spec.docx                     # docs/spec.md, docs/.images/spec/
md-doc-converter docx-to-md docs/spec.docx -o wiki/review.md   # wiki/review.md, wiki/.images/review/
```

`docx-to-md` writes the Markdown beside the Word document, or to the `-o` file,
and the document's images to `.images/<name>/` beside the Markdown. It needs
Pandoc, which `md-doc-converter setup --no-render` installs, and for EMF and
WMF drawings, LibreOffice and Poppler, which Linux distributions package and
winget installs on Windows; `md-doc-converter doctor` checks them.
`md-doc-converter docx-to-md --help` lists its options; this guide explains the
ones that need more than a line.

Pandoc reads a prepared copy of the document, transforms rewrite what it reads
into the Markdown described below, and Pandoc writes it as GitHub Flavored
Markdown. An element that a transform cannot handle stays as Pandoc reads it,
and the report says so.

## What the Markdown holds

The Markdown shows the document as Word shows it with every tracked change
accepted.

- **Headings** keep Word's levels 1 to 6; levels 7 to 9 become paragraphs. A
  paragraph in Word's Title style before the first heading becomes the only `#`
  heading, and every other heading moves down one level; a Heading 6 then has
  no level left, so it becomes a paragraph, and the report names it. A heading
  keeps the punctuation that ends it, and its level when it skips one, such as
  a Heading 3 right after a Heading 1: markdownlint reports both, and the
  report names each skip, to fix in Word. A heading with no text is omitted,
  and a line break in one becomes a space. With `--qualify-duplicate-headings`,
  a repeated heading also names the headings of the sections that hold it: two
  `## Overview` headings under `# Block A` and `# Block B` become
  `## Block A - Overview` and `## Block B - Overview`; the title, which holds
  every heading, names none.
- **Lists** use `-` for bullets and keep Word's numbers; a lettered or Roman
  level is numbered with digits, the only numbering Markdown has. An empty
  bullet is omitted, and so is an empty numbered item at the start or end of
  its list. A paragraph of a list that shows no number or bullet at any level,
  which Word uses to indent paragraphs, is a paragraph, in the list item before
  it if there is one. An empty paragraph between two items of a list is
  omitted, so that the list goes on, as in Word, rather than beginning again.
- **Line breaks** are Markdown hard line breaks, a backslash at the end of the
  line; two in a row start a new paragraph. In a table cell they are `<br>`.
- **Tables** are Markdown tables, with Word's first row as the header, or
  minimal HTML where Markdown cannot hold them, such as merged cells, a nested
  table, or a heading or nested list in a cell. In a cell, line breaks,
  paragraphs and a one-level list become lines separated by `<br>`; in the
  header, whose line breaks usually narrow a column, they become spaces, unless
  the table has only that row. Rows that show nothing are omitted, except the
  first after the header, such as a template's row to fill in. A table that
  shows nothing is omitted too, and the report names it. Column widths,
  colors, borders and cell alignment are not kept.
- **Code:** text in a monospace font, such as Consolas, becomes code: whole
  paragraphs of it, or a one-cell table of them, a fenced `text` block, and
  shorter runs inline code. Curly quotes and dashes in code become ASCII.
- **Formatting:** bold, italic and strikethrough stay Markdown, except where
  Markdown would not read them, as in `**Note:**next`, where they become
  `<strong>`, `<em>` and `<del>`. Underline, subscript, superscript and
  highlight become `<u>`, `<sub>`, `<sup>` and `<mark>`, except underline in
  links, headings and code. Paragraphs in Word's quote styles become block
  quotes. Text that Markdown would read as markup, such as `<speeds>`, a
  backtick, a leading `#` or a line of `---`, stays text. Brackets and angle
  brackets keep a backslash only where Markdown could read them as markup, as
  in `\<speeds>`: `bits [3:0]`, `a -> b` and `x <= 4` stay as typed.
- **Links** keep their targets, and a ScreenTip becomes the link's title. A
  link to a Word bookmark, such as a cross-reference that Word links, leads to
  the heading the bookmark marks, or to the section that holds it. A
  table-of-contents entry or cross-reference whose bookmark starts before the
  paragraph it marks, as when text was typed at the start of a heading, leads
  where the bookmark starts, as it does in Word, and the report names it:
  update the table of contents, and insert the cross-reference again, in Word.
  A web or e-mail address typed as text becomes a link.
- **Fields** keep the text Word shows, such as caption numbers, and a check box
  shows ☐ or ☒. Page numbers stay text. A table of contents keeps each entry's
  link, number and title, but not its page number. Word shows a field's text
  as of its last update, so update the fields before converting: in Word, press
  Ctrl+A and then F9.
- **Footnotes and endnotes** become footnotes, `[^1]`, numbered in order and
  defined at the end of the Markdown. VS Code's Markdown preview shows
  them only with the [Markdown Footnotes](https://marketplace.visualstudio.com/items?itemName=bierner.markdown-footnotes)
  extension; GitHub, GitLab and `md-to-html` show them.
- **Equations** are TeX: `` $`x`$ `` inline and a ```` ```math ```` block on
  their own line, as GitHub writes them, or with `--math dollars`, `$x$` and
  `$$x$$`, for renderers that read only those, such as Azure DevOps Wiki.
- **Text boxes:** the text of a text box follows the paragraph that holds it.
- **Images** keep their place, without Word's display size. An image's alt
  text is the title of its figure caption, or else Word's alt text; otherwise
  it is empty, and reported, to add in Word. EMF and WMF drawings, which Word
  keeps for drawings pasted from Visio and other programs, become SVG, cropped
  to the drawing: LibreOffice draws each as a PDF, which Poppler turns into
  SVG. Its text is drawn as shapes, so that it looks the same everywhere, with
  an invisible copy of the words that a search of a PDF of the page still
  finds; symbols that no font has, such as Word's Wingdings arrows, are left
  out of the copy. LibreOffice before 7.4 cannot report the drawing's frame, so
  its whole page is kept, and reported. An SVG image that contains a script is
  replaced by the PNG copy that Word keeps with it.
- **Spaces:** no-break and other Unicode spaces become plain spaces, except
  spaces that indent a line, which stay no-break spaces so that the indent
  shows. Zero-width spaces are dropped, and so are the left-to-right marks that
  Word puts in cross-references, unless the document has right-to-left text.
- **Symbols:** an arrow in a symbol font, such as Wingdings, becomes the arrow
  that every font has, such as → or ↖, by its direction. Pandoc reads these
  arrows as characters that few fonts have, which PDFs and browsers show as
  boxes. A no-break hyphen, as in Word's caption numbers, becomes a hyphen,
  which every font has and a search for `Table 2-1` finds.

For example, a short document with a Title paragraph, a cross-reference to a
bookmarked paragraph, text in a monospace font, a captioned figure, and a table
with a numbered list in a cell becomes:

```markdown
# Cache Controller

## Overview

The controller starts after [the reset step](#reset).

## Setup

#### Reset

Hold the reset for two cycles.

Then set `ctrl_reg[3:0]` to idle.

![Block diagram](.images/Example/image1.png)

Figure 1: Block diagram

| Field | Values |
| --- | --- |
| MODE | 1. Off<br>2. On<br>Takes effect after a reset. |
```

The Title paragraph is the only `#` heading, and the other headings moved down
a level; `Reset`, a level 3 heading in a level 1 section, keeps its level, and
the report names it. The cross-reference leads to the
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
converted spec.docx -> spec.md: 25 tables, 16 image references, 40 Word bookmarks
```

The summary quotes nothing from the document: to report a problem without
sharing the document, share the summary, not the warnings, which can quote it.

`--verbose` also shows the details that worked out, such as an image published
under another name because its own was taken, and the status of each transform,
such as `fields` or `tables`: `applied`, `partial`, `not applied`, `not needed`
or `not requested`.

`docx-to-md` exits with status 0 when it converts the document; 1 when it
refuses the document, Pandoc cannot read it, a path is invalid, an earlier
output needs `--replace-generated`, or the output cannot be written; and 2 when
an option is invalid.

## Converting again

An earlier conversion's output is replaced only with permission: a Markdown
file at the default output needs `-o` naming it or `--replace-generated`, and a
nonempty images folder always needs `--replace-generated`. A document without
images gets no images folder, and converting it again removes an earlier one.

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
