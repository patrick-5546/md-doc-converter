-- Render Mermaid diagrams, the `mermaid` code blocks, as SVG images with mermaid-cli.
--
-- mermaid-cli renders all of a document's diagrams in one run, with one browser. Each block
-- becomes a paragraph holding an image of its diagram, embedded as a data URI and tagged with
-- the mermaid-diagram class that style-overrides.css scales. Mermaid's defaults apply,
-- including its theme and layout for each diagram type, except that mermaid-config.json gives
-- labels the text's fonts; a diagram can choose others in its front matter.
--
-- MD_DOC_CONVERTER_MMDC names mermaid-cli's script, which MD_DOC_CONVERTER_NODE, Node.js, runs,
-- and MD_DOC_CONVERTER_MERMAID_CONFIG an optional Mermaid configuration file.

local IMAGE_CLASS = "mermaid-diagram"
-- In Markdown input, mmdc ends a diagram at a line of three backticks or colons.
local FENCE_LINE = "\n[ \t]*[`:][`:][`:][ \t]*\n"

local function is_diagram(block)
  return block.classes:includes("mermaid")
end

local function fail(message)
  error("mermaid.lua: " .. message, 0)
end

-- The command that runs mermaid-cli: Node.js and mermaid-cli's script.
local function mmdc_command()
  local mmdc = os.getenv("MD_DOC_CONVERTER_MMDC") or ""
  if mmdc == "" or not pandoc.path.exists(mmdc) then
    fail("mermaid-cli (mmdc) is not installed; run `md-doc-converter setup`")
  end
  local node = os.getenv("MD_DOC_CONVERTER_NODE") or ""
  if node == "" then
    fail("Node.js, which runs mermaid-cli, was not found on PATH")
  end
  return { node = node, script = mmdc }
end

-- Run mmdc; return whether it succeeded and what it wrote to stderr.
local function run(mmdc, input, output, extra)
  local arguments = { mmdc.script, "--input", input, "--output", output, "--quiet" }
  local config = os.getenv("MD_DOC_CONVERTER_MERMAID_CONFIG") or ""
  if config ~= "" then
    arguments[#arguments + 1] = "--configFile"
    arguments[#arguments + 1] = config
  end
  for _, value in ipairs(extra or {}) do
    arguments[#arguments + 1] = value
  end
  local status, _, errors = pandoc.system.command(mmdc.node, arguments)
  return not status, errors
end

local function read(file)
  if pandoc.path.exists(file) then
    return pandoc.system.read_file(file)
  end
end

-- Render the diagrams at `indices` in one run; return their SVGs by index, or nil.
local function render_together(mmdc, directory, diagrams, indices)
  local parts = {}
  for _, index in ipairs(indices) do
    parts[#parts + 1] = "```mermaid\n" .. diagrams[index] .. "\n```\n"
  end
  local input = pandoc.path.join({ directory, "diagrams.md" })
  local artefacts = pandoc.path.join({ directory, "svg" })
  pandoc.system.write_file(input, table.concat(parts, "\n"))
  pandoc.system.make_directory(artefacts, true)
  local output = pandoc.path.join({ directory, "rendered.md" })
  if not run(mmdc, input, output, { "--artefacts", artefacts }) then
    return nil
  end
  local svgs = {}
  for position, index in ipairs(indices) do
    -- mmdc names the image of the nth diagram <output name>-<n>.svg.
    svgs[index] = read(pandoc.path.join({ artefacts, "rendered-" .. position .. ".svg" }))
    if not svgs[index] then
      return nil
    end
  end
  return svgs
end

local function describe(diagram, index, count)
  local body = diagram:gsub("^%s*%-%-%-\n.-\n%-%-%-[ \t]*\n", "", 1)
  local first = body:match("^%s*([^\n]*)") or ""
  if #first > 40 then
    first = first:sub(1, 37) .. "..."
  end
  return string.format("Mermaid diagram %d of %d (%s)", index, count, first)
end

-- mmdc's message, without the JavaScript stack trace that follows it.
local function message(errors)
  local lines = {}
  for line in ((errors or "") .. "\n"):gmatch("(.-)\n") do
    if line:find("^%s+at ") or line:find("%(https?://") or line:find("%(file://") then
      break
    end
    lines[#lines + 1] = line
  end
  return (table.concat(lines, "\n"):gsub("%s+$", ""))
end

local function render_alone(mmdc, directory, diagrams, index)
  local input = pandoc.path.join({ directory, "diagram-" .. index .. ".mmd" })
  local output = pandoc.path.join({ directory, "diagram-" .. index .. ".svg" })
  pandoc.system.write_file(input, diagrams[index])
  local succeeded, errors = run(mmdc, input, output)
  local svg = succeeded and read(output)
  if not svg then
    fail(describe(diagrams[index], index, #diagrams) .. " could not be rendered:\n" .. message(errors))
  end
  return svg
end

local function render(mmdc, directory, diagrams)
  local together, alone = {}, {}
  for index, diagram in ipairs(diagrams) do
    local list = ("\n" .. diagram .. "\n"):find(FENCE_LINE) and alone or together
    list[#list + 1] = index
  end
  local svgs = {}
  if #together > 0 then
    svgs = render_together(mmdc, directory, diagrams, together)
    if not svgs then
      -- Render each diagram on its own to name the one that fails.
      svgs, alone = {}, {}
      for index = 1, #diagrams do
        alone[index] = index
      end
    end
  end
  for _, index in ipairs(alone) do
    svgs[index] = render_alone(mmdc, directory, diagrams, index)
  end
  return svgs
end

function Pandoc(document)
  local diagrams = {}
  document:walk({
    CodeBlock = function(block)
      if is_diagram(block) then
        diagrams[#diagrams + 1] = block.text
      end
    end,
  })
  if #diagrams == 0 then
    return nil
  end
  local mmdc = mmdc_command()
  local svgs = pandoc.system.with_temporary_directory("md-doc-converter-mermaid", function(directory)
    return render(mmdc, directory, diagrams)
  end)
  local position = 0
  return document:walk({
    CodeBlock = function(block)
      if not is_diagram(block) then
        return nil
      end
      position = position + 1
      local source = pandoc.mediabag.make_data_uri("image/svg+xml", svgs[position])
      return pandoc.Para({ pandoc.Image({}, source, "", pandoc.Attr(block.identifier, { IMAGE_CLASS })) })
    end,
  })
end
