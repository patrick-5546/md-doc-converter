-- The identifiers that Pandoc's Markdown reader gives a folder's headings when the folder's
-- pages are read as one document. Each page, a Div of its headings, is written as Markdown on
-- its own, so that a heading keeps an explicit identifier only when it is not the one that the
-- reader would make; then all the pages' headings are read back as one document, in order,
-- which this returns. The metadata field md-doc-converter-reader names the reader.
function Pandoc(document)
  local reader = pandoc.utils.stringify(document.meta["md-doc-converter-reader"])
  local writer = reader .. "-simple_tables-raw_attribute"
  local pages = {}
  for _, page in ipairs(document.blocks) do
    pages[#pages + 1] = pandoc.write(pandoc.Pandoc(page.content), writer)
  end
  return pandoc.read(table.concat(pages, "\n\n"), reader)
end
