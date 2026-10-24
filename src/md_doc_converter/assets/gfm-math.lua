-- Read GitHub's math syntax as Pandoc's GFM reader does (its tex_math_gfm extension, which
-- Pandoc's Markdown reader lacks), before katex.lua draws the math: a code block whose only
-- attribute is the math class is display math, and inline math between equal runs of
-- backticks, $`...`$, loses them. As on GitHub, \\{ and \\} in inline math are braces.

function CodeBlock(block)
  if
    block.identifier == ""
    and #block.classes == 1
    and block.classes[1] == "math"
    and #block.attributes == 0
  then
    return pandoc.Para({ pandoc.Math("DisplayMath", block.text) })
  end
end

function Math(math)
  if math.mathtype ~= "InlineMath" then
    return nil
  end
  local text = math.text:gsub("\\\\([{}])", "\\%1")
  local ticks, rest = text:match("^(`+)(.*)$")
  -- rest must end with the same run of backticks, and something must be left between them.
  if
    ticks
    and #rest > #ticks
    and rest:sub(-#ticks) == ticks
    and rest:sub(-#ticks - 1, -#ticks - 1) ~= "`"
  then
    text = rest:sub(1, -#ticks - 1)
  end
  math.text = text
  return math
end
