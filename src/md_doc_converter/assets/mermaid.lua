-- Render Mermaid diagrams, the `mermaid` code blocks, as SVG images with mermaid-cli.
--
-- mermaid-cli renders all of a document's diagrams in one run, with one browser. Each block
-- becomes a paragraph holding an image of its diagram, embedded as a data URI and tagged with
-- the mermaid-diagram class that style-overrides.css scales. Mermaid's defaults apply,
-- including its theme and layout for each diagram type, except that mermaid-config.json gives
-- labels the text's fonts; a diagram can choose others in its front matter.
--
-- An image's alt text is the diagram's accessible title and description, its accTitle and
-- accDescr; or else the title in its front matter; or else its type, such as "Flowchart".
--
-- MD_DOC_CONVERTER_MMDC names mermaid-cli's script, which MD_DOC_CONVERTER_NODE, Node.js, runs,
-- and MD_DOC_CONVERTER_MERMAID_CONFIG an optional Mermaid configuration file.

local IMAGE_CLASS = "mermaid-diagram"
-- In Markdown input, mmdc ends a diagram at a line of three backticks or colons.
local FENCE_LINE = "\n[ \t]*[`:][`:][`:][ \t]*\n"
-- The alt text for each type of diagram, which Mermaid names in its SVG's aria-roledescription.
local DIAGRAM_TYPES = {
  agentflow = "Agent flow diagram",
  architecture = "Architecture diagram",
  block = "Block diagram",
  c4 = "C4 diagram",
  classDiagram = "Class diagram",
  cynefin = "Cynefin diagram",
  er = "Entity relationship diagram",
  eventmodeling = "Event model",
  ["flowchart-elk"] = "Flowchart",
  ["flowchart-v2"] = "Flowchart",
  gantt = "Gantt chart",
  gitGraph = "Git graph",
  ishikawa = "Ishikawa diagram",
  journey = "User journey",
  kanban = "Kanban board",
  mindmap = "Mind map",
  packet = "Packet diagram",
  pie = "Pie chart",
  quadrantChart = "Quadrant chart",
  radar = "Radar chart",
  railroad = "Railroad diagram",
  railroadAbnf = "Railroad diagram",
  railroadEbnf = "Railroad diagram",
  railroadPeg = "Railroad diagram",
  requirement = "Requirement diagram",
  sankey = "Sankey diagram",
  sequence = "Sequence diagram",
  stateDiagram = "State diagram",
  swimlane = "Swimlane diagram",
  timeline = "Timeline",
  treeView = "Tree view",
  treemap = "Treemap",
  usecase = "Use case diagram",
  venn = "Venn diagram",
  wardley = "Wardley map",
  xychart = "XY chart",
  zenuml = "Sequence diagram",
}

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

-- A diagram's front matter, between lines of three dashes, or nil; and the rest of it.
local function front_matter(diagram)
  local front, body = diagram:match("^%s*%-%-%-[ \t]*\n(.-)\n%-%-%-[ \t]*\n(.*)$")
  if front then
    return front, body
  end
  return nil, diagram
end

local function describe(diagram, index, count)
  local _, body = front_matter(diagram)
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

local function squeeze(text)
  return (text:gsub("%s+", " "):gsub("^ ", ""):gsub(" $", ""))
end

-- The accessible title and description that a diagram gives itself, as sentences: `accTitle:`,
-- and `accDescr:` or `accDescr { ... }` over one line or several. They are read from the
-- diagram, since Mermaid escapes them twice in its SVG and drops any text that looks like a tag.
local function accessible_text(diagram)
  local _, body = front_matter(diagram)
  local parts, open = {}, nil
  for line in (body .. "\n"):gmatch("(.-)\n") do
    if open then
      local text, closed = line:match("^([^}]*)(}?)")
      open[#open + 1] = text
      if closed ~= "" then
        parts[#parts + 1] = squeeze(table.concat(open, " "))
        open = nil
      end
    else
      local key, text = line:match("^%s*(acc%a+)%s*:%s*(.-)%s*$")
      local braced = line:match("^%s*accDescr%s*{(.*)$")
      if key == "accTitle" or key == "accDescr" then
        parts[#parts + 1] = squeeze(text)
      elseif braced then
        local inline, closed = braced:match("^([^}]*)(}?)")
        if closed ~= "" then
          parts[#parts + 1] = squeeze(inline)
        else
          open = { inline }
        end
      end
    end
  end
  local text = ""
  for _, part in ipairs(parts) do
    if text == "" then
      text = part
    elseif part ~= "" then
      text = text .. (text:find("[%.%?!:]$") and " " or ". ") .. part
    end
  end
  return text
end

-- The title in a diagram's front matter, which Mermaid shows above it, without its quotes.
local function front_matter_title(diagram)
  local front = front_matter(diagram)
  for line in ((front or "") .. "\n"):gmatch("(.-)\n") do
    local value = line:match("^title:%s*(.-)%s*$")
    if value then
      local quote = value:sub(1, 1)
      if #value >= 2 and (quote == '"' or quote == "'") and value:sub(-1) == quote then
        value = value:sub(2, -2)
      end
      return value
    end
  end
  return ""
end

local function alt_text(diagram, svg)
  local text = accessible_text(diagram)
  if text == "" then
    text = front_matter_title(diagram)
  end
  if text == "" then
    local root = svg:match("<svg%f[%s>][^>]*>") or ""
    text = DIAGRAM_TYPES[root:match('%saria%-roledescription="([^"]*)"') or ""] or "Mermaid diagram"
  end
  return text
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
      local alt = alt_text(diagrams[position], svgs[position])
      return pandoc.Para({ pandoc.Image(alt, source, "", pandoc.Attr(block.identifier, { IMAGE_CLASS })) })
    end,
  })
end
