-- Keep footnotes source-scoped when independently prepared Markdown is combined.
function Pandoc(document)
  local prefix = pandoc.utils.stringify(document.meta["md-doc-converter-note-prefix"])
  document.meta["md-doc-converter-note-prefix"] = nil
  local definitions = {}
  document = document:walk({
    Note = function(note)
      local label = prefix .. tostring(#definitions + 1)
      local body = pandoc.write(
        pandoc.Pandoc(note.content), FORMAT, PANDOC_WRITER_OPTIONS
      ):gsub("\n+$", ""):gsub("\n", "\n    ")
      definitions[#definitions + 1] = "[^" .. label .. "]: " .. body
      return pandoc.RawInline("markdown", "[^" .. label .. "]")
    end
  })
  if #definitions > 0 then
    document.blocks:insert(pandoc.RawBlock("markdown", table.concat(definitions, "\n\n")))
  end
  return document
end
