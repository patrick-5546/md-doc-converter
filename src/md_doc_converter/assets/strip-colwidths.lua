-- Strip explicit column widths from tables so columns auto-size based on content.
-- Pandoc assigns equal-width columns from pipe table separator lines,
-- producing rigid layouts regardless of cell content.
function Table(tbl)
  for i, colspec in ipairs(tbl.colspecs) do
    colspec[2] = nil  -- remove width (ColWidth -> ColWidthDefault)
  end
  return tbl
end
