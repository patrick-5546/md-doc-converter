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

## What is left out

- Comments, tracked-change history, hidden text, headers and footers, and the
  page layout.
- Images linked from outside the document, and drawings without a picture, such
  as charts, SmartArt and shapes; the report names each.

## Transforms

These transforms apply, in this order:

| Transform | What it does |
| --- | --- |
| `fields` | Keeps the text of Word fields and drops table-of-contents page numbers. |
| `hidden-text` | Drops text formatted as Hidden. |
| `spaces` | Writes Unicode spaces as plain spaces, except in indents, and drops zero-width spaces. |
| `underline` | Drops underline from links, headings and monospace text. |
| `images` | Publishes each image once, with alt text from its caption. |
| `image-conversion` | Converts EMF and WMF drawings to cropped SVG. |
| `links` | Keeps hyperlinks and their ScreenTips, leads links to bookmarks, and links typed addresses. |
| `bookmarks` | Points each bookmark at its heading, its section, or the document's top. |
| `headings` | Sets heading levels, trims ending punctuation, closes skipped levels, and omits empty headings. |
| `lists` | Omits empty items and writes lists tight where Markdown means the same. |
| `tables` | Writes tables as Markdown or minimal HTML. |
| `code` | Writes monospace text as code. |
| `cleanup` | Writes one space after list numbers, trims trailing spaces, and removes escapes that nothing needs. |
| `duplicate-headings` | Distinguishes repeated headings, with `--qualify-duplicate-headings`. |
| `pages` | Settles heading IDs and links, and splits pages for `--split-depth`. |
| `title` | Makes the Title paragraph the only `#` heading. |
| `checks` | Checks the links, images and bookmarks of the result, changing nothing. |

### Examples

Each example shows Pandoc's own conversion of the document, then
`docx-to-md`'s.

```text
fields: a caption number held in a simple field
  Before: Figure : Block diagram
  After:  Figure 3: Block diagram

hidden-text: a word formatted as Hidden
  Before: Shown draft text.
  After:  Shown text.

images: published beside the Markdown, with its caption's title as alt text
  Before: <img src="media/image1.png" style="width:2.1in" alt="A diagram of a computer" />
  After:  ![Block diagram](.images/Example/image1.png)

bookmarks: a link to a bookmark in a section's text
  Before: <span id="_Ref200" class="anchor"></span>Set the clock divider.
          [clock divider step](#_Ref200)
  After:  Set the clock divider.
          [clock divider step](#setup)

headings: trailing punctuation and a skipped level
  Before: # Overview.         After: # Overview
          ### Details                ## Details

code: text in a monospace font
  Before: Set ctrl_reg\[3:0\] to idle.
  After:  Set `ctrl_reg[3:0]` to idle.

cleanup: escapes that nothing needs; text that would be a link keeps them
  Before: Bits \[3:0\] of CTRL\[7\]; the text \[a\](b) is not a link.
  After:  Bits [3:0] of CTRL[7]; the text \[a\](b) is not a link.

duplicate-headings: with --qualify-duplicate-headings
  Before: # Block A           After: # Block A
          ## Overview                ## Block A - Overview
          # Block B                  # Block B
          ## Overview                ## Block B - Overview

title: a first paragraph in Word's Title style
  Before: Cache Controller    After: # Cache Controller
          # Overview                 ## Overview
```

Bold that ends in punctuation right before a letter, which Markdown would show
with its asterisks, becomes HTML:

```text
Before: **Note:**the reset clears it.
After:  <strong>Note:</strong>the reset clears it.
```

A list that Word numbers from 7, with a nested list also numbered from 7,
keeps Word's numbers. The blank lines let the nested list start at 7:

```markdown
7. Alpha

8. Beta

    7. Beta one

9. Gamma
```

A table with merged cells becomes HTML, and the report says why
(`Table 1 remains HTML: merged cells`). A one-level list in a cell becomes
lines:

```markdown
| Field | Values |
| --- | --- |
| MODE | 1. Off<br>2. On<br>Takes effect after a reset. |
```

## The report

Each item that a transform could not handle gets a `docx-to-md: warning:` line,
which names it and where it is. Then a summary, which quotes nothing from the
document, gives each transform's status: `applied`, `partial`, `not applied`,
`not needed` or `not requested`. An excerpt:

```text
docx-to-md: transforms
  fields: applied (1 field(s) unwrapped, 6 simple field(s) kept)
  images: partial (14 applied, 2 not applied)
    not applied (2): DrawingML text box: no embedded image; its text is kept after its paragraph
  links: partial (30 applied, 1 not applied)
    not applied (1): link to a missing Word bookmark; kept as plain text
  tables: applied (3 HTML, 25 Markdown)
  duplicate-headings: not requested (--qualify-duplicate-headings)
  checks: passed
```

`docx-to-md` exits with status 0 when it converts the document, and 1 when it
refuses the document, Pandoc cannot read it, an option or path is invalid, an
earlier output needs `--replace-generated`, or the output cannot be written.
To share a problem without sharing the document, share the summary, from
`docx-to-md: transforms` on, not the warnings before it, which can quote the
document.

## Options

| Option | Effect |
| --- | --- |
| `-o FILE.md` | Write the Markdown here, and the images to `.images/<name>/` beside it. |
| `--split-depth 0..6` | Write a page for each heading of levels 1 to N, in a folder named like the output, which lists them. `0`, the default, writes one file. |
| `--qualify-duplicate-headings` | Distinguish repeated headings by the heading above them, keeping their levels. |
| `--dry-run` | Convert and check without writing the Markdown, images or pages. |
| `--replace-generated` | Allow replacing an existing Markdown file at the default output, and nonempty images and pages folders. |
| `--math github\|dollars` | Write equations as GitHub does, `` $`x`$ `` and ```` ```math ````, which GitHub, GitLab and VS Code show, the default; or as `$x$` and `$$x$$`, for renderers that read only those, such as Azure DevOps Wiki. |
| `--keep-intermediates DIR` | Keep the intermediate files, such as the prepared copy that Pandoc reads, and a `diagnostics.json` report in a new or empty directory, also with `--dry-run`. |

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
