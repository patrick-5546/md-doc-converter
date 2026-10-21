-- GFM reads an escaped pipe in a pipe-table cell as a pipe, even in a code span, where
-- Pandoc's Markdown reader keeps the backslash: `a\|b` in a cell is the code a|b.
function Table(tbl)
  return tbl:walk({
    Code = function(code)
      code.text = code.text:gsub("\\|", "|")
      return code
    end,
  })
end
