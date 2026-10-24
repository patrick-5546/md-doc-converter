-- Render math with KaTeX: replace each Math element with the HTML that KaTeX renders for it.
--
-- katex-render.mjs renders all of a document's math in one run of MD_DOC_CONVERTER_NODE,
-- Node.js, and a document without math runs none. Invalid TeX is shown in KaTeX's error color
-- instead of failing the render. A document with math gets the stylesheet that
-- MD_DOC_CONVERTER_KATEX_CSS names, with its fonts, as a data URI: Pandoc renders in its
-- sandbox, which reads no file that the document names.

local RENDERER = pandoc.path.join({ pandoc.path.directory(PANDOC_SCRIPT_FILE), "katex-render.mjs" })

local function fail(message)
  error("katex.lua: " .. message, 0)
end

-- The HTML of each formula, a { tex, display } pair, in order.
local function render(formulas)
  local node = os.getenv("MD_DOC_CONVERTER_NODE") or ""
  local succeeded, output = false, "Node.js was not found on PATH"
  if node ~= "" then
    succeeded, output = pcall(pandoc.pipe, node, { RENDERER }, pandoc.json.encode(formulas))
  end
  if not succeeded then
    -- pandoc.pipe raises a table with node's exit status, or a message if node cannot run.
    local reason = tostring(output)
    if type(output) == "table" then
      reason = "node exited with status " .. tostring(output.error_code)
    end
    fail("cannot render math (" .. reason .. "); run `md-doc-converter setup`")
  end
  local html = pandoc.json.decode(output, false)
  if type(html) ~= "table" or #html ~= #formulas then
    fail("katex-render.mjs did not render each formula")
  end
  return html
end

local function stylesheet()
  local path = os.getenv("MD_DOC_CONVERTER_KATEX_CSS") or ""
  if path == "" then
    fail("MD_DOC_CONVERTER_KATEX_CSS is not set")
  end
  return pandoc.mediabag.make_data_uri("text/css", pandoc.system.read_file(path))
end

-- Add a stylesheet to the document's css metadata, which Pandoc's template links.
local function add_stylesheet(meta, uri)
  local css = meta.css
  local entries = pandoc.List()
  if pandoc.utils.type(css) == "List" then
    entries = css
  elseif css ~= nil then
    entries:insert(css)
  end
  entries:insert(uri)
  meta.css = entries
end

function Pandoc(document)
  local formulas = {}
  document:walk({
    Math = function(math)
      formulas[#formulas + 1] = { math.text, math.mathtype == "DisplayMath" }
    end,
  })
  if #formulas == 0 then
    return nil
  end
  local html = render(formulas)
  local position = 0
  document = document:walk({
    Math = function()
      position = position + 1
      return pandoc.RawInline("html", html[position])
    end,
  })
  add_stylesheet(document.meta, stylesheet())
  return document
end
