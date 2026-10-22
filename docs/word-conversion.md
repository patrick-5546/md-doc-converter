# Word conversion

```sh
md-doc-converter docx-to-md path/to/input.docx [-o path/to/output.md]
```

Word conversion writes Markdown beside the DOCX unless `-o` selects another
file, and writes the document's images beside that Markdown. It needs Pandoc,
which `md-doc-converter setup --no-render` installs, for everything beyond the
MarkItDown baseline, and LibreOffice for EMF/WMF drawings;
`md-doc-converter doctor` reports both.

`docx-to-md` starts from a MarkItDown conversion and layers transforms on top
of it, most of them guided by a Pandoc structural reference pass. A transform
that cannot be applied is skipped and reported; conversion never refuses a
readable document. It produces an accepted/final document view, not an archive
of Word comments, tracked revisions, or exact page layout. As in Word with every
change accepted, a paragraph whose mark was deleted joins the paragraph after
it, and a table whose rows were all deleted is dropped. Text formatted as
Hidden, which Word neither shows nor prints, is dropped too. No-break, en, and
other Unicode spaces become plain spaces, which text search matches, and
zero-width spaces are dropped. Two or more spaces that indent a line, which
Markdown would drop, become no-break spaces instead, so the line keeps the
indent Word shows. Underlined text becomes `<u>…</u>`, since Markdown has no
underline, except in links, which are underlined anyway, in headings, whose text
also names pages and anchors, and in code, which would show the tag as text.

The default conversion preserves step labels, status labels, generated index
content, and every table row that shows something. A heading loses the
punctuation that ends it, the . , ; : ! and full-width forms that markdownlint's
MD026 reports; a numbered heading kept as a list item keeps it. A heading more
than one level below the heading it belongs to moves up, with its subheadings,
so that no level is skipped, which markdownlint's MD001 reports. A row with
nothing in it is omitted, unless it is in a vertical merge, is the table's only
row, or is the first of the empty rows that follow a table's first row, such as
a template's fill-in row; its bookmarks move to a row kept in the same table.
Text in a monospace font, such as Courier New or Consolas, becomes code, with
Word's curly quotes, dashes, and ellipses made ASCII: consecutive paragraphs that
are entirely monospace, or a one-cell table of them, become a fenced `text`
block, and other monospace text becomes inline code.
Table-format repair corrects converter artifacts against source structure.
Ordinary rectangular tables use Markdown pipes, with the first Word row as
the Markdown header whether or not Word marks it as a repeating header.
Column widths, percentages, fonts, colors, borders, and cell alignment are
discarded. Markdown table separators are always plain `---`, without alignment
colons. Supported emphasis remains, and subscript, superscript, highlight, and
underline stay `<sub>`, `<sup>`, `<mark>`, and `<u>` tags, as in prose; simple
multiline cells use `<br>`, as does a one-level list in a cell, one item per
line after its bullet (`•`) or number. An item's further paragraphs, and an
indented paragraph, which Pandoc reads as a block quote, become lines of their
own. Empty lines at the start or end of a cell are dropped. In the header row,
line breaks and paragraphs become spaces instead, so that a renderer wraps
each header as the column width allows.
Captions appear as adjacent Markdown.

A paragraph in Word's Title style, before the first heading, becomes the
document's only H1, and every heading moves down one level: Heading 1 becomes
`##`. When a heading is already at level 6, which has no level below it, or
when the title's heading ID would change another heading's ID, headings keep
their Word levels and the title stays a paragraph; the report says why. The
root page of a split document uses the title as its H1 and does not repeat it.

A Word heading with no text, often left by pressing Enter at the start of a
heading, is omitted. A heading with only a picture, which Pandoc and GFM would
give different IDs, becomes a paragraph. Both are reported.

Signal identifiers such as `_ctl_s_ready` are emitted without unnecessary
underscore escapes. Escapes needed to preserve literal text and prevent
unintended Markdown emphasis remain; code examples are not changed.

Pandoc escapes every `[` and `]`, and a number that starts a table cell. Those
escapes are removed wherever both md-doc-converter's and md-to-html's Markdown readers
read the page the same without them, so `bits [3:0]` stays readable. A bracket
pair stays escaped where markdownlint could take it for a link: after `)`, `!`,
or `]`, before `(` or `[`, and before `:` at the start of a line, where it would
define one.

Tables use minimal HTML only when needed to retain structure or content, such
as merged/nested tables, nested lists or headings inside cells, and
empty/zero-cell structures. A blank first row is omitted like any other empty
row, so the next row becomes the header; a table whose only row is blank stays
HTML, because the GFM reader would discard that row as a Markdown header. Each
HTML fallback reports its reason.

Markdown unordered lists always use dashes, without changing indentation or
content. Ordered lists keep Word's numbers, except that a lettered or Roman level
is numbered with decimals, the only numbering Markdown has, rather than written
as HTML. Each list marker is followed by one space, and no line ends in spaces
or no-break spaces, which show nothing in Markdown. Literal code and necessary
raw HTML are not reformatted as lists or trimmed. A list item that shows nothing
in Word is omitted: a bullet wherever it is, and a numbered item only at the
start or end of its list, so that the other items keep Word's numbers.
Heading-gap repair, title-period stripping, step merging, status normalization,
and index omission are not conversion options.

Word bookmarks are internal navigation metadata, not generated `<a id>` or
`<a name>` markup. A heading bookmark links to the final automatic GFM heading
ID, as does a bookmark with nothing visible between it and the next heading,
such as one left in an empty paragraph or an omitted empty heading above it.
Any other bookmark in prose or a table links to its containing section. A
bookmark before the first section links to the document top, without inventing
a heading. Referenced non-heading bookmarks report this section-level
approximation; unused bookmarks disappear quietly. A link or hyperlinked
cross-reference (a `REF`, `PAGEREF`, or `NOTEREF` field with `\h`) to a
bookmark that does not exist keeps its text without the link, and bookmarks
without a name or with an earlier bookmark's name are dropped; each is
reported. Link labels and external destinations are preserved, and
foreign-document fragments are not treated as local Word bookmarks. A
hyperlink whose package relationship is missing keeps its text without a
target, and a hyperlink nested in another keeps its text in the outer link;
both are reported. A web or e-mail address that Word has as text, which
markdownlint's MD034 reports, becomes an autolink, `<https://…>` or
`<name@example.com>`; angle brackets typed around it in Word become the
autolink's, and punctuation after it stays outside, as GFM reads bare addresses.
Link text, code, image alt text, and raw HTML blocks are unchanged.

Conversion recovers media and alt text, repairs narrowly identified
tooltip/fragment artifacts, and checks internal links and asset references
before publishing. A table, image, or link that cannot be mapped to the Word
source stays as MarkItDown converted it. Content that cannot be carried over
at all, such as an external image or the text of a DrawingML text box, is
reported rather than silently lost. Pandoc remains an internal structural
parser and renderer; using it does not require HTML in the canonical Markdown.

An image's alt text is the title of its figure caption, the text after the
figure number. The caption is the nearest paragraph with text above or below
the image, in Word's Caption style or numbered by a SEQ Figure field; table and
equation captions do not count. An image between two captions takes the one on
the side where most of the document's captions are, and a caption describes one
image. Word's own alt text, which Office often generates, is used only for an
image without a caption. An image without either, in a paragraph with no text
directly below a heading, with nothing but empty paragraphs between them, takes
the heading's text; the report counts these. Other missing alt text remains
empty and is reported, not invented.

A table-of-contents entry keeps its link, number, and title but not its Word
page number, which means nothing in Markdown: the page-reference field inside
the link and the tab leader before it are dropped. Literal
placeholders such as `<speeds>` and entity-like text remain text, rather than
being interpreted as HTML; so do backticks, as in `` `uvm_info ``, backslashes,
and a leading `#`, rather than becoming code, escapes, or headings. Prose,
code, and table contexts are preserved.

Images always belong beside the selected Markdown, in `.images/<name>/`:

```sh
md-doc-converter docx-to-md docs/cache.docx -o wiki/cache-review.md
```

This selects `wiki/cache-review.md` and `wiki/.images/cache-review/`. There is no
image-directory override or separate output-name setting. A document without
images gets no image directory; converting again without images removes the
previous one, and `.images/` when nothing else is in it. Existing
author-owned Markdown images are not moved or bundled by rendering. Images use
Markdown image syntax, `![alt](path)`, so Word's display size is not kept.

## Transforms and the conversion report

MarkItDown's output is the baseline. These transforms are layered on it, in
this order:

| Transform | Effect |
| --- | --- |
| `alignment` | Aligns the MarkItDown and Pandoc conversions through private markers. The transforms from `literal-text` to `floating-previews`, except `image-conversion`, need it. |
| `fields` | Keeps the cached text of Word simple fields, such as caption numbers, which MarkItDown and Pandoc would otherwise drop. Fields that Pandoc cannot close, which would move the text of later list items out of their lists, keep only their cached text: fields spanning paragraphs, such as a table of contents, and fields without an instruction or an end. Fields inside another field's instruction, which Word does not show, and stray field marks, which Word ignores, are removed. Page numbers in table-of-contents links are dropped. |
| `symbols` | Writes characters set in the Symbol, Wingdings, and Webdings fonts, such as a Wingdings check mark typed as `ü`, as the Unicode symbols they show. |
| `hidden-text` | Drops text formatted as Hidden, directly or through its character style, paragraph style, or the document defaults. |
| `spaces` | Writes no-break, en, and other Unicode spaces as plain spaces, which Markdown editors do not show and text search does not match, and drops zero-width spaces, in text and in Word alt text. Spaces that indent a line outside a heading, two or more with at least one a Unicode space, become no-break spaces, the only spaces Markdown keeps at the start of a paragraph. |
| `underline` | Drops Word underline, direct or through a style, from hyperlinks, headings, and monospace text; elsewhere both converters keep it as `<u>`. |
| `literal-text` | Keeps literal `<`, `>`, `&`, `` ` ``, `\`, `#`, `[`, and `]` as text rather than HTML, code, escapes, headings, or links. |
| `line-breaks` | Keeps Word line breaks, which MarkItDown loses, by starting a new paragraph at each. One in a heading or link text becomes a space; table cells keep theirs as `<br>`. |
| `images` | Places each Word image at its source position, as `![alt](path)` without Word's display size. The alt text is the image's figure caption title, or else Word's alt text, or else the text of a heading directly above it. |
| `image-conversion` | Converts EMF and WMF drawings to SVG, cropped. |
| `links` | Restores hyperlinks, including links to Word bookmarks. A Word ScreenTip becomes the link's title, which renderers show on hover. A web or e-mail address typed as text becomes an autolink, `<https://…>`. |
| `bookmarks` | Maps Word bookmarks to final headings, sections, or the document top. |
| `headings` | Restores Word heading levels 1-6; levels 7-9 stay paragraphs. A numbered paragraph that is a heading only by its outline level stays a list item, as Pandoc and MarkItDown read it. A heading with no text is omitted, and one with only a picture becomes a paragraph. Trailing `.`, `,`, `;`, `:`, and `!` are trimmed, and skipped levels are closed. |
| `lists` | Restores list structure and numbering; lettered and Roman levels are numbered. A paragraph whose numbering was removed, such as a list item's second paragraph, stays in the list item before it. List items that show nothing in Word are omitted, keeping Word's numbers for the rest. |
| `tables` | Rebuilds tables from the Word source as Markdown or minimal HTML. Rows that show nothing are omitted. A one-level list, a list item's further paragraphs, or an indented paragraph in a cell becomes lines, without empty lines at the cell's start or end; header line breaks become spaces. `<sub>`, `<sup>`, `<mark>`, and `<u>` stay tags. |
| `code` | Writes text in a monospace font as code. Consecutive body paragraphs that are entirely monospace, with the empty paragraphs between them, become a fenced `text` block with tabs as four spaces, as does a one-cell table that holds only such paragraphs. Other monospace text, outside headings, links, and fields, becomes inline code, in which a table cell's pipes are escaped with a backslash. Curly quotes, dashes, and ellipses in code become ASCII. |
| `floating-previews` | Removes MarkItDown's extra copies of images that are already placed. |
| `cleanup` | Normalizes list markers, trims trailing whitespace, repairs tooltip links, removes leftover markers, and removes escapes that are not needed. |
| `duplicate-headings` | Qualifies repeated headings when `--qualify-duplicate-headings` is set. |
| `pages` | Resolves final heading IDs and links, splitting pages for `--split-depth`. |
| `title` | Makes the Word title the only H1 and moves every heading down one level. On the root page of a split document, which already has the title as its H1, removes the repeated title paragraph. |
| `checks` | Checks links, images, and bookmark positions in the final pages. |

Each transform applies wherever it can. An element it cannot handle stays as
MarkItDown converted it, or is dropped when MarkItDown has nothing usable, and
a `docx-to-md: warning:` line names the element, its location, and the
consequence. A transform that fails outright is skipped and the rest still
apply. If alignment cannot run, because Pandoc is missing or the alignment
fails, the MarkItDown baseline is published with its images where MarkItDown
placed them, and the transforms that need alignment are reported as not
applied; without Pandoc, `pages`, `title`, and `checks` are not applied
either. If pages
cannot be split, the document is written as one page. `checks` reports
problems; it does not change the output.

After the warnings, standard error summarizes every transform:

```text
docx-to-md: transforms (baseline: MarkItDown)
  alignment: applied
  fields: applied (1 field(s) unwrapped, 6 simple field(s) kept)
  symbols: applied (3)
  literal-text: applied (2)
  line-breaks: applied (5)
  images: partial (14 applied, 2 not applied)
    not applied (2): DrawingML text box: no embedded image; its text is omitted
  image-conversion: applied (9 EMF to SVG)
  links: partial (30 applied, 1 not applied)
    not applied (1): link to a missing Word bookmark; kept as plain text
  bookmarks: applied (12)
  headings: applied (85)
  lists: applied (20)
  tables: applied (3 HTML, 25 Markdown)
  floating-previews: not needed
  cleanup: applied (4 list marker(s) normalized)
  duplicate-headings: not requested (--qualify-duplicate-headings)
  pages: applied (1)
  title: applied (85 heading(s) moved down one level)
  checks: passed
```

A transform is `applied`, `partial`, `not applied`, `not needed`, or
`not requested`. The summary gives counts and generic reasons without quoting
the document; the warnings name the individual items. When images are
published where MarkItDown placed them, the first line also counts them.

Conversion exits 0 whenever MarkItDown can convert the document. It exits 1
for an unreadable, encrypted, or unsafe package, a package whose contents expand
to more than 1,024 MiB, a document MarkItDown cannot convert, invalid paths or
options, existing outputs that need `--replace-generated`, or a publication
failure (see below).

### Explaining what was not applied

With `--explain`, the summary is followed by what the items behind each reason
have in common. It describes them with fixed labels and counts, never the
document's text, names, or link targets, so a conversion problem can be shared
and debugged without sharing the document. Everything from
`docx-to-md: transforms` on is free of document text; the
`docx-to-md: warning:` lines before it name the items and can quote the
document. An excerpt:

```text
docx-to-md: explain (fixed labels and counts only; no document text)
  document: paragraphs 64; text boxes 0; headings 12; list items 9; links 4
  links: link to a missing Word bookmark; kept as plain text (1)
    from: link 1
    name: _Ref 1
    bookmark: nowhere 1
  lists: Word/Pandoc/MarkItDown list content cannot be aligned; kept as MarkItDown converted it (1)
    difference: differ at the middle: Pandoc letter / MarkItDown bracket 1
    list items: 2 of 9
      contains: text 1/8; text+note reference 1/1
      after a list item: yes 1/6; no 1/3
```

The `document` line counts paragraphs, text boxes, and the Word items that
docx-to-md aligns; a `fields` line counts Word fields by kind. Under each
reason, lines with plain counts describe each mismatch, such as where the
MarkItDown and Pandoc text first differ. A line such as `list items: 2 of 9`
counts the items that were not applied, and each feature after it shows
`value N/M`: N of the M items with that value were not applied for this
reason. Values with the highest share point to the cause. Features that have
one value for every item are left out.

## Transform examples

Each example converts a small Word document. **Before** is MarkItDown's output
on its own; **after** is the output of `docx-to-md`.

### `alignment`

Nothing visible on its own: it lets the transforms below match MarkItDown's and
Pandoc's conversions of the same element. Without it, for example when Pandoc is
missing, the transforms from `literal-text` to `floating-previews`, except
`image-conversion`, are not applied, and images stay where MarkItDown put them.

### `fields`

```text
A caption number held in a simple field
  Before: Figure : Block diagram
  After:  Figure 3: Block diagram
```

A stray field mark, which Word ignores, is removed.

### `symbols`

A Wingdings check mark and cross, typed as `ü` and `û`:

```text
Before: ü Supported      û Not supported
After:  ✓ Supported      🗶 Not supported
```

### `underline`

```text
Underline in a sentence, a link, and a heading
  Before: Do <u>not</u> reset. See [<u>Spec</u>](https://example.test/spec).
          # <u>Overview</u>
  After:  Do <u>not</u> reset. See [Spec](https://example.test/spec).
          # Overview
```

### `literal-text`

```text
Word:   Set <speeds> & <mode> before reset.
Before: Set <speeds> & <mode> before reset.
After:  Set \<speeds\> & \<mode\> before reset.

Word:   Use `uvm_info and `uvm_error for messages.
Before: Use `uvm\_info and `uvm\_error for messages.
After:  Use \`uvm_info and \`uvm_error for messages.

Word:   # marks a comment in the script.
Before: # marks a comment in the script.
After:  \# marks a comment in the script.

Word:   Write 1 to bit[3](W1C) to clear the interrupt.
Before: Write 1 to bit[3](W1C) to clear the interrupt.
After:  Write 1 to bit\[3\](W1C) to clear the interrupt.
```

Rendered, the first **before** line loses `<speeds>` and `<mode>` as HTML tags,
the second shows `uvm\_info and` as code, the third becomes a heading, and the
fourth becomes a link to a file named `W1C`.

### `line-breaks`

A paragraph with two Word line breaks (Shift+Enter). MarkItDown loses them, so
its lines render as one paragraph; each break now starts a new paragraph.

Before:

```markdown
Line one
Line two
Line three
```

After:

```markdown
Line one

Line two

Line three
```

### `images`

MarkItDown writes a placeholder with Word's alt text. The image is published
beside the Markdown, at its Word position, with the title of its caption,
`Figure 3: Block diagram`, as alt text:

```text
Before: ![A diagram of a computer](data:image/png;base64...)
After:  ![Block diagram](.images/Example/image1.png)
```

### `image-conversion`

EMF and WMF drawings, such as pasted Visio diagrams, become SVG:

```text
Before: ![Timing diagram](data:image/x-emf;base64...)
After:  ![Timing diagram](.images/Example/image1.svg)
```

LibreOffice, which converts them, draws each one in the middle of a whole page,
such as US Letter. The SVG is cropped to the drawing's frame, the area Word
shows for it; when LibreOffice does not report the frame, the page is kept and
a warning says so. LibreOffice starts once for all of a document's drawings;
those it cannot export as SVG are exported as PDF, together, and converted to
SVG with Poppler's `pdftocairo`.

### `links`

```text
A mailto: link, which MarkItDown reduces to its text
  Before: Team
  After:  [Team](mailto:team@example.test)

A hyperlink field with a ScreenTip
  Before: [Spec](https://example.test/spec%22%20%5Co%20%22Open%20the%20spec)
  After:  [Spec](https://example.test/spec "Open the spec")

A link to a Word bookmark that no longer exists, kept as text and reported
  Before: [Missing target](#_RefGone)
  After:  Missing target
```

### `bookmarks`

```text
A link to a bookmark on a heading
  Before: [Reset steps](#_Ref100)
  After:  [Reset steps](#reset-sequence)

A link to a bookmark in the text of the Setup section, reported
  Before: [clock divider step](#_Ref200)
  After:  [clock divider step](#setup)
```

### `headings`

Custom heading styles based on Heading 1 and Heading 2, which MarkItDown does
not recognize:

```text
Before                   After
Registers                # Registers

Register map details.    Register map details.

Control                  ## Control
```

An empty heading above `Interfaces` that kept its table-of-contents bookmark:

```text
Before                              After
[2.1 Interfaces](#_Toc100)          [2.1 Interfaces](#interfaces)

##                                  ## Interfaces

## Interfaces
```

### `lists`

A Word list numbered from 7, with a sub-item numbered `a)`:

Before:

```markdown
1. Alpha
2. Beta
   1. Beta one
3. Gamma
```

After:

```markdown
7. Alpha

8. Beta

    1. Beta one

9. Gamma
```

### `tables`

Merged cells, which MarkItDown misaligns: `0x08` moves under Register.

Before:

```markdown
| Register | | Reset |
| --- | --- | --- |
| CTRL | 0x00 | 0x1 |
| STAT | 0x04 | 0x0 |
| 0x08 | 0x0 |
```

After, as minimal HTML because Markdown tables cannot merge cells; the warning
`Table 1 remains HTML: merged cells` reports it:

```html
<table>
<thead><tr>
<th colspan="2">Register</th>
<th>Reset</th>
</tr>
</thead><tbody><tr>
<td>CTRL</td>
<td>0x00</td>
<td>0x1</td>
</tr>
<tr>
<td rowspan="2">STAT</td>
<td>0x04</td>
<td>0x0</td>
</tr>
<tr>
<td>0x08</td>
<td>0x0</td>
</tr>
</tbody></table>
```

A one-level list in a cell, which MarkItDown writes as HTML, becomes lines, and
so does a further paragraph of an item, such as one whose Word number was turned
off; a nested list keeps the table HTML:

```markdown
| Field | Values |
| --- | --- |
| MODE | 1. Off<br>2. On<br>Takes effect after a reset. |
```

### `floating-previews`

MarkItDown writes some floating pictures after their paragraph:

```text
Before: Text before text after.

        ![](data:image/png;base64...)
After:  Text before ![Legacy preview](.images/Example/image1.png) text after.
```

`docx-to-md` places the picture where Word has it, and `floating-previews`
removes any copy MarkItDown still leaves elsewhere.

### `cleanup`

A list that `lists` could not rebuild keeps MarkItDown's markers, written as
dashes:

```text
Before            After
* First note      - First note
  + Nested note     - Nested note
```

It also trims the spaces and no-break spaces that end a line, such as those
after Word text or after an empty heading's `#` marks; they show nothing in
Markdown.

It removes the escapes Pandoc writes where nothing could read the text
differently; Word text that would be a link keeps them:

```text
Before: Bits \[3:0\] of CTRL\[7\]; the text \[a\](b) is not a link.
After:  Bits [3:0] of CTRL[7]; the text \[a\](b) is not a link.
```

### `duplicate-headings`

With `--qualify-duplicate-headings`:

```text
Before         After
# Block A      # Block A

## Overview    ## Block A - Overview

# Block B      # Block B

## Overview    ## Block B - Overview
```

### `pages`

With `--split-depth 1`, the document becomes `Example.md`, which lists the pages,
and one page per section:

```markdown
# Example

## Sections

- [Introduction](Example/01-introduction.md)
- [Setup](Example/02-setup.md)
```

In `Example/01-introduction.md`, MarkItDown's `[Setup](#_Toc300)` becomes
`[Setup](02-setup.md#setup)`.

### `title`

A document whose first paragraph is in Word's Title style:

```text
Before                   After
Cache Controller         # Cache Controller

# Overview               ## Overview

## Scope                 ### Scope
```

### `checks`

`checks` never changes the output. It compares links, images, and bookmark
positions with Word and reports differences, for example:

```text
checks: 1 issue(s)
  issue (1): a recovered Word hyperlink is missing from the output
```

## Optional controls

The DOCX converter has six optional controls beyond input/output selection:

| Control | Behavior |
| --- | --- |
| `--qualify-duplicate-headings` | Distinguish repeated headings using their context, keeping their levels, and retarget links to the final heading IDs. Off by default. |
| `--split-depth 0..6` | Generate ordered Wiki pages beneath a same-stem directory. Default `0` keeps one Markdown file. |
| `--dry-run` | Convert and validate without replacing final Markdown, images, or pages. |
| `--replace-generated` | Permit replacement of the selected nonempty image/page directories and an existing automatically named Markdown file. |
| `--keep-intermediates DIR` | Retain converter output, extracted media, and a diagnostic report in a new or empty, nonoverlapping directory. |
| `--explain` | After the summary, describe what the items each transform did not apply have in common, without document text (see [Explaining what was not applied](#explaining-what-was-not-applied)). |

Examples:

```sh
md-doc-converter docx-to-md docs/cache.docx --split-depth 2
md-doc-converter docx-to-md docs/cache.docx --qualify-duplicate-headings
md-doc-converter docx-to-md docs/cache.docx --dry-run --keep-intermediates "review diagnostics"
md-doc-converter docx-to-md docs/cache.docx --dry-run --explain
md-doc-converter docx-to-md docs/cache.docx --replace-generated
```

Relative paths resolve from the current directory. When rerunning conversion,
combine the selected controls with `--replace-generated` as required by the
replacement rules below.

Splitting preserves source order, preamble content, section navigation, and
rewritten cross-page/image links. Each page gets a local H1 with a constant relative
offset for its remaining headings, preserving source depth relationships and
intentional gaps. `--split-depth 0` does not remove a page tree left by an earlier
split conversion. Links use the final page-local heading IDs, including when
splitting or duplicate qualification changes a duplicate heading's suffix.

## Replacement, diagnostics, and recovery

An existing automatically derived Markdown destination requires either
`--replace-generated` or explicit selection with `-o`/`--output`. An explicitly
selected Markdown file may be replaced normally. Nonempty image and Wiki-page
directories always require `--replace-generated`, even with an explicit `-o`.
The flag authorizes only the selected generated destinations, never the input
or arbitrary cleanup.

Conversion rejects source aliases, unsafe overlaps, protected roots,
incompatible destination types, and unsafe symlink destinations. Diagnostics
must not overlap the input or generated destinations. A dry run still performs
temporary conversion work; it writes retained diagnostics only when
`--keep-intermediates DIR` is explicitly requested. Reports belong inside this
bundle, not beside the published Markdown.
The report includes table-fallback reasons, each bookmark's final page,
fragment, and destination kind (heading, containing section, or document top),
and a `transforms` section with each transform's status, what it applied, what
it did not apply and why, and any failure. With `--explain`, an `explain`
section holds the same lines as standard error.

All requested artifacts are prepared and validated before publication, with
staging beside each destination so different filesystems are supported. Existing
outputs are backed up until publication succeeds. If a later replacement fails,
the converter attempts to restore prior outputs. Failed restoration reports
surviving recovery paths and retains recoverable backups; inspect those paths
before retrying or removing anything.

Publication is a sequence of per-path replacements with best-effort rollback,
not one atomic multi-file transaction. Callers must serialize conversions
targeting the same output set.

## Before and after converting

Conversion requires unencrypted input. Use an authorized working copy of
protected Office documents. A Word file is a ZIP package; one whose contents
expand to more than 1,024 MiB is refused before any of it is read, since a
crafted package can expand to far more than its own size.

After Word conversion, review the transform summary and warnings, then inspect
tables, code blocks, escaped identifiers, links, images, and the selected Wiki
layout. Conversion reports content it could not carry over and makes no
editorial changes; those belong in a deliberate review of the canonical
Markdown.
